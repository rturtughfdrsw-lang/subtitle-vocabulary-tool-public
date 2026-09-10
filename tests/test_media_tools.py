from pathlib import Path
import os
import sys
import tempfile


RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
sys.path.insert(0, str(RUNTIME))

from media_tools import MediaToolsNotFound, resolve_media_tools


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    return path


def test_bundled_pair_has_priority_over_path():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "runtime"
        bundled_ffmpeg = _exe(root / "bin" / "ffmpeg.exe")
        bundled_ffprobe = _exe(root / "bin" / "ffprobe.exe")
        path_dir = Path(temp) / "path-tools"
        _exe(path_dir / "ffmpeg.exe")
        _exe(path_dir / "ffprobe.exe")

        tools = resolve_media_tools(root, search_path=str(path_dir))
        assert tools.ffmpeg == bundled_ffmpeg
        assert tools.ffprobe == bundled_ffprobe
        assert tools.source == "bundled"


def test_path_pair_is_used_when_bundled_pair_is_absent():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "runtime"
        path_dir = Path(temp) / "path-tools"
        path_ffmpeg = _exe(path_dir / "ffmpeg.exe")
        path_ffprobe = _exe(path_dir / "ffprobe.exe")

        tools = resolve_media_tools(root, search_path=str(path_dir))
        assert tools.ffmpeg == path_ffmpeg
        assert tools.ffprobe == path_ffprobe
        assert tools.source == "path"


def test_partial_bundled_pair_does_not_mix_with_path_pair():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "runtime"
        _exe(root / "bin" / "ffmpeg.exe")
        path_dir = Path(temp) / "path-tools"
        path_ffmpeg = _exe(path_dir / "ffmpeg.exe")
        path_ffprobe = _exe(path_dir / "ffprobe.exe")

        tools = resolve_media_tools(root, search_path=str(path_dir))
        assert tools.ffmpeg == path_ffmpeg
        assert tools.ffprobe == path_ffprobe
        assert tools.source == "path"


def test_missing_pair_has_stable_chinese_dependency_error():
    with tempfile.TemporaryDirectory() as temp:
        try:
            resolve_media_tools(Path(temp) / "runtime", search_path="")
        except MediaToolsNotFound as exc:
            assert str(exc) == "未找到 FFmpeg。请安装 FFmpeg 并加入 PATH。"
        else:
            raise AssertionError("expected MediaToolsNotFound")


if __name__ == "__main__":
    test_bundled_pair_has_priority_over_path()
    test_path_pair_is_used_when_bundled_pair_is_absent()
    test_partial_bundled_pair_does_not_mix_with_path_pair()
    test_missing_pair_has_stable_chinese_dependency_error()
    print("PASS: external FFmpeg lookup tests")
