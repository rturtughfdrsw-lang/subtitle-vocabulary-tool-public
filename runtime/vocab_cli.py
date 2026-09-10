from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

RUNTIME_ROOT = Path(__file__).resolve().parent
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from dictionary_lookup import DictionaryLookup, DictionaryResourceError
from frequency_lookup import FrequencyLookup, FrequencyResourceError
from subtitle_text_reader import (
    SubtitleContentEmptyError,
    SubtitleFileNotFoundError,
    SubtitleFormatUnsupportedError,
    SubtitleNotFoundError,
    SubtitleSourceAmbiguousError,
    SubtitleSourceError,
)
from vocabulary_db import (
    CollectionNameConflictError,
    CollectionNotFoundError,
    DefaultCollectionProtectedError,
    ImportCollectionMismatchError,
    ImportNotFoundError,
    VocabularyDatabase,
)
from vocabulary_quality import VocabularyQualityPolicy
from vocabulary_service import (
    SubtitleNoEnglishError,
    import_vocabulary,
    import_vocabulary_file,
    remove_vocabulary_import,
)


SCHEMA_VERSION = 1
SORT_VALUES = (
    "word_asc",
    "word_desc",
    "count_desc",
    "count_asc",
    "frequency_desc",
    "frequency_asc",
)
IMPORT_SORT_VALUES = (
    "word_asc",
    "word_desc",
    "import_count_desc",
    "import_count_asc",
    "total_count_desc",
    "total_count_asc",
    "frequency_desc",
    "frequency_asc",
)


class InvalidArgumentError(ValueError):
    pass


class TaskNotFoundError(FileNotFoundError):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InvalidArgumentError(message)


def project_root() -> Path:
    configured = os.environ.get("SUBTITLE_TOOL_ROOT")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1]


def _quality_database(path: str | Path) -> VocabularyDatabase:
    dictionary = DictionaryLookup()
    frequency = FrequencyLookup()
    return VocabularyDatabase(
        path,
        quality_policy=VocabularyQualityPolicy(dictionary, frequency),
    )


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        help="覆盖默认的 用户数据/vocabulary.sqlite3",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="将同一 JSON 以 UTF-8 BOM 原子写入文件",
    )


def _add_collection_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--collection-id",
        type=int,
        help="目标词汇表；省略时兼容性使用 is_default=1 的默认词汇表",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="vocab_cli.py")
    subparsers = parser.add_subparsers(
        dest="action", required=True, parser_class=JsonArgumentParser
    )

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("task_folder", type=Path)
    _add_collection_argument(import_parser)
    _add_common_arguments(import_parser)

    import_file_parser = subparsers.add_parser("import-file")
    import_file_parser.add_argument("subtitle_file", type=Path)
    _add_collection_argument(import_file_parser)
    _add_common_arguments(import_file_parser)

    query_parser = subparsers.add_parser("query")
    query_parser.add_argument("--search", default="")
    query_parser.add_argument("--sort", choices=SORT_VALUES, default="word_asc")
    query_parser.add_argument("--limit", type=int, default=200)
    query_parser.add_argument("--offset", type=int, default=0)
    _add_collection_argument(query_parser)
    _add_common_arguments(query_parser)

    query_import_parser = subparsers.add_parser("query-import")
    query_import_parser.add_argument("--import-id", type=int, required=True)
    query_import_parser.add_argument("--search", default="")
    query_import_parser.add_argument(
        "--sort", choices=IMPORT_SORT_VALUES, default="import_count_desc"
    )
    query_import_parser.add_argument("--limit", type=int, default=200)
    query_import_parser.add_argument("--offset", type=int, default=0)
    _add_collection_argument(query_import_parser)
    _add_common_arguments(query_import_parser)

    remove_import_parser = subparsers.add_parser("remove-import")
    remove_import_parser.add_argument("--import-id", type=int, required=True)
    _add_collection_argument(remove_import_parser)
    _add_common_arguments(remove_import_parser)

    collection_list_parser = subparsers.add_parser("collection-list")
    _add_common_arguments(collection_list_parser)

    collection_create_parser = subparsers.add_parser("collection-create")
    collection_create_parser.add_argument("--name", required=True)
    _add_common_arguments(collection_create_parser)

    collection_rename_parser = subparsers.add_parser("collection-rename")
    collection_rename_parser.add_argument("--collection-id", type=int, required=True)
    collection_rename_parser.add_argument("--name", required=True)
    _add_common_arguments(collection_rename_parser)

    collection_delete_parser = subparsers.add_parser("collection-delete")
    collection_delete_parser.add_argument("--collection-id", type=int, required=True)
    _add_common_arguments(collection_delete_parser)
    return parser


