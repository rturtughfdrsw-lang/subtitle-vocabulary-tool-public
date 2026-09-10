from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "runtime"
PYTHON = ROOT / "Python312" / "python.exe"
FFMPEG = ROOT / "bin" / "ffmpeg.exe"
FFPROBE = ROOT / "bin" / "ffprobe.exe"
OCR_SCRIPT = ROOT / "hard_subtitle_ocr.py"


def run(command: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def ass_filter_path(path: Path) -> str:
    value = str(path).replace("\\", "/").replace(":", r"\:")
    return value.replace("'", r"\'")


def create_hard_subtitle_video(folder: Path) -> Path:
    ass = folder / "burned.ass"
    ass.write_text(
        """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,42,&H00FFFFFF,&H000000FF,&H00000000,&H60000000,0,0,0,0,100,100,0,0,1,3,1,2,40,40,55,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:10.00,Default,,0,0,0,,{\\an8\\pos(640,430)\\fs30}官网62094 棋牌百家乐
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{\\an8\\pos(640,480)\\fs30}executive producer Test Person
Dialogue: 0,0:00:00.50,0:00:04.50,Default,,0,0,0,,你好，硬字幕 Hello
Dialogue: 0,0:00:05.00,0:00:09.00,Default,,0,0,0,,第二句话 OCR Test
""",
        encoding="utf-8-sig",
    )
    video = folder / "hard-subtitle.mp4"
    process = run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=25:duration=10",
            "-vf",
            f"subtitles='{ass_filter_path(ass)}'",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ]
    )
    assert process.returncode == 0, process.stderr
    return video


def assert_no_subtitle_stream(video: Path) -> None:
    probe = run(
        [
            str(FFPROBE),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            str(video),
        ]
    )
    assert probe.returncode == 0, probe.stderr
    streams = json.loads(probe.stdout)["streams"]
    assert not any(stream.get("codec_type") == "subtitle" for stream in streams)


def test_synthetic_hard_subtitle_video() -> None:
    with tempfile.TemporaryDirectory(prefix="hard-subtitle-ocr-") as temp:
        folder = Path(temp)
        video = create_hard_subtitle_video(folder)
        assert_no_subtitle_stream(video)
        output = folder / "output"
        output.mkdir()
        progress = output / "progress.json"
        result = run(
            [
                str(PYTHON),
                str(OCR_SCRIPT),
                str(video),
                str(output),
                str(progress),
                str(FFMPEG),
                str(FFPROBE),
            ],
            timeout=600,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        performance = re.search(
            r"OCR_PERFORMANCE .*decoded=(\d+).*ocr=(\d+).*reused=(\d+)",
            result.stdout,
        )
        assert performance, result.stdout
        decoded, recognized, reused = map(int, performance.groups())
        assert decoded >= 18
        assert recognized < decoded
        assert reused == decoded - recognized
        srt = output / "硬字幕OCR.srt"
        text = output / "硬字幕OCR文字.txt"
        assert srt.exists() and srt.stat().st_size > 0
        assert text.exists() and text.stat().st_size > 0
        srt_content = srt.read_text(encoding="utf-8-sig")
        assert srt_content.count("-->") >= 2
        plain = text.read_text(encoding="utf-8-sig")
        assert "Hello" in plain and "OCR" in plain
        assert "62094" not in plain
        assert "棋牌" not in plain
        assert "producer" not in plain.lower()
        groups = [
            [line for line in group.splitlines() if line.strip()]
            for group in re.split(r"\n\s*\n", plain.strip())
        ]
        bilingual_groups = [
            lines
            for lines in groups
            if any(re.search(r"[A-Za-z]", line) for line in lines)
            and any(re.search(r"[\u4e00-\u9fff]", line) for line in lines)
        ]
        assert bilingual_groups
        for lines in bilingual_groups:
            english_lines = [
                index
                for index, line in enumerate(lines)
                if re.search(r"[A-Za-z]", line)
            ]
            chinese_lines = [
                index
                for index, line in enumerate(lines)
                if re.search(r"[\u4e00-\u9fff]", line)
            ]
            assert max(english_lines) < min(chinese_lines)
        srt_bodies = []
        for block in re.split(r"\n\s*\n", srt_content.strip()):
            lines = block.splitlines()
            assert len(lines) >= 3 and "-->" in lines[1]
            srt_bodies.append("\n".join(lines[2:]))
        text_bodies = [
            group.strip()
            for group in re.split(r"\n\s*\n", plain.strip())
            if group.strip()
        ]
        assert srt_bodies == text_bodies
        for body in text_bodies:
            for line in body.splitlines():
                if re.search(r"[\u4e00-\u9fff]", line):
                    assert len(line) <= 24
                elif re.search(r"[A-Za-z]", line):
                    assert len(line) <= 48
        assert not (output / "ocr_frames").exists()


def test_video_without_text_is_rejected() -> None:
    with tempfile.TemporaryDirectory(prefix="no-hard-subtitle-") as temp:
        folder = Path(temp)
        video = folder / "plain.mp4"
        created = run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=#304050:size=1280x720:rate=25:duration=6",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(video),
            ]
        )
        assert created.returncode == 0, created.stderr
        output = folder / "output"
        output.mkdir()
        result = run(
            [
                str(PYTHON),
                str(OCR_SCRIPT),
                str(video),
                str(output),
                str(output / "progress.json"),
                str(FFMPEG),
                str(FFPROBE),
            ],
            timeout=600,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert not (output / "硬字幕OCR.srt").exists()
        assert not (output / "硬字幕OCR文字.txt").exists()


if __name__ == "__main__":
    test_synthetic_hard_subtitle_video()
    test_video_without_text_is_rejected()
    print("PASS: synthetic Chinese-English hard subtitle videos")
