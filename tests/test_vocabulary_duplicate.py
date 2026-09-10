import json
from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from vocabulary_db import VocabularyDatabase
from vocabulary_service import import_vocabulary, import_vocabulary_file
from vocabulary_test_support import import_without_enrichment


def write_task(folder: Path, filename: str, content: str, result_type: str) -> None:
    folder.mkdir()
    (folder / "status.json").write_text(
        json.dumps({"ok": True, "type": result_type}), encoding="utf-8"
    )
    (folder / filename).write_text(content, encoding="utf-8")


def test_duplicate_detection_uses_canonical_text_not_path_name_or_tokens():
    with tempfile.TemporaryDirectory(prefix="vocab-duplicate-") as temp:
        root = Path(temp)
        db_path = root / "用户数据" / "vocabulary.sqlite3"
        first = root / "first"
        renamed = root / "renamed"
        punctuation_changed = root / "punctuation"
        write_task(first, "硬字幕OCR文字.txt", "Hello,   world!\n", "硬字幕 OCR")
        write_task(
            renamed,
            "Different Name.srt",
            "1\n00:00:00,000 --> 00:00:01,000\nHello, world!\n",
            "网页原字幕",
        )
        write_task(
            punctuation_changed,
            "语音识别文字.txt",
            "Hello world\n",
            "语音识别字幕",
        )

        original = import_without_enrichment(import_vocabulary, first, db_path)
        duplicate = import_without_enrichment(import_vocabulary, renamed, db_path)
        changed = import_without_enrichment(
            import_vocabulary, punctuation_changed, db_path
        )
        same_file_again = import_without_enrichment(import_vocabulary, first, db_path)

        assert not original.duplicate
        assert duplicate.duplicate
        assert same_file_again.duplicate
        assert not changed.duplicate
        assert original.content_hash == duplicate.content_hash
        assert original.content_hash != changed.content_hash

        rows = VocabularyDatabase(db_path).list_words("alphabetical")
        assert {row["word"]: row["total_count"] for row in rows} == {
            "hello": 2,
            "world": 2,
        }
        assert VocabularyDatabase(db_path).count_inconsistencies() == []


def test_task_and_direct_file_import_share_canonical_hash_deduplication():
    with tempfile.TemporaryDirectory(prefix="vocab-cross-entry-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        task = root / "task"
        direct_file = root / "existing.srt"
        write_task(task, "硬字幕OCR文字.txt", "Hello,   world!\n", "硬字幕 OCR")
        direct_file.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nHello, world!\n",
            encoding="utf-8",
        )

        first = import_without_enrichment(import_vocabulary, task, db_path)
        duplicate = import_without_enrichment(
            import_vocabulary_file, direct_file, db_path
        )

        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert duplicate.content_hash == first.content_hash
        rows = VocabularyDatabase(db_path).list_words("alphabetical")
        assert {row["word"]: row["total_count"] for row in rows} == {
            "hello": 1,
            "world": 1,
        }


def test_same_canonical_subtitle_is_independently_importable_per_collection():
    with tempfile.TemporaryDirectory(prefix="vocab-cross-collection-") as temp:
        root = Path(temp)
        db_path = root / "vocabulary.sqlite3"
        task = root / "task"
        write_task(task, "硬字幕OCR文字.txt", "Hello world", "硬字幕 OCR")
        database = VocabularyDatabase(db_path)
        default = database.resolve_collection()
        other = database.create_collection("Sitcom")

        first = import_without_enrichment(
            import_vocabulary, task, db_path, collection_id=default.collection_id
        )
        duplicate = import_without_enrichment(
            import_vocabulary, task, db_path, collection_id=default.collection_id
        )
        cross_collection = import_without_enrichment(
            import_vocabulary, task, db_path, collection_id=other.collection_id
        )

        assert first.duplicate is False
        assert duplicate.duplicate is True
        assert cross_collection.duplicate is False
        assert database.query_words(
            collection_id=default.collection_id
        ).rows[0]["total_count"] == 1
        assert database.query_words(
            collection_id=other.collection_id
        ).rows[0]["total_count"] == 1


if __name__ == "__main__":
    test_duplicate_detection_uses_canonical_text_not_path_name_or_tokens()
    test_task_and_direct_file_import_share_canonical_hash_deduplication()
    test_same_canonical_subtitle_is_independently_importable_per_collection()
    print("PASS: vocabulary duplicate detection tests")
