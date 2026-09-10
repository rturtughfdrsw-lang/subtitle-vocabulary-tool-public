import json
from pathlib import Path
import subprocess
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HELPERS = PROJECT_ROOT / "runtime" / "vocabulary_ui_helpers.ps1"


def invoke_powershell(body: str):
    helper = str(HELPERS).replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop'; "
        f". '{helper}'; "
        "$result = & { " + body + " }; "
        "$result | ConvertTo-Json -Depth 10 -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return json.loads(completed.stdout.decode("utf-8-sig"))


def test_sort_mapping_and_cli_arguments_are_stable_and_reset_offset():
    result = invoke_powershell(
        "[ordered]@{ "
        "sorts = @(0..5 | ForEach-Object { Get-VocabularySortValue $_ }); "
        "importSorts = @(0..7 | ForEach-Object { Get-VocabularyImportSortValue $_ }); "
        "import = New-VocabularyCliArguments -Action import "
        "-TaskFolder 'task-folder' -CollectionId 3 -OutputJson 'temp-folder/out.json'; "
        "importFile = New-VocabularyCliArguments -Action import-file "
        "-SubtitleFile 'subtitles/existing.srt' -CollectionId 3 "
        "-OutputJson 'temp-folder/out.json'; "
        "query = New-VocabularyCliArguments -Action query -Search \"rid %_' text\" "
        "-Sort frequency_desc -Limit 200 -Offset 400 -CollectionId 3 "
        "-OutputJson 'temp-folder/out.json'; "
        "reset = New-VocabularyCliArguments -Action query -Search 'new' "
        "-Sort count_desc -Limit 200 -Offset 400 -ResetOffset "
        "-OutputJson 'temp-folder/out.json'; "
        "queryImport = New-VocabularyCliArguments -Action query-import "
        "-ImportId 17 -Search 'aroma %_' -ImportSort frequency_desc -CollectionId 3 "
        "-Limit 200 -Offset 400 "
        "-OutputJson 'temp-folder/out.json'; "
        "resetImport = New-VocabularyCliArguments -Action query-import "
        "-ImportId 17 -Search 'new' -ImportSort word_desc "
        "-Limit 200 -Offset 400 -ResetOffset "
        "-OutputJson 'temp-folder/out.json'; "
        "removeImport = New-VocabularyCliArguments -Action remove-import "
        "-ImportId 17 -CollectionId 3 -OutputJson 'temp-folder/out.json'; "
        "collectionList = New-VocabularyCliArguments -Action collection-list "
        "-OutputJson 'temp-folder/out.json'; "
        "collectionCreate = New-VocabularyCliArguments -Action collection-create "
        "-CollectionName ' Sitcom ' -OutputJson 'temp-folder/out.json'; "
        "collectionRename = New-VocabularyCliArguments -Action collection-rename "
        "-CollectionId 3 -CollectionName 'Comedy' -OutputJson 'temp-folder/out.json'; "
        "collectionDelete = New-VocabularyCliArguments -Action collection-delete "
        "-CollectionId 3 -OutputJson 'temp-folder/out.json'; "
        "quoted = ConvertTo-VocabularyCommandLineArgument 'say \"hello\"' "
        "}"
    )
    assert result["sorts"] == [
        "word_asc",
        "word_desc",
        "count_desc",
        "count_asc",
        "frequency_desc",
        "frequency_asc",
    ]
    assert result["importSorts"] == [
        "word_asc",
        "word_desc",
        "import_count_desc",
        "import_count_asc",
        "total_count_desc",
        "total_count_asc",
        "frequency_desc",
        "frequency_asc",
    ]
    assert result["import"] == (
        'import task-folder --collection-id 3 --output-json temp-folder/out.json'
    )
    assert result["importFile"] == (
        'import-file subtitles/existing.srt --collection-id 3 '
        '--output-json temp-folder/out.json'
    )
    assert result["query"] == (
        'query --search "rid %_\' text" --sort frequency_desc --limit 200 '
        '--offset 400 --collection-id 3 --output-json temp-folder/out.json'
    )
    assert "--offset 0" in result["reset"]
    assert result["queryImport"] == (
        'query-import --import-id 17 --search "aroma %_" --sort frequency_desc '
        '--limit 200 --offset 400 --collection-id 3 '
        '--output-json temp-folder/out.json'
    )
    assert "--search new --sort word_desc" in result["resetImport"]
    assert "--offset 0" in result["resetImport"]
    assert result["removeImport"] == (
        'remove-import --import-id 17 --collection-id 3 --output-json temp-folder/out.json'
    )
    assert result["collectionList"].startswith("collection-list --output-json")
    assert result["collectionCreate"] == (
        'collection-create --name " Sitcom " --output-json temp-folder/out.json'
    )
    assert result["collectionRename"] == (
        'collection-rename --collection-id 3 --name Comedy '
        '--output-json temp-folder/out.json'
    )
    assert result["collectionDelete"] == (
        'collection-delete --collection-id 3 --output-json temp-folder/out.json'
    )
    assert result["quoted"] == '"say \\"hello\\""'


