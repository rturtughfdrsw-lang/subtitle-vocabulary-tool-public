#!/usr/bin/env python3
"""Build the compact offline English-to-Chinese dictionary resource."""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
import gzip
import hashlib
from html import unescape
import json
from pathlib import Path
import re
import sqlite3
import unicodedata


SCHEMA_VERSION = "1"
MAX_MEANING_ITEMS = 5
MAX_ITEM_LENGTH = 80
MAX_MEANING_LENGTH = 240

_SPACE_RE = re.compile(r"\s+")
_SLASH_RE = re.compile(r"\s*/\s*")
_INFLECTION_TAGS = {
    "comparative",
    "gerund",
    "indicative",
    "participle",
    "past",
    "plural",
    "present",
    "simple",
    "singular",
    "superlative",
    "third-person",
}
_BLOCKED_FORM_TAGS = {
    "alternative",
    "archaic",
    "misspelling",
    "nonstandard",
    "romanization",
}
_NON_MANDARIN_TRANSLATION_TAGS = {
    "cantonese",
    "dungan",
    "gan",
    "hakka",
    "hokkien",
    "jin",
    "min-bei",
    "min-dong",
    "min-nan",
    "southern-min",
    "teochew",
    "wu",
    "xiang",
}
_WORD_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "ʼ": "'",
        "＇": "'",
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
        "﹘": "-",
        "﹣": "-",
        "－": "-",
    }
)


@dataclass(frozen=True)
class BuildResult:
    entry_count: int
    direct_count: int
    form_of_count: int
    source_sha256: str
    dictionary_sha256: str
    dictionary_bytes: int


def contains_cjk(value: str) -> bool:
    return any(
        "\u3400" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        for character in value
    )


