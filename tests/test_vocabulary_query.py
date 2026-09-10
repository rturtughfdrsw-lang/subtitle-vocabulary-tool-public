from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from vocabulary_db import VocabularyDatabase
from vocabulary_quality import VocabularyQualityPolicy


class MappingProvider:
    def __init__(self, values):
        self.values = values

    def lookup_many(self, words):
        return {word: self.values.get(word) for word in words}


def populated_database(path: Path) -> VocabularyDatabase:
    database = VocabularyDatabase(path)
    counts = {
        "alpha": 5,
        "beta": 2,
        "delta": 5,
        "literal%word": 1,
        "literal_word": 1,
        "o'clock": 1,
        "path\\word": 1,
        "part-time": 1,
        "the": 20,
        "is": 12,
        "don't": 9,
        "gpt5": 7,
        "unknown": 8,
    }
    database.import_counts(
        content_hash="a" * 64,
        source_task_folder="task-a",
        source_type="硬字幕 OCR",
        source_file_names=("硬字幕OCR文字.txt",),
        tokenizer_version="en-v1",
        counts=counts,
        meanings={"alpha": "阿尔法", "unknown": None},
        frequencies={
            "alpha": 5.0,
            "beta": 3.0,
            "delta": 5.0,
            "literal%word": None,
            "literal_word": None,
            "o'clock": None,
            "path\\word": None,
            "part-time": None,
            "the": None,
            "is": None,
            "don't": None,
            "gpt5": None,
            "unknown": None,
        },
        frequency_source="wordfreq",
        frequency_version="3.1.1",
    )
    return database


def words(result):
    return [row["word"] for row in result.rows]


def test_query_empty_database_and_pagination_metadata():
    with tempfile.TemporaryDirectory(prefix="vocab-query-empty-") as temp:
        result = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3").query_words(
            limit=25, offset=10
        )
        assert result.total == 0
        assert result.limit == 25
        assert result.offset == 10
        assert result.rows == []


def test_query_supports_all_stable_sort_orders_and_null_frequency_last():
    with tempfile.TemporaryDirectory(prefix="vocab-query-sort-") as temp:
        database = populated_database(Path(temp) / "vocabulary.sqlite3")
        expected = {
            "word_asc": [
                "alpha", "beta", "delta", "part-time", "unknown",
            ],
            "word_desc": [
                "unknown", "part-time", "delta", "beta", "alpha",
            ],
            "count_desc": [
                "unknown", "alpha", "delta", "beta", "part-time",
            ],
            "count_asc": [
                "part-time", "beta", "alpha", "delta", "unknown",
            ],
            "frequency_desc": [
                "alpha", "delta", "beta", "part-time", "unknown",
            ],
            "frequency_asc": [
                "beta", "alpha", "delta", "part-time", "unknown",
            ],
        }
        for sort, expected_words in expected.items():
            result = database.query_words(sort=sort, limit=100)
            assert words(result) == expected_words, sort
            assert result.total == 5
            assert list(result.rows[0]) == [
                "word", "meaning", "total_count", "english_frequency"
            ]


def test_query_search_is_literal_parameterized_substring_and_paginates():
    with tempfile.TemporaryDirectory(prefix="vocab-query-search-") as temp:
        database = populated_database(Path(temp) / "vocabulary.sqlite3")
        assert words(database.query_words(search="PH", limit=100)) == ["alpha"]
        assert words(database.query_words(search="part", limit=100)) == ["part-time"]
        assert words(database.query_words(search="%", limit=100)) == []
        assert words(database.query_words(search="_", limit=100)) == []
        assert words(database.query_words(search="'", limit=100)) == []
        assert words(database.query_words(search="\\", limit=100)) == []

        page = database.query_words(sort="word_asc", limit=3, offset=2)
        assert page.total == 5
        assert words(page) == ["delta", "part-time", "unknown"]


