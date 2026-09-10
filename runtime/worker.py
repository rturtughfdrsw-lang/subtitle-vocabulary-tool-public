from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from task_runtime import (
    DownloadProgress,
    ProcessStalled,
    TaskCancelled,
    parse_download_progress,
    run_process,
)
from media_tools import MediaTools, resolve_media_tools


ROOT = MODULE_DIR
TOOL_ROOT = ROOT.parent
RESULT_ROOT = TOOL_ROOT / "字幕结果"
BIN = ROOT / "bin"
YTDLP = BIN / "yt-dlp.exe"
_MEDIA_TOOLS: MediaTools | None = None
DOWNLOAD_TEMPLATE = (
    "download:PROGRESS:%(progress._percent_str)s|"
    "%(progress._speed_str)s|%(progress._eta_str)s"
)
URL_RE = re.compile(r"https?://[^\s]+", re.I)
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
GIB = 1024**3
AUDIO_MIN_FREE = 1 * GIB
OCR_VIDEO_MIN_FREE = 2 * GIB
FINAL_VIDEO_MIN_FREE = 3 * GIB
ROUTE_CACHE_PATH = ROOT / "route_cache.json"
ROUTE_CACHE_TTL_SECONDS = 7 * 24 * 3600


def get_media_tools() -> MediaTools:
    global _MEDIA_TOOLS
    if _MEDIA_TOOLS is None:
        _MEDIA_TOOLS = resolve_media_tools(ROOT)
    return _MEDIA_TOOLS


def elapsed_seconds(started_at: float, now: float | None = None) -> int:
    current = time.monotonic() if now is None else float(now)
    return max(0, int(current - float(started_at)))


