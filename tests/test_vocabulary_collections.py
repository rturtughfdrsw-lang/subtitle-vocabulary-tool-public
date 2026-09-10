from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from vocabulary_db import (
    CollectionNameConflictError,
    DefaultCollectionProtectedError,
    ImportCollectionMismatchError,
    VocabularyDatabase,
)


def import_words(database, collection_id, content_hash, counts):
    return database.import_counts(
        collection_id=collection_id,
        content_hash=content_hash,
        source_task_folder="fixture",
        source_type="硬字幕 OCR",
        source_file_names=("fixture.txt",),
        tokenizer_version="en-v2",
        counts=counts,
        meanings={word: f"释义：{word}" for word in counts},
    )


def test_database_creates_exactly_one_default_and_collection_names_are_normalized():
    with tempfile.TemporaryDirectory(prefix="vocab-collections-") as temp:
        database = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3")

        default = database.resolve_collection()
        assert default.name == "默认词汇表"
        assert default.is_default is True
        assert database.list_collections() == [default]

        created = database.create_collection("  Life English  ")
        assert created.name == "Life English"
        assert created.is_default is False
        assert database.query_words(collection_id=created.collection_id).total == 0

        try:
            database.create_collection("life english")
            raise AssertionError("collection names must be unique case-insensitively")
        except CollectionNameConflictError:
            pass

        renamed = database.rename_collection(created.collection_id, "  Sitcom  ")
        assert renamed.name == "Sitcom"
        try:
            database.delete_collection(default.collection_id)
            raise AssertionError("default collection must not be deletable")
        except DefaultCollectionProtectedError:
            pass


def test_totals_duplicates_removal_and_queries_are_collection_scoped():
    with tempfile.TemporaryDirectory(prefix="vocab-collection-scope-") as temp:
        database = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3")
        default = database.resolve_collection()
        other = database.create_collection("生活大爆炸")

        first = import_words(
            database, default.collection_id, "a" * 64, {"actually": 5, "hello": 1}
        )
        duplicate = import_words(
            database, default.collection_id, "a" * 64, {"actually": 5, "hello": 1}
        )
        cross_collection = import_words(
            database, other.collection_id, "a" * 64, {"actually": 2}
        )

        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert cross_collection.duplicate is False
        assert database.find_import_id("a" * 64, default.collection_id) == first.import_id
        assert (
            database.find_import_id("a" * 64, other.collection_id)
            == cross_collection.import_id
        )
        assert {
            row["word"]: row["total_count"]
            for row in database.query_words(
                collection_id=default.collection_id, limit=100
            ).rows
        } == {"actually": 5, "hello": 1}
        assert database.query_words(
            collection_id=other.collection_id, limit=100
        ).rows[0]["total_count"] == 2
        assert database.query_import(
            first.import_id, collection_id=default.collection_id
        ).rows[0]["total_count"] == 5

        for operation in (
            lambda: database.query_import(
                first.import_id, collection_id=other.collection_id
            ),
            lambda: database.remove_import(
                first.import_id, collection_id=other.collection_id
            ),
        ):
            try:
                operation()
                raise AssertionError("cross-collection import access must fail")
            except ImportCollectionMismatchError:
                pass

        removed = database.remove_import(
            first.import_id, collection_id=default.collection_id
        )
        assert removed.removed_token_count == 6
        assert database.query_words(collection_id=default.collection_id).total == 0
        assert database.query_words(
            collection_id=other.collection_id
        ).rows[0]["total_count"] == 2

        database.delete_collection(other.collection_id)
        assert database.list_collections() == [default]


if __name__ == "__main__":
    test_database_creates_exactly_one_default_and_collection_names_are_normalized()
    test_totals_duplicates_removal_and_queries_are_collection_scoped()
    print("PASS: vocabulary collection database tests")
