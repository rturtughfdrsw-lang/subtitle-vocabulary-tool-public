from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

from dictionary_lookup import DictionaryLookup, DictionaryResourceError
from ecdict_lookup import ECDICTLookup


def create_dictionary(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.executescript(
                """
                CREATE TABLE entries (
                    word TEXT NOT NULL PRIMARY KEY,
                    meaning TEXT,
                    form_of TEXT
                ) WITHOUT ROWID;
                CREATE TABLE metadata (
                    key TEXT NOT NULL PRIMARY KEY,
                    value TEXT NOT NULL
                ) WITHOUT ROWID;
                """
            )
            connection.executemany(
                "INSERT INTO entries(word, meaning, form_of) VALUES (?, ?, ?)",
                [
                    ("actually", "其实；真的", None),
                    ("play", "玩；演奏", None),
                    ("played", None, "play"),
                    ("microsoft", "微软", None),
                    ("don't", "不要", None),
                    ("well-known", "著名的", None),
                    ("naïve", "天真的", None),
                    ("orphan", None, "missing"),
                    ("cycle-a", None, "cycle-b"),
                    ("cycle-b", None, "cycle-a"),
                ],
            )
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                [("schema_version", "1"), ("source_version", "test")],
            )


def create_ecdict(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE stardict (
                    id INTEGER PRIMARY KEY,
                    word TEXT NOT NULL,
                    translation TEXT,
                    sw TEXT
                )
                """
            )
            connection.executemany(
                "INSERT INTO stardict(word, translation, sw) VALUES (?, ?, ?)",
                [
                    ("actually", "ECDICT其实", "actually"),
                    ("play", "ECDICT玩", "play"),
                    ("played", "ECDICT播放", "played"),
                    ("aromantic", "无浪漫倾向的\n无浪漫情感的", "aromantic"),
                    ("blank", "  \n", "blank"),
                ],
            )


def test_dictionary_lookup_handles_direct_form_and_token_types():
    with tempfile.TemporaryDirectory(prefix="dictionary-lookup-") as temp:
        database = Path(temp) / "dictionary.sqlite3"
        create_dictionary(database)
        lookup = DictionaryLookup(database)

        assert lookup.lookup("Actually") == "其实；真的"
        assert lookup.lookup("played") == "玩；演奏"
        assert lookup.lookup("MICROSOFT") == "微软"
        assert lookup.lookup("don't") == "不要"
        assert lookup.lookup("well-known") == "著名的"
        assert lookup.lookup("NAÏVE") == "天真的"
        assert lookup.lookup("gpt5") is None
        assert lookup.lookup("orphan") is None
        assert lookup.lookup("cycle-a") is None


def test_dictionary_lookup_many_preserves_requested_keys_and_caches_results():
    with tempfile.TemporaryDirectory(prefix="dictionary-batch-") as temp:
        database = Path(temp) / "dictionary.sqlite3"
        create_dictionary(database)
        lookup = DictionaryLookup(database)

        result = lookup.lookup_many(["Actually", "played", "ACTUALLY", "gpt5"])
        assert result == {
            "Actually": "其实；真的",
            "played": "玩；演奏",
            "ACTUALLY": "其实；真的",
            "gpt5": None,
        }

        database.unlink()
        assert lookup.lookup("actually") == "其实；真的"
        assert lookup.lookup("GPT5") is None


def test_dictionary_lookup_reports_missing_and_corrupt_resources():
    with tempfile.TemporaryDirectory(prefix="dictionary-errors-") as temp:
        root = Path(temp)
        missing = root / "missing.sqlite3"
        try:
            DictionaryLookup(missing)
            raise AssertionError("missing resource must fail")
        except DictionaryResourceError as exc:
            assert "不存在" in str(exc)

        corrupt = root / "corrupt.sqlite3"
        corrupt.write_text("not sqlite", encoding="utf-8")
        try:
            DictionaryLookup(corrupt)
            raise AssertionError("corrupt resource must fail")
        except DictionaryResourceError as exc:
            assert "损坏" in str(exc) or "无效" in str(exc)

        wrong_schema = root / "wrong.sqlite3"
        with closing(sqlite3.connect(wrong_schema)) as connection:
            with connection:
                connection.execute("CREATE TABLE unexpected(value TEXT)")
        try:
            DictionaryLookup(wrong_schema)
            raise AssertionError("wrong schema must fail")
        except DictionaryResourceError as exc:
            assert "schema" in str(exc).casefold()


def test_optional_ecdict_fallback_keeps_kaikki_direct_and_form_of_priority():
    with tempfile.TemporaryDirectory(prefix="dictionary-ecdict-") as temp:
        root = Path(temp)
        kaikki = root / "dictionary.sqlite3"
        ecdict = root / "ecdict.sqlite3"
        create_dictionary(kaikki)
        create_ecdict(ecdict)

        lookup = DictionaryLookup(kaikki, fallback_path=ecdict)

        assert lookup.lookup("actually") == "其实；真的"
        assert lookup.lookup("played") == "玩；演奏"
        assert lookup.lookup("aromantic") == "无浪漫倾向的；无浪漫情感的"
        assert lookup.lookup("blank") is None


def test_optional_ecdict_missing_is_disabled_but_present_corruption_is_reported():
    with tempfile.TemporaryDirectory(prefix="dictionary-ecdict-errors-") as temp:
        root = Path(temp)
        kaikki = root / "dictionary.sqlite3"
        create_dictionary(kaikki)

        lookup = DictionaryLookup(kaikki, fallback_path=root / "missing.sqlite3")
        assert lookup.lookup("aromantic") is None

        corrupt = root / "ecdict.sqlite3"
        corrupt.write_text("not sqlite", encoding="utf-8")
        try:
            ECDICTLookup(corrupt)
            raise AssertionError("present corrupt ECDICT must fail")
        except DictionaryResourceError as exc:
            assert "ECDICT" in str(exc)


if __name__ == "__main__":
    test_dictionary_lookup_handles_direct_form_and_token_types()
    test_dictionary_lookup_many_preserves_requested_keys_and_caches_results()
    test_dictionary_lookup_reports_missing_and_corrupt_resources()
    test_optional_ecdict_fallback_keeps_kaikki_direct_and_form_of_priority()
    test_optional_ecdict_missing_is_disabled_but_present_corruption_is_reported()
    print("PASS: dictionary lookup tests")