def _json_text(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _write_json_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _emit(payload: dict[str, object], output_json: Path | None) -> None:
    text = _json_text(payload)
    if output_json is not None:
        _write_json_atomic(output_json, text)
    print(text, flush=True)


def _error_code(exception: BaseException) -> str:
    if isinstance(exception, SubtitleNoEnglishError):
        return "SUBTITLE_NO_ENGLISH"
    if isinstance(exception, ImportNotFoundError):
        return "IMPORT_NOT_FOUND"
    if isinstance(exception, ImportCollectionMismatchError):
        return "COLLECTION_IMPORT_MISMATCH"
    if isinstance(exception, CollectionNotFoundError):
        return "COLLECTION_NOT_FOUND"
    if isinstance(exception, CollectionNameConflictError):
        return "COLLECTION_NAME_CONFLICT"
    if isinstance(exception, DefaultCollectionProtectedError):
        return "DEFAULT_COLLECTION_PROTECTED"
    if isinstance(exception, (InvalidArgumentError, ValueError)):
        return "INVALID_ARGUMENT"
    if isinstance(exception, TaskNotFoundError):
        return "TASK_NOT_FOUND"
    if isinstance(exception, SubtitleFileNotFoundError):
        return "SUBTITLE_FILE_NOT_FOUND"
    if isinstance(exception, SubtitleFormatUnsupportedError):
        return "SUBTITLE_FORMAT_UNSUPPORTED"
    if isinstance(exception, SubtitleContentEmptyError):
        return "SUBTITLE_CONTENT_EMPTY"
    if isinstance(exception, SubtitleSourceAmbiguousError):
        return "SUBTITLE_SOURCE_AMBIGUOUS"
    if isinstance(exception, (SubtitleNotFoundError, SubtitleSourceError)):
        return "SUBTITLE_NOT_FOUND"
    if isinstance(exception, DictionaryResourceError):
        return "DICTIONARY_RESOURCE_ERROR"
    if isinstance(exception, FrequencyResourceError):
        return "FREQUENCY_RESOURCE_ERROR"
    if isinstance(exception, (sqlite3.DatabaseError, OSError)):
        return "DATABASE_ERROR"
    return "INTERNAL_ERROR"


def _failure_payload(action: str | None, exception: BaseException) -> dict[str, object]:
    message = str(exception).strip() or exception.__class__.__name__
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": action,
        "error_code": _error_code(exception),
        "message": message,
    }


def _action_hint(arguments: Sequence[str]) -> str | None:
    if arguments and arguments[0] in {
        "import", "import-file", "query", "query-import", "remove-import",
        "collection-list", "collection-create", "collection-rename",
        "collection-delete",
    }:
        return arguments[0]
    return None


def _output_path_hint(arguments: Sequence[str]) -> Path | None:
    for index in range(len(arguments) - 1, -1, -1):
        if arguments[index] == "--output-json" and index + 1 < len(arguments):
            return Path(arguments[index + 1])
    return None


def _collection_fields(collection) -> dict[str, object]:
    return {
        "collection_id": int(collection.collection_id),
        "collection_name": str(collection.name),
    }


