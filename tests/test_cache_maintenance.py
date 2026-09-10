from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "runtime" / "cache_maintenance.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "cache_maintenance", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_file(path: Path, content: bytes, age_hours: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    timestamp = time.time() - age_hours * 3600
    os.utime(path, (timestamp, timestamp))


def test_scan_counts_only_old_download_cache_files():
    cache = load_module()
    with tempfile.TemporaryDirectory(prefix="cache-scan-") as temp:
        root = Path(temp)
        make_file(root / "task1" / "video.mp4.part", b"a" * 10, 48)
        make_file(
            root / "task1" / "video.mp4.part-Frag12.part",
            b"b" * 20,
            48,
        )
        make_file(root / "task2" / "video.mp4.ytdl", b"c" * 30, 48)
        make_file(root / "task2" / "young.mp4.part", b"d" * 40, 1)
        make_file(root / "task3" / "视频音频.mp3", b"e" * 50, 48)
        make_file(root / "task3" / "运行记录.txt", b"f" * 60, 48)
        stats = cache.scan_cache(root, older_than_hours=24)
        assert stats.count == 3
        assert stats.total_bytes == 60
        assert all(
            item.name.endswith((".part", ".ytdl")) for item in stats.files
        )


def test_clean_removes_old_cache_but_preserves_results_and_young_parts():
    cache = load_module()
    with tempfile.TemporaryDirectory(prefix="cache-clean-") as temp:
        root = Path(temp)
        old_part = root / "failed" / "video.mp4.part"
        old_ytdl = root / "failed" / "video.mp4.ytdl"
        young_part = root / "active" / "video.mp4.part"
        result = root / "success" / "字幕.srt"
        make_file(old_part, b"a" * 10, 48)
        make_file(old_ytdl, b"b" * 20, 48)
        make_file(young_part, b"c" * 30, 1)
        make_file(result, b"d" * 40, 48)
        removed = cache.clean_cache(root, older_than_hours=24)
        assert removed.count == 2
        assert removed.total_bytes == 30
        assert not old_part.exists()
        assert not old_ytdl.exists()
        assert young_part.exists()
        assert result.exists()


if __name__ == "__main__":
    test_scan_counts_only_old_download_cache_files()
    test_clean_removes_old_cache_but_preserves_results_and_young_parts()
    print("PASS: safe cache maintenance tests")
