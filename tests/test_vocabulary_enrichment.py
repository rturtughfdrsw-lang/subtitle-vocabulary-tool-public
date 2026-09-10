from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from dictionary_lookup import DictionaryResourceError
from subtitle_text_reader import read_task_subtitle
from vocabulary_db import VocabularyDatabase
from vocabulary_service import import_vocabulary


class FakeDictionary:
    def __init__(self, values=None, error=None):
        self.values = dict(values or {})
        self.error = error
        self.requests: list[tuple[str, ...]] = []

    def lookup_many(self, words):
        requested = tuple(words)
        self.requests.append(requested)
        if self.error is not None:
            raise self.error
        return {word: self.values.get(word) for word in requested}


class FakeFrequency:
    source = "wordfreq"
    version = "3.1.1"

    def __init__(self, values=None):
        self.values = dict(values or {})
        self.requests: list[tuple[str, ...]] = []

    def lookup_many(self, words):
        requested = tuple(words)
        self.requests.append(requested)
        return {word: self.values.get(word) for word in requested}


def write_task(folder: Path, text: str) -> None:
    folder.mkdir()
    (folder / "status.json").write_text(
        json.dumps({"ok": True, "type": "硬字幕 OCR"}), encoding="utf-8"
    )
    (folder / "硬字幕OCR文字.txt").write_text(text, encoding="utf-8")


def seed_old_word(database: VocabularyDatabase) -> None:
    database.import_counts(
        content_hash="a" * 64,
        source_task_folder="old-task",
        source_type="硬字幕 OCR",
        source_file_names=("硬字幕OCR文字.txt",),
        tokenizer_version="en-v1",
        counts=Counter({"actually": 5}),
    )


def test_existing_count_is_preserved_while_null_enrichment_is_filled():
    with tempfile.TemporaryDirectory(prefix="vocab-enrichment-") as temp:
        root = Path(temp)
        db_path = root / "用户数据" / "vocabulary.sqlite3"
        database = VocabularyDatabase(db_path)
        seed_old_word(database)
        task = root / "new-task"
        write_task(task, "actually actually unlisted")
        dictionary = FakeDictionary({"actually": "其实", "unlisted": None})
        frequency = FakeFrequency({"actually": 5.49, "unlisted": None})

        result = import_vocabulary(
            task,
            db_path,
            dictionary_provider=dictionary,
            frequency_provider=frequency,
        )

        assert not result.duplicate
        rows = {row["word"]: row for row in database.list_words("alphabetical")}
        assert rows["actually"]["total_count"] == 7
        assert rows["actually"]["meaning"] == "其实"
        assert rows["actually"]["english_frequency"] == 5.49
        assert rows["actually"]["frequency_source"] == "wordfreq"
        assert rows["actually"]["frequency_version"] == "3.1.1"
        assert "unlisted" not in rows
        assert database.count_inconsistencies() == []


def test_complete_or_versioned_frequency_cache_is_not_queried_again():
    with tempfile.TemporaryDirectory(prefix="vocab-cache-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        first_task = root / "first"
        second_task = root / "second"
        write_task(first_task, "actually unlisted")
        write_task(second_task, "actually unlisted newcomer")
        dictionary = FakeDictionary({"actually": "其实", "newcomer": "新来者"})
        frequency = FakeFrequency({"actually": 5.49, "unlisted": 2.5, "newcomer": 3.0})

        import_vocabulary(
            first_task,
            db_path,
            dictionary_provider=dictionary,
            frequency_provider=frequency,
        )
        dictionary.requests.clear()
        frequency.requests.clear()
        import_vocabulary(
            second_task,
            db_path,
            dictionary_provider=dictionary,
            frequency_provider=frequency,
        )

        assert dictionary.requests[0] == ("newcomer", "unlisted")
        assert frequency.requests[0] == ("newcomer", "unlisted")
        rows = {row["word"]: row for row in VocabularyDatabase(db_path).list_words()}
        assert rows["actually"]["total_count"] == 2
        assert rows["newcomer"]["total_count"] == 1
        assert "unlisted" not in rows


def test_duplicate_does_not_increment_or_query_resources_again():
    with tempfile.TemporaryDirectory(prefix="vocab-enrichment-duplicate-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        task = root / "task"
        write_task(task, "actually actually")
        dictionary = FakeDictionary({"actually": "其实"})
        frequency = FakeFrequency({"actually": 5.49})

        first = import_vocabulary(
            task,
            db_path,
            dictionary_provider=dictionary,
            frequency_provider=frequency,
        )
        dictionary.requests.clear()
        frequency.requests.clear()
        duplicate = import_vocabulary(
            task,
            db_path,
            dictionary_provider=dictionary,
            frequency_provider=frequency,
        )

        assert not first.duplicate and duplicate.duplicate
        assert dictionary.requests == []
        assert frequency.requests == []
        row = VocabularyDatabase(db_path).list_words()[0]
        assert row["total_count"] == 2


def test_resource_error_creates_no_partial_import():
    with tempfile.TemporaryDirectory(prefix="vocab-resource-error-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        task = root / "task"
        write_task(task, "actually")
        dictionary = FakeDictionary(error=DictionaryResourceError("resource broken"))
        frequency = FakeFrequency({"actually": 5.49})

        try:
            import_vocabulary(
                task,
                db_path,
                dictionary_provider=dictionary,
                frequency_provider=frequency,
            )
            raise AssertionError("resource error must escape")
        except DictionaryResourceError as exc:
            assert "broken" in str(exc)

        database = VocabularyDatabase(db_path)
        assert database.list_words() == []
        assert database.count_inconsistencies() == []


def test_duplicate_can_fill_null_meaning_without_changing_any_counts():
    with tempfile.TemporaryDirectory(prefix="vocab-duplicate-fallback-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        task = root / "task"
        write_task(task, "aromantic aromantic")
        frequency = FakeFrequency({"aromantic": 2.1})
        document = read_task_subtitle(task)
        content_hash = hashlib.sha256(
            document.canonical_text.encode("utf-8")
        ).hexdigest()
        database = VocabularyDatabase(db_path)
        legacy = database.import_counts(
            content_hash=content_hash,
            source_task_folder=str(task),
            source_type=document.source_type,
            source_file_names=document.source_file_names,
            tokenizer_version="en-v1",
            counts={"aromantic": 2},
        )
        duplicate = import_vocabulary(
            task,
            db_path,
            dictionary_provider=FakeDictionary({"aromantic": "无浪漫倾向的"}),
            frequency_provider=frequency,
        )

        assert duplicate.duplicate
        assert duplicate.import_id == legacy.import_id
        row = database.list_words()[0]
        assert row["meaning"] == "无浪漫倾向的"
        assert row["total_count"] == 2
        import_rows = database.query_import(legacy.import_id).rows
        assert import_rows[0]["import_count"] == 2
        assert database.count_inconsistencies() == []


if __name__ == "__main__":
    test_existing_count_is_preserved_while_null_enrichment_is_filled()
    test_complete_or_versioned_frequency_cache_is_not_queried_again()
    test_duplicate_does_not_increment_or_query_resources_again()
    test_resource_error_creates_no_partial_import()
    test_duplicate_can_fill_null_meaning_without_changing_any_counts()
    print("PASS: vocabulary enrichment integration tests")
