from collections.abc import Mapping


BASIC_WORDS_VERSION = "basic-v1"

BASIC_WORDS = frozenset(
    {
        "a",
        "am",
        "an",
        "are",
        "be",
        "been",
        "being",
        "did",
        "do",
        "does",
        "had",
        "has",
        "have",
        "he",
        "i",
        "is",
        "it",
        "she",
        "the",
        "they",
        "was",
        "we",
        "were",
        "you",
    }
)


def is_basic_word(word: str) -> bool:
    return word.casefold() in BASIC_WORDS


def filter_vocabulary_counts(counts: Mapping[str, int]) -> dict[str, int]:
    return {
        word: count
        for word, count in counts.items()
        if not is_basic_word(word)
    }
