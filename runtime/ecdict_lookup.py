from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import closing
from pathlib import Path
import sqlite3
import unicodedata

from dictionary_errors import DictionaryResourceError


DEFAULT_ECDICT_PATH = (
    Path(__file__).resolve().parents[1]
    / "用户数据"
    / "vocabulary_resources"
    / "ecdict.sqlite3"
)
_SQLITE_BATCH_SIZE = 400


def _normalize_word(word: str) -> str:
    if not isinstance(word, str):
        raise TypeError("word must be a string")
    return unicodedata.normalize("NFC", word).strip().casefold()


def _clean_translation(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).replace("\\n", "\n").replace("\r", "\n")
    meanings: list[str] = []
    seen: set[str] = set()
    for part in normalized.split("\n"):
        meaning = " ".join(part.split())
        if meaning and meaning not in seen:
            seen.add(meaning)
            meanings.append(meaning)
    return "；".join(meanings) or None


class ECDICTLookup:
    def __init__(self, path: str | Path = DEFAULT_ECDICT_PATH):
        self.path = Path(path)
        self._cache: dict[str, str | None] = {}
        self._validate_resource()

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.path.resolve().as_uri()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _validate_resource(self) -> None:
        if not self.path.is_file():
            raise DictionaryResourceError(f"本地 ECDICT 资源不存在：{self.path}")
        try:
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='stardict'"
                ).fetchone()
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(stardict)")
                }
        except sqlite3.DatabaseError as exc:
            raise DictionaryResourceError(
                "本地 ECDICT 资源损坏或不是有效 SQLite。"
            ) from exc
        if table is None or not {"word", "translation"}.issubset(columns):
            raise DictionaryResourceError("本地 ECDICT schema 无效。")

    def _fetch(self, words: Sequence[str]) -> dict[str, str | None]:
        result: dict[str, str | None] = {}
        try:
            with closing(self._connect()) as connection:
                for offset in range(0, len(words), _SQLITE_BATCH_SIZE):
                    batch = words[offset : offset + _SQLITE_BATCH_SIZE]
                    placeholders = ",".join("?" for _ in batch)
                    rows = connection.execute(
                        f"""
                        SELECT word, translation
                        FROM stardict
                        WHERE word IN ({placeholders})
                        """,
                        batch,
                    ).fetchall()
                    for row in rows:
                        result[str(row["word"]).casefold()] = _clean_translation(
                            row["translation"]
                        )
        except sqlite3.DatabaseError as exc:
            raise DictionaryResourceError("读取本地 ECDICT 失败，资源可能已损坏。") from exc
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
        fetched = self._fetch(missing) if missing else {}
        for word in missing:
            self._cache[word] = fetched.get(word)
        return {
            word: self._cache.get(normalized) if normalized else None
            for word, normalized in normalized_by_word.items()
        }

    def lookup(self, word: str) -> str | None:
        return self.lookup_many((word,))[word]
