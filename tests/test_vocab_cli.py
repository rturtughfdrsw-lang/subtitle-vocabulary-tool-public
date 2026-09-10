from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / "runtime"
PYTHON = Path(sys.executable)
CLI = RUNTIME_ROOT / "vocab_cli.py"
sys.path.insert(0, str(RUNTIME_ROOT))


def run_cli(*arguments: object, root: Path | None = None):
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if root is None:
        environment.pop("SUBTITLE_TOOL_ROOT", None)
    else:
        environment["SUBTITLE_TOOL_ROOT"] = str(root)
    completed = subprocess.run(
        [str(PYTHON), str(CLI), *(str(argument) for argument in arguments)],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"CLI stdout was not JSON: rc={completed.returncode}, "
            f"stdout={completed.stdout!r}, stderr={completed.stderr!r}"
        ) from exc
    assert completed.stderr == ""
    return completed, payload


def write_ocr_task(folder: Path, text: str) -> None:
    folder.mkdir(parents=True)
    (folder / "status.json").write_text(
        json.dumps({"ok": True, "type": "硬字幕 OCR"}), encoding="utf-8"
    )
    (folder / "硬字幕OCR文字.txt").write_text(text, encoding="utf-8")


def seed_query_database(path: Path) -> None:
    from vocabulary_db import VocabularyDatabase

    database = VocabularyDatabase(path)
    database.import_counts(
        content_hash="b" * 64,
        source_task_folder="seed",
        source_type="硬字幕 OCR",
        source_file_names=("硬字幕OCR文字.txt",),
        tokenizer_version="en-v1",
        counts={"actually": 4, "obnoxious": 2, "unlisted": 1},
        meanings={"actually": "实际上；其实", "obnoxious": "令人讨厌的"},
        frequencies={"actually": 5.49, "obnoxious": 3.38, "unlisted": None},
        frequency_source="wordfreq",
        frequency_version="3.1.1",
    )


def test_import_success_and_duplicate_are_versioned_json_and_exit_zero():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-import-") as temp:
        root = Path(temp)
        task = root / "task"
        database = root / "data" / "vocabulary.sqlite3"
        write_ocr_task(task, "Actually actually obnoxious.")

        first, first_payload = run_cli(
            "import", task, "--database", database
        )
        duplicate, duplicate_payload = run_cli(
            "import", task, "--database", database
        )

        assert first.returncode == duplicate.returncode == 0
        assert first_payload == {
            "schema_version": 1,
            "ok": True,
            "action": "import",
            "collection_id": 1,
            "collection_name": "默认词汇表",
            "duplicate": False,
            "import_id": 1,
            "token_count": 3,
            "unique_word_count": 2,
        }
        assert duplicate_payload == {
            **first_payload,
            "duplicate": True,
        }

        query, query_payload = run_cli(
            "query", "--database", database, "--sort", "word_asc"
        )
        assert query.returncode == 0
        assert [(row["word"], row["total_count"]) for row in query_payload["rows"]] == [
            ("actually", 2), ("obnoxious", 1)
        ]


def test_query_json_preserves_null_and_resolves_default_database_from_root():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-root-") as temp:
        root = Path(temp)
        database = root / "用户数据" / "vocabulary.sqlite3"
        seed_query_database(database)

        completed, payload = run_cli(
            "query", "--search", "list", "--sort", "frequency_asc",
            "--limit", 5, "--offset", 0, root=root
        )
        assert completed.returncode == 0
        assert payload == {
            "schema_version": 1,
            "ok": True,
            "action": "query",
            "collection_id": 1,
            "collection_name": "默认词汇表",
            "total": 0,
            "limit": 5,
            "offset": 0,
            "rows": [],
        }