def test_query_hides_legacy_rows_without_deleting_historical_counts():
    with tempfile.TemporaryDirectory(prefix="vocab-query-legacy-") as temp:
        database = populated_database(Path(temp) / "vocabulary.sqlite3")

        stored_words = {row["word"] for row in database.list_words()}
        assert {"the", "is", "don't", "gpt5"} <= stored_words
        assert len(stored_words) == 13
        assert database.count_inconsistencies() == []

        visible = database.query_words(sort="word_asc", limit=2, offset=0)
        second_page = database.query_words(sort="word_asc", limit=2, offset=2)
        last_page = database.query_words(sort="word_asc", limit=2, offset=4)
        assert visible.total == second_page.total == last_page.total == 5
        assert words(visible) == ["alpha", "beta"]
        assert words(second_page) == ["delta", "part-time"]
        assert words(last_page) == ["unknown"]
        assert database.query_words(search="the", limit=100).total == 0


def test_query_rejects_invalid_sort_and_pagination_values():
    with tempfile.TemporaryDirectory(prefix="vocab-query-invalid-") as temp:
        database = VocabularyDatabase(Path(temp) / "vocabulary.sqlite3")
        invalid_calls = (
            {"sort": "drop_table"},
            {"limit": 0},
            {"limit": 1001},
            {"offset": -1},
            {"limit": True},
        )
        for arguments in invalid_calls:
            try:
                database.query_words(**arguments)
                raise AssertionError(f"query must reject {arguments}")
            except ValueError:
                pass


def test_quality_policy_hides_legacy_rows_without_deleting_or_miscounting_pages():
    with tempfile.TemporaryDirectory(prefix="vocab-query-quality-") as temp:
        path = Path(temp) / "vocabulary.sqlite3"
        seed = VocabularyDatabase(path)
        imported = seed.import_counts(
            content_hash="9" * 64,
            source_task_folder="legacy",
            source_type="硬字幕 OCR",
            source_file_names=("硬字幕OCR文字.txt",),
            tokenizer_version="en-v2",
            counts={
                "aromantic": 5,
                "ala": 4,
                "lookat": 3,
                "rarejunk": 2,
                "part-time": 1,
            },
            meanings={
                "aromantic": "无浪漫倾向的",
                "part-time": "兼职的",
            },
            frequencies={
                "aromantic": 1.67,
                "ala": 3.0,
                "lookat": 4.0,
                "rarejunk": None,
                "part-time": 2.1,
            },
            frequency_source="wordfreq",
            frequency_version="3.1.1",
        )
        policy = VocabularyQualityPolicy(
            MappingProvider(
                {"aromantic": "无浪漫倾向的", "look": "看", "at": "在"}
            ),
            MappingProvider({"look": 5.0, "at": 6.0}),
        )
        database = VocabularyDatabase(path, quality_policy=policy)

        first = database.query_words(sort="word_asc", limit=2, offset=0)
        second = database.query_words(sort="word_asc", limit=2, offset=2)
        imported_page = database.query_import(
            imported.import_id, sort="frequency_desc", limit=10
        )

        assert first.total == second.total == imported_page.total == 2
        assert words(first) == ["aromantic", "part-time"]
        assert words(second) == []
        assert words(imported_page) == ["part-time", "aromantic"]
        assert database.query_words(search="ala", limit=10).total == 0
        assert database.query_words(search="lookat", limit=10).total == 0
        assert database.query_import(
            imported.import_id, search="rarejunk", limit=10
        ).total == 0
        assert {row["word"] for row in database.list_words()} == {
            "ala", "aromantic", "lookat", "part-time", "rarejunk"
        }
        assert database.count_inconsistencies() == []


if __name__ == "__main__":
    test_query_empty_database_and_pagination_metadata()
    test_query_supports_all_stable_sort_orders_and_null_frequency_last()
    test_query_search_is_literal_parameterized_substring_and_paginates()
    test_query_hides_legacy_rows_without_deleting_historical_counts()
    test_query_rejects_invalid_sort_and_pagination_values()
    test_quality_policy_hides_legacy_rows_without_deleting_or_miscounting_pages()
    print("PASS: vocabulary query tests")
