from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "runtime" / "ocr_performance.py"


def load_module():
    spec = importlib.util.spec_from_file_location("ocr_performance", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_identical_frames_are_reused_until_force_interval():
    performance = load_module()
    scheduler = performance.FrameScheduler(force_interval=2.0)
    assert scheduler.change_threshold == 0.03
    frame = np.zeros((160, 480, 3), dtype=np.uint8)
    assert scheduler.should_ocr(frame, 0.0, ())
    scheduler.record_ocr(frame, 0.0, (), 0.2)
    assert not scheduler.should_ocr(frame.copy(), 0.5, ())
    scheduler.record_reuse()
    assert not scheduler.should_ocr(frame.copy(), 1.5, ())
    scheduler.record_reuse()
    assert scheduler.should_ocr(frame.copy(), 2.0, ())


def test_new_subtitle_edges_trigger_ocr_before_force_interval():
    performance = load_module()
    scheduler = performance.FrameScheduler(force_interval=2.0)
    blank = np.zeros((160, 480, 3), dtype=np.uint8)
    text = blank.copy()
    text[110:125, 150:330] = 255
    assert scheduler.should_ocr(blank, 0.0, ())
    scheduler.record_ocr(blank, 0.0, (), 0.1)
    assert scheduler.should_ocr(text, 0.5, ())


def test_small_background_change_is_reused():
    performance = load_module()
    scheduler = performance.FrameScheduler(
        force_interval=2.0, change_threshold=0.02
    )
    frame = np.zeros((160, 480, 3), dtype=np.uint8)
    changed = frame.copy()
    changed[5:10, 5:10] = 100
    scheduler.record_ocr(frame, 0.0, (), 0.1)
    assert not scheduler.should_ocr(changed, 0.5, ())


def test_stats_count_decoded_ocr_and_reused_frames():
    performance = load_module()
    scheduler = performance.FrameScheduler()
    frame = np.zeros((160, 480, 3), dtype=np.uint8)
    scheduler.record_ocr(frame, 0.0, (), 0.25)
    scheduler.record_reuse()
    stats = scheduler.stats
    assert stats.decoded_frames == 2
    assert stats.ocr_frames == 1
    assert stats.reused_frames == 1
    assert stats.ocr_seconds == 0.25


if __name__ == "__main__":
    test_identical_frames_are_reused_until_force_interval()
    test_new_subtitle_edges_trigger_ocr_before_force_interval()
    test_small_background_change_is_reused()
    test_stats_count_decoded_ocr_and_reused_frames()
    print("PASS: adaptive OCR performance scheduler tests")