def test_query_import_returns_stable_page_and_missing_import_error():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-query-import-") as temp:
        root = Path(temp)
        database = root / "vocabulary.sqlite3"
        seed_query_database(database)

        completed, payload = run_cli(
            "query-import", "--import-id", 1, "--limit", 2, "--offset", 1,
            "--database", database,
        )
        assert completed.returncode == 0
        assert payload == {
            "schema_version": 1,
            "ok": True,
            "action": "query-import",
            "collection_id": 1,
            "collection_name": "默认词汇表",
            "import_id": 1,
            "total": 2,
            "limit": 2,
            "offset": 1,
            "rows": [{
                    "word": "obnoxious",
                    "meaning": "令人讨厌的",
                    "import_count": 2,
                    "total_count": 2,
                    "english_frequency": 3.38,
                }],
        }

        filtered, filtered_payload = run_cli(
            "query-import", "--import-id", 1, "--search", "LIST",
            "--sort", "word_desc", "--database", database,
        )
        assert filtered.returncode == 0
        assert filtered_payload["total"] == 0
        assert filtered_payload["rows"] == []

        sorts = (
            "word_asc", "word_desc", "import_count_desc", "import_count_asc",
            "total_count_desc", "total_count_asc", "frequency_desc", "frequency_asc",
        )
        for sort in sorts:
            sorted_result, sorted_payload = run_cli(
                "query-import", "--import-id", 1, "--sort", sort,
                "--database", database,
            )
            assert sorted_result.returncode == 0, sort
            assert sorted_payload["total"] == 2, sort

        missing, missing_payload = run_cli(
            "query-import", "--import-id", 999, "--database", database
        )
        assert missing.returncode == 1
        assert missing_payload["action"] == "query-import"
        assert missing_payload["error_code"] == "IMPORT_NOT_FOUND"


def test_remove_import_returns_stable_counts_and_allows_same_content_reimport():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-remove-") as temp:
        root = Path(temp)
        database = root / "vocabulary.sqlite3"
        task = root / "task"
        write_ocr_task(task, "Actually actually obnoxious.")
        imported, imported_payload = run_cli("import", task, "--database", database)
        assert imported.returncode == 0

        removed, removed_payload = run_cli(
            "remove-import", "--import-id", imported_payload["import_id"],
            "--database", database,
        )
        assert removed.returncode == 0
        assert removed_payload == {
            "schema_version": 1,
            "ok": True,
            "action": "remove-import",
            "collection_id": 1,
            "collection_name": "默认词汇表",
            "import_id": imported_payload["import_id"],
            "removed_word_count": 2,
            "removed_token_count": 3,
        }

        missing, missing_payload = run_cli(
            "remove-import", "--import-id", imported_payload["import_id"],
            "--database", database,
        )
        assert missing.returncode == 1
        assert missing_payload["action"] == "remove-import"
        assert missing_payload["error_code"] == "IMPORT_NOT_FOUND"

        reimported, reimported_payload = run_cli(
            "import", task, "--database", database
        )
        assert reimported.returncode == 0
        assert reimported_payload["duplicate"] is False


def test_output_json_is_atomic_utf8_bom_and_powerShell_51_reads_chinese():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-json-") as temp:
        root = Path(temp)
        database = root / "vocabulary.sqlite3"
        output = root / "nested" / "query.json"
        seed_query_database(database)

        completed, stdout_payload = run_cli(
            "query", "--database", database, "--sort", "word_asc",
            "--output-json", output
        )
        assert completed.returncode == 0
        raw = output.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert json.loads(raw.decode("utf-8-sig")) == stdout_payload
        assert not list(output.parent.glob(f".{output.name}.*.tmp"))

        escaped_output = str(output).replace("'", "''")
        command = (
            f"$j = Get-Content -LiteralPath '{escaped_output}' -Raw | ConvertFrom-Json; "
            "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
            "[Console]::Write(($j.rows | ForEach-Object meaning | "
            "Where-Object { $_ }) -join '|')"
        )
        powershell = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True, check=False
        )
        assert powershell.returncode == 0, powershell.stderr.decode(errors="replace")
        assert powershell.stdout.decode("utf-8") == "实际上；其实|令人讨厌的"


