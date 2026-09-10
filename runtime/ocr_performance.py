from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PerformanceStats:
    decoded_frames: int = 0
    ocr_frames: int = 0
    reused_frames: int = 0
    ocr_seconds: float = 0.0


class FrameScheduler:
    def __init__(
        self,
        force_interval: float = 2.0,
        change_threshold: float = 0.03,
    ) -> None:
        self.force_interval = max(0.5, float(force_interval))
        self.change_threshold = max(0.001, float(change_threshold))
        self._last_ocr_time: float | None = None
        self._last_signature: np.ndarray | None = None
        self._decoded_frames = 0
        self._ocr_frames = 0
        self._reused_frames = 0
        self._ocr_seconds = 0.0

    @staticmethod
    def _signature(frame: np.ndarray) -> np.ndarray:
        if frame.ndim == 3:
            gray = (
                frame[:, :, 0].astype(np.float32) * 0.114
                + frame[:, :, 1].astype(np.float32) * 0.587
                + frame[:, :, 2].astype(np.float32) * 0.299
            )
        else:
            gray = frame.astype(np.float32)
        height, width = gray.shape
        rows = np.linspace(
            0, max(0, height - 1), min(64, height), dtype=np.int32
        )
        columns = np.linspace(
            0, max(0, width - 1), min(160, width), dtype=np.int32
        )
        return gray[np.ix_(rows, columns)] / 255.0

    def should_ocr(
        self,
        frame: np.ndarray,
        timestamp: float,
        blocks: tuple[Any, ...],
    ) -> bool:
        del blocks
        if self._last_signature is None or self._last_ocr_time is None:
            return True
        if float(timestamp) - self._last_ocr_time >= self.force_interval:
            return True
        current = self._signature(frame)
        difference = float(np.mean(np.abs(current - self._last_signature)))
        return difference >= self.change_threshold

    def record_ocr(
        self,
        frame: np.ndarray,
        timestamp: float,
        blocks: tuple[Any, ...],
        elapsed: float,
    ) -> None:
        del blocks
        self._last_signature = self._signature(frame)
        self._last_ocr_time = float(timestamp)
        self._decoded_frames += 1
        self._ocr_frames += 1
        self._ocr_seconds += max(0.0, float(elapsed))

    def record_reuse(self) -> None:
        self._decoded_frames += 1
        self._reused_frames += 1

    @property
    def stats(self) -> PerformanceStats:
        return PerformanceStats(
            decoded_frames=self._decoded_frames,
            ocr_frames=self._ocr_frames,
            reused_frames=self._reused_frames,
            ocr_seconds=round(self._ocr_seconds, 6),
        )
