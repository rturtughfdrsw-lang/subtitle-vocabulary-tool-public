from dataclasses import dataclass
from html import unescape
import json
from pathlib import Path
import re
import unicodedata


class SubtitleSourceError(RuntimeError):
    pass


class SubtitleNotFoundError(SubtitleSourceError):
    pass


class SubtitleSourceAmbiguousError(SubtitleSourceError):
    pass


class SubtitleFileNotFoundError(SubtitleSourceError):
    pass


class SubtitleFormatUnsupportedError(SubtitleSourceError):
    pass


class SubtitleContentEmptyError(SubtitleSourceError):
    pass


@dataclass(frozen=True)
class SubtitleDocument:
    canonical_text: str
    source_type: str
    source_file_names: tuple[str, ...]


_TIME_RANGE = re.compile(
    r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->\s*"
    r"\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}"
)
_HTML_TAG = re.compile(r"<[^>]+>")
_ASS_OVERRIDE = re.compile(r"\{\\[^}]*\}")
_ENGLISH_LANGUAGE_TAG = re.compile(
    r"(?:^|[._ -])en(?:[-_][a-z]{2,8})?(?=[._ -]|$)", re.IGNORECASE
)
_TASK_SUBTITLE_SUFFIXES = {".srt", ".vtt", ".ass"}
_DIRECT_SUBTITLE_SUFFIXES = _TASK_SUBTITLE_SUFFIXES | {".txt"}


def _remove_formatting(text: str) -> str:
    text = unescape(text)
    text = _ASS_OVERRIDE.sub("", text)
    text = _HTML_TAG.sub("", text)
    return text


def _timed_text_lines(text: str) -> list[str]:
    source_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[str] = []
    in_vtt_header = False
    in_metadata_block = False
    for index, line in enumerate(source_lines):
        stripped = line.strip().lstrip("\ufeff")
        if not stripped:
            in_vtt_header = False
            in_metadata_block = False
            continue
        if stripped.upper() == "WEBVTT":
            in_vtt_header = True
            continue
        if in_vtt_header or in_metadata_block:
            continue
        if stripped.startswith(("NOTE", "STYLE", "REGION")):
            in_metadata_block = True
            continue
        next_line = ""
        if index + 1 < len(source_lines):
            next_line = source_lines[index + 1].strip()
        if _TIME_RANGE.match(stripped):
            continue
        if next_line and _TIME_RANGE.match(next_line):
            continue
        if stripped.startswith("X-TIMESTAMP-MAP"):
            continue
        result.append(stripped)
    return result


def _ass_text_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_events = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw_line.strip().lstrip("\ufeff")
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = stripped.casefold() == "[events]"
            continue
        if not in_events or not stripped.casefold().startswith("dialogue:"):
            continue
        fields = stripped.split(":", 1)[1].lstrip().split(",", 9)
        if len(fields) == 10:
            dialogue = fields[9].replace("\\h", " ")
            lines.extend(dialogue.replace("\\N", "\n").replace("\\n", "\n").split("\n"))
    return lines


def canonicalize_subtitle_text(text: str, suffix: str = ".txt") -> str:
    suffix = suffix.casefold()
    if suffix in {".srt", ".vtt"}:
        lines = _timed_text_lines(text)
    elif suffix == ".ass":
        lines = _ass_text_lines(text)
    else:
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    canonical_lines: list[str] = []
    for line in lines:
        cleaned = _remove_formatting(line)
        cleaned = re.sub(r"[^\S\r\n]+", " ", cleaned).strip()
        if cleaned:
            canonical_lines.append(cleaned)
    return unicodedata.normalize("NFC", "\n".join(canonical_lines))


def _remote_subtitle(folder: Path) -> Path:
    candidates = sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file() and path.suffix.casefold() in _TASK_SUBTITLE_SUFFIXES
        ),
        key=lambda path: path.name.casefold(),
    )
    if not candidates:
        raise SubtitleNotFoundError("网页原字幕任务中没有可统计的字幕文件。")
    if len(candidates) == 1:
        return candidates[0]

    english = [path for path in candidates if _ENGLISH_LANGUAGE_TAG.search(path.name)]
    if len(english) == 1:
        return english[0]

    exact_en = [
        path
        for path in english
        if re.search(r"(?:^|[._ -])en(?=\.[^.]+$)", path.name, re.IGNORECASE)
    ]
    if len(exact_en) == 1:
        return exact_en[0]
    raise SubtitleSourceAmbiguousError(
        "任务目录中存在多个字幕文件，无法唯一确定英文统计来源。"
    )


def _select_source(folder: Path, result_type: str) -> Path:
    fixed_names = {
        "硬字幕 OCR": "硬字幕OCR文字.txt",
        "语音识别字幕": "语音识别文字.txt",
        "极速语音识别字幕": "语音识别文字.txt",
        "视频字幕轨": "视频字幕轨.srt",
    }
    if result_type == "网页原字幕":
        return _remote_subtitle(folder)
    filename = fixed_names.get(result_type)
    if filename is None:
        raise SubtitleSourceError(f"不支持的字幕结果类型：{result_type}")
    path = folder / filename
    if not path.is_file():
        raise SubtitleNotFoundError(f"字幕结果文件不存在：{filename}")
    return path


def read_task_subtitle(task_folder: str | Path) -> SubtitleDocument:
    folder = Path(task_folder)
    status_path = folder / "status.json"
    if not status_path.is_file():
        raise SubtitleSourceError("任务目录缺少 status.json。")
    try:
        status = json.loads(status_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SubtitleSourceError("无法读取任务 status.json。") from exc
    if status.get("ok") is not True:
        raise SubtitleSourceError("只能统计已经成功完成的字幕任务。")

    result_type = str(status.get("type", ""))
    source = _select_source(folder, result_type)
    try:
        raw_text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise SubtitleSourceError(f"无法读取字幕结果文件：{source.name}") from exc
    canonical = canonicalize_subtitle_text(raw_text, source.suffix)
    if not canonical:
        raise SubtitleSourceError("字幕结果中没有可统计的正文。")
    return SubtitleDocument(canonical, result_type, (source.name,))


def read_subtitle_file(subtitle_file: str | Path) -> SubtitleDocument:
    source = Path(subtitle_file)
    if not source.is_file():
        raise SubtitleFileNotFoundError(f"字幕文件不存在：{source}")
    suffix = source.suffix.casefold()
    if suffix not in _DIRECT_SUBTITLE_SUFFIXES:
        raise SubtitleFormatUnsupportedError(
            "不支持的字幕格式；请选择 .srt、.vtt、.ass 或 .txt 文件。"
        )
    try:
        raw_text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise SubtitleSourceError(f"无法读取字幕文件：{source.name}") from exc
    canonical = canonicalize_subtitle_text(raw_text, suffix)
    if not canonical:
        raise SubtitleContentEmptyError("字幕文件中没有可统计的正文。")
    return SubtitleDocument(canonical, "手动字幕文件", (source.name,))
