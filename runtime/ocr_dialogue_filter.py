from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, replace


HAN_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
LEADING_COM_RE = re.compile(r"(?i)^\s*com(?:\s+|[.,;:，。；：|_-]+\s*)")
SITE_MARKER_RE = re.compile(
    r"(?i)(?:官[网網]|宫网|赞助发布|赞助发行|62094)"
)
GAMBLING_RE = re.compile(
    r"(?:体育.?棋牌|棋牌体育|棋牌.{0,18}(?:六合彩|百家乐|澳门|电子|"
    r"麻将|牛牛|炸金花|捕鱼)|"
    r"(?:香港|澳门|电子).{0,3}六合彩|六合彩|百家乐|"
    r"真人娱乐|电子捕鱼|^棋牌[，。！？；：、,.!?;:]*$)"
)
CORRUPTED_GAMBLING_TAIL_RE = re.compile(
    r"(?:麻将.{0,6}(?:炸[金花]|捕鱼)|(?:胡了|了)?炸[金花])"
)
NATURAL_ACTIVITY_RE = re.compile(
    r"(?:去|玩|打|想|一起|正在|喜欢).{0,2}(?:炸金花|捕鱼)"
)
ENGLISH_CREDIT_RE = re.compile(
    r"(?i)^(?:(?:co-|executive |associate |assistant |line )*)"
    r"(?:producer|director|writer|starring|casting|editor|edited by|"
    r"camera(?:s)? by|cinematography|music by|costume|make-?up|"
    r"hair stylist|script supervisor|re-recording mixer|"
    r"production coordinator|guest star)\b"
)
CHINESE_CREDIT_RE = re.compile(
    r"^(?:(?:执行|联合|助理|总|第一|第二|现场|后期|科学|美术|服装)*)"
    r"(?:制片人|制片主任|导演|副导演|演员|主演|客串|摄影|剪辑|"
    r"选角|顾问|主题曲|化妆师|美发师|混录师|布景|编剧)"
)
COPYRIGHT_RE = re.compile(
    r"(?i)(?:未经授权|版权归|版权所有|all rights reserved|"
    r"unauthori[sz]ed (?:copy|duplication)|copyright)"
)
SHORT_DIALOGUE = {
    "no",
    "yes",
    "why",
    "okay",
    "ok",
    "hi",
    "hey",
    "uh",
    "oh",
    "what",
    "how",
    "good",
    "right",
    "great",
    "really",
    "sorry",
    "thanks",
    "hello",
}
KNOWN_OCR_FRAGMENTS = {
    "ASAN",
    "GALITIOA",
    "UTION",
}


@dataclass(frozen=True)
class TextBlock:
    text: str
    score: float
    center_x: float
    center_y: float
    width: float
    height: float


@dataclass(frozen=True)
class FrameText:
    timestamp: float
    blocks: tuple[TextBlock, ...]


@dataclass
class FilterStats:
    raw_blocks: int = 0
    persistent_removed: int = 0
    promotions_removed: int = 0
    credits_removed: int = 0
    noise_removed: int = 0


def normalize_for_output(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text)).strip()
    value = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", value)
    value = re.sub(r"([，。！？；：、])\s+", r"\1", value)
    return value


def clean_promotional_text(text: str) -> tuple[str, bool]:
    value = normalize_for_output(text)
    changed = False
    leading = LEADING_COM_RE.match(value)
    if leading:
        value = value[leading.end() :].strip()
        changed = True
    elif value.lower() == "com":
        return "", True

    starts: list[int] = []
    for pattern in (SITE_MARKER_RE, GAMBLING_RE):
        match = pattern.search(value)
        if match:
            starts.append(match.start())
    if starts:
        value = value[: min(starts)].strip(" ,.;:，。；：|-_")
        changed = True
    elif not NATURAL_ACTIVITY_RE.search(value):
        corrupted = CORRUPTED_GAMBLING_TAIL_RE.search(value)
        if corrupted:
            prefix = value[: corrupted.start()].strip(" ,.;:，。；：|-_")
            value = "" if len(text_fingerprint(prefix)) < 4 else prefix
            changed = True
        elif value.endswith("捕鱼") and len(text_fingerprint(value)) >= 8:
            value = ""
            changed = True
    return value, changed


def clean_transcript_text(text: str) -> tuple[str, bool]:
    """Remove only unambiguous promotional markers from spoken text."""
    value = normalize_for_output(text)
    changed = False
    leading = LEADING_COM_RE.match(value)
    if leading:
        value = value[leading.end() :].strip()
        changed = True
    elif value.lower() == "com":
        return "", True

    starts: list[int] = []
    for pattern in (SITE_MARKER_RE, GAMBLING_RE):
        match = pattern.search(value)
        if match:
            starts.append(match.start())
    if starts:
        value = value[: min(starts)].strip(" ,.;:，。；：|-_")
        changed = True
    return value, changed


def is_credit_or_copyright(text: str) -> bool:
    value = normalize_for_output(text)
    if not value:
        return False
    return bool(
        COPYRIGHT_RE.search(value)
        or ENGLISH_CREDIT_RE.search(value)
        or CHINESE_CREDIT_RE.search(value)
    )


