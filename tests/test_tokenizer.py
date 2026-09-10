from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from tokenizer import TOKENIZER_VERSION, tokenize


def test_tokenizer_normalizes_case_and_keeps_meaningful_hyphen_compounds():
    text = (
        "Actually ACTUALLY actually don't don’t isn't I'm he's we're they've "
        "well-known well‐known part-time mother‑in‑law"
    )
    assert tokenize(text) == [
        "actually",
        "actually",
        "actually",
        "well-known",
        "well-known",
        "part-time",
        "mother-in-law",
    ]


def test_tokenizer_excludes_whole_apostrophe_and_alphanumeric_tokens_without_splitting():
    text = "don't isn't I'm O’Connor GPT5 10bbc 18401185q Win11 H264"
    assert tokenize(text) == []


def test_tokenizer_keeps_plain_words_inflections_and_basic_words_for_later_filtering():
    text = "Sheldon Microsoft California play plays played playing the a is have do"
    assert tokenize(text) == [
        "sheldon",
        "microsoft",
        "california",
        "play",
        "plays",
        "played",
        "playing",
        "the",
        "a",
        "is",
        "have",
        "do",
    ]


def test_tokenizer_keeps_complete_unicode_latin_words_without_collecting_cjk():
    assert tokenize("Café naïve résumé coöperate Zoë 你好") == [
        "café",
        "naïve",
        "résumé",
        "coöperate",
        "zoë",
    ]


def test_tokenizer_version_is_fixed_for_persisted_imports():
    assert TOKENIZER_VERSION == "en-v2"


if __name__ == "__main__":
    test_tokenizer_normalizes_case_and_keeps_meaningful_hyphen_compounds()
    test_tokenizer_excludes_whole_apostrophe_and_alphanumeric_tokens_without_splitting()
    test_tokenizer_keeps_plain_words_inflections_and_basic_words_for_later_filtering()
    test_tokenizer_keeps_complete_unicode_latin_words_without_collecting_cjk()
    test_tokenizer_version_is_fixed_for_persisted_imports()
    print("PASS: vocabulary tokenizer V2 tests")
