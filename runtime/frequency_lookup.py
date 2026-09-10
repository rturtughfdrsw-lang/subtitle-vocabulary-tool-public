from __future__ import annotations

from collections.abc import Callable, Iterable
import importlib
import importlib.metadata
import math
from pathlib import Path
import sys
import unicodedata


WORDFREQ_VERSION = "3.1.1"
DEFAULT_VENDOR_PATH = Path(__file__).resolve().parent / "vocabulary_resources" / "vendor"


class FrequencyResourceError(RuntimeError):
    pass


def _normalize_word(word: str) -> str:
    if not isinstance(word, str):
        raise TypeError("word must be a string")
    return unicodedata.normalize("NFC", word).strip().casefold()


def _load_wordfreq(vendor_path: Path) -> tuple[Callable[[str, str], float], str]:
    if not vendor_path.is_dir():
        raise FrequencyResourceError(f"wordfreq vendor 资源不存在：{vendor_path}")
    vendor_text = str(vendor_path.resolve())
    if vendor_text not in sys.path:
        sys.path.insert(0, vendor_text)
    try:
        module = importlib.import_module("wordfreq")
        package_version = importlib.metadata.version("wordfreq")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise FrequencyResourceError("无法加载离线 wordfreq 资源。") from exc
    return module.zipf_frequency, package_version


class FrequencyLookup:
    source = "wordfreq"
    version = WORDFREQ_VERSION

    def __init__(
        self,
        *,
        zipf_function: Callable[[str, str], float] | None = None,
        package_version: str | None = None,
        vendor_path: str | Path = DEFAULT_VENDOR_PATH,
    ):
        if zipf_function is None:
            zipf_function, package_version = _load_wordfreq(Path(vendor_path))
        if package_version != WORDFREQ_VERSION:
            raise FrequencyResourceError(
                f"wordfreq 版本必须为 {WORDFREQ_VERSION}，实际为 {package_version or '未知'}。"
            )
        self._zipf_frequency = zipf_function
        self._cache: dict[str, float | None] = {}

    def lookup_many(self, words: Iterable[str]) -> dict[str, float | None]:
        requested = list(words)
        normalized_by_word = {word: _normalize_word(word) for word in requested}
        missing = sorted(
            {
                normalized
                for normalized in normalized_by_word.values()
                if normalized and normalized not in self._cache
            }
        )
        for word in missing:
            raw_value = float(self._zipf_frequency(word, "en"))
            value = raw_value if math.isfinite(raw_value) and raw_value > 0.0 else None
            self._cache[word] = value
        return {
            word: self._cache.get(normalized) if normalized else None
            for word, normalized in normalized_by_word.items()
        }

    def lookup(self, word: str) -> float | None:
        return self.lookup_many((word,))[word]