def is_obvious_noise(
    text: str, *, has_companion_translation: bool = False
) -> bool:
    value = normalize_for_output(text)
    if not value:
        return True
    word = re.sub(r"[^A-Za-z]", "", value).lower()
    if word in SHORT_DIALOGUE:
        return False
    if HAN_RE.search(value):
        return False
    if has_companion_translation and LATIN_RE.search(value):
        return False
    if value.count("(") != value.count(")") or value.count("[") != value.count(
        "]"
    ):
        if len(value) <= 8:
            return True
    formula_marks = len(re.findall(r"[=⇒→×÷<>]", value))
    useful = len(re.findall(r"[A-Za-z0-9]", value))
    if formula_marks >= 2 or (formula_marks and useful <= 8):
        return True
    compact = re.sub(r"[^A-Za-z]", "", value)
    if compact in KNOWN_OCR_FRAGMENTS:
        return True
    symbols = len(re.findall(r"[^A-Za-z0-9\s.,!?'\-]", value))
    if useful and symbols / max(1, useful + symbols) >= 0.35:
        return True
    return False


def clean_text_content(text: str) -> str:
    value, _ = clean_promotional_text(text)
    if not value:
        return ""
    if is_credit_or_copyright(value) or is_obvious_noise(value):
        return ""
    return value


def text_fingerprint(text: str) -> str:
    return re.sub(
        r"[^\u4e00-\u9fffA-Za-z0-9]+", "", normalize_for_output(text).lower()
    )


def block_key(block: TextBlock) -> tuple[str, int, int]:
    return (
        text_fingerprint(block.text),
        round(block.center_x / 0.04),
        round(block.center_y / 0.04),
    )


def find_persistent_keys(
    frames: list[FrameText],
) -> set[tuple[str, int, int]]:
    if not frames:
        return set()
    occurrences: dict[tuple[str, int, int], list[float]] = defaultdict(list)
    for frame in frames:
        seen: set[tuple[str, int, int]] = set()
        for item in frame.blocks:
            key = block_key(item)
            if len(key[0]) < 4 or key in seen:
                continue
            occurrences[key].append(frame.timestamp)
            seen.add(key)

    total_frames = len(frames)
    total_span = max(0.0, frames[-1].timestamp - frames[0].timestamp)
    persistent: set[tuple[str, int, int]] = set()
    for key, timestamps in occurrences.items():
        count = len(timestamps)
        span = timestamps[-1] - timestamps[0]
        covers_short_video = (
            count >= max(4, math.ceil(total_frames * 0.25))
            and span >= max(4.0, total_span * 0.50)
        )
        recurs_across_long_video = (
            count >= max(10, math.ceil(total_frames * 0.02))
            and span >= max(20.0, total_span * 0.20)
        )
        if covers_short_video or recurs_across_long_video:
            persistent.add(key)
    return persistent


def _horizontal_overlap(left: TextBlock, right: TextBlock) -> bool:
    left_start = left.center_x - left.width / 2
    left_end = left.center_x + left.width / 2
    right_start = right.center_x - right.width / 2
    right_end = right.center_x + right.width / 2
    return min(left_end, right_end) >= max(left_start, right_start)


def _has_companion_translation(
    item: TextBlock, blocks: tuple[TextBlock, ...]
) -> bool:
    item_has_han = bool(HAN_RE.search(item.text))
    item_has_latin = bool(LATIN_RE.search(item.text))
    for other in blocks:
        if other is item or abs(other.center_y - item.center_y) > 0.18:
            continue
        if item_has_han and LATIN_RE.search(other.text):
            return True
        if item_has_latin and HAN_RE.search(other.text):
            return True
    return False


def _credit_indexes(blocks: tuple[TextBlock, ...]) -> set[int]:
    direct = {
        index
        for index, item in enumerate(blocks)
        if is_credit_or_copyright(item.text)
    }
    related = set(direct)
    for index in direct:
        source = blocks[index]
        for other_index, other in enumerate(blocks):
            if (
                abs(source.center_y - other.center_y) <= 0.08
                and _horizontal_overlap(source, other)
            ):
                related.add(other_index)
    return related


def clean_frames(
    frames: list[FrameText],
) -> tuple[list[tuple[float, list[TextBlock]]], FilterStats]:
    persistent = find_persistent_keys(frames)
    stats = FilterStats()
    cleaned_frames: list[tuple[float, list[TextBlock]]] = []
    for frame in frames:
        credit_indexes = _credit_indexes(frame.blocks)
        cleaned_blocks: list[TextBlock] = []
        for index, item in enumerate(frame.blocks):
            stats.raw_blocks += 1
            if index in credit_indexes:
                stats.credits_removed += 1
                continue

            value, promotion_changed = clean_promotional_text(item.text)
            if promotion_changed:
                stats.promotions_removed += 1
            if not value:
                continue
            candidate = replace(item, text=value)

            if block_key(candidate) in persistent:
                stats.persistent_removed += 1
                continue
            if is_obvious_noise(
                candidate.text,
                has_companion_translation=_has_companion_translation(
                    item, frame.blocks
                ),
            ):
                stats.noise_removed += 1
                continue
            cleaned_blocks.append(candidate)
        cleaned_frames.append((frame.timestamp, cleaned_blocks))
    return cleaned_frames, stats
