from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT_ROOT / "runtime"
PYTHON = Path(sys.executable)
WORKER_PATH = RUNTIME / "worker.py"
RESULT_ROOT = PROJECT_ROOT / "字幕结果"
sys.path.insert(0, str(RUNTIME))


def load_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_transcription_profiles_choose_base_only_for_fast_mode():
    profiles = load_path(
        "transcription_profiles",
        RUNTIME / "transcription_profiles.py",
    )
    assert profiles.get_transcription_profile("standard").model_name == "small"
    assert profiles.get_transcription_profile("fast").model_name == "base"
    assert profiles.get_transcription_profile("fast").compute_type == "int8"


def test_transcribe_entrypoint_loads_sibling_modules_with_bundled_python():
    result = subprocess.run(
        [str(PYTHON), str(RUNTIME / "transcribe.py")],
        cwd=RUNTIME,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "ModuleNotFoundError" not in output
    assert "RuntimeError" in output


def test_transcribe_uses_bundled_model_without_network_lookup():
    transcribe = load_path("offline_transcribe", RUNTIME / "transcribe.py")
    captured: dict[str, object] = {}

    class FakeInfo:
        duration = 1.0

    class FakeModel:
        def __init__(self, _name, **kwargs):
            captured.update(kwargs)

        def transcribe(self, *_args, **_kwargs):
            return [], FakeInfo()

    transcribe.WhisperModel = FakeModel
    with tempfile.TemporaryDirectory(prefix="offline-whisper-") as temp:
        folder = Path(temp)
        audio = folder / "silence.wav"
        audio.write_bytes(b"fixture")
        output = folder / "output"
        output.mkdir()
        assert transcribe.main(
            [
                str(audio),
                str(output),
                str(output / "progress.json"),
                "fast",
            ]
        ) == 0
    assert captured.get("local_files_only") is True


def test_fast_subtitle_mode_skips_video_and_ocr_after_remote_subtitle_miss():
    worker = load_path("fast_mode_worker", WORKER_PATH)
    calls: list[tuple[str, str]] = []

    def no_remote(*args, **kwargs):
        return False

    def fake_audio(folder, page_url, media_urls, start=15, end=45):
        calls.append(("audio", ""))
        audio = folder / "视频音频.mp3"
        audio.write_bytes(b"audio")
        return audio

    def fake_transcribe(folder, audio, profile="standard"):
        calls.append(("transcribe", profile))

    def forbidden(*args, **kwargs):
        raise AssertionError("极速模式不得下载 OCR 视频或运行 OCR")

    originals = {
        "try_remote_subtitles": getattr(worker, "try_remote_subtitles", None),
        "download_audio": worker.download_audio,
        "transcribe_audio": worker.transcribe_audio,
        "download_ocr_video": worker.download_ocr_video,
        "extract_embedded_subtitle": worker.extract_embedded_subtitle,
        "try_hard_subtitle_ocr": worker.try_hard_subtitle_ocr,
    }
    worker.try_remote_subtitles = no_remote
    worker.download_audio = fake_audio
    worker.transcribe_audio = fake_transcribe
    worker.download_ocr_video = forbidden
    worker.extract_embedded_subtitle = forbidden
    worker.try_hard_subtitle_ocr = forbidden
    try:
        with tempfile.TemporaryDirectory(prefix="fast-mode-") as temp:
            result = worker.process_subtitle_mode(
                Path(temp),
                "https://page.example/video",
                ["https://cdn.example/index.m3u8"],
                "fast",
            )
    finally:
        for name, value in originals.items():
            if value is None:
                delattr(worker, name)
            else:
                setattr(worker, name, value)

    assert result == "极速语音识别字幕"
    assert calls == [("audio", ""), ("transcribe", "fast")]


def test_fast_subtitle_mode_returns_remote_subtitles_without_audio():
    worker = load_path("fast_remote_worker", WORKER_PATH)
    original_remote = getattr(worker, "try_remote_subtitles", None)
    original_audio = worker.download_audio
    worker.try_remote_subtitles = lambda *args, **kwargs: True
    worker.download_audio = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("已有远程字幕时不得下载音频")
    )
    try:
        with tempfile.TemporaryDirectory(prefix="fast-remote-") as temp:
            result = worker.process_subtitle_mode(
                Path(temp),
                "https://page.example/video",
                ["https://cdn.example/index.m3u8"],
                "fast",
            )
    finally:
        worker.download_audio = original_audio
        if original_remote is None:
            delattr(worker, "try_remote_subtitles")
        else:
            worker.try_remote_subtitles = original_remote
    assert result == "网页原字幕"


