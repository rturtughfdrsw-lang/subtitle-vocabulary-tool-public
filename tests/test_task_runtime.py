from __future__ import annotations

import importlib.util
import sys
import tempfile
import threading
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "runtime" / "task_runtime.py"


def load_module():
    spec = importlib.util.spec_from_file_location("task_runtime", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_download_progress_parses_percent_speed_and_eta():
    runtime = load_module()
    state = runtime.parse_download_progress(
        "PROGRESS: 37.5%| 4.20MiB/s|00:18"
    )
    assert state is not None
    assert state.percent == 37.5
    assert state.speed == "4.20MiB/s"
    assert state.eta == "00:18"
    assert runtime.parse_download_progress("ordinary log") is None


def test_run_process_forwards_output_and_returns_exit_code():
    runtime = load_module()
    lines: list[str] = []
    code = runtime.run_process(
        [sys.executable, "-u", "-c", "print('hello supervisor')"],
        on_line=lines.append,
        poll_interval=0.02,
    )
    assert code == 0
    assert any("hello supervisor" in line for line in lines)


def test_existing_cancel_request_stops_long_process_quickly():
    runtime = load_module()
    with tempfile.TemporaryDirectory(prefix="task-cancel-") as temp:
        cancel = Path(temp) / "cancel.request"
        cancel.write_text("cancel", encoding="utf-8")
        started = time.perf_counter()
        try:
            runtime.run_process(
                [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
                cancel_path=cancel,
                poll_interval=0.02,
            )
        except runtime.TaskCancelled:
            pass
        else:
            raise AssertionError("TaskCancelled was not raised")
        assert time.perf_counter() - started < 5


def test_inactivity_timeout_stops_silent_process():
    runtime = load_module()
    started = time.perf_counter()
    try:
        runtime.run_process(
            [sys.executable, "-u", "-c", "import time; time.sleep(30)"],
            inactivity_timeout=0.25,
            poll_interval=0.02,
        )
    except runtime.ProcessStalled:
        pass
    else:
        raise AssertionError("ProcessStalled was not raised")
    assert time.perf_counter() - started < 5


def test_heartbeat_file_prevents_false_inactivity_timeout():
    runtime = load_module()
    with tempfile.TemporaryDirectory(prefix="task-heartbeat-") as temp:
        heartbeat = Path(temp) / "progress.json"

        def update_heartbeat():
            for index in range(8):
                heartbeat.write_text(str(index), encoding="utf-8")
                time.sleep(0.1)

        updater = threading.Thread(target=update_heartbeat, daemon=True)
        updater.start()
        code = runtime.run_process(
            [sys.executable, "-u", "-c", "import time; time.sleep(0.75)"],
            inactivity_timeout=0.25,
            heartbeat_path=heartbeat,
            poll_interval=0.02,
        )
        updater.join(timeout=2)
        assert code == 0


if __name__ == "__main__":
    test_download_progress_parses_percent_speed_and_eta()
    test_run_process_forwards_output_and_returns_exit_code()
    test_existing_cancel_request_stops_long_process_quickly()
    test_inactivity_timeout_stops_silent_process()
    test_heartbeat_file_prevents_false_inactivity_timeout()
    print("PASS: task runtime supervision tests")
