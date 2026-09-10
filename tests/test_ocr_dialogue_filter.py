from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "runtime" / "ocr_dialogue_filter.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "ocr_dialogue_filter", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def block(filters, text: str, x: float, y: float):
    return filters.TextBlock(text, 0.95, x, y, 0.35, 0.05)


def test_promotional_text_is_removed_or_trimmed():
    filters = load_module()
    clean, changed = filters.clean_promotional_text(
        "我们都认识体育棋牌香港六合彩百家乐电子澳门六合彩"
    )
    assert changed
    assert clean == "我们都认识"
    assert filters.clean_promotional_text("官网62094.")[0] == ""
    assert filters.clean_promotional_text("棋牌")[0] == ""
    assert filters.clean_promotional_text("棋牌。")[0] == ""
    assert filters.clean_promotional_text(
        "抱歉棋牌电子面家乐抢庄车牛"
    )[0] == "抱歉"
    assert filters.clean_promotional_text("我们周末玩棋牌游戏吧")[0] == (
        "我们周末玩棋牌游戏吧"
    )
    assert filters.clean_promotional_text("com How?")[0] == "How?"
    assert filters.clean_promotional_text("Good lord.")[0] == "Good lord."
    assert filters.clean_promotional_text("come with me")[0] == "come with me"
    assert filters.clean_promotional_text("你提出的实验方案百家乐棂")[0] == (
        "你提出的实验方案"
    )
    assert filters.clean_promotional_text("我会把它放进杯香港六合彩")[0] == (
        "我会把它放进杯"
    )
    assert filters.clean_promotional_text("我去澳门旅行")[0] == "我去澳门旅行"


def test_credit_copyright_and_noise_classification():
    filters = load_module()
    assert filters.is_credit_or_copyright("executive producer Chuck Lorre")
    assert filters.is_credit_or_copyright("未经授权的复制或展出可能导致诉讼")
    assert filters.is_credit_or_copyright("联合制片人玛丽·奎格利")
    assert not filters.is_credit_or_copyright(
        "The producer called me yesterday."
    )
    assert filters.is_obvious_noise("J=0 L=0, S=0 or J:L,⇒")
    assert filters.is_obvious_noise("UTION")
    assert filters.is_obvious_noise("ASAN")
    assert not filters.is_obvious_noise("No")
    assert not filters.is_obvious_noise("Why?")
    assert not filters.is_obvious_noise("Okay")
    assert not filters.is_obvious_noise("NASA")


def test_persistent_overlay_is_removed_but_dialogue_remains():
    filters = load_module()
    frames = []
    for index in range(40):
        blocks = [block(filters, "62094", 0.50, 0.20)]
        if 4 <= index <= 7:
            blocks.extend(
                [
                    block(filters, "Hello there", 0.50, 0.70),
                    block(filters, "你好", 0.50, 0.82),
                ]
            )
        if 20 <= index <= 23:
            blocks.extend(
                [
                    block(filters, "Good night", 0.50, 0.70),
                    block(filters, "晚安", 0.50, 0.82),
                ]
            )
        frames.append(filters.FrameText(index / 2, tuple(blocks)))
    cleaned, stats = filters.clean_frames(frames)
    joined = "\n".join(
        item.text for _, items in cleaned for item in items
    )
    assert "62094" not in joined
    assert "Hello there" in joined
    assert "你好" in joined
    assert "Good night" in joined
    assert "晚安" in joined
    assert stats.persistent_removed > 0 or stats.promotions_removed > 0


def test_repeated_short_dialogue_is_not_classified_as_persistent():
    filters = load_module()
    frames = [
        filters.FrameText(
            index / 2,
            (block(filters, "Okay", 0.50, 0.78),)
            if 5 <= index <= 10
            else (),
        )
        for index in range(40)
    ]
    cleaned, _ = filters.clean_frames(frames)
    assert any(
        item.text == "Okay" for _, items in cleaned for item in items
    )


def test_frame_cleaning_preserves_translation_and_removes_credit_cluster():
    filters = load_module()
    frame = filters.FrameText(
        1.0,
        (
            block(filters, "executive producer", 0.42, 0.22),
            block(filters, "Chuck Lorre", 0.63, 0.24),
            block(filters, "No", 0.50, 0.70),
            block(filters, "不", 0.50, 0.82),
        ),
    )
    cleaned, stats = filters.clean_frames([frame])
    values = [item.text for _, items in cleaned for item in items]
    assert "No" in values and "不" in values
    assert "executive producer" not in " ".join(values)
    assert "Chuck Lorre" not in " ".join(values)
    assert stats.credits_removed == 2


def test_user_sample_representatives_keep_dialogue_and_remove_noise():
    filters = load_module()
    cases = {
        "官网62094.": "",
        "体育棋牌香港六合彩百家乐电子澳门六合彩": "",
        "我们都认识体育棋牌香港六合彩百家乐": "我们都认识",
        "com Nothing.": "Nothing.",
        "联合制片人皮特查克斯": "",
        "Wamer rs. sthe toraf WB 未经授权的复制": "",
        "J=0 L=0, S=0 or J:L,⇒": "",
        "Good lord.": "Good lord.",
        "我的天啊": "我的天啊",
        "I was wondering if you had plans for dinner.": (
            "I was wondering if you had plans for dinner."
        ),
    }
    for source, expected in cases.items():
        assert filters.clean_text_content(source) == expected, source


def test_corrupted_gambling_ad_tails_are_removed_without_harming_dialogue():
    filters = load_module()
    cases = {
        "樽管常手再飾进乳步子捕鱼": "",
        "压俫麻将炸金花": "",
        "合理的技术没有多余的唾沫棋牌电子百家乐抢庄牛牛麻将胡了炸金花": (
            "合理的技术没有多余的唾沫"
        ),
        "我们周末玩棋牌游戏吧": "我们周末玩棋牌游戏吧",
        "我去澳门旅行": "我去澳门旅行",
        "今晚一起去捕鱼": "今晚一起去捕鱼",
    }
    for source, expected in cases.items():
        assert filters.clean_promotional_text(source)[0] == expected, source


def test_transcript_ad_cleaning_uses_only_strong_markers():
    filters = load_module()
    cases = {
        "官网62094.赞助发布主营棋牌体育真人娱乐电子捕鱼": "",
        "本片由62094.赞助发布": "本片由",
        "我们周末玩棋牌游戏吧": "我们周末玩棋牌游戏吧",
        "我去澳门旅行": "我去澳门旅行",
        "今晚一起去捕鱼": "今晚一起去捕鱼",
    }
    for source, expected in cases.items():
        assert filters.clean_transcript_text(source)[0] == expected, source


if __name__ == "__main__":
    test_promotional_text_is_removed_or_trimmed()
    test_credit_copyright_and_noise_classification()
    test_persistent_overlay_is_removed_but_dialogue_remains()
    test_repeated_short_dialogue_is_not_classified_as_persistent()
    test_frame_cleaning_preserves_translation_and_removes_credit_cluster()
    test_user_sample_representatives_keep_dialogue_and_remove_noise()
    test_corrupted_gambling_ad_tails_are_removed_without_harming_dialogue()
    test_transcript_ad_cleaning_uses_only_strong_markers()
    print("PASS: OCR dialogue filtering unit tests")
