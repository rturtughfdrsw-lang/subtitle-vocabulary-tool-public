import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from build_dictionary_resource import build_dictionary


def write_jsonl(path: Path) -> None:
    entries = [
        {
            "word": "Actually",
            "lang_code": "en",
            "translations": [
                {"lang_code": "cmn", "word": "其實 /其实"},
                {"lang_code": "zh", "word": "真的"},
                {"lang_code": "cmn", "word": "真的"},
                {"lang_code": "cmn", "word": "zor"},
            ],
        },
        {
            "word": "play",
            "lang_code": "en",
            "translations": [
                {
                    "lang_code": "zh",
                    "lang": "Chinese",
                    "tags": ["Hokkien"],
                    "word": "𨑨迌",
                },
                {"lang_code": "cmn", "word": "玩"},
            ],
            "forms": [
                {"form": "plays", "tags": ["third-person", "singular"]},
                {"form": "played", "tags": ["past"]},
                {"form": "playing", "tags": ["participle"]},
                {"form": "playe", "tags": ["alternative"]},
                {"form": "-", "tags": ["past"]},
            ],
        },
        {
            "word": "played",
            "lang_code": "en",
            "senses": [{"form_of": [{"word": "play"}]}],
        },
        {
            "word": "ambiguous",
            "lang_code": "en",
            "senses": [
                {"form_of": [{"word": "target-a"}, {"word": "target-b"}]}
            ],
        },
        {
            "word": "Microsoft",
            "lang_code": "en",
            "translations": [{"lang_code": "cmn", "word": "微软"}],
        },
        {
            "word": "obnoxious",
            "lang_code": "en",
            "senses": [
                {
                    "translations": [
                        {"lang_code": "cmn", "word": "令人讨厌的"},
                        {"lang_code": "cmn", "word": "可憎的"},
                    ]
                }
            ],
        },
        {
            "word": "naïve",
            "lang_code": "en",
            "translations": [{"lang_code": "zh", "word": "天真的"}],
        },
        {
            "word": "longword",
            "lang_code": "en",
            "translations": [
                {"lang_code": "cmn", "word": "长" * 81},
                {"lang_code": "cmn", "word": "短义"},
            ],
        },
        {
            "word": "as usual",
            "lang_code": "en",
            "translations": [{"lang_code": "cmn", "word": "照常"}],
        },
        {
            "word": "bonjour",
            "lang_code": "fr",
            "translations": [{"lang_code": "cmn", "word": "你好"}],
        },
    ]
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )


def source_metadata() -> dict[str, str]:
    return {
        "dictionary_source": "Kaikki / English Wiktionary",
        "source_url": "https://example.invalid/kaikki.jsonl",
        "source_version": "test-snapshot",
        "source_date": "2026-08-24",
        "build_date": "2026-08-24",
        "license": "CC-BY-SA-4.0",
        "wordfreq_version": "3.1.1",
    }


def test_builder_cleans_meanings_and_uses_only_explicit_form_relations():
    with tempfile.TemporaryDirectory(prefix="dictionary-build-") as temp:
        root = Path(temp)
        source = root / "source.jsonl"
        database = root / "dictionary.sqlite3"
        manifest = root / "MANIFEST.json"
        write_jsonl(source)

        result = build_dictionary(source, database, manifest, source_metadata())

        assert result.entry_count == 9
        assert result.direct_count == 6
        assert result.form_of_count == 3
        with closing(sqlite3.connect(database)) as connection:
            rows = {
                row[0]: (row[1], row[2])
                for row in connection.execute(
                    "SELECT word, meaning, form_of FROM entries ORDER BY word"
                )
            }
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))

        assert rows["actually"] == ("其实；真的", None)
        assert rows["microsoft"] == ("微软", None)
        assert rows["obnoxious"] == ("令人讨厌的；可憎的", None)
        assert rows["naïve"] == ("天真的", None)
        assert rows["longword"] == ("短义", None)
        assert rows["play"] == ("玩", None)
        assert rows["played"] == (None, "play")
        assert rows["playing"] == (None, "play")
        assert rows["plays"] == (None, "play")
        assert "playe" not in rows
        assert "-" not in rows
        assert "as usual" not in rows
        assert "ambiguous" not in rows
        assert metadata["schema_version"] == "1"
        assert metadata["source_sha256"] == result.source_sha256

        manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        assert manifest_data["entry_count"] == 9
        assert manifest_data["direct_entry_count"] == 6
        assert manifest_data["form_of_entry_count"] == 3
        assert manifest_data["dictionary_sha256"] == result.dictionary_sha256
        assert manifest_data["dictionary_bytes"] == database.stat().st_size


def test_builder_is_binary_reproducible_for_identical_inputs_and_metadata():
    with tempfile.TemporaryDirectory(prefix="dictionary-rebuild-") as temp:
        root = Path(temp)
        source = root / "source.jsonl"
        first = root / "first.sqlite3"
        second = root / "second.sqlite3"
        write_jsonl(source)

        first_result = build_dictionary(
            source, first, root / "first.json", source_metadata()
        )
        second_result = build_dictionary(
            source, second, root / "second.json", source_metadata()
        )

        assert first.read_bytes() == second.read_bytes()
        assert first_result.dictionary_sha256 == second_result.dictionary_sha256


if __name__ == "__main__":
    test_builder_cleans_meanings_and_uses_only_explicit_form_relations()
    test_builder_is_binary_reproducible_for_identical_inputs_and_metadata()
    print("PASS: dictionary resource builder tests")
