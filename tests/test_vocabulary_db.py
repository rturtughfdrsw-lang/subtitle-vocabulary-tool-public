from collections import Counter
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from vocabulary_db import ImportNotFoundError, VocabularyDatabase


def import_counts(db, content_hash, counts):
    return db.import_counts(
        content_hash=content_hash,
        source_task_folder=f"task-{content_hash}",
        source_type="硬字幕 OCR",
        source_file_names=("硬字幕OCR文字.txt",),
        tokenizer_version="en-v1",
        counts=Counter(counts),
    )


def test_database_initializes_schema_accumulates_and_persists():
    with tempfile.TemporaryDirectory(prefix="vocab-db-") as temp:
        path = Path(temp) / "nested" / "vocabulary.sqlite3"
        db = VocabularyDatabase(path)
        first = import_counts(db, "a" * 64, {"actually": 5, "hello": 2})
        second = import_counts(db, "b" * 64, {"actually": 8, "world": 3})
        assert not first.duplicate and not second.duplicate
        assert path.is_file()

        reopened = VocabularyDatabase(path)
        rows = {row["word"]: row for row in reopened.list_words("alphabetical")}
        assert {word: row["total_count"] for word, row in rows.items()} == {
            "actually": 13,
            "hello": 2,
            "world": 3,
        }
        assert rows["actually"]["meaning"] is None
        assert rows["actually"]["english_frequency"] is None


def test_database_schema_requires_non_null_word_keys():
    with tempfile.TemporaryDirectory(prefix="vocab-db-schema-") as temp:
        path = Path(temp) / "vocabulary.sqlite3"
        VocabularyDatabase(path)
        with closing(sqlite3.connect(path)) as connection:
            columns = {
                row[1]: row for row in connection.execute("PRAGMA table_info(words)")
            }
        assert columns["word"][3] == 1


def test_database_duplicate_hash_and_count_consistency():
    with tempfile.TemporaryDirectory(prefix="vocab-db-duplicate-") as temp:
        db = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3")
        import_counts(db, "c" * 64, {"actually": 5, "hello": 2})
        duplicate = import_counts(db, "c" * 64, {"actually": 99})
        assert duplicate.duplicate
        assert db.list_words("count")[0]["total_count"] == 5
        assert db.count_inconsistencies() == []


def test_database_rolls_back_every_table_when_a_batch_write_fails():
    with tempfile.TemporaryDirectory(prefix="vocab-db-rollback-") as temp:
        path = Path(temp) / "vocabulary.sqlite3"
        db = VocabularyDatabase(path)
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute(
                """
                CREATE TRIGGER reject_explode BEFORE INSERT ON words
                WHEN NEW.word = 'explode'
                BEGIN SELECT RAISE(ABORT, 'forced failure'); END
                """
            )
        try:
            import_counts(db, "d" * 64, {"good": 2, "explode": 1})
            raise AssertionError("forced database failure must escape")
        except sqlite3.DatabaseError as exc:
            assert "forced failure" in str(exc)

        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM import_word_counts"
            ).fetchone()[0] == 0


def test_query_import_joins_original_counts_with_current_totals_and_paginates():
    with tempfile.TemporaryDirectory(prefix="vocab-db-query-import-") as temp:
        db = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3")
        first = db.import_counts(
            content_hash="e" * 64,
            source_task_folder="first",
            source_type="硬字幕 OCR",
            source_file_names=("硬字幕OCR文字.txt",),
            tokenizer_version="en-v1",
            counts={"actually": 5, "aromantic": 2, "the": 20},
            meanings={"actually": "其实", "aromantic": "无浪漫倾向的"},
            frequencies={"actually": 5.77, "aromantic": 1.67},
            frequency_source="wordfreq",
            frequency_version="3.1.1",
        )
        import_counts(db, "f" * 64, {"actually": 8, "later": 1})

        first_page = db.query_import(first.import_id, limit=1, offset=0)
        second_page = db.query_import(first.import_id, limit=1, offset=1)

        assert first_page.total == second_page.total == 2
        assert first_page.rows == [{
            "word": "actually",
            "meaning": "其实",
            "import_count": 5,
            "total_count": 13,
            "english_frequency": 5.77,
        }]
        assert second_page.rows == [{
            "word": "aromantic",
            "meaning": "无浪漫倾向的",
            "import_count": 2,
            "total_count": 2,
            "english_frequency": 1.67,
        }]

        try:
            db.query_import(999, limit=200, offset=0)
            raise AssertionError("missing import must fail")
        except ImportNotFoundError:
            pass