def test_ui_helpers_format_elapsed_and_map_fast_mode():
    helper = RUNTIME / "ui_helpers.ps1"
    command = (
        f". '{helper}'; "
        "Format-Elapsed 0; Format-Elapsed 65; Format-Elapsed 3661; "
        "Get-WorkerMode 0; Get-WorkerMode 1; Get-WorkerMode 2; Get-WorkerMode 3; "
        "Get-ModeItems"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "00:00:00",
        "00:01:05",
        "01:01:01",
        "smart",
        "audio",
        "video",
        "fast",
        "智能字幕提取",
        "仅提取 MP3 音频",
        "下载 MP4 视频",
        "极速字幕（语音识别）",
    ]


def test_ui_helpers_format_phase_summary_and_flash_without_error():
    helper = RUNTIME / "ui_helpers.ps1"
    command = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        f". '{helper}'; "
        "$phases=[ordered]@{'线路解析'=2;'视频下载'=65;'结果整理'=1}; "
        "Format-PhaseTimings $phases; "
        "$form=New-Object Windows.Forms.Form; "
        "$null=$form.Handle; Flash-Taskbar $form; $form.Dispose(); "
        "Write-Output 'FLASH_OK'"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "线路解析 00:00:02；视频下载 00:01:05；结果整理 00:00:01",
        "FLASH_OK",
    ]


def test_cancelled_worker_status_contains_elapsed_seconds():
    with tempfile.TemporaryDirectory(prefix="timed-cancel-", dir=RESULT_ROOT) as temp:
        folder = Path(temp)
        status = folder.parent / f".{folder.name}.status.json"
        (folder / "cancel.request").write_text("cancel", encoding="utf-8")
        try:
            result = subprocess.run(
                [
                    str(PYTHON),
                    str(WORKER_PATH),
                    "fast",
                    "https://example.invalid/video",
                    str(folder),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            assert result.returncode == 2, result.stdout + result.stderr
            state = json.loads(status.read_text(encoding="utf-8"))
            assert state["cancelled"] is True
            assert isinstance(state["elapsed_seconds"], int)
            assert state["elapsed_seconds"] >= 0
        finally:
            status.unlink(missing_ok=True)


def test_measure_phase_accumulates_elapsed_time_and_logs_it():
    worker = load_path("phase_timing_worker", WORKER_PATH)
    clock = iter([10.0, 12.6, 20.0, 21.8])
    original = worker.time.monotonic
    worker.time.monotonic = lambda: next(clock)
    try:
        with tempfile.TemporaryDirectory(prefix="phase-timing-") as temp:
            folder = Path(temp)
            timings: dict[str, int] = {}
            with worker.measure_phase(folder, timings, "视频下载"):
                pass
            with worker.measure_phase(folder, timings, "视频下载"):
                pass
            log_text = (folder / "运行记录.txt").read_text(
                encoding="utf-8-sig"
            )
    finally:
        worker.time.monotonic = original

    assert timings == {"视频下载": 3}
    assert log_text.count("阶段耗时：视频下载") == 2
    assert "00:00:02" in log_text
    assert "00:00:01" in log_text


if __name__ == "__main__":
    test_transcription_profiles_choose_base_only_for_fast_mode()
    test_transcribe_entrypoint_loads_sibling_modules_with_bundled_python()
    test_transcribe_uses_bundled_model_without_network_lookup()
    test_fast_subtitle_mode_skips_video_and_ocr_after_remote_subtitle_miss()
    test_fast_subtitle_mode_returns_remote_subtitles_without_audio()
    test_ui_helpers_format_elapsed_and_map_fast_mode()
    test_ui_helpers_format_phase_summary_and_flash_without_error()
    test_cancelled_worker_status_contains_elapsed_seconds()
    test_measure_phase_accumulates_elapsed_time_and_logs_it()
    print("PASS: fast subtitle mode and elapsed timing tests")
