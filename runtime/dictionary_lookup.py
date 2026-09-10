from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import closing
from pathlib import Path
import sqlite3
import unicodedata

from dictionary_errors import DictionaryResourceError
from ecdict_lookup import DEFAULT_ECDICT_PATH, ECDICTLookup


DICTIONARY_SCHEMA_VERSION = "1"
DEFAULT_DICTIONARY_PATH = (
    Path(__file__).resolve().parent / "vocabulary_resources" / "dictionary.sqlite3"
)
_SQLITE_BATCH_SIZE = 400
_DEFAULT_FALLBACK = object()


def _normalize_word(word: str) -> str:
    if not isinstance(word, str):
        raise TypeError("word must be a string")
    return unicodedata.normalize("NFC", word).strip().casefold()


class DictionaryLookup:
    def __init__(
        self,
        path: str | Path = DEFAULT_DICTIONARY_PATH,
        *,
        fallback_path: str | Path | None | object = _DEFAULT_FALLBACK,
    ):
        self.path = Path(path)
        self._cache: dict[str, str | None] = {}
        self._validate_resource()
        if fallback_path is _DEFAULT_FALLBACK:
            fallback_path = (
                DEFAULT_ECDICT_PATH if self.path == DEFAULT_DICTIONARY_PATH else None
            )
        self.fallback = (
            ECDICTLookup(fallback_path)
            if fallback_path is not None and Path(fallback_path).is_file()
            else None
        )

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.path.resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _validate_resource(self) -> None:
        if not self.path.is_file():
            raise DictionaryResourceError(f"离线词典资源不存在：{self.path}")
        try:
            with closing(self._connect()) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                if not {"entries", "metadata"}.issubset(tables):
                    raise DictionaryResourceError("离线词典 schema 缺少必要数据表。")
                schema_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'schema_version'"
                ).fetchone()
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(entries)")
                }
        except DictionaryResourceError:
            raise
        except sqlite3.DatabaseError as exc:
            raise DictionaryResourceError("离线词典资源损坏或不是有效 SQLite。") from exc
        if schema_row is None or str(schema_row["value"]) != DICTIONARY_SCHEMA_VERSION:
            raise DictionaryResourceError("离线词典 schema 版本无效。")
        if columns != {"word", "meaning", "form_of"}:
            raise DictionaryResourceError("离线词典 entries schema 无效。")

    def _fetch_rows(self, words: Sequence[str]) -> dict[str, sqlite3.Row]:
        if not words:
            return {}
        result: dict[str, sqlite3.Row] = {}
        try:
            with closing(self._connect()) as connection:
                for offset in range(0, len(words), _SQLITE_BATCH_SIZE):
                    batch = words[offset : offset + _SQLITE_BATCH_SIZE]
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"""
                        SELECT word, meaning, form_of
                        FROM entries
                        WHERE word IN ({placeholders})
                        """,
                        batch,
                    ).fetchall()
                    result.update({str(row["word"]): row for row in rows})
        except sqlite3.DatabaseError as exc:
            raise DictionaryResourceError("读取离线词典失败，资源可能已损坏。") from exc
        return result

    def lookup_many(self, words: Iterable[str]) -> dict[str, str | None]:
        requested = list(words)
        normalized_by_word = {word: _normalize_word(word) for word in requested}
        missing = sorted(
            {
                normalized
                for normalized in normalized_by_word.values()
                if normalized and normalized not in self._cache
            }
        )
        rows = self._fetch_rows(missing)
        lemma_words = sorted(
            {
                str(row["form_of"])
                for row in rows.values()
                if row["meaning"] is None and row["form_of"] is not None
            }
        )
        lemma_rows = self._fetch_rows(lemma_words)

        for word in missing:
            row = rows.get(word)
            meaning: str | None = None
            if row is not None and row["meaning"] is not None:
                meaning = str(row["meaning"])
            elif row is not None and row["form_of"] is not None:
                lemma = lemma_rows.get(str(row["form_of"]))
                if lemma is not None and lemma["meaning"] is not None:
                    meaning = str(lemma["meaning"])
            self._cache[word] = meaning

        if self.fallback is not None:
            unresolved = [word for word in missing if self._cache[word] is None]
            fallback_meanings = self.fallback.lookup_many(unresolved)
            for word in unresolved:
                self._cache[word] = fallback_meanings[word]

        return {
            word: self._cache.get(normalized) if normalized else None
            for word, normalized in normalized_by_word.items()
        }

    def lookup(self, word: str) -> str | None:
        return self.lookup_many((word,))[word]
