from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class TaskCancelled(RuntimeError):
    pass


class ProcessStalled(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadProgress:
    percent: float
    speed: str = ""
    eta: str = ""


def parse_download_progress(line: str) -> DownloadProgress | None:
    match = re.search(
        r"PROGRESS:\s*([\d.]+)%?\s*\|\s*([^|]*)\|\s*([^\r\n]*)",
        str(line),
    )
    if not match:
        return None
    percent, speed, eta = match.groups()
    return DownloadProgress(
        min(100.0, max(0.0, float(percent))),
        speed.strip(),
        eta.strip(),
    )


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            [
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def run_process(
    command: Sequence[str],
    *,
    on_line: Callable[[str], None] | None = None,
    cancel_path: Path | None = None,
    inactivity_timeout: float | None = None,
    heartbeat_path: Path | None = None,
    poll_interval: float = 0.2,
    cwd: Path | None = None,
) -> int:
    process = subprocess.Popen(
        [str(item) for item in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        creationflags=CREATE_NO_WINDOW,
    )
    assert process.stdout is not None
    output: queue.Queue[str | None] = queue.Queue()

    def read_output() -> None:
        try:
            for line in process.stdout:
                output.put(line.rstrip("\r\n"))
        finally:
            output.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    last_activity = time.monotonic()
    heartbeat_stamp: int | None = None
    reader_finished = False
    interval = max(0.01, float(poll_interval))

    try:
        while True:
            try:
                item = output.get(timeout=interval)
                if item is None:
                    reader_finished = True
                else:
                    last_activity = time.monotonic()
                    if on_line is not None:
                        on_line(item)
            except queue.Empty:
                pass

            if heartbeat_path is not None:
                try:
                    current_stamp = heartbeat_path.stat().st_mtime_ns
                except OSError:
                    current_stamp = None
                if current_stamp is not None and current_stamp != heartbeat_stamp:
                    heartbeat_stamp = current_stamp
                    last_activity = time.monotonic()

            if cancel_path is not None and cancel_path.exists():
                terminate_process_tree(process)
                raise TaskCancelled("用户取消了任务")

            if (
                inactivity_timeout is not None
                and inactivity_timeout > 0
                and time.monotonic() - last_activity > inactivity_timeout
            ):
                terminate_process_tree(process)
                raise ProcessStalled(
                    f"子任务连续 {int(inactivity_timeout)} 秒没有进度"
                )

            if process.poll() is not None and reader_finished and output.empty():
                break
    finally:
        if process.poll() is None:
            terminate_process_tree(process)
        reader.join(timeout=1)
        process.stdout.close()

    return int(process.returncode or 0)
