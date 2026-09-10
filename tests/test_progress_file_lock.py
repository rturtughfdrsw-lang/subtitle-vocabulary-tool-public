from __future__ import annotations

import ctypes
import importlib.util
import json
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT_ROOT / "runtime"
WORKERS = [RUNTIME / "worker.py"]
LAUNCHERS = [RUNTIME / "launcher.ps1"]


def load_worker(path: Path, index: int):
    spec = importlib.util.spec_from_file_location(f"subtitle_worker_{index}", path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"无法加载：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_without_delete_sharing(path: Path) -> int:
    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ (intentionally excludes FILE_SHARE_DELETE)
        None,
        3,  # OPEN_EXISTING
        0,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError()
    return handle


def test_progress_update_is_nonfatal_during_windows_read_lock() -> None:
    for index, worker_path in enumerate(WORKERS):
        worker = load_worker(worker_path, index)
        with tempfile.TemporaryDirectory(prefix="subtitle-progress-lock-") as temp:
            folder = Path(temp)
            progress_path = folder / "progress.json"
            progress_path.write_text('{"value": 1, "message": "old"}', encoding="utf-8")
            handle = open_without_delete_sharing(progress_path)
            try:
                worker.progress(folder, 50, "locked update")
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)

            worker.progress(folder, 75, "unlocked update")
            state = json.loads(progress_path.read_text(encoding="utf-8"))
            assert state == {"value": 75, "message": "unlocked update"}, worker_path


def test_launcher_json_reader_allows_atomic_replacement() -> None:
    for launcher_path in LAUNCHERS:
        source = launcher_path.read_text(encoding="utf-8-sig")
        assert "function Read-JsonShared" in source, launcher_path
        assert "[IO.FileShare]::Delete" in source, launcher_path
        for variable in ("$progressPath", "$statusPath", "$setupProgress", "$setupStatus"):
            assert f"Read-JsonShared {variable}" in source or variable not in source, (
                launcher_path,
                variable,
            )


if __name__ == "__main__":
    test_progress_update_is_nonfatal_during_windows_read_lock()
    test_launcher_json_reader_allows_atomic_replacement()
    print("PASS: 当前项目的进度文件锁回归测试")