def run(
    argv: Sequence[str] | None = None,
    *,
    import_function: Callable[..., object] | None = None,
    import_file_function: Callable[..., object] | None = None,
    database_factory: Callable[[str | Path], VocabularyDatabase] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    action = _action_hint(arguments)
    output_json = _output_path_hint(arguments)
    try:
        options = build_parser().parse_args(arguments)
        action = options.action
        output_json = options.output_json
        database_path = options.database or (
            project_root() / "用户数据" / "vocabulary.sqlite3"
        )

        if action == "import":
            if not options.task_folder.is_dir():
                raise TaskNotFoundError(f"字幕任务目录不存在：{options.task_folder}")
            database = VocabularyDatabase(database_path)
            collection = database.resolve_collection(options.collection_id)
            importer = import_function or import_vocabulary
            result = importer(
                options.task_folder,
                database_path,
                collection_id=collection.collection_id,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "import",
                **_collection_fields(collection),
                "duplicate": bool(result.duplicate),
                "import_id": int(result.import_id),
                "token_count": int(result.token_count),
                "unique_word_count": int(result.unique_word_count),
            }
        elif action == "import-file":
            database = VocabularyDatabase(database_path)
            collection = database.resolve_collection(options.collection_id)
            importer = import_file_function or import_vocabulary_file
            result = importer(
                options.subtitle_file,
                database_path,
                collection_id=collection.collection_id,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "import-file",
                **_collection_fields(collection),
                "duplicate": bool(result.duplicate),
                "import_id": int(result.import_id),
                "token_count": int(result.token_count),
                "unique_word_count": int(result.unique_word_count),
            }
        elif action == "query":
            factory = database_factory or _quality_database
            database = factory(database_path)
            collection = database.resolve_collection(options.collection_id)
            result = database.query_words(
                search=options.search,
                sort=options.sort,
                limit=options.limit,
                offset=options.offset,
                collection_id=collection.collection_id,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "query",
                **_collection_fields(collection),
                "total": result.total,
                "limit": result.limit,
                "offset": result.offset,
                "rows": result.rows,
            }
        elif action == "query-import":
            factory = database_factory or _quality_database
            database = factory(database_path)
            collection = database.resolve_collection(options.collection_id)
            result = database.query_import(
                options.import_id,
                search=options.search,
                sort=options.sort,
                limit=options.limit,
                offset=options.offset,
                collection_id=collection.collection_id,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "query-import",
                **_collection_fields(collection),
                "import_id": options.import_id,
                "total": result.total,
                "limit": result.limit,
                "offset": result.offset,
                "rows": result.rows,
            }
        elif action == "remove-import":
            database = VocabularyDatabase(database_path)
            collection = database.resolve_collection(options.collection_id)
            result = remove_vocabulary_import(
                options.import_id,
                database_path,
                collection_id=collection.collection_id,
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "remove-import",
                **_collection_fields(collection),
                "import_id": result.import_id,
                "removed_word_count": result.removed_word_count,
                "removed_token_count": result.removed_token_count,
            }
        elif action == "collection-list":
            database = VocabularyDatabase(database_path)
            collection = database.resolve_collection()
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "collection-list",
                **_collection_fields(collection),
                "collections": [
                    {
                        "collection_id": item.collection_id,
                        "name": item.name,
                        "is_default": item.is_default,
                    }
                    for item in database.list_collections()
                ],
            }
        elif action == "collection-create":
            database = VocabularyDatabase(database_path)
            collection = database.create_collection(options.name)
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "collection-create",
                **_collection_fields(collection),
            }
        elif action == "collection-rename":
            database = VocabularyDatabase(database_path)
            collection = database.rename_collection(
                options.collection_id, options.name
            )
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "collection-rename",
                **_collection_fields(collection),
            }
        else:
            database = VocabularyDatabase(database_path)
            deleted = database.resolve_collection(options.collection_id)
            database.delete_collection(deleted.collection_id)
            collection = database.resolve_collection()
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "collection-delete",
                **_collection_fields(collection),
                "deleted_collection_id": deleted.collection_id,
                "deleted_collection_name": deleted.name,
            }
    except Exception as exception:
        payload = _failure_payload(action, exception)
        try:
            _emit(payload, output_json)
        except OSError:
            print(_json_text(payload), flush=True)
        return 1

    try:
        _emit(payload, output_json)
    except OSError as exception:
        print(_json_text(_failure_payload(action, exception)), flush=True)
        return 1
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
