from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from tokenizer import is_valid_token
from vocabulary_filter import is_basic_word


DATABASE_SCHEMA_VERSION = 1
DEFAULT_COLLECTION_NAME = "默认词汇表"


@dataclass(frozen=True)
class VocabularyCollection:
    collection_id: int
    name: str
    is_default: bool
    created_at: str


@dataclass(frozen=True)
class DatabaseImportResult:
    import_id: int
    duplicate: bool


@dataclass(frozen=True)
class DatabaseRemoveResult:
    import_id: int
    removed_word_count: int
    removed_token_count: int


@dataclass(frozen=True)
class VocabularyQueryResult:
    total: int
    limit: int
    offset: int
    rows: list[dict[str, object]]


class ImportNotFoundError(LookupError):
    pass


class CollectionNotFoundError(LookupError):
    pass


class CollectionNameConflictError(ValueError):
    pass


class DefaultCollectionProtectedError(ValueError):
    pass


class ImportCollectionMismatchError(LookupError):
    pass


class MigrationConsistencyError(sqlite3.DatabaseError):
    pass


class VocabularyDatabase:
    def __init__(self, path: str | Path, *, quality_policy=None):
        self.path = Path(path)
        self.quality_policy = quality_policy
        self.migration_backup_path: Path | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def schema_version(self) -> int:
        with closing(self._connect()) as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "is_valid_vocabulary_token",
            1,
            lambda word: int(isinstance(word, str) and is_valid_token(word)),
            deterministic=True,
        )
        if self.quality_policy is not None:
            connection.create_function(
                "is_quality_vocabulary_word",
                3,
                lambda word, meaning, frequency: int(
                    self.quality_policy.should_keep(word, meaning, frequency)
                ),
                deterministic=True,
            )
        connection.create_function(
            "is_basic_vocabulary_word",
            1,
            lambda word: int(isinstance(word, str) and is_basic_word(word)),
            deterministic=True,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _table_names(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if not str(row[0]).startswith("sqlite_")
        }

    @staticmethod
    def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _create_schema_tables(connection: sqlite3.Connection, suffix: str = "") -> None:
        words = f"words{suffix}"
        collections = f"vocabulary_collections{suffix}"
        stats = f"collection_word_stats{suffix}"
        imports = f"imports{suffix}"
        details = f"import_word_counts{suffix}"
        statements = (
            f"""CREATE TABLE {words} (
                word TEXT NOT NULL PRIMARY KEY COLLATE NOCASE,
                meaning TEXT,
                english_frequency REAL,
                frequency_source TEXT,
                frequency_version TEXT,
                updated_at TEXT NOT NULL
            )""",
            f"""CREATE TABLE {collections} (
                collection_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE
                    CHECK (length(trim(name)) > 0 AND name = trim(name)),
                created_at TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1))
            )""",
            f"""CREATE TABLE {stats} (
                collection_id INTEGER NOT NULL
                    REFERENCES {collections}(collection_id) ON DELETE CASCADE,
                word TEXT NOT NULL REFERENCES {words}(word),
                total_count INTEGER NOT NULL CHECK (total_count > 0),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (collection_id, word)
            )""",
            f"""CREATE TABLE {imports} (
                import_id INTEGER PRIMARY KEY,
                collection_id INTEGER NOT NULL
                    REFERENCES {collections}(collection_id) ON DELETE CASCADE,
                content_hash TEXT NOT NULL,
                source_task_folder TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_file_names TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                tokenizer_version TEXT NOT NULL,
                UNIQUE (collection_id, content_hash)
            )""",
            f"""CREATE TABLE {details} (
                import_id INTEGER NOT NULL
                    REFERENCES {imports}(import_id) ON DELETE CASCADE,
                word TEXT NOT NULL REFERENCES {words}(word),
                count INTEGER NOT NULL CHECK (count > 0),
                PRIMARY KEY (import_id, word)
            )""",
        )
        for statement in statements:
            connection.execute(statement)

    @staticmethod
    def _create_schema_indexes(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE UNIQUE INDEX ux_vocabulary_collections_one_default "
            "ON vocabulary_collections(is_default) WHERE is_default = 1"
        )
        connection.execute(
            "CREATE INDEX idx_collection_word_stats_total "
            "ON collection_word_stats(collection_id, total_count DESC, word ASC)"
        )
        connection.execute(
            "CREATE INDEX idx_words_english_frequency "
            "ON words(english_frequency DESC, word ASC)"
        )
        connection.execute(
            "CREATE INDEX idx_imports_collection_imported "
            "ON imports(collection_id, imported_at DESC, import_id DESC)"
        )

    def _create_new_database(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._create_schema_tables(connection)
            self._create_schema_indexes(connection)
            connection.execute(
                "INSERT INTO vocabulary_collections (name, created_at, is_default) "
                "VALUES (?, ?, 1)",
                (DEFAULT_COLLECTION_NAME, self._utc_now()),
            )
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    def _validate_legacy_counts(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            """SELECT COUNT(*) AS inconsistent FROM (
                SELECT words.word FROM words
                LEFT JOIN import_word_counts ON import_word_counts.word = words.word
                GROUP BY words.word, words.total_count
                HAVING words.total_count != COALESCE(SUM(import_word_counts.count), 0)
            )"""
        ).fetchone()
        if int(row["inconsistent"]):
            raise MigrationConsistencyError(
                "旧词汇数据库累计次数与导入明细不一致，已停止迁移。"
            )

    def _create_migration_backup(self, source: sqlite3.Connection) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = self.path.with_name(f"{self.path.name}.backup-{timestamp}")
        suffix = 1
        while candidate.exists():
            candidate = self.path.with_name(
                f"{self.path.name}.backup-{timestamp}-{suffix}"
            )
            suffix += 1
        with closing(sqlite3.connect(candidate)) as destination:
            source.backup(destination)
        self.migration_backup_path = candidate
        return candidate

    def _migrate_legacy_database(self, connection: sqlite3.Connection) -> None:
        self._validate_legacy_counts(connection)
        self._create_migration_backup(connection)
        legacy_counts = {
            "words": int(connection.execute("SELECT COUNT(*) FROM words").fetchone()[0]),
            "imports": int(connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0]),
            "details": int(
                connection.execute("SELECT COUNT(*) FROM import_word_counts").fetchone()[0]
            ),
            "tokens": int(
                connection.execute(
                    "SELECT COALESCE(SUM(count), 0) FROM import_word_counts"
                ).fetchone()[0]
            ),
        }
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._create_schema_tables(connection, "_new")
            cursor = connection.execute(
                "INSERT INTO vocabulary_collections_new "
                "(name, created_at, is_default) VALUES (?, ?, 1)",
                (DEFAULT_COLLECTION_NAME, self._utc_now()),
            )
            default_id = int(cursor.lastrowid)
            connection.execute(
                """INSERT INTO words_new (
                    word, meaning, english_frequency, frequency_source,
                    frequency_version, updated_at
                ) SELECT word, meaning, english_frequency, frequency_source,
                         frequency_version, updated_at FROM words"""
            )
            connection.execute(
                """INSERT INTO imports_new (
                    import_id, collection_id, content_hash, source_task_folder,
                    source_type, source_file_names, imported_at, tokenizer_version
                ) SELECT import_id, ?, content_hash, source_task_folder, source_type,
                         source_file_names, imported_at, tokenizer_version FROM imports""",
                (default_id,),
            )
            connection.execute(
                "INSERT INTO import_word_counts_new (import_id, word, count) "
                "SELECT import_id, word, count FROM import_word_counts"
            )
            connection.execute(
                """INSERT INTO collection_word_stats_new (
                    collection_id, word, total_count, updated_at
                ) SELECT ?, word, total_count, updated_at
                  FROM words WHERE total_count > 0""",
                (default_id,),
            )
            migrated_counts = {
                "words": int(connection.execute("SELECT COUNT(*) FROM words_new").fetchone()[0]),
                "imports": int(connection.execute("SELECT COUNT(*) FROM imports_new").fetchone()[0]),
                "details": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM import_word_counts_new"
                    ).fetchone()[0]
                ),
                "tokens": int(
                    connection.execute(
                        "SELECT COALESCE(SUM(total_count), 0) "
                        "FROM collection_word_stats_new"
                    ).fetchone()[0]
                ),
            }
            if migrated_counts != legacy_counts:
                raise MigrationConsistencyError(
                    "词汇数据库迁移后的行数或累计次数校验失败。"
                )
            for table in ("import_word_counts", "imports", "words"):
                connection.execute(f"DROP TABLE {table}")
            for old, new in (
                ("words_new", "words"),
                ("vocabulary_collections_new", "vocabulary_collections"),
                ("collection_word_stats_new", "collection_word_stats"),
                ("imports_new", "imports"),
                ("import_word_counts_new", "import_word_counts"),
            ):
                connection.execute(f"ALTER TABLE {old} RENAME TO {new}")
            self._create_schema_indexes(connection)
            connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise sqlite3.IntegrityError("迁移后的外键校验失败。")
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    @staticmethod
    def _ensure_one_default(connection: sqlite3.Connection) -> None:
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM vocabulary_collections WHERE is_default = 1"
            ).fetchone()[0]
        )
        if count != 1:
            raise sqlite3.DatabaseError("词汇数据库必须且只能有一个默认词汇表。")

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            tables = self._table_names(connection)
            if not tables:
                self._create_new_database(connection)
                return
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            legacy = (
                version == 0
                and {"words", "imports", "import_word_counts"} <= tables
                and "total_count" in self._column_names(connection, "words")
                and "collection_id" not in self._column_names(connection, "imports")
            )
            if legacy:
                self._migrate_legacy_database(connection)
            elif version != DATABASE_SCHEMA_VERSION:
                raise sqlite3.DatabaseError(
                    f"不支持的词汇数据库 schema 版本：{version}"
                )
            self._ensure_one_default(connection)

    @staticmethod
    def _normalize_collection_name(name: str) -> str:
        if not isinstance(name, str):
            raise ValueError("collection name must be a string")
        normalized = name.strip()
        if not normalized:
            raise ValueError("collection name must not be empty")
        return normalized

    @staticmethod
    def _collection_from_row(row: sqlite3.Row) -> VocabularyCollection:
        return VocabularyCollection(
            collection_id=int(row["collection_id"]),
            name=str(row["name"]),
            is_default=bool(row["is_default"]),
            created_at=str(row["created_at"]),
        )

    def _resolve_collection(
        self, connection: sqlite3.Connection, collection_id: int | None
    ) -> VocabularyCollection:
        if collection_id is None:
            row = connection.execute(
                "SELECT collection_id, name, is_default, created_at "
                "FROM vocabulary_collections WHERE is_default = 1"
            ).fetchone()
        else:
            if (
                isinstance(collection_id, bool)
                or not isinstance(collection_id, int)
                or collection_id <= 0
            ):
                raise ValueError("collection_id must be a positive integer or None")
            row = connection.execute(
                "SELECT collection_id, name, is_default, created_at "
                "FROM vocabulary_collections WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
        if row is None:
            if collection_id is None:
                raise CollectionNotFoundError("找不到默认词汇表。")
            raise CollectionNotFoundError(f"找不到词汇表：{collection_id}")
        return self._collection_from_row(row)

    def resolve_collection(self, collection_id: int | None = None) -> VocabularyCollection:
        with closing(self._connect()) as connection:
            return self._resolve_collection(connection, collection_id)

    def list_collections(self) -> list[VocabularyCollection]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT collection_id, name, is_default, created_at
                   FROM vocabulary_collections
                   ORDER BY is_default DESC, name COLLATE NOCASE ASC, collection_id ASC"""
            ).fetchall()
        return [self._collection_from_row(row) for row in rows]

    def create_collection(self, name: str) -> VocabularyCollection:
        normalized = self._normalize_collection_name(name)
        with closing(self._connect()) as connection:
            try:
                with connection:
                    cursor = connection.execute(
                        "INSERT INTO vocabulary_collections "
                        "(name, created_at, is_default) VALUES (?, ?, 0)",
                        (normalized, self._utc_now()),
                    )
                    collection_id = int(cursor.lastrowid)
            except sqlite3.IntegrityError as exc:
                raise CollectionNameConflictError(
                    f"词汇表名称已存在：{normalized}"
                ) from exc
            return self._resolve_collection(connection, collection_id)

    def rename_collection(self, collection_id: int, name: str) -> VocabularyCollection:
        normalized = self._normalize_collection_name(name)
        with closing(self._connect()) as connection:
            self._resolve_collection(connection, collection_id)
            try:
                with connection:
                    connection.execute(
                        "UPDATE vocabulary_collections SET name = ? WHERE collection_id = ?",
                        (normalized, collection_id),
                    )
            except sqlite3.IntegrityError as exc:
                raise CollectionNameConflictError(
                    f"词汇表名称已存在：{normalized}"
                ) from exc
            return self._resolve_collection(connection, collection_id)

    def delete_collection(self, collection_id: int) -> None:
        with closing(self._connect()) as connection:
            collection = self._resolve_collection(connection, collection_id)
            if collection.is_default:
                raise DefaultCollectionProtectedError("默认词汇表不允许删除。")
            with connection:
                connection.execute(
                    "DELETE FROM vocabulary_collections WHERE collection_id = ?",
                    (collection.collection_id,),
                )

    def _prepare_quality_policy(
        self,
        connection: sqlite3.Connection,
        collection_id: int,
        import_id: int | None = None,
    ) -> None:
        if self.quality_policy is None:
            return
        parameters: list[object] = [collection_id]
        import_join = ""
        import_condition = ""
        if import_id is not None:
            import_join = "JOIN import_word_counts ON import_word_counts.word = words.word"
            import_condition = "AND import_word_counts.import_id = ?"
            parameters.append(import_id)
        rows = connection.execute(
            f"""SELECT words.word, words.meaning, words.english_frequency
                FROM words
                JOIN collection_word_stats
                  ON collection_word_stats.word = words.word
                 AND collection_word_stats.collection_id = ?
                {import_join}
                WHERE is_valid_vocabulary_token(words.word) = 1
                  AND is_basic_vocabulary_word(words.word) = 0
                  {import_condition}""",
            parameters,
        ).fetchall()
        self.quality_policy.prepare(
            [(str(row["word"]), row["meaning"], row["english_frequency"]) for row in rows]
        )

    def import_counts(
        self,
        *,
        content_hash: str,
        source_task_folder: str,
        source_type: str,
        source_file_names: Sequence[str],
        tokenizer_version: str,
        counts: Mapping[str, int],
        meanings: Mapping[str, str | None] | None = None,
        frequencies: Mapping[str, float | None] | None = None,
        frequency_source: str | None = None,
        frequency_version: str | None = None,
        collection_id: int | None = None,
    ) -> DatabaseImportResult:
        unique_counts = dict(counts)
        for word, count in unique_counts.items():
            if not isinstance(word, str) or not word:
                raise ValueError("word must be a non-empty string")
            if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
                raise ValueError("word count must be a positive integer")
        meaning_updates = dict(meanings or {})
        frequency_updates = dict(frequencies or {})
        if (meaning_updates.keys() | frequency_updates.keys()) - unique_counts.keys():
            raise ValueError("enrichment words must be present in counts")
        for meaning in meaning_updates.values():
            if meaning is not None and (not isinstance(meaning, str) or not meaning):
                raise ValueError("meaning must be a non-empty string or None")
        for frequency in frequency_updates.values():
            if frequency is not None and (
                isinstance(frequency, bool)
                or not isinstance(frequency, (int, float))
                or frequency <= 0
            ):
                raise ValueError("frequency must be a positive number or None")
        if frequency_updates and (not frequency_source or not frequency_version):
            raise ValueError("frequency source and version are required")

        now = self._utc_now()
        encoded_names = json.dumps(
            list(source_file_names), ensure_ascii=False, separators=(",", ":")
        )
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                collection = self._resolve_collection(connection, collection_id)
                existing = connection.execute(
                    "SELECT import_id FROM imports "
                    "WHERE collection_id = ? AND content_hash = ?",
                    (collection.collection_id, content_hash),
                ).fetchone()
                if existing is not None:
                    return DatabaseImportResult(int(existing["import_id"]), True)
                cursor = connection.execute(
                    """INSERT INTO imports (
                        collection_id, content_hash, source_task_folder, source_type,
                        source_file_names, imported_at, tokenizer_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        collection.collection_id, content_hash, source_task_folder,
                        source_type, encoded_names, now, tokenizer_version,
                    ),
                )
                import_id = int(cursor.lastrowid)
                connection.executemany(
                    "INSERT INTO words (word, updated_at) VALUES (?, ?) "
                    "ON CONFLICT(word) DO UPDATE SET updated_at = excluded.updated_at",
                    [(word, now) for word in unique_counts],
                )
                connection.executemany(
                    """INSERT INTO collection_word_stats (
                        collection_id, word, total_count, updated_at
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(collection_id, word) DO UPDATE SET
                        total_count = total_count + excluded.total_count,
                        updated_at = excluded.updated_at""",
                    [
                        (collection.collection_id, word, count, now)
                        for word, count in unique_counts.items()
                    ],
                )
                connection.executemany(
                    "INSERT INTO import_word_counts (import_id, word, count) VALUES (?, ?, ?)",
                    [(import_id, word, count) for word, count in unique_counts.items()],
                )
                connection.executemany(
                    "UPDATE words SET meaning = COALESCE(meaning, ?), updated_at = ? "
                    "WHERE word = ?",
                    [
                        (meaning, now, word)
                        for word, meaning in meaning_updates.items()
                        if meaning is not None
                    ],
                )
                connection.executemany(
                    """UPDATE words SET english_frequency = ?, frequency_source = ?,
                       frequency_version = ?, updated_at = ? WHERE word = ?""",
                    [
                        (frequency, frequency_source, frequency_version, now, word)
                        for word, frequency in frequency_updates.items()
                    ],
                )
                return DatabaseImportResult(import_id, False)

    def _validate_import_ownership(
        self,
        connection: sqlite3.Connection,
        import_id: int,
        collection_id: int | None,
    ) -> VocabularyCollection:
        if isinstance(import_id, bool) or not isinstance(import_id, int) or import_id <= 0:
            raise ValueError("import_id must be a positive integer")
        collection = self._resolve_collection(connection, collection_id)
        row = connection.execute(
            "SELECT collection_id FROM imports WHERE import_id = ?", (import_id,)
        ).fetchone()
        if row is None:
            raise ImportNotFoundError(f"找不到词汇导入记录：{import_id}")
        if int(row["collection_id"]) != collection.collection_id:
            raise ImportCollectionMismatchError(
                f"导入记录 {import_id} 不属于词汇表 {collection.collection_id}。"
            )
        return collection

    def remove_import(
        self, import_id: int, *, collection_id: int | None = None
    ) -> DatabaseRemoveResult:
        now = self._utc_now()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                collection = self._validate_import_ownership(
                    connection, import_id, collection_id
                )
                contributions = connection.execute(
                    "SELECT word, count FROM import_word_counts WHERE import_id = ?",
                    (import_id,),
                ).fetchall()
                contribution_values = [
                    (int(row["count"]), collection.collection_id, str(row["word"]))
                    for row in contributions
                ]
                connection.executemany(
                    """DELETE FROM collection_word_stats
                       WHERE total_count = ? AND collection_id = ? AND word = ?""",
                    contribution_values,
                )
                connection.executemany(
                    """UPDATE collection_word_stats
                       SET total_count = total_count - ?, updated_at = ?
                       WHERE collection_id = ? AND word = ? AND total_count > ?""",
                    [
                        (count, now, target_collection, word, count)
                        for count, target_collection, word in contribution_values
                    ],
                )
                connection.execute(
                    "DELETE FROM import_word_counts WHERE import_id = ?", (import_id,)
                )
                connection.execute("DELETE FROM imports WHERE import_id = ?", (import_id,))
                return DatabaseRemoveResult(
                    import_id=import_id,
                    removed_word_count=len(contributions),
                    removed_token_count=sum(int(row["count"]) for row in contributions),
                )

    def find_import_id(
        self, content_hash: str, collection_id: int | None = None
    ) -> int | None:
        with closing(self._connect()) as connection:
            collection = self._resolve_collection(connection, collection_id)
            row = connection.execute(
                "SELECT import_id FROM imports "
                "WHERE collection_id = ? AND content_hash = ?",
                (collection.collection_id, content_hash),
            ).fetchone()
        return int(row["import_id"]) if row is not None else None

    def get_enrichments(self, words: Sequence[str]) -> dict[str, dict[str, object]]:
        unique_words = sorted(set(words))
        result: dict[str, dict[str, object]] = {}
        with closing(self._connect()) as connection:
            for offset in range(0, len(unique_words), 400):
                batch = unique_words[offset : offset + 400]
                if not batch:
                    continue
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""SELECT word, meaning, english_frequency,
                               frequency_source, frequency_version
                        FROM words WHERE word IN ({placeholders})""",
                    batch,
                ).fetchall()
                result.update({str(row["word"]): dict(row) for row in rows})
        return result

    def get_import_words(
        self, import_id: int, *, collection_id: int | None = None
    ) -> list[str]:
        with closing(self._connect()) as connection:
            self._validate_import_ownership(connection, import_id, collection_id)
            rows = connection.execute(
                """SELECT word FROM import_word_counts
                   WHERE import_id = ?
                     AND is_valid_vocabulary_token(word) = 1
                     AND is_basic_vocabulary_word(word) = 0
                   ORDER BY word COLLATE NOCASE ASC""",
                (import_id,),
            ).fetchall()
        return [str(row["word"]) for row in rows]

    def update_meanings(self, meanings: Mapping[str, str | None]) -> None:
        updates = [(meaning, word) for word, meaning in meanings.items() if meaning is not None]
        if not updates:
            return
        for meaning, word in updates:
            if not isinstance(word, str) or not word:
                raise ValueError("word must be a non-empty string")
            if not isinstance(meaning, str) or not meaning:
                raise ValueError("meaning must be a non-empty string")
        now = self._utc_now()
        with closing(self._connect()) as connection:
            with connection:
                connection.executemany(
                    "UPDATE words SET meaning = COALESCE(meaning, ?), updated_at = ? "
                    "WHERE word = ?",
                    [(meaning, now, word) for meaning, word in updates],
                )

    def list_words(
        self,
        order_by: str = "alphabetical",
        *,
        collection_id: int | None = None,
    ) -> list[dict[str, object]]:
        order_clauses = {
            "alphabetical": "words.word COLLATE NOCASE ASC",
            "count": "stats.total_count DESC, words.word COLLATE NOCASE ASC",
            "frequency": (
                "words.english_frequency IS NULL, words.english_frequency DESC, "
                "words.word COLLATE NOCASE ASC"
            ),
        }
        try:
            order_clause = order_clauses[order_by]
        except KeyError as exc:
            raise ValueError(f"unknown word ordering: {order_by}") from exc
        with closing(self._connect()) as connection:
            collection = self._resolve_collection(connection, collection_id)
            rows = connection.execute(
                f"""SELECT words.word, words.meaning, stats.total_count,
                           words.english_frequency, words.frequency_source,
                           words.frequency_version, stats.updated_at
                    FROM collection_word_stats AS stats
                    JOIN words ON words.word = stats.word
                    WHERE stats.collection_id = ? ORDER BY {order_clause}""",
                (collection.collection_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _validate_query_arguments(search: str, limit: int, offset: int) -> None:
        if not isinstance(search, str):
            raise ValueError("search must be a string")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("limit must be an integer from 1 to 1000")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")

    @staticmethod
    def _escaped_search(search: str) -> str:
        return search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def query_words(
        self,
        *,
        search: str = "",
        sort: str = "word_asc",
        limit: int = 200,
        offset: int = 0,
        collection_id: int | None = None,
    ) -> VocabularyQueryResult:
        order_clauses = {
            "word_asc": "words.word COLLATE NOCASE ASC",
            "word_desc": "words.word COLLATE NOCASE DESC",
            "count_desc": "stats.total_count DESC, words.word COLLATE NOCASE ASC",
            "count_asc": "stats.total_count ASC, words.word COLLATE NOCASE ASC",
            "frequency_desc": (
                "words.english_frequency IS NULL ASC, words.english_frequency DESC, "
                "words.word COLLATE NOCASE ASC"
            ),
            "frequency_asc": (
                "words.english_frequency IS NULL ASC, words.english_frequency ASC, "
                "words.word COLLATE NOCASE ASC"
            ),
        }
        self._validate_query_arguments(search, limit, offset)
        try:
            order_clause = order_clauses[sort]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"unknown word ordering: {sort}") from exc
        conditions = [
            "stats.collection_id = ?",
            "is_valid_vocabulary_token(words.word) = 1",
            "is_basic_vocabulary_word(words.word) = 0",
        ]
        if self.quality_policy is not None:
            conditions.append(
                "is_quality_vocabulary_word(words.word, words.meaning, "
                "words.english_frequency) = 1"
            )
        with closing(self._connect()) as connection:
            collection = self._resolve_collection(connection, collection_id)
            parameters: list[object] = [collection.collection_id]
            if search:
                conditions.append("words.word LIKE ? ESCAPE '\\'")
                parameters.append(f"%{self._escaped_search(search)}%")
            where_clause = "WHERE " + " AND ".join(conditions)
            self._prepare_quality_policy(connection, collection.collection_id)
            total_row = connection.execute(
                f"""SELECT COUNT(*) AS total
                    FROM collection_word_stats AS stats
                    JOIN words ON words.word = stats.word {where_clause}""",
                parameters,
            ).fetchone()
            rows = connection.execute(
                f"""SELECT words.word, words.meaning, stats.total_count,
                           words.english_frequency
                    FROM collection_word_stats AS stats
                    JOIN words ON words.word = stats.word
                    {where_clause} ORDER BY {order_clause} LIMIT ? OFFSET ?""",
                [*parameters, limit, offset],
            ).fetchall()
        return VocabularyQueryResult(
            total=int(total_row["total"]), limit=limit, offset=offset,
            rows=[dict(row) for row in rows],
        )

    def query_import(
        self,
        import_id: int,
        *,
        search: str = "",
        sort: str = "import_count_desc",
        limit: int = 200,
        offset: int = 0,
        collection_id: int | None = None,
    ) -> VocabularyQueryResult:
        order_clauses = {
            "word_asc": "words.word COLLATE NOCASE ASC",
            "word_desc": "words.word COLLATE NOCASE DESC",
            "import_count_desc": "details.count DESC, words.word COLLATE NOCASE ASC",
            "import_count_asc": "details.count ASC, words.word COLLATE NOCASE ASC",
            "total_count_desc": "stats.total_count DESC, words.word COLLATE NOCASE ASC",
            "total_count_asc": "stats.total_count ASC, words.word COLLATE NOCASE ASC",
            "frequency_desc": (
                "words.english_frequency IS NULL ASC, words.english_frequency DESC, "
                "words.word COLLATE NOCASE ASC"
            ),
            "frequency_asc": (
                "words.english_frequency IS NULL ASC, words.english_frequency ASC, "
                "words.word COLLATE NOCASE ASC"
            ),
        }
        self._validate_query_arguments(search, limit, offset)
        try:
            order_clause = order_clauses[sort]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"unknown import word ordering: {sort}") from exc
        with closing(self._connect()) as connection:
            collection = self._validate_import_ownership(connection, import_id, collection_id)
            conditions = [
                "details.import_id = ?", "stats.collection_id = ?",
                "is_valid_vocabulary_token(details.word) = 1",
                "is_basic_vocabulary_word(details.word) = 0",
            ]
            if self.quality_policy is not None:
                conditions.append(
                    "is_quality_vocabulary_word(words.word, words.meaning, "
                    "words.english_frequency) = 1"
                )
            parameters: list[object] = [import_id, collection.collection_id]
            if search:
                conditions.append("details.word LIKE ? ESCAPE '\\'")
                parameters.append(f"%{self._escaped_search(search)}%")
            where_clause = "WHERE " + " AND ".join(conditions)
            self._prepare_quality_policy(connection, collection.collection_id, import_id)
            joins = """FROM import_word_counts AS details
                JOIN words ON words.word = details.word
                JOIN collection_word_stats AS stats ON stats.word = details.word"""
            total_row = connection.execute(
                f"SELECT COUNT(*) AS total {joins} {where_clause}", parameters
            ).fetchone()
            rows = connection.execute(
                f"""SELECT words.word, words.meaning,
                           details.count AS import_count, stats.total_count,
                           words.english_frequency
                    {joins} {where_clause} ORDER BY {order_clause} LIMIT ? OFFSET ?""",
                [*parameters, limit, offset],
            ).fetchall()
        return VocabularyQueryResult(
            total=int(total_row["total"]), limit=limit, offset=offset,
            rows=[dict(row) for row in rows],
        )

    def count_inconsistencies(self) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT stats.collection_id, stats.word, stats.total_count,
                          COALESCE(SUM(details.count), 0) AS contributed_count
                   FROM collection_word_stats AS stats
                   LEFT JOIN imports ON imports.collection_id = stats.collection_id
                   LEFT JOIN import_word_counts AS details
                     ON details.import_id = imports.import_id
                    AND details.word = stats.word
                   GROUP BY stats.collection_id, stats.word, stats.total_count
                   HAVING stats.total_count != COALESCE(SUM(details.count), 0)
                   ORDER BY stats.collection_id, stats.word COLLATE NOCASE"""
            ).fetchall()
        return [dict(row) for row in rows]
