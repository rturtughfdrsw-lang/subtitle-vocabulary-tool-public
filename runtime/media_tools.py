from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import os
from pathlib import Path
import shutil


MISSING_MESSAGE = "未找到 FFmpeg。请安装 FFmpeg 并加入 PATH。"


class MediaToolsNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaTools:
    ffmpeg: Path
    ffprobe: Path
    source: str


def resolve_media_tools(
    runtime_root: Path | None = None,
    *,
    search_path: str | None = None,
) -> MediaTools:
    root = Path(runtime_root or Path(__file__).resolve().parent)
    bundled_ffmpeg = root / "bin" / "ffmpeg.exe"
    bundled_ffprobe = root / "bin" / "ffprobe.exe"
    if bundled_ffmpeg.is_file() and bundled_ffprobe.is_file():
        return MediaTools(bundled_ffmpeg, bundled_ffprobe, "bundled")

    path_value = os.environ.get("PATH", "") if search_path is None else search_path
    ffmpeg = shutil.which("ffmpeg.exe", path=path_value) or shutil.which(
        "ffmpeg", path=path_value
    )
    ffprobe = shutil.which("ffprobe.exe", path=path_value) or shutil.which(
        "ffprobe", path=path_value
    )
    if ffmpeg and ffprobe:
        return MediaTools(Path(ffmpeg), Path(ffprobe), "path")
    raise MediaToolsNotFound(MISSING_MESSAGE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        tools = resolve_media_tools()
    except MediaToolsNotFound as exc:
        if args.json:
            print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False))
        else:
            print(str(exc))
        return 1
    payload = {
        "ok": True,
        "ffmpeg": str(tools.ffmpeg),
        "ffprobe": str(tools.ffprobe),
        "source": tools.source,
    }
    print(json.dumps(payload, ensure_ascii=False) if args.json else payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
