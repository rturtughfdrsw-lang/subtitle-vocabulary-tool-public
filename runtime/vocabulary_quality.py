from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

QUALITY_VERSION = "quality-v1"


@dataclass(frozen=True)
class QualityDecision:
    keep: bool
    reason: str


class VocabularyQualityPolicy:
    version = QUALITY_VERSION

    def __init__(self, dictionary_provider=None, frequency_provider=None):
        self.dictionary_provider = dictionary_provider
        self.frequency_provider = frequency_provider

    def prepare(self, rows: Iterable[object]) -> None:
        # Kept as a stable batch-preparation hook for database callers. Admission
        # deliberately uses only the persisted meaning passed to evaluate().
        del rows

    def evaluate(
        self, word: str, meaning: str | None, frequency: float | None
    ) -> QualityDecision:
        del word, frequency
        if isinstance(meaning, str) and meaning.strip():
            return QualityDecision(True, "meaning")
        return QualityDecision(False, "missing_meaning")

    def should_keep(
        self, word: str, meaning: str | None, frequency: float | None
    ) -> bool:
        return self.evaluate(word, meaning, frequency).keep
