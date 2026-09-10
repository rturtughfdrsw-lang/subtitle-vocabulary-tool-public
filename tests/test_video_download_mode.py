from __future__ import annotations

import functools
import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT_ROOT / "runtime"
WORKER_PATH = RUNTIME / "worker.py"
FFMPEG = RUNTIME / "bin" / "ffmpeg.exe"
FFPROBE = RUNTIME / "bin" / "ffprobe.exe"
sys.path.insert(0, str(RUNTIME))


def load_worker():
    spec = importlib.util.spec_from_file_location(
        "video_download_worker",
        WORKER_PATH,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def test_real_local_mp4_download_keeps_video_and_audio_streams():
    worker = load_worker()
    with tempfile.TemporaryDirectory(prefix="video-download-integration-") as temp:
        root = Path(temp)
        source_dir = root / "source"
        output_dir = root / "result"
        source_dir.mkdir()
        output_dir.mkdir()
        source = source_dir / "sample.mp4"
        created = subprocess.run(
            [
                str(FFMPEG),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=25",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:sample_rate=44100",
                "-t",
                "2",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                "-shortest",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert created.returncode == 0, created.stderr

        handler = functools.partial(QuietHandler, directory=str(source_dir))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/{source.name}"
            selected = worker.select_downloadable_media_url(
                output_dir,
                url,
                ["http://127.0.0.1:9/unreachable.m3u8", url],
            )
            assert selected == url
            result = worker.download_video(output_dir, url, url)
            complete_result = root / "complete-result"
            completed = subprocess.run(
                [
                    str(RUNTIME / "Python312" / "python.exe"),
                    str(WORKER_PATH),
                    "video",
                    url,
                    str(complete_result),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        assert result == output_dir / "下载视频.mp4"
        assert result.stat().st_size > 0
        probed = subprocess.run(
            [
                str(FFPROBE),
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "json",
                str(result),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert probed.returncode == 0, probed.stderr
        stream_types = {
            stream["codec_type"]
            for stream in json.loads(probed.stdout)["streams"]
        }
        assert stream_types == {"video", "audio"}
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert (complete_result / "下载视频.mp4").stat().st_size > 0
        assert (complete_result / "视频结果说明.txt").is_file()
        state = json.loads(
            (complete_result / "status.json").read_text(encoding="utf-8")
        )
        assert state["ok"] is True
        assert state["type"] == "MP4 视频"
        assert set(state["phase_timings"]) >= {"线路解析", "视频下载", "结果整理"}
        assert all(
            isinstance(value, int) and value >= 0
            for value in state["phase_timings"].values()
        )


if __name__ == "__main__":
    test_real_local_mp4_download_keeps_video_and_audio_streams()
    print("PASS: real MP4 video download with video and audio streams")
