from collections import Counter
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from vocabulary_filter import (
    BASIC_WORDS_VERSION,
    filter_vocabulary_counts,
    is_basic_word,
)


def test_basic_filter_excludes_only_explicit_small_function_word_set():
    excluded = (
        "the", "a", "an", "is", "am", "are", "was", "were", "be",
        "been", "being", "do", "does", "did", "have", "has", "had",
        "i", "you", "he", "she", "it", "we", "they",
    )
    for word in excluded:
        assert is_basic_word(word), word
    assert is_basic_word("THE") is True

    retained = ("actually", "probably", "instead", "although", "especially",
                "part-time", "well-known", "mother-in-law")
    for word in retained:
        assert is_basic_word(word) is False, word


def test_basic_filter_removes_basic_counts_without_changing_other_counts():
    counts = Counter({
        "the": 10,
        "is": 6,
        "actually": 4,
        "part-time": 2,
    })
    assert filter_vocabulary_counts(counts) == {
        "actually": 4,
        "part-time": 2,
    }
    assert counts == Counter({
        "the": 10,
        "is": 6,
        "actually": 4,
        "part-time": 2,
    })


def test_basic_word_policy_has_an_independent_version():
    assert BASIC_WORDS_VERSION == "basic-v1"


if __name__ == "__main__":
    test_basic_filter_excludes_only_explicit_small_function_word_set()
    test_basic_filter_removes_basic_counts_without_changing_other_counts()
    test_basic_word_policy_has_an_independent_version()
    print("PASS: basic vocabulary filter tests")
