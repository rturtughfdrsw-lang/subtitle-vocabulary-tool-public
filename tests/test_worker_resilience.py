from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT_ROOT / "runtime"
MODULE_PATH = RUNTIME / "worker.py"
PYTHON = RUNTIME / "Python312" / "python.exe"
RESULT_ROOT = PROJECT_ROOT / "字幕结果"
sys.path.insert(0, str(RUNTIME))


def load_module():
    spec = importlib.util.spec_from_file_location("subtitle_worker", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_download_commands_have_progress_resume_and_concurrency():
    worker = load_module()
    folder = PROJECT_ROOT / "test-results" / "task"
    audio = worker.build_download_command(
        "audio",
        folder,
        "https://page.example/video",
        "https://cdn.example/index.m3u8?token=fixture",
        concurrent_fragments=8,
    )
    video = worker.build_download_command(
        "video",
        folder,
        "https://page.example/video",
        "https://cdn.example/index.m3u8?token=fixture",
        concurrent_fragments=8,
    )
    for command in (audio, video):
        assert "--newline" in command
        assert "--progress-template" in command
        assert "--continue" in command
        assert "--abort-on-unavailable-fragments" in command
        assert "--concurrent-fragments" in command
        assert command[command.index("--concurrent-fragments") + 1] == "8"
        assert "--force-overwrites" not in command
    assert "-x" in audio
    assert (
        "bv*[height<=480]+ba/b[height<=480]/"
        "bv*[height<=720]+ba/b[height<=720]/b"
    ) in video


def test_final_video_download_command_uses_1080p_mp4_output():
    worker = load_module()
    folder = PROJECT_ROOT / "test-results" / "task"
    command = worker.build_download_command(
        "download_video",
        folder,
        "https://page.example/video",
        "https://cdn.example/index.m3u8?token=fixture",
        concurrent_fragments=8,
    )
    assert "bv*[height<=1080]+ba/b[height<=1080]/b" in command
    assert command[command.index("--merge-output-format") + 1] == "mp4"
    assert command[command.index("--remux-video") + 1] == "mp4"
    assert str(folder / "下载视频.%(ext)s") in command
    assert str(folder / "OCR临时视频.%(ext)s") not in command
    assert command[command.index("--socket-timeout") + 1] == "30"
    assert "fragment:exp=1:20" in command
    assert "http:exp=1:20" in command
    assert command[command.index("-S") + 1] == (
        "res:1080,vcodec:h264,acodec:aac"
    )


def test_subprocess_failures_are_translated_to_actionable_chinese():
    worker = load_module()
    cases = [
        (["HTTP Error 403: Forbidden"], "403"),
        (["This video is DRM protected"], "DRM"),
        (["Sign in to confirm. Use --cookies-from-browser"], "登录"),
        (["No space left on device"], "磁盘空间不足"),
        (["ERROR: Unsupported URL"], "暂不支持"),
    ]
    for lines, expected in cases:
        assert expected in worker.friendly_subprocess_error(lines, 1)


def test_resolver_failures_preserve_the_actionable_reason():
    worker = load_module()
    cases = [
        ("RuntimeError: 页面内容过大（超过 16 MB）", "页面内容过大"),
        ("urllib.error.HTTPError: HTTP Error 403: Forbidden", "403"),
        ("TimeoutError: timed out", "超时"),
        ("URLError: [Errno 11001] getaddrinfo failed", "域名"),
    ]
    for output, expected in cases:
        assert expected in worker.friendly_resolve_error(output)


def test_deterministic_download_error_does_not_retry():
    worker = load_module()
    calls = 0

    def fake_run(folder, command, **kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("网站拒绝访问（403），链接可能已过期或需要登录。")

    original = worker.run
    worker.run = fake_run
    try:
        with tempfile.TemporaryDirectory(prefix="no-retry-403-") as temp:
            try:
                worker.download_with_retry(
                    "download_video",
                    Path(temp),
                    "https://page.example/video",
                    "https://cdn.example/index.m3u8",
                    (12, 97),
                    "视频",
                )
            except RuntimeError as exc:
                message = str(exc)
            else:
                raise AssertionError("403 必须立即失败")
    finally:
        worker.run = original
    assert calls == 1
    assert "403" in message


def test_ansi_sequences_are_removed_from_logs():
    worker = load_module()
    assert worker.strip_ansi("\x1b[33mWARNING\x1b[0m") == "WARNING"


def test_download_progress_is_logged_once_per_ten_percent_bucket():
    worker = load_module()
    script = (
        "print('PROGRESS:1%|1MiB/s|99');"
        "print('PROGRESS:9%|1MiB/s|91');"
        "print('PROGRESS:10%|2MiB/s|80');"
        "print('PROGRESS:19%|2MiB/s|71');"
        "print('PROGRESS:20%|3MiB/s|60')"
    )
    with tempfile.TemporaryDirectory(prefix="progress-log-") as temp:
        folder = Path(temp)
        worker.run(
            folder,
            [sys.executable, "-c", script],
            progress_range=(12, 97),
            phase="视频",
            inactivity_timeout=10,
        )
        lines = (folder / "运行记录.txt").read_text(
            encoding="utf-8-sig"
        ).splitlines()
    progress_lines = [line for line in lines if line.startswith("下载视频进度：")]
    assert len(progress_lines) == 3
    assert "1%" in progress_lines[0]
    assert "10%" in progress_lines[1]
    assert "20%" in progress_lines[2]


def test_video_metadata_requires_video_stream_and_positive_duration():
    worker = load_module()
    invalid = [
        {"streams": [{"codec_type": "audio"}], "format": {"duration": "10"}},
        {"streams": [{"codec_type": "video"}], "format": {}},
        {"streams": [{"codec_type": "video"}], "format": {"duration": "0"}},
    ]
    for metadata in invalid:
        try:
            worker.validate_video_metadata(metadata)
        except RuntimeError:
            pass
        else:
            raise AssertionError("无视频流或无正时长的 MP4 必须被拒绝")
    worker.validate_video_metadata(
        {"streams": [{"codec_type": "video"}], "format": {"duration": "1.25"}}
    )
    worker.validate_video_metadata(
        {"streams": [{"codec_type": "video", "duration": "2.5"}], "format": {}}
    )


def test_disk_preflight_rejects_insufficient_space():
    worker = load_module()
    with tempfile.TemporaryDirectory(prefix="disk-preflight-") as temp:
        try:
            worker.ensure_free_space(
                Path(temp),
                3 * 1024**3,
                available_bytes=512 * 1024**2,
            )
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("磁盘空间不足时必须拒绝开始下载")
    assert "磁盘空间不足" in message
    assert "3.00 GB" in message
    assert "0.50 GB" in message


def test_invalid_nonempty_mp4_is_rejected_by_ffprobe():
    worker = load_module()
    with tempfile.TemporaryDirectory(prefix="invalid-video-") as temp:
        folder = Path(temp)
        video = folder / "下载视频.mp4"
        video.write_bytes(b"not-a-real-mp4")
        try:
            worker.validate_downloaded_video(folder, video)
        except RuntimeError as exc:
            message = str(exc)
        else:
            raise AssertionError("非空但损坏的 MP4 必须被拒绝")
    assert "无法正常播放" in message or "视频流" in message


def test_resolved_progress_message_matches_selected_mode():
    worker = load_module()
    assert "字幕轨" in worker.resolved_progress_message("smart")
    assert "MP3" in worker.resolved_progress_message("audio")
    assert "MP4" in worker.resolved_progress_message("video")


def test_merger_output_updates_progress_to_mp4_packaging_phase():
    worker = load_module()
    with tempfile.TemporaryDirectory(prefix="merger-progress-") as temp:
        folder = Path(temp)
        worker.run(
            folder,
            [
                sys.executable,
                "-c",
                "print('[Merger] Merging formats into \"video.mp4\"')",
            ],
            progress_range=(12, 97),
            phase="视频",
            inactivity_timeout=10,
        )
        state = json.loads(
            (folder / "progress.json").read_text(encoding="utf-8")
        )
    assert state["value"] == 98
    assert "合并音视频" in state["message"]


def test_zero_byte_audio_is_not_accepted_as_success():
    worker = load_module()

    def fake_download(kind, folder, page_url, media_url, progress_range, phase):
        (folder / "视频音频.mp3").touch()

    original = worker.download_with_retry
    worker.download_with_retry = fake_download
    try:
        with tempfile.TemporaryDirectory(prefix="empty-audio-") as temp:
            try:
                worker.download_audio(
                    Path(temp),
                    "https://page.example/video",
                    "https://cdn.example/audio.m3u8",
                )
            except RuntimeError as exc:
                message = str(exc)
            else:
                raise AssertionError("零字节 MP3 不应被视为成功")
    finally:
        worker.download_with_retry = original
    assert "下载失败" in message


def test_download_progress_message_contains_phase_speed_and_eta():
    worker = load_module()
    state = worker.DownloadProgress(37.5, "4.20MiB/s", "00:18")
    message = worker.download_progress_message("OCR 视频", state)
    assert "OCR 视频" in message
    assert "37%" in message
    assert "4.20MiB/s" in message
    assert "00:18" in message


def test_failure_log_is_redacted_and_pruned_to_thirty_files():
    worker = load_module()
    with tempfile.TemporaryDirectory(prefix="failure-log-") as temp:
        root = Path(temp)
        folder = root / "字幕结果" / "episode-task"
        folder.mkdir(parents=True)
        (folder / "运行记录.txt").write_text(
            "真实视频线路：https://cdn.example/v.m3u8?token=fixture&expires=9\n"
            "错误：下载失败\n",
            encoding="utf-8-sig",
        )
        logs = root / "失败日志"
        logs.mkdir()
        for index in range(35):
            item = logs / f"old-{index:02}.txt"
            item.write_text("old", encoding="utf-8")
            item.touch()
        saved = worker.preserve_failure_log(folder, logs_dir=logs, limit=30)
        assert saved is not None and saved.exists()
        content = saved.read_text(encoding="utf-8-sig")
        assert "token=fixture" not in content
        assert "?[查询参数已隐藏]" in content
        assert "下载失败" in content
        assert len(list(logs.glob("*.txt"))) == 30


def test_worker_has_explicit_cancelled_status_and_safe_missing_args_path():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "except TaskCancelled" in source
    assert "'cancelled': True" in source or '"cancelled": True' in source
    assert "folder = None" in source


def test_preexisting_cancel_request_returns_cancelled_status_and_cleans_folder():
    with tempfile.TemporaryDirectory(
        prefix="cancel-integration-",
        dir=RESULT_ROOT,
    ) as temp:
        folder = Path(temp)
        status = folder.parent / f".{folder.name}.status.json"
        (folder / "cancel.request").write_text("cancel", encoding="utf-8")
        result = subprocess.run(
            [
                str(PYTHON),
                str(MODULE_PATH),
                "smart",
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
        assert not folder.exists()
        state = json.loads(status.read_text(encoding="utf-8"))
        assert state["ok"] is False
        assert state["cancelled"] is True
        status.unlink()


def test_download_retry_steps_down_from_fast_to_stable_to_single_lane():
    worker = load_module()
    calls: list[list[str]] = []

    def fake_run(folder, command, **kwargs):
        calls.append(list(command))
        if len(calls) < 3:
            raise worker.ProcessStalled("simulated stall")

    original = worker.run
    worker.run = fake_run
    try:
        with tempfile.TemporaryDirectory(prefix="download-retry-") as temp:
            worker.download_with_retry(
                "video",
                Path(temp),
                "https://page.example/video",
                "https://cdn.example/index.m3u8",
                (15, 22),
                "OCR 视频",
            )
    finally:
        worker.run = original
    assert len(calls) == 3
    levels = [
        command[command.index("--concurrent-fragments") + 1]
        for command in calls
    ]
    assert levels == ["12", "4", "1"]


def test_full_video_download_uses_conservative_adaptive_concurrency():
    worker = load_module()
    assert worker.download_concurrency_levels("download_video") == (8, 4, 1)


def test_broken_hls_fragments_switch_candidate_without_single_lane_replay():
    worker = load_module()
    bad = "https://bad.example/index.m3u8"
    good = "https://good.example/index.m3u8"
    calls: list[list[str]] = []

    def fake_run(folder, command, **kwargs):
        calls.append(list(command))
        if command[-1] == bad:
            (folder / "OCR临时视频.mp4.part").write_bytes(b"partial")
            (folder / "OCR临时视频.mp4.ytdl").write_text("state")
            raise RuntimeError("视频分片不可用（SSL EOF）")

    original = worker.run
    worker.run = fake_run
    try:
        with tempfile.TemporaryDirectory(prefix="candidate-failover-") as temp:
            folder = Path(temp)
            selected = worker.download_with_retry(
                "video",
                folder,
                "https://page.example/video",
                [bad, good],
                (15, 22),
                "OCR 视频",
            )
            assert not (folder / "OCR临时视频.mp4.part").exists()
            assert not (folder / "OCR临时视频.mp4.ytdl").exists()
    finally:
        worker.run = original

    assert selected == good
    assert [command[-1] for command in calls] == [bad, good]
    assert all(
        command[command.index("--concurrent-fragments") + 1] == "12"
        for command in calls
    )


def test_ssl_fragment_failure_has_specific_error_classification():
    worker = load_module()
    message = worker.friendly_subprocess_error(
        [
            "Got error: EOF occurred in violation of protocol (_ssl.c:1007)",
            "fragment not found; Skipping fragment 1",
        ],
        1,
    )
    assert "分片不可用" in message


def test_candidate_ordering_stops_probing_after_first_surface_success():
    worker = load_module()
    candidates = [
        "https://first.example/index.m3u8",
        "https://second.example/index.m3u8",
        "https://third.example/index.m3u8",
    ]
    probed: list[str] = []

    def fake_probe(folder, page_url, media_url):
        probed.append(media_url)
        if len(probed) > 1:
            raise AssertionError("第一条表面可用后不应继续预探测备用线路")
        return True

    original = worker.probe_media_stream
    worker.probe_media_stream = fake_probe
    try:
        with tempfile.TemporaryDirectory(prefix="lazy-candidates-") as temp:
            ordered = worker.order_downloadable_media_urls(
                Path(temp),
                "https://page.example/video",
                candidates,
            )
    finally:
        worker.probe_media_stream = original

    assert probed == [candidates[0]]
    assert ordered == candidates


def test_route_cache_stores_only_hosts_and_expires_after_seven_days():
    worker = load_module()
    with tempfile.TemporaryDirectory(prefix="route-cache-") as temp:
        cache = Path(temp) / "routes.json"
        worker.remember_successful_route(
            "https://page.example/watch/3?session=fixture",
            "https://CDN.example/video/index.m3u8?token=fixture&expires=9",
            cache_path=cache,
            now=100.0,
        )
        payload = cache.read_text(encoding="utf-8")
        assert "secret" not in payload
        assert "session" not in payload
        assert worker.read_route_cache(cache, now=100.0) == {
            "page.example": "cdn.example"
        }
        assert worker.read_route_cache(
            cache,
            now=100.0 + 7 * 24 * 3600 + 1,
        ) == {}


def test_cached_media_host_is_probed_first_without_dropping_candidates():
    worker = load_module()
    candidates = [
        "https://slow.example/index.m3u8",
        "https://fast.example/index.m3u8?token=fresh",
        "https://backup.example/index.m3u8",
    ]
    probed: list[str] = []

    def fake_probe(folder, page_url, media_url):
        probed.append(media_url)
        return True

    original = worker.probe_media_stream
    worker.probe_media_stream = fake_probe
    try:
        with tempfile.TemporaryDirectory(prefix="cached-route-order-") as temp:
            root = Path(temp)
            cache = root / "routes.json"
            worker.remember_successful_route(
                "https://page.example/watch/3",
                candidates[1],
                cache_path=cache,
                now=500.0,
            )
            ordered = worker.order_downloadable_media_urls(
                root,
                "https://page.example/watch/4",
                candidates,
                cache_path=cache,
                now=500.0,
            )
    finally:
        worker.probe_media_stream = original

    assert probed == [candidates[1]]
    assert ordered == [candidates[1], candidates[0], candidates[2]]


def test_media_probe_short_reads_real_stream_with_referer():
    worker = load_module()
    captured: list[str] = []

    def fake_capture(folder, command, **kwargs):
        captured.extend(command)
        return 0, ""

    original = worker.capture
    worker.capture = fake_capture
    try:
        with tempfile.TemporaryDirectory(prefix="media-probe-") as temp:
            ok = worker.probe_media_stream(
                Path(temp),
                "https://page.example/watch/3",
                "https://cdn.example/index.m3u8?token=fresh",
            )
    finally:
        worker.capture = original

    assert ok is True
    assert str(RUNTIME / "bin" / "ffmpeg.exe") == captured[0]
    assert captured[captured.index("-t") + 1] == "0.25"
    assert "Referer: https://page.example/watch/3\r\n" in captured
    assert "https://cdn.example/index.m3u8?token=fresh" in captured
    assert os.devnull in captured


def test_download_command_uses_resolved_path_ffmpeg_binary():
    worker = load_module()
    from media_tools import MediaTools

    fake_ffmpeg = Path("external-tools/ffmpeg.exe")
    fake_ffprobe = Path("external-tools/ffprobe.exe")
    worker._MEDIA_TOOLS = MediaTools(fake_ffmpeg, fake_ffprobe, "path")
    command = worker.build_download_command(
        "audio",
        Path("task"),
        "https://page.example/watch",
        "https://cdn.example/media.m3u8",
    )
    assert command[command.index("--ffmpeg-location") + 1] == str(fake_ffmpeg)


def test_failed_worker_preserves_log_and_removes_incomplete_folder():
    with tempfile.TemporaryDirectory(
        prefix="failure-integration-",
        dir=RESULT_ROOT,
    ) as temp:
        folder = Path(temp)
        status = folder.parent / f".{folder.name}.status.json"
        saved_log: Path | None = None
        try:
            result = subprocess.run(
                [
                    str(PYTHON),
                    str(MODULE_PATH),
                    "smart",
                    "http://127.0.0.1:9/unreachable.m3u8",
                    str(folder),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            assert result.returncode == 1, result.stdout + result.stderr
            assert not folder.exists()
            state = json.loads(status.read_text(encoding="utf-8"))
            assert state["ok"] is False
            assert state["cancelled"] is False
            saved_log = Path(state["log"])
            assert saved_log.exists()
            content = saved_log.read_text(
                encoding="utf-8-sig",
                errors="replace",
            )
            assert "错误：" in content
            assert "任务失败，总用时：" in content
        finally:
            status.unlink(missing_ok=True)
            if saved_log is not None:
                saved_log.unlink(missing_ok=True)


if __name__ == "__main__":
    test_download_commands_have_progress_resume_and_concurrency()
    test_final_video_download_command_uses_1080p_mp4_output()
    test_subprocess_failures_are_translated_to_actionable_chinese()
    test_resolver_failures_preserve_the_actionable_reason()
    test_deterministic_download_error_does_not_retry()
    test_ansi_sequences_are_removed_from_logs()
    test_download_progress_is_logged_once_per_ten_percent_bucket()
    test_video_metadata_requires_video_stream_and_positive_duration()
    test_disk_preflight_rejects_insufficient_space()
    test_invalid_nonempty_mp4_is_rejected_by_ffprobe()
    test_resolved_progress_message_matches_selected_mode()
    test_merger_output_updates_progress_to_mp4_packaging_phase()
    test_zero_byte_audio_is_not_accepted_as_success()
    test_download_progress_message_contains_phase_speed_and_eta()
    test_failure_log_is_redacted_and_pruned_to_thirty_files()
    test_worker_has_explicit_cancelled_status_and_safe_missing_args_path()
    test_preexisting_cancel_request_returns_cancelled_status_and_cleans_folder()
    test_download_retry_steps_down_from_fast_to_stable_to_single_lane()
    test_full_video_download_uses_conservative_adaptive_concurrency()
    test_broken_hls_fragments_switch_candidate_without_single_lane_replay()
    test_ssl_fragment_failure_has_specific_error_classification()
    test_candidate_ordering_stops_probing_after_first_surface_success()
    test_route_cache_stores_only_hosts_and_expires_after_seven_days()
    test_cached_media_host_is_probed_first_without_dropping_candidates()
    test_media_probe_short_reads_real_stream_with_referer()
    test_download_command_uses_resolved_path_ffmpeg_binary()
    test_failed_worker_preserves_log_and_removes_incomplete_folder()
    print("PASS: worker resilience tests")