def test_import_reports_task_subtitle_and_ambiguity_errors_without_tracebacks():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-errors-") as temp:
        root = Path(temp)
        database = root / "vocabulary.sqlite3"

        missing, missing_payload = run_cli(
            "import", root / "missing", "--database", database
        )
        assert missing.returncode == 1
        assert missing_payload["error_code"] == "TASK_NOT_FOUND"

        no_subtitle = root / "no-subtitle"
        no_subtitle.mkdir()
        (no_subtitle / "status.json").write_text(
            json.dumps({"ok": True, "type": "硬字幕 OCR"}), encoding="utf-8"
        )
        absent, absent_payload = run_cli(
            "import", no_subtitle, "--database", database
        )
        assert absent.returncode == 1
        assert absent_payload["error_code"] == "SUBTITLE_NOT_FOUND"

        ambiguous = root / "ambiguous"
        ambiguous.mkdir()
        (ambiguous / "status.json").write_text(
            json.dumps({"ok": True, "type": "网页原字幕"}), encoding="utf-8"
        )
        (ambiguous / "first.srt").write_text("First", encoding="utf-8")
        (ambiguous / "second.srt").write_text("Second", encoding="utf-8")
        conflict, conflict_payload = run_cli(
            "import", ambiguous, "--database", database
        )
        assert conflict.returncode == 1
        assert conflict_payload["error_code"] == "SUBTITLE_SOURCE_AMBIGUOUS"

        for payload in (missing_payload, absent_payload, conflict_payload):
            assert payload["schema_version"] == 1
            assert payload["ok"] is False
            assert payload["action"] == "import"
            assert isinstance(payload["message"], str) and payload["message"]


def test_import_file_success_duplicate_and_content_errors_are_stable():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-file-") as temp:
        root = Path(temp)
        database = root / "vocabulary.sqlite3"
        subtitle = root / "existing.srt"
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:01,000\nActually actually.\n",
            encoding="utf-8",
        )

        first, first_payload = run_cli(
            "import-file", subtitle, "--database", database
        )
        duplicate, duplicate_payload = run_cli(
            "import-file", subtitle, "--database", database
        )
        assert first.returncode == duplicate.returncode == 0
        assert first_payload["action"] == "import-file"
        assert first_payload["duplicate"] is False
        assert first_payload["token_count"] == 2
        assert duplicate_payload["duplicate"] is True

        missing, missing_payload = run_cli(
            "import-file", root / "missing.srt", "--database", database
        )
        unsupported_file = root / "subtitle.json"
        unsupported_file.write_text("English text.", encoding="utf-8")
        unsupported, unsupported_payload = run_cli(
            "import-file", unsupported_file, "--database", database
        )
        empty_file = root / "empty.txt"
        empty_file.write_text(" \r\n\t", encoding="utf-8")
        empty, empty_payload = run_cli(
            "import-file", empty_file, "--database", database
        )
        chinese_file = root / "chinese.txt"
        chinese_file.write_text("这是有效字幕正文。", encoding="utf-8")
        no_english, no_english_payload = run_cli(
            "import-file", chinese_file, "--database", database
        )

        errors = (
            (missing, missing_payload, "SUBTITLE_FILE_NOT_FOUND"),
            (unsupported, unsupported_payload, "SUBTITLE_FORMAT_UNSUPPORTED"),
            (empty, empty_payload, "SUBTITLE_CONTENT_EMPTY"),
            (no_english, no_english_payload, "SUBTITLE_NO_ENGLISH"),
        )
        for completed, payload, expected_code in errors:
            assert completed.returncode == 1
            assert payload["action"] == "import-file"
            assert payload["error_code"] == expected_code


def test_invalid_argument_and_database_error_are_stable_json():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-invalid-") as temp:
        root = Path(temp)
        invalid_output = root / "invalid.json"
        invalid, invalid_payload = run_cli(
            "query", "--sort", "not-a-sort", "--output-json", invalid_output
        )
        assert invalid.returncode == 1
        assert invalid_payload["error_code"] == "INVALID_ARGUMENT"
        assert json.loads(invalid_output.read_text(encoding="utf-8-sig")) == invalid_payload

        database_directory = root / "database-directory"
        database_directory.mkdir()
        failed, failed_payload = run_cli(
            "query", "--database", database_directory
        )
        assert failed.returncode == 1
        assert failed_payload["error_code"] == "DATABASE_ERROR"


