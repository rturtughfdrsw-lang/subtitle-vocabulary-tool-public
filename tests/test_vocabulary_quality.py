from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from vocabulary_quality import VocabularyQualityPolicy


class MappingProvider:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def lookup_many(self, words):
        requested = list(words)
        self.calls.append(requested)
        return {word: self.values.get(word) for word in requested}


def test_quality_v1_requires_meaning_regardless_of_zipf_frequency():
    policy = VocabularyQualityPolicy()

    cases = (
        ("common", "常见的", 6.0, True),
        ("rare", "罕见的", 1.0, True),
        ("unranked", "未排名的", None, True),
        ("frequentjunk", None, 6.0, False),
        ("ala", None, 3.53, False),
        ("rarejunk", None, 1.0, False),
        ("unknown", None, None, False),
        ("lookat", None, 4.0, False),
        ("aromantic", None, 1.67, False),
    )
    for word, meaning, frequency, expected in cases:
        assert policy.should_keep(word, meaning, frequency) is expected, word


def test_quality_v1_prepare_does_not_turn_unpersisted_provider_meaning_visible():
    meanings = MappingProvider({"look": "看", "at": "在"})
    frequencies = MappingProvider({"look": 5.0, "note": 4.5, "book": 4.7})
    policy = VocabularyQualityPolicy(meanings, frequencies)
    rows = [
        ("lookat", None, 4.0),
        ("notebook", None, 4.0),
        ("ala", None, 3.0),
        ("part-time", None, 2.1),
        ("aromantic", "无浪漫倾向的", 1.67),
    ]

    policy.prepare(rows)

    assert policy.evaluate("lookat", None, 4.0).reason == "missing_meaning"
    assert policy.should_keep("lookat", None, 4.0) is False
    assert policy.should_keep("notebook", None, 4.0) is False
    assert policy.should_keep("ala", None, 3.0) is False
    assert policy.should_keep("part-time", None, 2.1) is False
    assert policy.should_keep("aromantic", "无浪漫倾向的", 1.67) is True


if __name__ == "__main__":
    test_quality_v1_requires_meaning_regardless_of_zipf_frequency()
    test_quality_v1_prepare_does_not_turn_unpersisted_provider_meaning_visible()
    print("PASS: vocabulary quality-v1 tests")