def test_query_import_supports_literal_search_and_all_stable_sorts():
    with tempfile.TemporaryDirectory(prefix="vocab-db-query-import-sort-") as temp:
        db = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3")
        imported = db.import_counts(
            content_hash="1" * 64,
            source_task_folder="first",
            source_type="硬字幕 OCR",
            source_file_names=("硬字幕OCR文字.txt",),
            tokenizer_version="en-v2",
            counts={"alpha": 4, "beta": 3, "gamma": 2, "delta": 1},
            meanings={"alpha": "阿尔法", "beta": "贝塔"},
            frequencies={"alpha": 5.0, "beta": None, "gamma": 3.0, "delta": 3.0},
            frequency_source="wordfreq",
            frequency_version="3.1.1",
        )
        import_counts(db, "2" * 64, {"beta": 10, "delta": 6})

        expected = {
            "word_asc": ["alpha", "beta", "delta", "gamma"],
            "word_desc": ["gamma", "delta", "beta", "alpha"],
            "import_count_desc": ["alpha", "beta", "gamma", "delta"],
            "import_count_asc": ["delta", "gamma", "beta", "alpha"],
            "total_count_desc": ["beta", "delta", "alpha", "gamma"],
            "total_count_asc": ["gamma", "alpha", "delta", "beta"],
            "frequency_desc": ["alpha", "delta", "gamma", "beta"],
            "frequency_asc": ["delta", "gamma", "alpha", "beta"],
        }
        for sort, words in expected.items():
            result = db.query_import(imported.import_id, sort=sort, limit=100)
            assert [row["word"] for row in result.rows] == words, sort
            assert result.total == 4

        searched = db.query_import(
            imported.import_id,
            search="TA",
            sort="total_count_desc",
            limit=1,
            offset=0,
        )
        searched_second = db.query_import(
            imported.import_id,
            search="TA",
            sort="total_count_desc",
            limit=1,
            offset=1,
        )
        assert searched.total == searched_second.total == 2
        assert [row["word"] for row in searched.rows] == ["beta"]
        assert [row["word"] for row in searched_second.rows] == ["delta"]
        for literal in ("%", "_", "\\"):
            assert db.query_import(
                imported.import_id, search=literal, limit=100
            ).total == 0


def test_remove_import_reverses_only_its_contribution_and_frees_hash():
    with tempfile.TemporaryDirectory(prefix="vocab-db-remove-") as temp:
        path = Path(temp) / "vocabulary.sqlite3"
        db = VocabularyDatabase(path)
        first = import_counts(db, "3" * 64, {"shared": 5, "firstonly": 2})
        second = import_counts(db, "4" * 64, {"shared": 8, "secondonly": 3})
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO words (word, updated_at) VALUES (?, ?)",
                    ("unrelatedzero", "2026-08-30T00:00:00+00:00"),
                )

        removed = db.remove_import(first.import_id)

        assert removed.import_id == first.import_id
        assert removed.removed_word_count == 2
        assert removed.removed_token_count == 7
        assert {row["word"]: row["total_count"] for row in db.list_words()} == {
            "secondonly": 3,
            "shared": 8,
        }
        assert db.count_inconsistencies() == []
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM import_word_counts WHERE import_id = ?",
                (first.import_id,),
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM imports WHERE import_id = ?", (first.import_id,)
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM imports WHERE import_id = ?", (second.import_id,)
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT COUNT(*) FROM words WHERE word = 'unrelatedzero'"
            ).fetchone()[0] == 1

        reimported = import_counts(db, "3" * 64, {"shared": 5, "firstonly": 2})
        assert not reimported.duplicate
        assert reimported.import_id != first.import_id


def test_remove_import_rejects_missing_id_and_removed_query_is_not_found():
    with tempfile.TemporaryDirectory(prefix="vocab-db-remove-missing-") as temp:
        db = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3")
        imported = import_counts(db, "5" * 64, {"actually": 2})
        db.remove_import(imported.import_id)

        for operation in (
            lambda: db.remove_import(imported.import_id),
            lambda: db.query_import(imported.import_id),
        ):
            try:
                operation()
                raise AssertionError("removed import must be reported as missing")
            except ImportNotFoundError:
                pass


def test_remove_import_rolls_back_all_changes_when_delete_fails():
    with tempfile.TemporaryDirectory(prefix="vocab-db-remove-rollback-") as temp:
        path = Path(temp) / "vocabulary.sqlite3"
        db = VocabularyDatabase(path)
        imported = import_counts(db, "6" * 64, {"actually": 5, "hello": 2})
        before = db.list_words()
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TRIGGER reject_import_delete BEFORE DELETE ON imports
                    BEGIN SELECT RAISE(ABORT, 'forced remove failure'); END
                    """
                )

        try:
            db.remove_import(imported.import_id)
            raise AssertionError("forced removal failure must escape")
        except sqlite3.DatabaseError as exc:
            assert "forced remove failure" in str(exc)

        assert db.list_words() == before
        assert db.count_inconsistencies() == []
        result = db.query_import(imported.import_id)
        assert result.total == 2
        assert sum(row["import_count"] for row in result.rows) == 7


if __name__ == "__main__":
    test_database_initializes_schema_accumulates_and_persists()
    test_database_schema_requires_non_null_word_keys()
    test_database_duplicate_hash_and_count_consistency()
    test_database_rolls_back_every_table_when_a_batch_write_fails()
    test_query_import_joins_original_counts_with_current_totals_and_paginates()
    test_query_import_supports_literal_search_and_all_stable_sorts()
    test_remove_import_reverses_only_its_contribution_and_frees_hash()
    test_remove_import_rejects_missing_id_and_removed_query_is_not_found()
    test_remove_import_rolls_back_all_changes_when_delete_fails()
    print("PASS: vocabulary SQLite tests")