def format_elapsed(seconds: int | float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"


@contextmanager
def measure_phase(
    folder: Path,
    timings: dict[str, int],
    name: str,
):
    started_at = time.monotonic()
    try:
        yield
    finally:
        duration = elapsed_seconds(started_at)
        timings[name] = timings.get(name, 0) + duration
        log(folder, f"阶段耗时：{name} {format_elapsed(duration)}。")


def write_json(path, data, *, required=True):
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    last_error = None
    try:
        for attempt in range(8):
            try:
                temp.write_text(
                    json.dumps(data, ensure_ascii=False),
                    encoding="utf-8",
                )
                temp.replace(path)
                return True
            except OSError as exc:
                last_error = exc
                if (
                    getattr(exc, "winerror", None) not in {5, 32}
                    or attempt == 7
                ):
                    break
                time.sleep(0.05 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)
    if required and last_error is not None:
        raise last_error
    return False


def progress(folder, value, message):
    write_json(
        folder / "progress.json",
        {"value": int(value), "message": message},
        required=False,
    )


def log(folder, text):
    with (folder / "运行记录.txt").open("a", encoding="utf-8-sig") as handle:
        handle.write(str(text).rstrip() + "\n")


def redact_url_queries(text: str) -> str:
    def replace(match: re.Match) -> str:
        value = match.group(0)
        if "?" not in value:
            return value
        return value.split("?", 1)[0] + "?[查询参数已隐藏]"

    return URL_RE.sub(replace, str(text))


def preserve_failure_log(
    folder: Path,
    *,
    logs_dir: Path | None = None,
    limit: int = 30,
) -> Path | None:
    source = folder / "运行记录.txt"
    if not source.exists():
        return None
    destination_root = logs_dir or (TOOL_ROOT / "失败日志")
    destination_root.mkdir(parents=True, exist_ok=True)
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", folder.name).strip(" .")
    label = label[:80] or "task"
    destination = destination_root / (
        f"{datetime.now():%Y%m%d-%H%M%S}-{label}.txt"
    )
    content = source.read_text(encoding="utf-8-sig", errors="replace")
    destination.write_text(
        redact_url_queries(content),
        encoding="utf-8-sig",
    )
    keep = max(1, int(limit))
    logs = sorted(
        destination_root.glob("*.txt"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    for old in logs[keep:]:
        old.unlink(missing_ok=True)
    return destination


def download_progress_message(
    phase: str,
    state: DownloadProgress,
) -> str:
    message = f"正在下载{phase}… {int(state.percent)}%"
    if state.speed and state.speed.upper() not in {"N/A", "UNKNOWN"}:
        message += f" · {state.speed}"
    if state.eta and state.eta.upper() not in {"N/A", "UNKNOWN"}:
        message += f" · 剩余{state.eta}"
    return message


def resolved_progress_message(mode: str) -> str:
    if mode == "audio":
        return "已找到视频，正在准备提取 MP3 音频…"
    if mode == "video":
        return "已找到视频，正在准备下载 MP4 视频…"
    if mode == "fast":
        return "已找到视频，正在极速检查远程字幕…"
    return "已找到视频，正在检查字幕轨…"


def friendly_subprocess_error(
    lines: Sequence[str],
    code: int,
) -> str:
    text = "\n".join(map(str, lines)).lower()
    if (
        "eof occurred in violation of protocol" in text
        or "fragment not found" in text
    ):
        return "视频分片不可用（SSL EOF）。"
    if "no space left on device" in text or "not enough space" in text:
        return "磁盘空间不足，无法继续保存结果。"
    if "drm" in text:
        return "视频使用 DRM 加密，当前工具无法下载。"
    if "http error 403" in text or "403 forbidden" in text:
        return "网站拒绝访问（403），链接可能已过期或需要登录。"
    if (
        "sign in" in text
        or "login required" in text
        or "cookies-from-browser" in text
        or "authentication required" in text
    ):
        return "该视频需要登录或 Cookie，当前地址无法直接获取。"
    if "unsupported url" in text:
        return "该网页暂不支持，请尝试粘贴 m3u8 或 MP4 直链。"
    return f"子任务失败（错误码 {code}）"


def friendly_resolve_error(output: str) -> str:
    text = str(output)
    lower = text.lower()
    if "页面内容过大" in text:
        return "页面内容过大，请粘贴视频页面、m3u8 或 MP4 直链。"
    if "http error 403" in lower or "403 forbidden" in lower:
        return "网站拒绝访问（403），链接可能已过期或需要登录。"
    if "timed out" in lower or "timeout" in lower:
        return "访问视频页面超时，请检查网络后重试。"
    if (
        "getaddrinfo failed" in lower
        or "name or service not known" in lower
        or "nodename nor servname" in lower
    ):
        return "无法解析网站域名，请检查网址或网络。"
    return "无法访问或解析这个视频页面，请检查网址和网络。"


def is_retryable_download_error(exc: Exception) -> bool:
    if isinstance(exc, ProcessStalled):
        return True
    text = str(exc).lower()
    non_retryable = (
        "403",
        "drm",
        "cookie",
        "需要登录",
        "暂不支持",
        "磁盘空间不足",
        "unsupported url",
    )
    return not any(marker in text for marker in non_retryable)


def is_broken_fragment_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "分片不可用" in text or "ssl eof" in text or "fragment not found" in text


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", str(text))


def validate_video_metadata(metadata: dict) -> None:
    streams = metadata.get("streams") or []
    video_streams = [
        stream for stream in streams if stream.get("codec_type") == "video"
    ]
    if not video_streams:
        raise RuntimeError("下载的 MP4 不包含有效视频流。")
    values = [(metadata.get("format") or {}).get("duration")]
    values.extend(stream.get("duration") for stream in video_streams)
    for value in values:
        try:
            if value is not None and float(value) > 0:
                return
        except (TypeError, ValueError):
            continue
    raise RuntimeError("下载的 MP4 缺少有效时长，文件可能不完整。")


def ensure_free_space(
    folder: Path,
    required_bytes: int,
    *,
    available_bytes: int | None = None,
) -> None:
    required = max(0, int(required_bytes))
    available = (
        shutil.disk_usage(folder).free
        if available_bytes is None
        else max(0, int(available_bytes))
    )
    if available < required:
        raise RuntimeError(
            "磁盘空间不足："
            f"至少需要 {required / GIB:.2f} GB，"
            f"当前可用 {available / GIB:.2f} GB。"
        )


def check_cancel(folder: Path) -> None:
    if (folder / "cancel.request").exists():
        raise TaskCancelled("用户取消了任务")


def run(
    folder: Path,
    command: Sequence[str],
    *,
    progress_range: tuple[int, int] | None = None,
    phase: str = "文件",
    inactivity_timeout: float | None = None,
    heartbeat_path: Path | None = None,
) -> None:
    log(
        folder,
        "> "
        + " ".join(map(str, command[:4]))
        + (" …" if len(command) > 4 else ""),
    )

    recent_lines: list[str] = []
    last_logged_bucket = -1

    def handle_line(line: str) -> None:
        nonlocal last_logged_bucket
        state = parse_download_progress(line)
        if state is not None and progress_range is not None:
            start, end = progress_range
            value = start + (end - start) * state.percent / 100
            progress(folder, value, download_progress_message(phase, state))
            bucket = min(10, max(0, int(state.percent) // 10))
            if bucket > last_logged_bucket:
                last_logged_bucket = bucket
                detail = f"下载{phase}进度：{int(state.percent)}%"
                if state.speed and state.speed.upper() not in {"N/A", "UNKNOWN"}:
                    detail += f" · {state.speed}"
                if state.eta and state.eta.upper() not in {"N/A", "UNKNOWN"}:
                    detail += f" · 剩余{state.eta}"
                log(folder, detail)
        elif state is None:
            safe_line = redact_url_queries(strip_ansi(line))
            recent_lines.append(safe_line)
            del recent_lines[:-40]
            log(folder, safe_line)
            lower = safe_line.lower()
            if phase == "视频" and (
                "[merger]" in lower
                or "[videoremuxer]" in lower
                or "merging formats" in lower
                or "remuxing video" in lower
            ):
                progress(folder, 98, "正在合并音视频并生成 MP4…")

    code = run_process(
        command,
        on_line=handle_line,
        cancel_path=folder / "cancel.request",
        inactivity_timeout=inactivity_timeout,
        heartbeat_path=heartbeat_path,
        cwd=ROOT,
    )
    if code != 0:
        raise RuntimeError(friendly_subprocess_error(recent_lines, code))


def capture(
    folder: Path,
    command: Sequence[str],
    *,
    inactivity_timeout: float = 75,
) -> tuple[int, str]:
    lines: list[str] = []
    code = run_process(
        command,
        on_line=lines.append,
        cancel_path=folder / "cancel.request",
        inactivity_timeout=inactivity_timeout,
        cwd=ROOT,
    )
    return code, "\n".join(lines)


def media_host(url: str) -> str:
    try:
        return (urlsplit(str(url)).hostname or "").lower()
    except ValueError:
        return ""


def read_route_cache(
    cache_path: Path = ROUTE_CACHE_PATH,
    *,
    now: float | None = None,
) -> dict[str, str]:
    current = time.time() if now is None else float(now)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    result: dict[str, str] = {}
    for page_host, entry in (payload.get("routes") or {}).items():
        if not isinstance(entry, dict):
            continue
        host = str(entry.get("media_host") or "").lower()
        try:
            saved_at = float(entry.get("saved_at"))
        except (TypeError, ValueError):
            continue
        if (
            page_host
            and host
            and 0 <= current - saved_at <= ROUTE_CACHE_TTL_SECONDS
        ):
            result[str(page_host).lower()] = host
    return result


def remember_successful_route(
    page_url: str,
    media_url: str,
    *,
    cache_path: Path = ROUTE_CACHE_PATH,
    now: float | None = None,
) -> None:
    page = media_host(page_url)
    media = media_host(media_url)
    if not page or not media:
        return
    current = time.time() if now is None else float(now)
    active = read_route_cache(cache_path, now=current)
    active[page] = media
    payload = {
        "routes": {
            host: {"media_host": cached_media, "saved_at": current}
            for host, cached_media in active.items()
        }
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(cache_path, payload, required=False)
    except OSError:
        return


def probe_media_stream(folder: Path, page_url: str, media_url: str) -> bool:
    media_path = urlsplit(media_url).path.lower()
    if ".m3u8" not in media_path:
        command = [
            str(YTDLP),
            "--ffmpeg-location",
            str(get_media_tools().ffmpeg),
            "--referer",
            page_url,
            "--simulate",
            "--no-warnings",
            "--socket-timeout",
            "8",
            "--retries",
            "0",
            "--fragment-retries",
            "0",
            "--print",
            "id",
            media_url,
        ]
        try:
            code, _ = capture(folder, command, inactivity_timeout=15)
        except (ProcessStalled, RuntimeError):
            return False
        return code == 0

    command = [
        str(get_media_tools().ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-rw_timeout",
        "8000000",
        "-headers",
        f"Referer: {page_url}\r\n",
        "-i",
        media_url,
        "-t",
        "0.25",
        "-map",
        "0:v:0?",
        "-map",
        "0:a:0?",
        "-f",
        "null",
        os.devnull,
    ]
    try:
        code, _ = capture(folder, command, inactivity_timeout=15)
    except (ProcessStalled, RuntimeError):
        return False
    return code == 0


def order_downloadable_media_urls(
    folder: Path,
    page_url: str,
    candidates: Sequence[str],
    *,
    cache_path: Path = ROUTE_CACHE_PATH,
    now: float | None = None,
) -> list[str]:
    unique: list[str] = []
    for candidate in candidates:
        value = str(candidate).strip()
        if value and value not in unique:
            unique.append(value)
    if not unique:
        raise RuntimeError("网页没有返回可用的视频线路。")
    preferred_host = read_route_cache(cache_path, now=now).get(media_host(page_url))
    if preferred_host:
        preferred = [item for item in unique if media_host(item) == preferred_host]
        others = [item for item in unique if media_host(item) != preferred_host]
        unique = preferred + others
    if len(unique) == 1:
        return unique

    progress(folder, 6, f"正在测试 {len(unique)} 条备用视频线路…")
    failed: list[str] = []
    for index, candidate in enumerate(unique, 1):
        check_cancel(folder)
        if probe_media_stream(folder, page_url, candidate):
            log(folder, f"视频线路 {index}/{len(unique)} 真实分片检测通过。")
            remaining = unique[index:]
            return [candidate] + remaining + failed
        failed.append(candidate)
        log(folder, f"视频线路 {index}/{len(unique)} 真实分片检测失败。")

    log(folder, "备用线路真实分片检测均失败，将按优先顺序进行完整下载尝试。")
    return unique


def select_downloadable_media_url(
    folder: Path,
    page_url: str,
    candidates: Sequence[str],
) -> str:
    return order_downloadable_media_urls(folder, page_url, candidates)[0]


def resolve_media_urls(folder, page_url, mode="smart") -> list[str]:
    progress(folder, 3, "正在解析视频网页…")
    code, output = capture(
        folder,
        [sys.executable, str(ROOT / "resolve_url.py"), page_url],
        inactivity_timeout=75,
    )
    if code:
        safe_output = redact_url_queries(strip_ansi(output))
        log(folder, "网页解析失败：" + friendly_resolve_error(safe_output))
        raise RuntimeError(friendly_resolve_error(safe_output))
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    candidates = [line for line in lines if line.lower().startswith(("http://", "https://"))]
    if not candidates:
        raise RuntimeError("网页没有返回可用的视频线路。")
    media_urls = order_downloadable_media_urls(
        folder,
        page_url,
        candidates,
    )
    log(folder, "优先视频线路：" + redact_url_queries(media_urls[0]))
    if len(media_urls) > 1:
        log(folder, f"已保留 {len(media_urls)} 条候选线路，下载失败时自动换线。")
    progress(folder, 8, resolved_progress_message(mode))
    return media_urls


def resolve(folder, page_url, mode="smart"):
    return resolve_media_urls(folder, page_url, mode)[0]


def build_download_command(
    kind: str,
    folder: Path,
    page_url: str,
    media_url: str,
    *,
    concurrent_fragments: int = 8,
) -> list[str]:
    command = [
        str(YTDLP),
        "--ffmpeg-location",
        str(get_media_tools().ffmpeg),
        "--referer",
        page_url,
        "--newline",
        "--progress-template",
        DOWNLOAD_TEMPLATE,
        "--continue",
        "--abort-on-unavailable-fragments",
        "--retries",
        "3",
        "--fragment-retries",
        "3",
        "--socket-timeout",
        "30",
        "--retry-sleep",
        "http:exp=1:20",
        "--retry-sleep",
        "fragment:exp=1:20",
        "--concurrent-fragments",
        str(max(1, int(concurrent_fragments))),
    ]
    if kind == "audio":
        return command + [
            "-x",
            "--audio-format",
            "mp3",
            "-o",
            str(folder / "视频音频.%(ext)s"),
            media_url,
        ]
    if kind == "video":
        return command + [
            "-f",
            (
                "bv*[height<=480]+ba/b[height<=480]/"
                "bv*[height<=720]+ba/b[height<=720]/b"
            ),
            "--merge-output-format",
            "mp4",
            "-o",
            str(folder / "OCR临时视频.%(ext)s"),
            media_url,
        ]
    if kind == "download_video":
        return command + [
            "-f",
            "bv*[height<=1080]+ba/b[height<=1080]/b",
            "-S",
            "res:1080,vcodec:h264,acodec:aac",
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "-o",
            str(folder / "下载视频.%(ext)s"),
            media_url,
        ]
    raise ValueError(f"未知下载类型：{kind}")


def download_concurrency_levels(kind: str) -> tuple[int, ...]:
    if kind == "video":
        return (12, 4, 1)
    if kind == "download_video":
        return (8, 4, 1)
    return (8, 3, 1)


def normalize_media_urls(media_urls: str | Sequence[str]) -> list[str]:
    values = [media_urls] if isinstance(media_urls, str) else list(media_urls)
    unique: list[str] = []
    for item in values:
        value = str(item).strip()
        if value and value not in unique:
            unique.append(value)
    if not unique:
        raise RuntimeError("没有可用的视频线路。")
    return unique


def cleanup_partial_downloads(folder: Path, kind: str) -> None:
    prefixes = {
        "audio": "视频音频",
        "video": "OCR临时视频",
        "download_video": "下载视频",
    }
    prefix = prefixes.get(kind)
    if not prefix:
        return
    for path in folder.glob(prefix + "*"):
        name = path.name.lower()
        if path.is_file() and (name.endswith(".part") or name.endswith(".ytdl")):
            path.unlink(missing_ok=True)


def download_with_retry(
    kind: str,
    folder: Path,
    page_url: str,
    media_url: str | Sequence[str],
    progress_range: tuple[int, int],
    phase: str,
) -> str:
    media_urls = normalize_media_urls(media_url)
    last_error: Exception | None = None
    for candidate_index, candidate in enumerate(media_urls, 1):
        concurrency_levels = download_concurrency_levels(kind)
        for attempt, concurrent in enumerate(concurrency_levels, 1):
            command = build_download_command(
                kind,
                folder,
                page_url,
                candidate,
                concurrent_fragments=concurrent,
            )
            try:
                run(
                    folder,
                    command,
                    progress_range=progress_range,
                    phase=phase,
                    inactivity_timeout=600 if kind == "download_video" else 180,
                )
                if candidate_index > 1:
                    log(folder, f"备用视频线路 {candidate_index}/{len(media_urls)} 下载成功。")
                return candidate
            except TaskCancelled:
                raise
            except (ProcessStalled, RuntimeError) as exc:
                last_error = exc
                if not is_retryable_download_error(exc):
                    raise RuntimeError(f"{phase}下载失败：{exc}") from exc
                if is_broken_fragment_error(exc):
                    log(
                        folder,
                        f"视频线路 {candidate_index}/{len(media_urls)} 分片不可用，立即换线：{exc}",
                    )
                    cleanup_partial_downloads(folder, kind)
                    break
                if attempt < len(concurrency_levels):
                    next_concurrent = concurrency_levels[attempt]
                    log(
                        folder,
                        f"{concurrent} 路下载失败，自动降级为 {next_concurrent} 路续传：{exc}",
                    )
                    progress(
                        folder,
                        progress_range[0],
                        f"{phase}下载不稳定，正在切换 {next_concurrent} 路续传…",
                    )
                    continue
                cleanup_partial_downloads(folder, kind)
                break
        if candidate_index < len(media_urls):
            log(folder, f"正在切换备用视频线路 {candidate_index + 1}/{len(media_urls)}。")
            progress(
                folder,
                progress_range[0],
                f"{phase}线路不可用，正在切换备用线路…",
            )
    raise RuntimeError(f"{phase}下载失败：{last_error}")


def download_audio(folder, page_url, media_url, start=15, end=45):
    audio = folder / "视频音频.mp3"
    ensure_free_space(folder, AUDIO_MIN_FREE)
    selected = download_with_retry(
        "audio",
        folder,
        page_url,
        media_url,
        (start, end),
        "音频",
    )
    remember_successful_route(page_url, selected)
    if not audio.is_file() or audio.stat().st_size <= 0:
        raise RuntimeError("视频音频下载失败。")
    return audio


def validate_downloaded_video(folder: Path, video: Path) -> None:
    if not video.is_file() or video.stat().st_size <= 0:
        raise RuntimeError("MP4 视频下载失败或结果文件为空。")
    code, output = capture(
        folder,
        [
            str(get_media_tools().ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(video),
        ],
        inactivity_timeout=75,
    )
    if code:
        raise RuntimeError("下载的 MP4 无法正常播放。")
    try:
        metadata = json.loads(output or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("下载的 MP4 无法正常播放。") from exc
    validate_video_metadata(metadata)


def download_video(folder, page_url, media_url, start=12, end=97):
    ensure_free_space(folder, FINAL_VIDEO_MIN_FREE)
    selected = download_with_retry(
        "download_video",
        folder,
        page_url,
        media_url,
        (start, end),
        "视频",
    )
    remember_successful_route(page_url, selected)
    video = folder / "下载视频.mp4"
    validate_downloaded_video(folder, video)
    return video


def download_ocr_video(folder, page_url, media_url):
    ensure_free_space(folder, OCR_VIDEO_MIN_FREE)
    progress(folder, 15, "未发现独立字幕，正在下载 OCR 视频…")
    selected = download_with_retry(
        "video",
        folder,
        page_url,
        media_url,
        (15, 22),
        "OCR 视频",
    )
    remember_successful_route(page_url, selected)
    candidates = [
        path
        for path in folder.glob("OCR临时视频.*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}
    ]
    if not candidates:
        raise RuntimeError("OCR 视频下载失败。")
    return max(candidates, key=lambda path: path.stat().st_size)


def extract_embedded_subtitle(folder, video):
    check_cancel(folder)
    progress(folder, 22, "正在检查视频内嵌字幕轨…")
    try:
        code, output = capture(
            folder,
            [
                str(get_media_tools().ffprobe),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                str(video),
            ],
            inactivity_timeout=75,
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        log(folder, "字幕轨探测失败：" + str(exc))
        return None
    if code:
        return None
    streams = json.loads(output or "{}").get("streams", [])
    subtitle = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "subtitle"
        ),
        None,
    )
    if subtitle is None:
        return None
    target = folder / "视频字幕轨.srt"
    try:
        run(
            folder,
            [
                str(get_media_tools().ffmpeg),
                "-y",
                "-i",
                str(video),
                "-map",
                f"0:{subtitle['index']}",
                str(target),
            ],
            phase="视频字幕轨",
            inactivity_timeout=120,
        )
    except TaskCancelled:
        raise
    except Exception as exc:
        log(folder, "视频字幕轨提取失败：" + str(exc))
        target.unlink(missing_ok=True)
        return None
    if not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        return None
    return target


def try_hard_subtitle_ocr(folder, video):
    progress(folder, 25, "正在快速检测硬字幕…")
    code = 1
    try:
        run(
            folder,
            [
                sys.executable,
                str(ROOT / "hard_subtitle_ocr.py"),
                str(video),
                str(folder),
                str(folder / "progress.json"),
                str(get_media_tools().ffmpeg),
                str(get_media_tools().ffprobe),
            ],
            phase="硬字幕",
            inactivity_timeout=180,
            heartbeat_path=folder / "progress.json",
        )
        code = 0
    except TaskCancelled:
        raise
    except ProcessStalled as exc:
        log(folder, "OCR 长时间没有进度：" + str(exc))
        code = 4
    except RuntimeError as exc:
        match = re.search(r"错误码\s+(\d+)", str(exc))
        code = int(match.group(1)) if match else 1

    srt = folder / "硬字幕OCR.srt"
    text = folder / "硬字幕OCR文字.txt"
    success = (
        code == 0
        and srt.exists()
        and srt.stat().st_size > 0
        and text.exists()
        and text.stat().st_size > 0
    )
    if not success:
        srt.unlink(missing_ok=True)
        text.unlink(missing_ok=True)
        if code == 2:
            log(folder, "未检测到稳定硬字幕，切换 Whisper。")
        elif code == 3:
            log(folder, "OCR 有效字幕不足，切换 Whisper。")
        else:
            log(folder, f"OCR 子任务失败（错误码 {code}），切换 Whisper。")
    return success


def video_to_audio(folder, video, audio):
    progress(folder, 45, "OCR 未采用，正在从临时视频提取音频…")
    run(
        folder,
        [
            str(get_media_tools().ffmpeg),
            "-y",
            "-i",
            str(video),
            "-vn",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(audio),
        ],
        phase="音频",
        inactivity_timeout=180,
    )
    if not audio.exists():
        raise RuntimeError("无法从 OCR 临时视频提取音频。")


def transcribe_audio(folder, audio, profile="standard"):
    progress(folder, 46, "正在启动 Whisper 语音识别…")
    run(
        folder,
        [
            sys.executable,
            str(ROOT / "transcribe.py"),
            str(audio),
            str(folder),
            str(folder / "progress.json"),
            profile,
        ],
        phase="语音识别",
        inactivity_timeout=600,
        heartbeat_path=folder / "progress.json",
    )


def find_subtitle_files(folder: Path) -> list[Path]:
    return [
        path
        for path in folder.iterdir()
        if path.suffix.lower() in {".srt", ".vtt", ".ass"}
    ]


def try_remote_subtitles(
    folder: Path,
    page_url: str,
    media_urls: str | Sequence[str],
) -> bool:
    last_error: Exception | None = None
    urls = normalize_media_urls(media_urls)
    for index, media_url in enumerate(urls, 1):
        try:
            run(
                folder,
                [
                    str(YTDLP),
                    "--ffmpeg-location",
                    str(get_media_tools().ffmpeg),
                    "--referer",
                    page_url,
                    "--skip-download",
                    "--write-subs",
                    "--write-auto-subs",
                    "--sub-langs",
                    "zh.*,zh,en.*",
                    "--sub-format",
                    "srt/vtt/best",
                    "--convert-subs",
                    "srt",
                    "-o",
                    str(folder / "%(title).120B [%(id)s].%(ext)s"),
                    media_url,
                ],
                phase="网页字幕",
                inactivity_timeout=180,
            )
            return bool(find_subtitle_files(folder))
        except TaskCancelled:
            raise
        except (ProcessStalled, RuntimeError) as exc:
            last_error = exc
            if index < len(urls) and is_retryable_download_error(exc):
                log(folder, f"远程字幕线路 {index}/{len(urls)} 不可用，尝试下一条。")
                continue
            raise
    if last_error is not None:
        raise last_error
    return False


def process_subtitle_mode(
    folder: Path,
    page_url: str,
    media_urls: str | Sequence[str],
    mode: str,
    timings: dict[str, int] | None = None,
) -> str:
    phase_timings = timings if timings is not None else {}
    with measure_phase(folder, phase_timings, "远程字幕检测"):
        remote_subtitles = try_remote_subtitles(folder, page_url, media_urls)
    if remote_subtitles:
        progress(folder, 99, "已提取网页原字幕，正在整理…")
        return "网页原字幕"

    if mode == "fast":
        progress(folder, 15, "未发现远程字幕，极速模式正在下载音频…")
        with measure_phase(folder, phase_timings, "音频下载"):
            audio = download_audio(folder, page_url, media_urls, 15, 45)
        with measure_phase(folder, phase_timings, "语音识别"):
            transcribe_audio(folder, audio, profile="fast")
        return "极速语音识别字幕"

    video = None
    try:
        try:
            with measure_phase(folder, phase_timings, "OCR视频下载"):
                video = download_ocr_video(folder, page_url, media_urls)
        except TaskCancelled:
            raise
        except Exception as exc:
            log(folder, "OCR 视频不可用：" + str(exc))
        if video is not None:
            with measure_phase(folder, phase_timings, "字幕轨检测"):
                embedded = extract_embedded_subtitle(folder, video)
            if embedded is not None:
                progress(folder, 99, "已提取视频字幕轨，正在整理…")
                return "视频字幕轨"
            with measure_phase(folder, phase_timings, "OCR识别"):
                ocr_succeeded = try_hard_subtitle_ocr(folder, video)
            if ocr_succeeded:
                progress(folder, 99, "硬字幕 OCR 完成，正在整理…")
                return "硬字幕 OCR"
            audio = folder / "视频音频.mp3"
            try:
                with measure_phase(folder, phase_timings, "音频准备"):
                    video_to_audio(folder, video, audio)
            except TaskCancelled:
                raise
            except Exception as exc:
                log(folder, "临时视频音频提取失败，改为重新下载音频：" + str(exc))
                audio.unlink(missing_ok=True)
                with measure_phase(folder, phase_timings, "音频下载"):
                    audio = download_audio(folder, page_url, media_urls, 45, 55)
            with measure_phase(folder, phase_timings, "语音识别"):
                transcribe_audio(folder, audio)
            return "语音识别字幕"

        progress(folder, 15, "视频检测不可用，自动切换 Whisper…")
        with measure_phase(folder, phase_timings, "音频下载"):
            audio = download_audio(folder, page_url, media_urls, 15, 45)
        with measure_phase(folder, phase_timings, "语音识别"):
            transcribe_audio(folder, audio)
        return "语音识别字幕"
    finally:
        cleanup_ocr_temporary_files(folder)


def cleanup_ocr_temporary_files(folder):
    for path in folder.glob("OCR临时视频.*"):
        if path.is_file():
            path.unlink(missing_ok=True)


def failure_status_path(folder: Path) -> Path:
    return folder.parent / f".{folder.name}.status.json"


def remove_task_folder(folder: Path) -> None:
    try:
        resolved = folder.resolve()
        resolved.relative_to(RESULT_ROOT.resolve())
    except (OSError, ValueError):
        return
    if resolved == RESULT_ROOT.resolve():
        return
    shutil.rmtree(resolved, ignore_errors=True)


def main(started_at: float | None = None):
    task_started_at = time.monotonic() if started_at is None else started_at
    if len(sys.argv) < 4:
        raise RuntimeError("启动参数不足")
    mode, page_url, folder_text = sys.argv[1:4]
    folder = Path(folder_text)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "来源网址.txt").write_text(page_url, encoding="utf-8-sig")
    if mode == "audio":
        (folder / "音频结果说明.txt").write_text(
            "本文件夹包含从网页视频中提取的 MP3 音频。\n",
            encoding="utf-8-sig",
        )
    elif mode == "video":
        (folder / "视频结果说明.txt").write_text(
            "本文件夹包含下载并合并完成的 MP4 视频。\n"
            "默认选择源站提供的最高不超过 1080P 的清晰度。\n",
            encoding="utf-8-sig",
        )
    elif mode in {"smart", "fast"}:
        (folder / "SRT使用说明.txt").write_text(
            "1. 查看文字：右键 SRT→打开方式→记事本。\n"
            "2. 配合视频：用 VLC/PotPlayer 打开视频，再将 SRT 拖入播放器。\n"
            "3. TXT 是不带时间轴的纯文字。\n"
            + (
                "4. 极速模式优先远程字幕，否则使用较快的 Whisper base。\n"
                if mode == "fast"
                else ""
            ),
            encoding="utf-8-sig",
        )
    else:
        raise RuntimeError("未知的处理方式。")
    log(folder, f"任务开始：{datetime.now():%Y-%m-%d %H:%M:%S}")
    phase_timings: dict[str, int] = {}
    with measure_phase(folder, phase_timings, "线路解析"):
        media_urls = resolve_media_urls(folder, page_url, mode)
    check_cancel(folder)

    if mode == "audio":
        progress(folder, 12, "正在准备提取音频…")
        with measure_phase(folder, phase_timings, "音频下载"):
            download_audio(folder, page_url, media_urls, 12, 97)
        result_type = "MP3 音频"
        progress(folder, 99, "音频提取完成，正在整理…")
    elif mode == "video":
        progress(folder, 12, "正在准备下载 MP4 视频…")
        with measure_phase(folder, phase_timings, "视频下载"):
            download_video(folder, page_url, media_urls, 12, 97)
        result_type = "MP4 视频"
        progress(folder, 99, "视频下载完成，正在整理…")
    else:
        result_type = process_subtitle_mode(
            folder,
            page_url,
            media_urls,
            mode,
            phase_timings,
        )

    with measure_phase(folder, phase_timings, "结果整理"):
        check_cancel(folder)
        (folder / "cancel.request").unlink(missing_ok=True)
        progress(folder, 100, "处理完成")
    duration = elapsed_seconds(task_started_at)
    log(folder, f"任务完成，总用时：{format_elapsed(duration)}。")
    write_json(
        folder / "status.json",
        {
            "ok": True,
            "message": "处理完成",
            "type": result_type,
            "elapsed_seconds": duration,
            "phase_timings": phase_timings,
        },
    )


if __name__ == "__main__":
    folder = None
    started_at = time.monotonic()
    try:
        if len(sys.argv) > 3:
            folder = Path(sys.argv[3])
        main(started_at)
    except TaskCancelled:
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            try:
                duration = elapsed_seconds(started_at)
                log(folder, f"任务已由用户取消，总用时：{format_elapsed(duration)}。")
            except Exception:
                pass
            write_json(
                failure_status_path(folder),
                {
                    "ok": False,
                    "cancelled": True,
                    "message": "任务已取消",
                    "cleanup": True,
                    "elapsed_seconds": elapsed_seconds(started_at),
                },
            )
            remove_task_folder(folder)
        raise SystemExit(2)
    except Exception as exc:
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            try:
                log(folder, "错误：" + str(exc))
                log(folder, traceback.format_exc())
                duration = elapsed_seconds(started_at)
                log(folder, f"任务失败，总用时：{format_elapsed(duration)}。")
            except Exception:
                pass
            saved_log = preserve_failure_log(folder)
            write_json(
                failure_status_path(folder),
                {
                    "ok": False,
                    "cancelled": False,
                    "message": str(exc),
                    "cleanup": True,
                    "log": str(saved_log) if saved_log else "",
                    "elapsed_seconds": elapsed_seconds(started_at),
                },
            )
            remove_task_folder(folder)
        else:
            traceback.print_exc()
        raise SystemExit(1)