def test_page_state_covers_empty_first_middle_and_last_pages():
    pages = invoke_powershell(
        "@("
        "Get-VocabularyPageState -Total 0 -Limit 200 -Offset 0; "
        "Get-VocabularyPageState -Total 401 -Limit 200 -Offset 0; "
        "Get-VocabularyPageState -Total 401 -Limit 200 -Offset 200; "
        "Get-VocabularyPageState -Total 401 -Limit 200 -Offset 400"
        ")"
    )
    assert pages == [
        {
            "PageNumber": 0,
            "PageCount": 0,
            "HasPrevious": False,
            "HasNext": False,
            "PageText": "第 0 / 0 页",
        },
        {
            "PageNumber": 1,
            "PageCount": 3,
            "HasPrevious": False,
            "HasNext": True,
            "PageText": "第 1 / 3 页",
        },
        {
            "PageNumber": 2,
            "PageCount": 3,
            "HasPrevious": True,
            "HasNext": True,
            "PageText": "第 2 / 3 页",
        },
        {
            "PageNumber": 3,
            "PageCount": 3,
            "HasPrevious": True,
            "HasNext": False,
            "PageText": "第 3 / 3 页",
        },
    ]


def test_query_view_formats_chinese_nulls_and_empty_database():
    result = invoke_powershell(
        "$payload = '{\"schema_version\":1,\"ok\":true,\"action\":\"query\","
        "\"total\":2,\"limit\":200,\"offset\":0,\"rows\":["
        "{\"word\":\"actually\",\"meaning\":\"实际上；其实\","
        "\"total_count\":17,\"english_frequency\":5.49},"
        "{\"word\":\"gpt5\",\"meaning\":null,\"total_count\":3,"
        "\"english_frequency\":null}]}' | ConvertFrom-Json; "
        "$empty = '{\"schema_version\":1,\"ok\":true,\"action\":\"query\","
        "\"total\":0,\"limit\":200,\"offset\":0,\"rows\":[]}' | ConvertFrom-Json; "
        "[ordered]@{ populated = ConvertTo-VocabularyQueryView $payload; "
        "empty = ConvertTo-VocabularyQueryView $empty }"
    )
    assert result["populated"]["Rows"] == [
        {
            "Word": "actually",
            "Meaning": "实际上；其实",
            "TotalCount": 17,
            "EnglishFrequency": "5.49（很常见）",
        },
        {
            "Word": "gpt5",
            "Meaning": "未收录",
            "TotalCount": 3,
            "EnglishFrequency": "—",
        },
    ]
    assert result["populated"]["StatusText"] == "共 2 条词汇记录"
    assert result["populated"]["IsEmpty"] is False
    assert result["empty"]["Rows"] == []
    assert result["empty"]["StatusText"] == "暂无词汇记录"
    assert result["empty"]["IsEmpty"] is True


