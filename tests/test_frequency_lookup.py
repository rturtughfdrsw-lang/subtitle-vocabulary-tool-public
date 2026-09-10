from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from frequency_lookup import FrequencyLookup, FrequencyResourceError


class CountingZipf:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.values = {
            "the": 7.73,
            "actually": 5.49,
            "obnoxious": 3.38,
            "well-known": 5.30,
            "gpt5": 0.0,
            "naïve": 2.25,
            "negative": -1.0,
        }

    def __call__(self, word: str, language: str) -> float:
        self.calls.append((word, language))
        return self.values.get(word, 0.0)


def test_frequency_lookup_returns_zipf_values_and_none_for_unrecorded_words():
    zipf = CountingZipf()
    lookup = FrequencyLookup(zipf_function=zipf, package_version="3.1.1")

    assert lookup.source == "wordfreq"
    assert lookup.version == "3.1.1"
    assert lookup.lookup("the") == 7.73
    assert lookup.lookup("Actually") == 5.49
    assert lookup.lookup("obnoxious") == 3.38
    assert lookup.lookup("well-known") == 5.30
    assert lookup.lookup("gpt5") is None
    assert lookup.lookup("negative") is None
    assert lookup.lookup("NAÏVE") == 2.25


def test_frequency_lookup_many_deduplicates_and_caches_none_results():
    zipf = CountingZipf()
    lookup = FrequencyLookup(zipf_function=zipf, package_version="3.1.1")

    result = lookup.lookup_many(["Actually", "ACTUALLY", "gpt5", "GPT5"])
    assert result == {
        "Actually": 5.49,
        "ACTUALLY": 5.49,
        "gpt5": None,
        "GPT5": None,
    }
    assert zipf.calls == [("actually", "en"), ("gpt5", "en")]

    assert lookup.lookup("actually") == 5.49
    assert lookup.lookup("GPT5") is None
    assert zipf.calls == [("actually", "en"), ("gpt5", "en")]


def test_frequency_lookup_rejects_missing_or_wrong_version_resources():
    try:
        FrequencyLookup(zipf_function=CountingZipf(), package_version="3.0.0")
        raise AssertionError("wrong wordfreq version must fail")
    except FrequencyResourceError as exc:
        assert "3.1.1" in str(exc)

    with tempfile.TemporaryDirectory(prefix="wordfreq-missing-") as temp:
        missing_vendor = Path(temp) / "missing"
        try:
            FrequencyLookup(vendor_path=missing_vendor)
            raise AssertionError("missing vendor must fail")
        except FrequencyResourceError as exc:
            assert "wordfreq" in str(exc).casefold()


if __name__ == "__main__":
    test_frequency_lookup_returns_zipf_values_and_none_for_unrecorded_words()
    test_frequency_lookup_many_deduplicates_and_caches_none_results()
    test_frequency_lookup_rejects_missing_or_wrong_version_resources()
    print("PASS: word frequency lookup tests")
