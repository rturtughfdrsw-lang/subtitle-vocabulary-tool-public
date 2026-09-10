from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from vocabulary_db import VocabularyDatabase
from vocabulary_service import import_vocabulary
from vocabulary_test_support import import_without_enrichment


class MappingDictionary:
    def __init__(self, values):
        self.values = values

    def lookup_many(self, words):
        return {word: self.values.get(word) for word in words}


class MappingFrequency:
    source = "wordfreq"
    version = "3.1.1"

    def __init__(self, values):
        self.values = values

    def lookup_many(self, words):
        return {word: self.values.get(word) for word in words}


def make_ocr_task(folder: Path, text: str) -> None:
    folder.mkdir()
    (folder / "status.json").write_text(
        json.dumps({"ok": True, "type": "硬字幕 OCR"}), encoding="utf-8"
    )
    (folder / "硬字幕OCR文字.txt").write_text(text, encoding="utf-8")


def test_import_service_accumulates_counts_across_successful_subtitles():
    with tempfile.TemporaryDirectory(prefix="vocab-import-") as temp:
        root = Path(temp)
        db_path = root / "用户数据" / "vocabulary.sqlite3"
        task_a = root / "task-a"
        task_b = root / "task-b"
        make_ocr_task(task_a, "actually actually actually actually actually hello hello")
        make_ocr_task(
            task_b,
            "actually actually actually actually actually actually actually actually "
            "world world world",
        )

        result_a = import_without_enrichment(import_vocabulary, task_a, db_path)
        result_b = import_without_enrichment(import_vocabulary, task_b, db_path)
        assert result_a.token_count == 7 and result_a.unique_word_count == 2
        assert result_b.token_count == 11 and result_b.unique_word_count == 2

        rows = VocabularyDatabase(db_path).list_words("alphabetical")
        assert {row["word"]: row["total_count"] for row in rows} == {
            "actually": 13,
            "hello": 2,
            "world": 3,
        }


def test_en_v2_import_stores_only_valid_non_basic_vocabulary():
    with tempfile.TemporaryDirectory(prefix="vocab-import-v2-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        task = root / "task"
        make_ocr_task(
            task,
            "The is actually probably instead don't GPT5 part-time well-known.",
        )

        result = import_without_enrichment(import_vocabulary, task, db_path)
        rows = VocabularyDatabase(db_path).list_words("alphabetical")

        assert result.token_count == 5
        assert result.unique_word_count == 5
        assert [row["word"] for row in rows] == [
            "actually",
            "instead",
            "part-time",
            "probably",
            "well-known",
        ]
        with closing(sqlite3.connect(db_path)) as connection:
            version = connection.execute(
                "SELECT tokenizer_version FROM imports"
            ).fetchone()[0]
        assert version == "en-v2"


def test_quality_v1_new_import_persists_only_enriched_keep_words():
    with tempfile.TemporaryDirectory(prefix="vocab-import-quality-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        task = root / "task"
        make_ocr_task(
            task,
            "aromantic aromantic ala lookat rarejunk unknown part-time",
        )
        dictionary = MappingDictionary(
            {"aromantic": "无浪漫倾向的", "look": "看", "at": "在"}
        )
        frequency = MappingFrequency(
            {
                "aromantic": 1.67,
                "ala": 3.0,
                "lookat": 4.0,
                "rarejunk": 1.5,
                "unknown": None,
                "part-time": 2.1,
                "look": 5.0,
                "at": 6.0,
            }
        )

        result = import_vocabulary(
            task,
            db_path,
            dictionary_provider=dictionary,
            frequency_provider=frequency,
        )

        assert result.token_count == 2
        assert result.unique_word_count == 1
        rows = VocabularyDatabase(db_path).list_words("alphabetical")
        assert [row["word"] for row in rows] == ["aromantic"]
        assert rows[0]["meaning"] == "无浪漫倾向的"


if __name__ == "__main__":
    test_import_service_accumulates_counts_across_successful_subtitles()
    test_en_v2_import_stores_only_valid_non_basic_vocabulary()
    test_quality_v1_new_import_persists_only_enriched_keep_words()
    print("PASS: vocabulary import accumulation tests")
