from pathlib import Path
from contextlib import closing
import sqlite3
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from vocabulary_db import VocabularyDatabase


LEGACY_SCHEMA = """
CREATE TABLE words (
    word TEXT NOT NULL PRIMARY KEY COLLATE NOCASE,
    meaning TEXT,
    total_count INTEGER NOT NULL DEFAULT 0 CHECK (total_count >= 0),
    english_frequency REAL,
    frequency_source TEXT,
    frequency_version TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE imports (
    import_id INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL UNIQUE,
    source_task_folder TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_file_names TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    tokenizer_version TEXT NOT NULL
);
CREATE TABLE import_word_counts (
    import_id INTEGER NOT NULL REFERENCES imports(import_id) ON DELETE CASCADE,
    word TEXT NOT NULL REFERENCES words(word),
    count INTEGER NOT NULL CHECK (count > 0),
    PRIMARY KEY (import_id, word)
);
"""


def create_legacy_database(path: Path, *, orphan_detail: bool = False) -> None:
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.executescript(LEGACY_SCHEMA)
            connection.executemany(
                "INSERT INTO words VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ("actually", "其实", 7, 5.49, "wordfreq", "3.1.1", "2026-01-01"),
                    ("ala", None, 1, 3.53, "wordfreq", "3.1.1", "2026-01-01"),
                ),
            )
            connection.executemany(
                "INSERT INTO imports VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    (1, "a" * 64, "task-a", "硬字幕 OCR", '["a.txt"]', "2026-01-01", "en-v2"),
                    (2, "b" * 64, "task-b", "硬字幕 OCR", '["b.txt"]', "2026-01-02", "en-v2"),
                ),
            )
            connection.executemany(
                "INSERT INTO import_word_counts VALUES (?, ?, ?)",
                ((1, "actually", 5), (2, "actually", 2), (2, "ala", 1)),
            )
            if orphan_detail:
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute(
                    "INSERT INTO import_word_counts VALUES (?, ?, ?)",
                    (2, "orphan", 1),
                )


def test_legacy_database_migrates_once_with_backup_and_preserves_history():
    with tempfile.TemporaryDirectory(prefix="vocab-migration-") as temp:
        path = Path(temp) / "vocabulary.sqlite3"
        create_legacy_database(path)

        database = VocabularyDatabase(path)
        backups = list(path.parent.glob("vocabulary.sqlite3.backup-*"))
        default = database.resolve_collection()

        assert len(backups) == 1
        assert database.schema_version == 1
        assert default.is_default is True
        assert len(database.list_collections()) == 1
        assert database.find_import_id("a" * 64, default.collection_id) == 1
        assert {
            row["word"]: row["total_count"]
            for row in database.list_words(collection_id=default.collection_id)
        } == {"actually": 7, "ala": 1}
        assert database.query_import(
            1, collection_id=default.collection_id
        ).rows[0]["import_count"] == 5
        assert database.count_inconsistencies() == []
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert "total_count" not in {
                row[1] for row in connection.execute("PRAGMA table_info(words)")
            }

        reopened = VocabularyDatabase(path)
        assert reopened.resolve_collection() == default
        assert list(path.parent.glob("vocabulary.sqlite3.backup-*")) == backups


def test_mid_migration_foreign_key_failure_rolls_back_legacy_schema():
    with tempfile.TemporaryDirectory(prefix="vocab-migration-rollback-") as temp:
        path = Path(temp) / "vocabulary.sqlite3"
        create_legacy_database(path, orphan_detail=True)

        try:
            VocabularyDatabase(path)
            raise AssertionError("orphan detail must make migration fail")
        except sqlite3.IntegrityError:
            pass

        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
            assert "total_count" in {
                row[1] for row in connection.execute("PRAGMA table_info(words)")
            }
            assert connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='vocabulary_collections'"
            ).fetchone()[0] == 0


if __name__ == "__main__":
    test_legacy_database_migrates_once_with_backup_and_preserves_history()
    test_mid_migration_foreign_key_failure_rolls_back_legacy_schema()
    print("PASS: vocabulary schema migration tests")
