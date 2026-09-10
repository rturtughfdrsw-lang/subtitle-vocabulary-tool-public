from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path

from dictionary_lookup import DictionaryLookup
from ecdict_lookup import DEFAULT_ECDICT_PATH
from frequency_lookup import FrequencyLookup
from subtitle_text_reader import SubtitleDocument, read_subtitle_file, read_task_subtitle
from tokenizer import TOKENIZER_VERSION, tokenize
from vocabulary_db import VocabularyDatabase
from vocabulary_filter import filter_vocabulary_counts
from vocabulary_quality import VocabularyQualityPolicy


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[1] / "用户数据" / "vocabulary.sqlite3"


@dataclass(frozen=True)
class VocabularyImportResult:
    import_id: int
    duplicate: bool
    content_hash: str
    token_count: int
    unique_word_count: int


@dataclass(frozen=True)
class VocabularyRemoveResult:
    import_id: int
    removed_word_count: int
    removed_token_count: int


class SubtitleNoEnglishError(ValueError):
    pass


def _import_document(
    document: SubtitleDocument,
    source_reference: Path,
    database_path: str | Path,
    *,
    dictionary_provider=None,
    frequency_provider=None,
    require_english: bool = False,
    collection_id: int | None = None,
) -> VocabularyImportResult:
    canonical_bytes = document.canonical_text.encode("utf-8")
    content_hash = hashlib.sha256(canonical_bytes).hexdigest()
    tokens = tokenize(document.canonical_text)
    token_counts = Counter(tokens)
    if require_english and not token_counts:
        raise SubtitleNoEnglishError("字幕正文中没有检测到可统计的英文单词。")
    counts = filter_vocabulary_counts(token_counts)
    token_count = sum(counts.values())
    database = VocabularyDatabase(database_path)
    duplicate_id = database.find_import_id(content_hash, collection_id)
    if duplicate_id is not None:
        if dictionary_provider is not None or DEFAULT_ECDICT_PATH.is_file():
            if dictionary_provider is None:
                dictionary_provider = DictionaryLookup()
            import_words = database.get_import_words(
                duplicate_id, collection_id=collection_id
            )
            existing = database.get_enrichments(import_words)
            missing_meanings = [
                word
                for word in import_words
                if word not in existing or existing[word]["meaning"] is None
            ]
            if missing_meanings:
                meanings = dictionary_provider.lookup_many(missing_meanings)
                database.update_meanings(meanings)
        return VocabularyImportResult(
            import_id=duplicate_id,
            duplicate=True,
            content_hash=content_hash,
            token_count=token_count,
            unique_word_count=len(counts),
        )

    if dictionary_provider is None:
        dictionary_provider = DictionaryLookup()
    if frequency_provider is None:
        frequency_provider = FrequencyLookup()

    existing = database.get_enrichments(tuple(counts))
    meaning_words = sorted(
        word
        for word in counts
        if word not in existing or existing[word]["meaning"] is None
    )
    frequency_words = sorted(
        word
        for word in counts
        if word not in existing
        or existing[word]["frequency_source"] != frequency_provider.source
        or existing[word]["frequency_version"] != frequency_provider.version
    )
    meanings = (
        dictionary_provider.lookup_many(meaning_words) if meaning_words else {}
    )
    frequencies = (
        frequency_provider.lookup_many(frequency_words) if frequency_words else {}
    )
    effective_rows = []
    for word in counts:
        cached = existing.get(word, {})
        meaning = cached.get("meaning")
        if word in meanings and meanings[word] is not None:
            meaning = meanings[word]
        frequency = cached.get("english_frequency")
        if word in frequencies:
            frequency = frequencies[word]
        effective_rows.append((word, meaning, frequency))
    quality_policy = VocabularyQualityPolicy(
        dictionary_provider, frequency_provider
    )
    quality_policy.prepare(effective_rows)
    counts = {
        word: counts[word]
        for word, meaning, frequency in effective_rows
        if quality_policy.should_keep(word, meaning, frequency)
    }
    meanings = {word: meanings[word] for word in counts if word in meanings}
    frequencies = {
        word: frequencies[word] for word in counts if word in frequencies
    }
    token_count = sum(counts.values())
    stored = database.import_counts(
        content_hash=content_hash,
        source_task_folder=str(source_reference.resolve()),
        source_type=document.source_type,
        source_file_names=document.source_file_names,
        tokenizer_version=TOKENIZER_VERSION,
        counts=counts,
        meanings=meanings,
        frequencies=frequencies,
        frequency_source=frequency_provider.source,
        frequency_version=frequency_provider.version,
        collection_id=collection_id,
    )
    return VocabularyImportResult(
        import_id=stored.import_id,
        duplicate=stored.duplicate,
        content_hash=content_hash,
        token_count=token_count,
        unique_word_count=len(counts),
    )


def import_vocabulary(
    task_folder: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    dictionary_provider=None,
    frequency_provider=None,
    collection_id: int | None = None,
) -> VocabularyImportResult:
    folder = Path(task_folder)
    document = read_task_subtitle(folder)
    return _import_document(
        document,
        folder,
        database_path,
        dictionary_provider=dictionary_provider,
        frequency_provider=frequency_provider,
        collection_id=collection_id,
    )


def import_vocabulary_file(
    subtitle_file: str | Path,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    dictionary_provider=None,
    frequency_provider=None,
    collection_id: int | None = None,
) -> VocabularyImportResult:
    source = Path(subtitle_file)
    document = read_subtitle_file(source)
    return _import_document(
        document,
        source,
        database_path,
        dictionary_provider=dictionary_provider,
        frequency_provider=frequency_provider,
        require_english=True,
        collection_id=collection_id,
    )


def remove_vocabulary_import(
    import_id: int,
    database_path: str | Path = DEFAULT_DATABASE_PATH,
    *,
    collection_id: int | None = None,
) -> VocabularyRemoveResult:
    removed = VocabularyDatabase(database_path).remove_import(
        import_id, collection_id=collection_id
    )
    return VocabularyRemoveResult(
        import_id=removed.import_id,
        removed_word_count=removed.removed_word_count,
        removed_token_count=removed.removed_token_count,
    )