def normalize_word(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = text.translate(_WORD_PUNCTUATION_TRANSLATION)
    return _SPACE_RE.sub(" ", text).strip().casefold()


def _is_latin_letter(character: str) -> bool:
    return character.isalpha() and "LATIN" in unicodedata.name(character, "")


def _is_token_character(character: str) -> bool:
    return character.isdigit() or _is_latin_letter(character)


def is_supported_token(word: str) -> bool:
    if not word or not any(_is_latin_letter(character) for character in word):
        return False
    for index, character in enumerate(word):
        if _is_token_character(character):
            continue
        if (
            character in {"'", "-"}
            and index > 0
            and index + 1 < len(word)
            and _is_token_character(word[index - 1])
            and _is_token_character(word[index + 1])
        ):
            continue
        return False
    return True


def clean_translation(value: object) -> str | None:
    text = unicodedata.normalize("NFC", unescape(str(value or "")))
    text = _SPACE_RE.sub(" ", text).strip(" ,，;；")
    if not text:
        return None

    parts = [part.strip(" ,，;；") for part in _SLASH_RE.split(text)]
    chinese_parts = [part for part in parts if part and contains_cjk(part)]
    if len(parts) > 1 and chinese_parts:
        text = chinese_parts[-1]
    if not contains_cjk(text) or len(text) > MAX_ITEM_LENGTH:
        return None
    return text


def is_mandarin_translation(translation: dict[str, object]) -> bool:
    if translation.get("lang_code") not in {"cmn", "zh"}:
        return False
    tags = {str(tag).casefold() for tag in translation.get("tags", [])}
    return not tags & _NON_MANDARIN_TRANSLATION_TAGS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_jsonl(path: Path):
    if path.suffix.casefold() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _explicit_form_targets(entry: dict[str, object], lemma: str):
    for sense in entry.get("senses", []):
        if not isinstance(sense, dict):
            continue
        for relation in sense.get("form_of", []):
            if not isinstance(relation, dict):
                continue
            target = normalize_word(relation.get("word"))
            if is_supported_token(target) and target != lemma:
                yield lemma, target

    for form in entry.get("forms", []):
        if not isinstance(form, dict):
            continue
        tags = {str(tag).casefold() for tag in form.get("tags", [])}
        if tags & _BLOCKED_FORM_TAGS or not tags & _INFLECTION_TAGS:
            continue
        inflected = normalize_word(form.get("form"))
        if is_supported_token(inflected) and inflected != lemma:
            yield inflected, lemma


def _translations(entry: dict[str, object]):
    for translation in entry.get("translations", []):
        if isinstance(translation, dict):
            yield translation
    for sense in entry.get("senses", []):
        if not isinstance(sense, dict):
            continue
        for translation in sense.get("translations", []):
            if isinstance(translation, dict):
                yield translation


def _compose_meaning(parts: list[str]) -> str | None:
    selected: list[str] = []
    total_length = 0
    for part in parts:
        additional = len(part) + (1 if selected else 0)
        if len(selected) >= MAX_MEANING_ITEMS:
            break
        if total_length + additional > MAX_MEANING_LENGTH:
            continue
        selected.append(part)
        total_length += additional
    return "；".join(selected) if selected else None


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA page_size = 4096;
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;

        CREATE TABLE entries (
            word TEXT NOT NULL PRIMARY KEY,
            meaning TEXT,
            form_of TEXT,
            CHECK (meaning IS NOT NULL OR form_of IS NOT NULL),
            CHECK (form_of IS NULL OR form_of <> word)
        ) WITHOUT ROWID;

        CREATE TABLE metadata (
            key TEXT NOT NULL PRIMARY KEY,
            value TEXT NOT NULL
        ) WITHOUT ROWID;

        CREATE TABLE _meaning_parts (
            word TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            meaning TEXT NOT NULL,
            PRIMARY KEY (word, meaning)
        ) WITHOUT ROWID;

        CREATE TABLE _form_candidates (
            word TEXT NOT NULL,
            form_of TEXT NOT NULL,
            PRIMARY KEY (word, form_of)
        ) WITHOUT ROWID;
        """
    )


def build_dictionary(
    source_path: str | Path,
    output_path: str | Path,
    manifest_path: str | Path,
    source_metadata: dict[str, str],
) -> BuildResult:
    source = Path(source_path)
    output = Path(output_path)
    manifest = Path(manifest_path)
    if not source.is_file():
        raise FileNotFoundError(source)

    required_metadata = {
        "dictionary_source",
        "source_url",
        "source_version",
        "source_date",
        "build_date",
        "license",
        "wordfreq_version",
    }
    missing = sorted(required_metadata - source_metadata.keys())
    if missing:
        raise ValueError(f"missing source metadata: {', '.join(missing)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.building")
    temporary.unlink(missing_ok=True)
    source_hash = _sha256(source)

    connection = sqlite3.connect(temporary)
    try:
        _create_schema(connection)
        ordinal = 0
        with _open_jsonl(source) as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at line {line_number}") from exc
                if not isinstance(entry, dict) or entry.get("lang_code") != "en":
                    continue
                word = normalize_word(entry.get("word"))
                if not is_supported_token(word):
                    continue
                for translation in _translations(entry):
                    if not is_mandarin_translation(translation):
                        continue
                    meaning = clean_translation(translation.get("word"))
                    if meaning is None:
                        continue
                    ordinal += 1
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO _meaning_parts(word, ordinal, meaning)
                        VALUES (?, ?, ?)
                        """,
                        (word, ordinal, meaning),
                    )
                for form, lemma in _explicit_form_targets(entry, word):
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO _form_candidates(word, form_of)
                        VALUES (?, ?)
                        """,
                        (form, lemma),
                    )

        direct_rows = []
        current_word = None
        current_parts: list[str] = []
        for word, meaning in connection.execute(
            "SELECT word, meaning FROM _meaning_parts ORDER BY word, ordinal"
        ):
            if current_word is not None and word != current_word:
                composed = _compose_meaning(current_parts)
                if composed is not None:
                    direct_rows.append((current_word, composed, None))
                current_parts = []
            current_word = word
            current_parts.append(meaning)
        if current_word is not None:
            composed = _compose_meaning(current_parts)
            if composed is not None:
                direct_rows.append((current_word, composed, None))

        connection.executemany(
            "INSERT INTO entries(word, meaning, form_of) VALUES (?, ?, ?)",
            direct_rows,
        )
        connection.execute(
            """
            INSERT INTO entries(word, meaning, form_of)
            SELECT candidates.word, NULL, MIN(candidates.form_of)
            FROM _form_candidates AS candidates
            JOIN entries AS lemma ON lemma.word = candidates.form_of
                                  AND lemma.meaning IS NOT NULL
            LEFT JOIN entries AS direct ON direct.word = candidates.word
            WHERE direct.word IS NULL
            GROUP BY candidates.word
            HAVING COUNT(DISTINCT candidates.form_of) = 1
            ORDER BY candidates.word
            """
        )

        metadata = {**source_metadata, "schema_version": SCHEMA_VERSION}
        metadata["source_sha256"] = source_hash
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted((str(key), str(value)) for key, value in metadata.items()),
        )
        connection.execute("DROP TABLE _meaning_parts")
        connection.execute("DROP TABLE _form_candidates")
        connection.commit()
        connection.execute("VACUUM")
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    temporary.replace(output)
    dictionary_hash = _sha256(output)
    with closing(sqlite3.connect(output)) as readonly:
        entry_count = int(readonly.execute("SELECT COUNT(*) FROM entries").fetchone()[0])
        direct_count = int(
            readonly.execute(
                "SELECT COUNT(*) FROM entries WHERE meaning IS NOT NULL"
            ).fetchone()[0]
        )
        form_of_count = entry_count - direct_count

    result = BuildResult(
        entry_count=entry_count,
        direct_count=direct_count,
        form_of_count=form_of_count,
        source_sha256=source_hash,
        dictionary_sha256=dictionary_hash,
        dictionary_bytes=output.stat().st_size,
    )
    manifest_data = {
        **source_metadata,
        "schema_version": int(SCHEMA_VERSION),
        "entry_count": result.entry_count,
        "direct_entry_count": result.direct_count,
        "form_of_entry_count": result.form_of_count,
        "dictionary_file": output.name,
        "dictionary_sha256": result.dictionary_sha256,
        "dictionary_bytes": result.dictionary_bytes,
        "source_sha256": result.source_sha256,
    }
    manifest_temporary = manifest.with_name(f".{manifest.name}.building")
    manifest_temporary.write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_temporary.replace(manifest)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--source-date", required=True)
    parser.add_argument("--build-date", required=True)
    args = parser.parse_args()
    result = build_dictionary(
        args.source,
        args.output,
        args.manifest,
        {
            "dictionary_source": "Kaikki / English Wiktionary",
            "source_url": args.source_url,
            "source_version": args.source_version,
            "source_date": args.source_date,
            "build_date": args.build_date,
            "license": "CC-BY-SA-4.0",
            "wordfreq_version": "3.1.1",
        },
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