def test_frequency_labels_and_import_result_view_are_display_only():
    result = invoke_powershell(
        "$values = @(6.0,5.77,5.0,4.82,4.0,3.0,2.0,1.67,$null); "
        "$formatted = @($values | ForEach-Object { Format-VocabularyFrequency $_ }); "
        "$payload = '{\"schema_version\":1,\"ok\":true,\"action\":\"query-import\","
        "\"import_id\":7,\"total\":2,\"limit\":200,\"offset\":0,\"rows\":["
        "{\"word\":\"actually\",\"meaning\":\"其实\",\"import_count\":5,"
        "\"total_count\":17,\"english_frequency\":5.77},"
        "{\"word\":\"aromantic\",\"meaning\":null,\"import_count\":2,"
        "\"total_count\":2,\"english_frequency\":null}]}' | ConvertFrom-Json; "
        "[ordered]@{ formatted = $formatted; view = ConvertTo-VocabularyImportQueryView $payload }"
    )
    assert result["formatted"] == [
        "6.00（极常见）",
        "5.77（很常见）",
        "5.00（很常见）",
        "4.82（常见）",
        "4.00（常见）",
        "3.00（较少见）",
        "2.00（少见）",
        "1.67（非常少见）",
        "—",
    ]
    assert result["view"]["Rows"] == [
        {
            "Word": "actually",
            "Meaning": "其实",
            "ImportCount": 5,
            "TotalCount": 17,
            "EnglishFrequency": "5.77（很常见）",
        },
        {
            "Word": "aromantic",
            "Meaning": "未收录",
            "ImportCount": 2,
            "TotalCount": 2,
            "EnglishFrequency": "—",
        },
    ]
    assert result["view"]["ImportId"] == 7
    assert result["view"]["StatusText"] == "本次共 2 个单词"


def test_import_outcomes_errors_and_action_guard_are_user_facing():
    result = invoke_powershell(
        "$success = '{\"ok\":true,\"action\":\"import\",\"duplicate\":false,\"collection_name\":\"Sitcom\"}' | ConvertFrom-Json; "
        "$duplicate = '{\"ok\":true,\"action\":\"import\",\"duplicate\":true,\"collection_name\":\"Sitcom\"}' | ConvertFrom-Json; "
        "$failure = '{\"ok\":false,\"action\":\"import\","
        "\"error_code\":\"DICTIONARY_RESOURCE_ERROR\",\"message\":\"broken\"}' | ConvertFrom-Json; "
        "$removed = '{\"ok\":true,\"action\":\"remove-import\","
        "\"import_id\":7,\"removed_word_count\":2,\"removed_token_count\":9}' | ConvertFrom-Json; "
        "$removeFailed = '{\"ok\":false,\"action\":\"remove-import\","
        "\"error_code\":\"IMPORT_NOT_FOUND\",\"message\":\"gone\"}' | ConvertFrom-Json; "
        "$codes = @('INVALID_ARGUMENT','TASK_NOT_FOUND','SUBTITLE_NOT_FOUND',"
        "'SUBTITLE_FILE_NOT_FOUND','SUBTITLE_FORMAT_UNSUPPORTED',"
        "'SUBTITLE_CONTENT_EMPTY','SUBTITLE_NO_ENGLISH',"
        "'SUBTITLE_SOURCE_AMBIGUOUS','DICTIONARY_RESOURCE_ERROR',"
        "'FREQUENCY_RESOURCE_ERROR','IMPORT_NOT_FOUND','COLLECTION_NOT_FOUND',"
        "'COLLECTION_NAME_CONFLICT','DEFAULT_COLLECTION_PROTECTED',"
        "'COLLECTION_IMPORT_MISMATCH','DATABASE_ERROR','INTERNAL_ERROR'); "
        "[ordered]@{ success = Get-VocabularyImportOutcome $success; "
        "duplicate = Get-VocabularyImportOutcome $duplicate; "
        "failure = Get-VocabularyImportOutcome $failure; "
        "removed = Get-VocabularyRemoveOutcome $removed; "
        "removeFailed = Get-VocabularyRemoveOutcome $removeFailed; "
        "confirmYes = Test-VocabularyRemoveConfirmation 'Yes'; "
        "confirmNo = Test-VocabularyRemoveConfirmation 'No'; "
        "errors = @($codes | ForEach-Object { Get-VocabularyErrorMessage $_ 'detail' }); "
        "idle = Test-VocabularyActionCanStart $false; "
        "busy = Test-VocabularyActionCanStart $true }"
    )
    assert result["success"]["ShouldQuery"] is True
    assert result["success"]["IsDuplicate"] is False
    assert result["duplicate"]["ShouldQuery"] is True
    assert result["duplicate"]["IsDuplicate"] is True
    assert result["duplicate"]["Message"] == "词汇表“Sitcom”中已统计过该字幕，本次没有重复累计。"
    assert result["failure"]["ShouldQuery"] is False
    assert "词典" in result["failure"]["Message"]
    assert result["removed"] == {
        "ShouldRefresh": True,
        "ImportId": 7,
        "Message": "本次导入已撤销（2 个单词，共 9 次出现）。",
    }
    assert result["removeFailed"]["ShouldRefresh"] is False
    assert "找不到" in result["removeFailed"]["Message"]
    assert result["confirmYes"] is True
    assert result["confirmNo"] is False
    assert len(result["errors"]) == 17
    assert all(isinstance(message, str) and message for message in result["errors"])
    assert result["idle"] is True
    assert result["busy"] is False