def test_collection_cli_crud_scoping_and_implicit_default_compatibility():
    with tempfile.TemporaryDirectory(prefix="vocab-cli-collections-") as temp:
        root = Path(temp)
        database = root / "vocabulary.sqlite3"
        subtitle = root / "subtitle.txt"
        subtitle.write_text("Actually actually.", encoding="utf-8")

        listed, listed_payload = run_cli(
            "collection-list", "--database", database
        )
        assert listed.returncode == 0
        assert listed_payload["collection_name"] == "默认词汇表"
        assert listed_payload["collections"] == [{
            "collection_id": listed_payload["collection_id"],
            "name": "默认词汇表",
            "is_default": True,
        }]
        default_id = listed_payload["collection_id"]

        created, created_payload = run_cli(
            "collection-create", "--name", "  Sitcom  ", "--database", database
        )
        assert created.returncode == 0
        other_id = created_payload["collection_id"]
        assert created_payload["collection_name"] == "Sitcom"

        default_import, default_payload = run_cli(
            "import-file", subtitle, "--database", database
        )
        other_import, other_payload = run_cli(
            "import-file", subtitle, "--collection-id", other_id,
            "--database", database,
        )
        other_duplicate, duplicate_payload = run_cli(
            "import-file", subtitle, "--collection-id", other_id,
            "--database", database,
        )
        assert default_import.returncode == other_import.returncode == 0
        assert default_payload["collection_id"] == default_id
        assert other_payload["collection_id"] == other_id
        assert other_payload["duplicate"] is False
        assert other_duplicate.returncode == 0
        assert duplicate_payload["duplicate"] is True

        mismatch, mismatch_payload = run_cli(
            "query-import", "--import-id", other_payload["import_id"],
            "--collection-id", default_id, "--database", database,
        )
        assert mismatch.returncode == 1
        assert mismatch_payload["error_code"] == "COLLECTION_IMPORT_MISMATCH"

        renamed, renamed_payload = run_cli(
            "collection-rename", "--collection-id", other_id,
            "--name", "Comedy", "--database", database,
        )
        assert renamed.returncode == 0
        assert renamed_payload["collection_name"] == "Comedy"

        protected, protected_payload = run_cli(
            "collection-delete", "--collection-id", default_id,
            "--database", database,
        )
        assert protected.returncode == 1
        assert protected_payload["error_code"] == "DEFAULT_COLLECTION_PROTECTED"

        deleted, deleted_payload = run_cli(
            "collection-delete", "--collection-id", other_id,
            "--database", database,
        )
        assert deleted.returncode == 0
        assert deleted_payload["collection_id"] == default_id
        assert deleted_payload["collection_name"] == "默认词汇表"


def test_resource_and_internal_exceptions_map_to_stable_error_json():
    import vocab_cli
    from dictionary_lookup import DictionaryResourceError
    from frequency_lookup import FrequencyResourceError

    with tempfile.TemporaryDirectory(prefix="vocab-cli-mapped-") as temp:
        task = Path(temp) / "task"
        task.mkdir()
        database = Path(temp) / "vocabulary.sqlite3"
        cases = (
            (DictionaryResourceError("dictionary broken"), "DICTIONARY_RESOURCE_ERROR"),
            (FrequencyResourceError("frequency broken"), "FREQUENCY_RESOURCE_ERROR"),
            (RuntimeError("unexpected"), "INTERNAL_ERROR"),
        )
        for exception, expected_code in cases:
            def fail_import(*_args, error=exception, **_kwargs):
                raise error

            stream = io.StringIO()
            with redirect_stdout(stream):
                exit_code = vocab_cli.run(
                    ["import", str(task), "--database", str(database)],
                    import_function=fail_import,
                )
            payload = json.loads(stream.getvalue())
            assert exit_code == 1
            assert payload["error_code"] == expected_code
            assert payload["ok"] is False


def test_project_root_falls_back_to_cli_location_without_environment_variable():
    import vocab_cli

    previous = os.environ.pop("SUBTITLE_TOOL_ROOT", None)
    try:
        assert vocab_cli.project_root() == PROJECT_ROOT
    finally:
        if previous is not None:
            os.environ["SUBTITLE_TOOL_ROOT"] = previous


if __name__ == "__main__":
    test_import_success_and_duplicate_are_versioned_json_and_exit_zero()
    test_query_json_preserves_null_and_resolves_default_database_from_root()
    test_query_import_returns_stable_page_and_missing_import_error()
    test_remove_import_returns_stable_counts_and_allows_same_content_reimport()
    test_output_json_is_atomic_utf8_bom_and_powerShell_51_reads_chinese()
    test_import_reports_task_subtitle_and_ambiguity_errors_without_tracebacks()
    test_import_file_success_duplicate_and_content_errors_are_stable()
    test_invalid_argument_and_database_error_are_stable_json()
    test_collection_cli_crud_scoping_and_implicit_default_compatibility()
    test_resource_and_internal_exceptions_map_to_stable_error_json()
    test_project_root_falls_back_to_cli_location_without_environment_variable()
    print("PASS: vocabulary CLI protocol tests")
