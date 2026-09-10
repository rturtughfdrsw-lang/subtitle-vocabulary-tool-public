from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

RUNTIME_DIR = Path(__file__).resolve().parent
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

from ocr_dialogue_filter import (
    FrameText,
    TextBlock,
    clean_frames,
)
from ocr_performance import FrameScheduler


HAN_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")
OPENING_PUNCTUATION = set("（【《“‘([{")
GPU_BOOTSTRAP_DETAIL = ""


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int


@dataclass(frozen=True)
class BackendInfo:
    name: str
    provider: str
    accelerated: bool
    detail: str = ""


def normalize_text(text: str) -> str:
    lines: list[str] = []
    for raw_line in re.split(r"[\r\n]+", str(text)):
        line = re.sub(r"[^\S\r\n]+", " ", raw_line).strip()
        line = re.sub(r"\s+([，。！？；：、,.!?;:])", r"\1", line)
        line = re.sub(r"([，。！？；：、])\s+", r"\1", line)
        line = re.sub(r"([（【《(])\s+", r"\1", line)
        line = re.sub(r"\s+([）】》)])", r"\1", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def wrap_english(text: str, width: int = 48) -> list[str]:
    text = normalize_text(text).replace("\n", " ")
    tokens: list[str] = []
    token = ""
    for index, char in enumerate(text):
        token += char
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if char.isspace() or (
            char in ",.;:!?" and next_char and not next_char.isspace()
        ):
            tokens.append(token)
            token = ""
    if token:
        tokens.append(token)

    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = current + token
        if current and len(candidate.rstrip()) > width:
            lines.append(current.rstrip())
            current = token.lstrip()
        else:
            current = candidate
    if current.strip():
        lines.append(current.strip())
    return lines


def wrap_chinese(text: str, width: int = 24) -> list[str]:
    lines: list[str] = []
    for source_line in normalize_text(text).splitlines():
        pieces = re.findall(r".+?[。！？；，、：,.!?;:]|.+$", source_line)
        current = ""
        for piece in pieces:
            if current and len(current) + len(piece) > width:
                lines.append(current)
                current = ""
            while len(piece) > width:
                lines.append(piece[:width])
                piece = piece[width:]
            current += piece
        if current:
            lines.append(current)
    return lines


def format_subtitle_text(
    text_or_blocks: str | list[str],
    english_width: int = 48,
    chinese_width: int = 24,
) -> str:
    blocks = (
        text_or_blocks.splitlines()
        if isinstance(text_or_blocks, str)
        else text_or_blocks
    )
    english: list[str] = []
    chinese: list[str] = []
    unknown: list[str] = []
    last_language = ""

    def append_language(kind: str, fragment: str) -> None:
        nonlocal last_language
        target = chinese if kind == "zh" else english
        if not last_language and unknown:
            target.extend(unknown)
            unknown.clear()
        target.append(fragment)
        last_language = kind

    for block in blocks:
        value = normalize_text(block)
        if not value:
            continue
        active = ""
        buffer: list[str] = []
        for char in value:
            kind = (
                "zh"
                if HAN_RE.fullmatch(char)
                else "en"
                if LATIN_RE.fullmatch(char)
                else "neutral"
            )
            if kind == "neutral" or not active or kind == active:
                if kind != "neutral" and not active:
                    active = kind
                buffer.append(char)
                continue
            boundary: list[str] = []
            while buffer and (
                buffer[-1].isspace() or buffer[-1] in OPENING_PUNCTUATION
            ):
                boundary.insert(0, buffer.pop())
            fragment = normalize_text("".join(buffer))
            if fragment:
                append_language(active, fragment)
            active = kind
            buffer = boundary + [char]
        fragment = normalize_text("".join(buffer))
        if active == "zh" and fragment:
            append_language("zh", fragment)
        elif active == "en" and fragment:
            append_language("en", fragment)
        elif fragment:
            if last_language == "zh":
                chinese.append(fragment)
            elif last_language == "en":
                english.append(fragment)
            else:
                unknown.append(fragment)
    english_text = normalize_text(" ".join(english))
    chinese_text = normalize_text("".join(chinese))
    lines = wrap_english(english_text, english_width)
    lines.extend(wrap_chinese(chinese_text, chinese_width))
    if not lines:
        lines = wrap_english(normalize_text(" ".join(unknown)), english_width)
    return "\n".join(lines)


def is_valid_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", normalize_text(text))
    useful = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", compact)
    if len(useful) < 2:
        return False
    return bool(re.search(r"[\u4e00-\u9fffA-Za-z]", compact))


def texts_similar(left: str, right: str, threshold: float = 0.72) -> bool:
    left = normalize_text(left)
    right = normalize_text(right)
    if not left or not right:
        return left == right
    return SequenceMatcher(None, left, right).ratio() >= threshold


def build_segments(samples: list[tuple[float, str]], step: float) -> list[Segment]:
    segments: list[Segment] = []
    current_text = ""
    start = 0.0
    last_time = 0.0
    candidates: list[str] = []

    def finish(end: float) -> None:
        nonlocal current_text, candidates
        if current_text and end > start:
            best = Counter(candidates or [current_text]).most_common(1)[0][0]
            segments.append(Segment(round(start, 3), round(end, 3), best))
        current_text = ""
        candidates = []

    for timestamp, raw_text in samples:
        text = normalize_text(raw_text)
        if not is_valid_text(text):
            finish(timestamp)
            last_time = timestamp
            continue
        if not current_text:
            current_text = text
            candidates = [text]
            start = timestamp
        elif texts_similar(current_text, text):
            candidates.append(text)
        else:
            finish(timestamp)
            current_text = text
            candidates = [text]
            start = timestamp
        last_time = timestamp
    finish(last_time + step)
    return segments


def has_stable_hard_subtitles(texts: list[str]) -> bool:
    valid = [normalize_text(text) for text in texts if is_valid_text(text)]
    if len(valid) < 3:
        return False
    groups: list[str] = []
    for text in valid:
        if not any(texts_similar(text, existing, 0.82) for existing in groups):
            groups.append(text)
    return len(groups) >= 2


def stamp(seconds: float) -> str:
    value = max(0, round(seconds * 1000))
    hours, value = divmod(value, 3_600_000)
    minutes, value = divmod(value, 60_000)
    secs, millis = divmod(value, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_outputs(segments: list[Segment], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    srt_path = output / "硬字幕OCR.srt"
    text_path = output / "硬字幕OCR文字.txt"
    with srt_path.open("w", encoding="utf-8-sig") as srt, text_path.open(
        "w", encoding="utf-8-sig"
    ) as text:
        for number, segment in enumerate(segments, 1):
            formatted = format_subtitle_text(segment.text)
            srt.write(
                f"{number}\n{stamp(segment.start)} --> {stamp(segment.end)}\n"
                f"{formatted}\n\n"
            )
            text.write(formatted + "\n\n")
    return srt_path, text_path


def write_progress(path: Path, value: int, message: str) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    for attempt in range(4):
        try:
            temp.write_text(
                json.dumps(
                    {"value": int(value), "message": message},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(temp, path)
            return
        except OSError as exc:
            temp.unlink(missing_ok=True)
            if getattr(exc, "winerror", None) not in {5, 32}:
                return
            time.sleep(0.05 * (attempt + 1))


def probe_video(video: Path, ffprobe: Path) -> VideoInfo:
    process = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(video),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if process.returncode:
        raise RuntimeError("无法读取 OCR 视频信息")
    data = json.loads(process.stdout)
    stream = next(
        item
        for item in data.get("streams", [])
        if item.get("codec_type") == "video"
    )
    duration = float(
        data.get("format", {}).get("duration") or stream.get("duration") or 0
    )
    if duration <= 0:
        raise RuntimeError("无法确定 OCR 视频时长")
    return VideoInfo(duration, int(stream["width"]), int(stream["height"]))


def output_size(
    info: VideoInfo, target_width: int = 960
) -> tuple[int, int]:
    crop_height = max(2, int(info.height * 0.45) // 2 * 2)
    target_width = max(320, int(target_width) // 2 * 2)
    if info.width <= target_width:
        return max(2, info.width // 2 * 2), crop_height
    scaled_height = max(
        2, int(crop_height * target_width / info.width) // 2 * 2
    )
    return target_width, scaled_height


def crop_filter(
    info: VideoInfo, *, with_fps: bool, target_width: int = 960
) -> str:
    width, height = output_size(info, target_width)
    crop_y = max(0, int(info.height * 0.55))
    crop_height = max(2, min(info.height - crop_y, int(info.height * 0.45)))
    value = f"crop=iw:{crop_height}:0:{crop_y},scale={width}:{height}"
    return value + (",fps=2" if with_fps else "")


def extract_frame(
    video: Path, ffmpeg: Path, info: VideoInfo, timestamp: float
) -> np.ndarray:
    width, height = output_size(info)
    process = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            crop_filter(info, with_fps=False),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "pipe:1",
        ],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    expected = width * height * 3
    if process.returncode or len(process.stdout) != expected:
        raise RuntimeError("OCR 抽帧失败")
    return np.frombuffer(process.stdout, dtype=np.uint8).reshape(
        (height, width, 3)
    )


def rapidocr_backend(engine: Any) -> BackendInfo:
    providers: list[list[str]] = []
    for stage_name in ("text_det", "text_rec", "text_cls"):
        stage = getattr(engine, stage_name)
        holder = getattr(stage, "session")
        session = getattr(holder, "session")
        providers.append(list(session.get_providers()))
    first = [items[0] if items else "" for items in providers]
    detail = "; ".join(
        f"{name}={','.join(items) or '<空>'}"
        for name, items in zip(("Det", "Rec", "Cls"), providers)
    )
    if first and all(item == "CUDAExecutionProvider" for item in first):
        return BackendInfo(
            "RTX 4060 / CUDA",
            "CUDAExecutionProvider",
            True,
            detail,
        )
    if first and all(item == "DmlExecutionProvider" for item in first):
        return BackendInfo(
            "GPU / DirectML",
            "DmlExecutionProvider",
            True,
            detail,
        )
    return BackendInfo("CPU", "CPUExecutionProvider", False, detail)


def bootstrap_gpu_runtime(runtime_dir: Path = RUNTIME_DIR) -> bool:
    global GPU_BOOTSTRAP_DETAIL
    gpu_dir = runtime_dir / "gpu_runtime"
    if not (gpu_dir / "onnxruntime").is_dir():
        GPU_BOOTSTRAP_DETAIL = "未安装隔离 GPU 运行库"
        return False
    gpu_path = str(gpu_dir)
    if gpu_path not in sys.path:
        sys.path.insert(0, gpu_path)
    try:
        import onnxruntime

        if hasattr(onnxruntime, "preload_dlls"):
            onnxruntime.preload_dlls(directory="")
        providers = list(onnxruntime.get_available_providers())
        GPU_BOOTSTRAP_DETAIL = "可用 provider：" + ",".join(providers)
        return "CUDAExecutionProvider" in providers
    except Exception as exc:
        if gpu_path in sys.path:
            sys.path.remove(gpu_path)
        GPU_BOOTSTRAP_DETAIL = f"{type(exc).__name__}: {exc}"
        return False


def ensure_rapidocr():
    gpu_available = bootstrap_gpu_runtime()
    try:
        from rapidocr import ModelType, RapidOCR
    except ImportError:
        site = Path(sys.executable).parent / "Lib" / "site-packages"
        subprocess.check_call(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--target",
                str(site),
                "rapidocr==3.9.2",
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        importlib.invalidate_caches()
        from rapidocr import ModelType, RapidOCR
    params: dict[str, Any] = {
        "Det.model_type": ModelType.TINY,
        "Rec.model_type": ModelType.TINY,
    }
    if gpu_available:
        params.update(
            {
                "EngineConfig.onnxruntime.use_cuda": True,
                "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0,
                "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": (
                    "HEURISTIC"
                ),
            }
        )
    engine = RapidOCR(params=params)
    backend = rapidocr_backend(engine)
    if not backend.accelerated and GPU_BOOTSTRAP_DETAIL:
        backend = BackendInfo(
            backend.name,
            backend.provider,
            backend.accelerated,
            backend.detail + "; " + GPU_BOOTSTRAP_DETAIL,
        )
    return engine, backend


def recognize_frame_blocks(
    engine: Any, frame: np.ndarray
) -> tuple[TextBlock, ...]:
    result = engine(frame, use_det=True, use_cls=False, use_rec=True)
    texts: list[TextBlock] = []
    boxes = result.boxes if result.boxes is not None else []
    values = result.txts if result.txts is not None else []
    scores = result.scores if result.scores is not None else []
    frame_width = frame.shape[1]
    frame_height = frame.shape[0]
    for box, text, score in zip(boxes, values, scores):
        if float(score) < 0.60:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        center_x = sum(xs) / len(xs)
        if not frame_width * 0.12 <= center_x <= frame_width * 0.88:
            continue
        value = normalize_text(text)
        if is_valid_text(value):
            center_y = sum(ys) / len(ys)
            texts.append(
                TextBlock(
                    text=value,
                    score=float(score),
                    center_x=center_x / frame_width,
                    center_y=center_y / frame_height,
                    width=(max(xs) - min(xs)) / frame_width,
                    height=(max(ys) - min(ys)) / frame_height,
                )
            )
    return tuple(sorted(texts, key=lambda item: (item.center_y, item.center_x)))


def blocks_to_text(blocks: list[TextBlock] | tuple[TextBlock, ...]) -> str:
    ordered = sorted(blocks, key=lambda item: (item.center_y, item.center_x))
    return format_subtitle_text([item.text for item in ordered])


def recognize_frame(engine: Any, frame: np.ndarray) -> str:
    return blocks_to_text(recognize_frame_blocks(engine, frame))


def detect_hard_subtitles(
    video: Path,
    ffmpeg: Path,
    info: VideoInfo,
    engine: Any,
) -> bool:
    timestamps = [
        max(0.0, min(info.duration - 0.1, info.duration * index / 13))
        for index in range(1, 13)
    ]
    frames = [
        FrameText(
            timestamp,
            recognize_frame_blocks(
                engine,
                extract_frame(video, ffmpeg, info, timestamp),
            ),
        )
        for timestamp in timestamps
    ]
    raw_texts = [blocks_to_text(frame.blocks) for frame in frames]
    cleaned_frames, stats = clean_frames(frames)
    texts = [blocks_to_text(blocks) for _, blocks in cleaned_frames]
    print(
        "硬字幕检测原始样本："
        + " | ".join(text or "<空>" for text in raw_texts)
    )
    print(
        "硬字幕检测净化样本："
        + " | ".join(text or "<空>" for text in texts)
    )
    print(
        "硬字幕检测净化统计："
        f"原始={stats.raw_blocks} 常驻={stats.persistent_removed} "
        f"广告={stats.promotions_removed} "
        f"名单版权={stats.credits_removed} 乱码={stats.noise_removed}"
    )
    return has_stable_hard_subtitles(texts)


def read_exact(stream, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        block = stream.read(remaining)
        if not block:
            break
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)


def recognize_video(
    video: Path,
    ffmpeg: Path,
    info: VideoInfo,
    engine: Any,
    progress_path: Path,
    backend_name: str = "CPU",
) -> list[Segment]:
    total_started = time.perf_counter()
    width, height = output_size(info)
    process = subprocess.Popen(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-i",
            str(video),
            "-vf",
            crop_filter(info, with_fps=True),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert process.stdout is not None
    frame_size = width * height * 3
    expected_frames = max(1, int(info.duration * 2))
    frames: list[FrameText] = []
    scheduler = FrameScheduler(force_interval=2.0, change_threshold=0.03)
    last_blocks: tuple[TextBlock, ...] = ()
    index = 0
    while True:
        raw = read_exact(process.stdout, frame_size)
        if not raw:
            break
        if len(raw) != frame_size:
            process.kill()
            process.wait()
            raise RuntimeError("OCR 视频帧数据不完整")
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(
            (height, width, 3)
        )
        timestamp = index / 2.0
        if scheduler.should_ocr(frame, timestamp, last_blocks):
            ocr_started = time.perf_counter()
            last_blocks = recognize_frame_blocks(engine, frame)
            scheduler.record_ocr(
                frame,
                timestamp,
                last_blocks,
                time.perf_counter() - ocr_started,
            )
        else:
            scheduler.record_reuse()
        frames.append(FrameText(timestamp, last_blocks))
        index += 1
        ratio = min(1.0, index / expected_frames)
        write_progress(
            progress_path,
            35 + int(ratio * 50),
            f"正在识别硬字幕… {int(ratio * 100)}%",
        )
    error_text = ""
    if process.stderr is not None:
        error_text = process.stderr.read().decode("utf-8", errors="replace")
    if process.wait() != 0:
        raise RuntimeError("OCR 视频读取失败：" + error_text.strip())
    cleaned_frames, stats = clean_frames(frames)
    samples = [
        (timestamp, blocks_to_text(blocks))
        for timestamp, blocks in cleaned_frames
    ]
    segments = build_segments(samples, step=0.5)
    performance = scheduler.stats
    total_seconds = time.perf_counter() - total_started
    average = (
        performance.ocr_frames / performance.ocr_seconds
        if performance.ocr_seconds > 0
        else 0.0
    )
    print(
        "OCR性能："
        f"后端={backend_name} 解码={performance.decoded_frames} "
        f"识别={performance.ocr_frames} 复用={performance.reused_frames} "
        f"OCR耗时={performance.ocr_seconds:.2f}秒 "
        f"总耗时={total_seconds:.2f}秒 平均={average:.2f}帧/秒"
    )
    print(
        "OCR_PERFORMANCE "
        f"backend={backend_name.replace(' ', '_')} "
        f"decoded={performance.decoded_frames} "
        f"ocr={performance.ocr_frames} "
        f"reused={performance.reused_frames} "
        f"ocr_seconds={performance.ocr_seconds:.3f} "
        f"total_seconds={total_seconds:.3f}"
    )
    print(
        "OCR净化统计："
        f"原始={stats.raw_blocks} 常驻={stats.persistent_removed} "
        f"广告={stats.promotions_removed} "
        f"名单版权={stats.credits_removed} 乱码={stats.noise_removed} "
        f"最终段={len(segments)}"
    )
    return segments


def remove_outputs(output: Path) -> None:
    (output / "硬字幕OCR.srt").unlink(missing_ok=True)
    (output / "硬字幕OCR文字.txt").unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 5:
        print(
            "用法：hard_subtitle_ocr.py VIDEO OUTPUT PROGRESS_JSON "
            "FFMPEG FFPROBE"
        )
        return 4
    video, output, progress_path, ffmpeg, ffprobe = map(Path, args)
    remove_outputs(output)
    try:
        write_progress(progress_path, 25, "正在准备硬字幕识别组件…")
        info = probe_video(video, ffprobe)
        engine, backend = ensure_rapidocr()
        print(
            f"OCR 加速：{backend.name}；provider={backend.provider}；"
            f"{backend.detail}"
        )
        if backend.accelerated:
            write_progress(progress_path, 27, f"OCR 加速：{backend.name}")
        else:
            write_progress(progress_path, 27, "OCR 加速不可用，已回退 CPU")
        write_progress(progress_path, 28, "正在快速检测硬字幕…")
        if not detect_hard_subtitles(video, ffmpeg, info, engine):
            print("未检测到稳定的硬字幕。")
            return 2
        write_progress(progress_path, 35, "检测到硬字幕，正在进行 OCR…")
        segments = recognize_video(
            video,
            ffmpeg,
            info,
            engine,
            progress_path,
            backend.name,
        )
        if len(segments) < 2:
            print(f"OCR 仅生成 {len(segments)} 条有效字幕，结果不足。")
            remove_outputs(output)
            return 3
        write_outputs(segments, output)
        write_progress(progress_path, 88, "硬字幕 OCR 完成，正在整理…")
        print(f"硬字幕 OCR 生成 {len(segments)} 条字幕。")
        return 0
    except Exception:
        remove_outputs(output)
        traceback.print_exc()
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
