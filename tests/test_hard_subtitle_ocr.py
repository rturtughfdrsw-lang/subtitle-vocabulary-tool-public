from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "runtime" / "hard_subtitle_ocr.py"
WORKER_PATH = PROJECT_ROOT / "runtime" / "worker.py"


def load_module():
    runtime = str(MODULE_PATH.parent)
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    spec = importlib.util.spec_from_file_location("hard_subtitle_ocr", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rapidocr_blocks_keep_relative_geometry_and_exclude_edges():
    ocr = load_module()

    class Result:
        boxes = np.array(
            [
                [[50, 60], [150, 60], [150, 75], [50, 75]],
                [[60, 78], [140, 78], [140, 94], [60, 94]],
                [[0, 40], [20, 40], [20, 55], [0, 55]],
            ],
            dtype=np.float32,
        )
        txts = ["Hello there", "你好", "edge"]
        scores = [0.96, 0.94, 0.99]

    class Engine:
        def __call__(self, *_args, **_kwargs):
            return Result()

    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    blocks = ocr.recognize_frame_blocks(Engine(), frame)
    assert [item.text for item in blocks] == ["Hello there", "你好"]
    assert blocks[0].center_x == 0.5
    assert blocks[0].center_y == 0.675
    assert blocks[0].width == 0.5
    assert blocks[0].height == 0.15
    assert ocr.blocks_to_text(blocks) == "Hello there\n你好"


def test_backend_info_requires_all_rapidocr_sessions_to_use_cuda():
    ocr = load_module()

    class OrtSession:
        def __init__(self, providers):
            self._providers = providers

        def get_providers(self):
            return list(self._providers)

    class Holder:
        def __init__(self, providers):
            self.session = OrtSession(providers)

    class Stage:
        def __init__(self, providers):
            self.session = Holder(providers)

    class Engine:
        def __init__(self, providers):
            self.text_det = Stage(providers)
            self.text_rec = Stage(providers)
            self.text_cls = Stage(providers)

    cuda = ocr.rapidocr_backend(
        Engine(["CUDAExecutionProvider", "CPUExecutionProvider"])
    )
    assert cuda.accelerated
    assert cuda.provider == "CUDAExecutionProvider"
    assert cuda.name == "RTX 4060 / CUDA"

    cpu = ocr.rapidocr_backend(Engine(["CPUExecutionProvider"]))
    assert not cpu.accelerated
    assert cpu.provider == "CPUExecutionProvider"
    assert cpu.name == "CPU"

    mixed = Engine(["CUDAExecutionProvider", "CPUExecutionProvider"])
    mixed.text_rec = Stage(["CPUExecutionProvider"])
    partial = ocr.rapidocr_backend(mixed)
    assert not partial.accelerated
    assert partial.provider == "CPUExecutionProvider"


def test_gpu_bootstrap_is_optional_when_runtime_directory_is_absent():
    ocr = load_module()
    with tempfile.TemporaryDirectory() as temp:
        assert not ocr.bootstrap_gpu_runtime(Path(temp))


def test_installed_gpu_runtime_accelerates_real_rapidocr_sessions():
    ocr = load_module()
    engine, backend = ocr.ensure_rapidocr()
    assert engine is not None
    assert backend.accelerated, backend.detail
    assert backend.provider == "CUDAExecutionProvider"


def test_output_size_uses_target_width_without_upscaling():
    ocr = load_module()
    assert ocr.output_size(
        ocr.VideoInfo(10.0, 1920, 1080), target_width=960
    ) == (960, 242)
    assert ocr.output_size(
        ocr.VideoInfo(10.0, 640, 360), target_width=960
    ) == (640, 162)


def test_normalization_and_validation():
    ocr = load_module()
    assert ocr.normalize_text("  你好， 世界！  ") == "你好，世界！"
    assert ocr.normalize_text("Hello   world") == "Hello world"
    assert ocr.is_valid_text("你好 Hello")
    assert not ocr.is_valid_text("...")
    assert not ocr.is_valid_text("1")


def test_bilingual_text_places_english_above_chinese():
    ocr = load_module()
    formatted = ocr.format_subtitle_text(
        ["我认为是改变我们", "I was going to characterize it as the modification"]
    )
    assert formatted.splitlines() == [
        "I was going to characterize it as the",
        "modification",
        "我认为是改变我们",
    ]


def test_single_language_text_stays_on_one_line_until_full():
    ocr = load_module()
    assert ocr.format_subtitle_text("Hello world") == "Hello world"
    assert ocr.format_subtitle_text("这是一句清楚的中文字幕") == "这是一句清楚的中文字幕"


def test_long_english_wraps_without_splitting_words():
    ocr = load_module()
    source = "This sentence contains several ordinary words and should wrap clearly."
    lines = ocr.format_subtitle_text(source, english_width=24).splitlines()
    assert all(len(line) <= 24 for line in lines)
    assert " ".join(lines) == source


def test_long_chinese_prefers_punctuation_boundaries():
    ocr = load_module()
    source = "这是第一部分，这是第二部分，而且还要继续说明。最后一句结束。"
    lines = ocr.format_subtitle_text(source, chinese_width=12).splitlines()
    assert all(len(line) <= 12 for line in lines)
    assert "".join(lines) == source
    assert lines[0].endswith("，")


def test_near_duplicate_samples_merge_into_one_segment():
    ocr = load_module()
    samples = [
        (0.0, ""),
        (0.5, "你好世界"),
        (1.0, "你好世界"),
        (1.5, "你好世畀"),
        (2.0, ""),
        (2.5, "Hello world"),
        (3.0, "Hello world"),
        (3.5, ""),
    ]
    segments = ocr.build_segments(samples, step=0.5)
    assert [(s.start, s.end, s.text) for s in segments] == [
        (0.5, 2.0, "你好世界"),
        (2.5, 3.5, "Hello world"),
    ]


def test_output_contains_srt_and_plain_text():
    ocr = load_module()
    segments = [
        ocr.Segment(0.5, 2.0, "你好世界"),
        ocr.Segment(2.5, 3.5, "Hello world"),
    ]
    with tempfile.TemporaryDirectory() as temp:
        srt, text = ocr.write_outputs(segments, Path(temp))
        assert "00:00:00,500 --> 00:00:02,000" in srt.read_text(
            encoding="utf-8-sig"
        )
        assert text.read_text(encoding="utf-8-sig") == (
            "你好世界\n\nHello world\n\n"
        )


def test_output_contains_matching_bilingual_layout():
    ocr = load_module()
    segment = ocr.Segment(
        0.5,
        2.0,
        "我认为是改变我们 I was going to characterize it as the modification",
    )
    with tempfile.TemporaryDirectory() as temp:
        srt, text = ocr.write_outputs([segment], Path(temp))
        body = "\n".join(
            [
                "I was going to characterize it as the",
                "modification",
                "我认为是改变我们",
            ]
        )
        assert body in srt.read_text(encoding="utf-8-sig")
        assert text.read_text(encoding="utf-8-sig").strip() == body


def test_plain_text_separates_subtitle_groups_with_blank_line():
    ocr = load_module()
    segments = [
        ocr.Segment(0.0, 1.0, "Hello 你好"),
        ocr.Segment(1.0, 2.0, "Next 下一句"),
    ]
    with tempfile.TemporaryDirectory() as temp:
        _, text = ocr.write_outputs(segments, Path(temp))
        assert "Hello\n你好\n\nNext\n下一句" in text.read_text(
            encoding="utf-8-sig"
        )


def test_formatting_is_idempotent():
    ocr = load_module()
    once = ocr.format_subtitle_text("English line 中文字幕")
    assert ocr.format_subtitle_text(once) == once


def test_reformatting_preserves_a_numeric_line():
    ocr = load_module()
    source = "Please enter the verification code shown here 123456"
    once = ocr.format_subtitle_text(source)
    assert once.splitlines()[-1] == "123456"
    assert ocr.format_subtitle_text(once) == once


def test_reformatting_preserves_a_leading_numeric_line():
    ocr = load_module()
    source = "123456 " + "A" * 45
    once = ocr.format_subtitle_text(source)
    assert once.splitlines()[0] == "123456"
    assert ocr.format_subtitle_text(once) == once


def test_bilingual_parentheses_remain_balanced():
    ocr = load_module()
    assert ocr.format_subtitle_text("你好（Hello）") == "（Hello）\n你好"


def test_english_without_spaces_wraps_after_punctuation():
    ocr = load_module()
    source = "One,two,three,four,five,six,seven,eight,nine,ten"
    lines = ocr.format_subtitle_text(source, english_width=16).splitlines()
    assert all(len(line) <= 16 for line in lines)
    assert "".join(lines) == source
    assert lines[0].endswith(",")


def test_chinese_wrapper_preserves_existing_lines():
    ocr = load_module()
    assert ocr.wrap_chinese("第一行\n第二行", width=24) == ["第一行", "第二行"]


def test_chinese_wrap_recognizes_half_width_punctuation():
    ocr = load_module()
    source = "第一部分,第二部分,第三部分。"
    lines = ocr.format_subtitle_text(source, chinese_width=6).splitlines()
    assert all(len(line) <= 6 for line in lines)
    assert "".join(lines) == source
    assert lines[0].endswith(",")


def test_detection_requires_repeated_and_changing_subtitles():
    ocr = load_module()
    assert ocr.has_stable_hard_subtitles(
        ["你好世界", "你好世界", "下一句话", "", "下一句话"]
    )
    assert not ocr.has_stable_hard_subtitles(
        ["电视台", "电视台", "电视台", "", ""]
    )
    assert not ocr.has_stable_hard_subtitles(["", "", "偶然文字", "", ""])


def test_worker_places_ocr_before_whisper():
    source = WORKER_PATH.read_text(encoding="utf-8")
    track = source.index("extract_embedded_subtitle")
    ocr = source.index("try_hard_subtitle_ocr")
    whisper = source.index("transcribe.py")
    assert track < ocr < whisper
    assert "OCR临时视频" in source
    assert "硬字幕 OCR" in source


if __name__ == "__main__":
    test_rapidocr_blocks_keep_relative_geometry_and_exclude_edges()
    test_backend_info_requires_all_rapidocr_sessions_to_use_cuda()
    test_gpu_bootstrap_is_optional_when_runtime_directory_is_absent()
    test_installed_gpu_runtime_accelerates_real_rapidocr_sessions()
    test_output_size_uses_target_width_without_upscaling()
    test_normalization_and_validation()
    test_bilingual_text_places_english_above_chinese()
    test_single_language_text_stays_on_one_line_until_full()
    test_long_english_wraps_without_splitting_words()
    test_long_chinese_prefers_punctuation_boundaries()
    test_near_duplicate_samples_merge_into_one_segment()
    test_output_contains_srt_and_plain_text()
    test_output_contains_matching_bilingual_layout()
    test_plain_text_separates_subtitle_groups_with_blank_line()
    test_formatting_is_idempotent()
    test_reformatting_preserves_a_numeric_line()
    test_reformatting_preserves_a_leading_numeric_line()
    test_bilingual_parentheses_remain_balanced()
    test_english_without_spaces_wraps_after_punctuation()
    test_chinese_wrapper_preserves_existing_lines()
    test_chinese_wrap_recognizes_half_width_punctuation()
    test_detection_requires_repeated_and_changing_subtitles()
    test_worker_places_ocr_before_whisper()
    print("PASS: hard subtitle OCR unit and workflow tests")