def test_collection_list_view_identifies_default_and_selected_collection():
    result = invoke_powershell(
        "$payload = '{\"ok\":true,\"action\":\"collection-list\","
        "\"collection_id\":4,\"collection_name\":\"默认词汇表\",\"collections\":["
        "{\"collection_id\":4,\"name\":\"默认词汇表\",\"is_default\":true},"
        "{\"collection_id\":9,\"name\":\"Sitcom\",\"is_default\":false}]}' | ConvertFrom-Json; "
        "ConvertTo-VocabularyCollectionListView $payload 9"
    )
    assert result["DefaultCollectionId"] == 4
    assert result["SelectedCollectionId"] == 9
    assert result["Collections"][1]["Name"] == "Sitcom"
    assert result["CanDeleteSelected"] is True


def test_output_cleanup_removes_existing_file_and_accepts_missing_file():
    with tempfile.TemporaryDirectory(prefix="vocab-ui-cleanup-") as temp:
        output = Path(temp) / "output.json"
        output.write_text("{}", encoding="utf-8")
        escaped = str(output).replace("'", "''")
        result = invoke_powershell(
            f"Remove-VocabularyOutputFile '{escaped}'; "
            f"Remove-VocabularyOutputFile '{escaped}'; "
            f"[ordered]@{{ exists = Test-Path -LiteralPath '{escaped}' }}"
        )
        assert result == {"exists": False}
        assert not output.exists()


def test_subtitle_open_file_dialog_only_selects_supported_files():
    result = invoke_powershell(
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$dialog = New-VocabularySubtitleOpenFileDialog; "
        "try { [ordered]@{ filter = $dialog.Filter; check = $dialog.CheckFileExists; "
        "multi = $dialog.Multiselect; title = $dialog.Title } } "
        "finally { $dialog.Dispose() }"
    )
    assert result == {
        "filter": "字幕文件 (*.srt;*.vtt;*.ass;*.txt)|*.srt;*.vtt;*.ass;*.txt",
        "check": True,
        "multi": False,
        "title": "选择要统计的字幕文件",
    }


if __name__ == "__main__":
    test_sort_mapping_and_cli_arguments_are_stable_and_reset_offset()
    test_page_state_covers_empty_first_middle_and_last_pages()
    test_query_view_formats_chinese_nulls_and_empty_database()
    test_frequency_labels_and_import_result_view_are_display_only()
    test_import_outcomes_errors_and_action_guard_are_user_facing()
    test_collection_list_view_identifies_default_and_selected_collection()
    test_output_cleanup_removes_existing_file_and_accepts_missing_file()
    test_subtitle_open_file_dialog_only_selects_supported_files()
    print("PASS: vocabulary UI helper tests")
