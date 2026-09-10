import json
from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_ROOT / "runtime" / "launcher.ps1"


def build_gui_snapshot():
    launcher = str(LAUNCHER).replace("'", "''")
    project_root = str(PROJECT_ROOT).replace("'", "''")
    replacement = r"""
$script:VocabularyBusy = $true
$form.Show()
$startupTimer.Stop()
$mainTabs.SelectedTab = $vocabularyTab
[Windows.Forms.Application]::DoEvents()
$expandButton = $vocabularyTab.Controls.Find('VocabularyExpand',$true)[0]
$normalBounds = $form.Bounds
$normalState = [string]$form.WindowState
$expandButton.PerformClick()
[Windows.Forms.Application]::DoEvents()
$expandedState = [string]$form.WindowState
$expandedText = $expandButton.Text
$expandedGridInside = (
    $vocabularyGrid.Right -le $vocabularyTab.ClientSize.Width -and
    $vocabularyGrid.Bottom -le $vocabularyTab.ClientSize.Height
)
$expandButton.PerformClick()
[Windows.Forms.Application]::DoEvents()
$restoredState = [string]$form.WindowState
$restoredBounds = $form.Bounds
$restoredText = $expandButton.Text
$form.WindowState = [Windows.Forms.FormWindowState]::Maximized
[Windows.Forms.Application]::DoEvents()
$expandButton.PerformClick()
$expandButton.PerformClick()
[Windows.Forms.Application]::DoEvents()
$maximizedRestoreState = [string]$form.WindowState
$importWindow = New-VocabularyImportResultWindow -Duplicate $false -CollectionName 'Sitcom'
$importWindow.Show($form)
[Windows.Forms.Application]::DoEvents()
$importGrid = $importWindow.Controls.Find('ImportResultGrid',$true)[0]
$importSearch = $importWindow.Controls.Find('ImportResultSearch',$true)[0]
$importSearchButton = $importWindow.Controls.Find('ImportResultSearchButton',$true)[0]
$importSort = $importWindow.Controls.Find('ImportResultSort',$true)[0]
$importPrevious = $importWindow.Controls.Find('ImportResultPrevious',$true)[0]
$importNext = $importWindow.Controls.Find('ImportResultNext',$true)[0]
$importRemove = $importWindow.Controls.Find('ImportResultRemove',$true)[0]
$duplicateWindow = New-VocabularyImportResultWindow -Duplicate $true -CollectionName 'Sitcom'
$duplicateNotice = $duplicateWindow.Controls.Find('ImportResultNotice',$true)[0]
$duplicateRemove = $duplicateWindow.Controls.Find('ImportResultRemove',$true)[0]
function Test-ControlInsideClient($Control,$Container) {
    return (
        $Control.Left -ge 0 -and
        $Control.Top -ge 0 -and
        $Control.Right -le $Container.ClientSize.Width -and
        $Control.Bottom -le $Container.ClientSize.Height
    )
}
[pscustomobject][ordered]@{
    FormWidth = $form.Width
    FormHeight = $form.Height
    Tabs = @($mainTabs.TabPages | ForEach-Object Text)
    ExtractionButtons = @($extractTab.Controls | Where-Object {
        $_ -is [Windows.Forms.Button]
    } | ForEach-Object Text)
    AnalyzeEnabled = $analyze.Enabled
    VocabularyColumns = @($vocabularyGrid.Columns | ForEach-Object {
        [pscustomobject]@{ Header = $_.HeaderText; SortMode = [string]$_.SortMode }
    })
    SortItems = @($vocabularySort.Items | ForEach-Object { [string]$_ })
    VocabularyTimerInterval = $vocabularyTimer.Interval
    VocabularyLimit = $script:VocabularyLimit
    VocabularyTabHasSearch = $vocabularyTab.Controls.Contains($vocabularySearch)
    VocabularyTabHasGrid = $vocabularyTab.Controls.Contains($vocabularyGrid)
    VocabularyButtons = @($vocabularyTab.Controls | Where-Object {
        $_ -is [Windows.Forms.Button]
    } | ForEach-Object Text)
    CollectionSelectorParent = $vocabularyCollection.Parent.Text
    CollectionButtons = @($vocabularyCollectionCreate,$vocabularyCollectionRename,$vocabularyCollectionDelete | ForEach-Object Text)
    CollectionSelectorInsideClient = Test-ControlInsideClient $vocabularyCollection $vocabularyTab
    CollectionCreateInsideClient = Test-ControlInsideClient $vocabularyCollectionCreate $vocabularyTab
    CollectionDeleteInsideClient = Test-ControlInsideClient $vocabularyCollectionDelete $vocabularyTab
    ExpandButtonParent = $expandButton.Parent.Text
    NormalState = $normalState
    ExpandedState = $expandedState
    ExpandedText = $expandedText
    ExpandedGridInside = $expandedGridInside
    RestoredState = $restoredState
    RestoredBoundsMatch = ($restoredBounds -eq $normalBounds)
    RestoredText = $restoredText
    MaximizedRestoreState = $maximizedRestoreState
    ImportButtonVisible = $vocabularyImportFile.Visible
    ImportButtonInsideClient = Test-ControlInsideClient $vocabularyImportFile $vocabularyTab
    ImportButtonParent = $vocabularyImportFile.Parent.Text
    ImportButtonAboveSearch = $vocabularyImportFile.Bottom -le $vocabularySearchLabel.Top
    ImportButtonLocation = [pscustomobject]@{
        X = $vocabularyImportFile.Left
        Y = $vocabularyImportFile.Top
    }
    PreviousButtonInsideClient = Test-ControlInsideClient $vocabularyPrevious $vocabularyTab
    NextButtonInsideClient = Test-ControlInsideClient $vocabularyNext $vocabularyTab
    ImportResultTitle = $importWindow.Text
    ImportResultCollection = $importWindow.Controls.Find('ImportResultCollection',$true)[0].Text
    ImportResultOwnerTitle = $importWindow.Owner.Text
    ImportResultColumns = @($importGrid.Columns | ForEach-Object HeaderText)
    ImportResultSortItems = @($importSort.Items | ForEach-Object { [string]$_ })
    ImportResultSearchInsideClient = Test-ControlInsideClient $importSearch $importWindow
    ImportResultSearchButtonInsideClient = Test-ControlInsideClient $importSearchButton $importWindow
    ImportResultSortInsideClient = Test-ControlInsideClient $importSort $importWindow
    ImportResultSearchAboveGrid = $importSearch.Bottom -le $importGrid.Top
    ImportResultPreviousInsideClient = Test-ControlInsideClient $importPrevious $importWindow
    ImportResultNextInsideClient = Test-ControlInsideClient $importNext $importWindow
    ImportResultRemoveText = $importRemove.Text
    ImportResultRemoveEnabled = $importRemove.Enabled
    ImportResultRemoveParent = $importRemove.Parent.Text
    ImportResultRemoveInsideClient = Test-ControlInsideClient $importRemove $importWindow
    DuplicateRemoveEnabled = $duplicateRemove.Enabled
    DuplicateResultTitle = $duplicateWindow.Text
    DuplicateNotice = $duplicateNotice.Text
    RemovedState = $(
        Set-VocabularyImportRemovedState $importWindow
        [pscustomobject]@{
            Title = $importWindow.Text
            Notice = $importWindow.Controls.Find('ImportResultNotice',$true)[0].Text
            RemoveEnabled = $importRemove.Enabled
        }
    )
} | ConvertTo-Json -Depth 6 -Compress
$duplicateWindow.Dispose()
$importWindow.Close()
$importWindow.Dispose()
$script:VocabularyBusy = $false
$form.Close()
""".strip()
    escaped_replacement = replacement.replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
        f"$env:SUBTITLE_TOOL_ROOT='{project_root}'; "
        f"$source=Get-Content -LiteralPath '{launcher}' -Raw -Encoding UTF8; "
        f"$source=$source.Replace('[void]$form.ShowDialog()','{escaped_replacement}'); "
        "Invoke-Expression $source"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return json.loads(completed.stdout.decode("utf-8-sig"))


def test_launcher_builds_two_tabs_and_preserves_extraction_actions():
    snapshot = build_gui_snapshot()
    assert snapshot["Tabs"] == ["字幕提取", "词汇统计"]
    assert snapshot["ExtractionButtons"] == [
        "开始处理",
        "取消任务",
        "打开结果",
        "分析词汇",
        "清理缓存",
        "查看详细日志 ▾",
    ]
    assert snapshot["AnalyzeEnabled"] is False
    assert snapshot["FormWidth"] >= 780


def test_vocabulary_tab_uses_read_only_server_sorted_grid_and_fixed_page_size():
    snapshot = build_gui_snapshot()
    assert snapshot["VocabularyColumns"] == [
        {"Header": "单词", "SortMode": "NotSortable"},
        {"Header": "中文释义", "SortMode": "NotSortable"},
        {"Header": "累计出现次数", "SortMode": "NotSortable"},
        {"Header": "英语总体词频", "SortMode": "NotSortable"},
    ]
    assert snapshot["SortItems"] == [
        "单词 A-Z",
        "单词 Z-A",
        "累计次数 高→低",
        "累计次数 低→高",
        "英语词频 高→低",
        "英语词频 低→高",
    ]
    assert snapshot["VocabularyLimit"] == 200
    assert snapshot["VocabularyTimerInterval"] <= 250
    assert snapshot["VocabularyTabHasSearch"] is True
    assert snapshot["VocabularyTabHasGrid"] is True
    assert "导入字幕文件" in snapshot["VocabularyButtons"]


def test_vocabulary_actions_are_visible_inside_the_shown_tab_client_area():
    snapshot = build_gui_snapshot()
    assert snapshot["ImportButtonVisible"] is True
    assert snapshot["ImportButtonInsideClient"] is True
    assert snapshot["ImportButtonParent"] == "词汇统计"
    assert snapshot["ImportButtonAboveSearch"] is True
    assert snapshot["PreviousButtonInsideClient"] is True
    assert snapshot["NextButtonInsideClient"] is True
    assert snapshot["CollectionSelectorParent"] == "词汇统计"
    assert snapshot["CollectionButtons"] == ["新建", "重命名", "删除"]
    assert snapshot["CollectionSelectorInsideClient"] is True
    assert snapshot["CollectionCreateInsideClient"] is True
    assert snapshot["CollectionDeleteInsideClient"] is True


def test_vocabulary_table_can_expand_and_restore_without_losing_layout():
    snapshot = build_gui_snapshot()
    assert snapshot["ExpandButtonParent"] == "词汇统计"
    assert snapshot["NormalState"] == "Normal"
    assert snapshot["ExpandedState"] == "Maximized"
    assert snapshot["ExpandedText"] == "恢复窗口"
    assert snapshot["ExpandedGridInside"] is True
    assert snapshot["RestoredState"] == "Normal"
    assert snapshot["RestoredBoundsMatch"] is True
    assert snapshot["RestoredText"] == "放大表格"
    assert snapshot["MaximizedRestoreState"] == "Maximized"


def test_import_result_window_has_five_columns_pagination_and_duplicate_notice():
    snapshot = build_gui_snapshot()
    assert snapshot["ImportResultTitle"] == "本次导入结果"
    assert snapshot["ImportResultOwnerTitle"] == "网页视频处理工具"
    assert snapshot["ImportResultCollection"] == "词汇表：Sitcom"
    assert snapshot["ImportResultColumns"] == [
        "单词",
        "中文释义",
        "本次出现次数",
        "历史累计次数",
        "英语总体词频",
    ]
    assert snapshot["ImportResultSortItems"] == [
        "单词 A-Z",
        "单词 Z-A",
        "本次次数 高→低",
        "本次次数 低→高",
        "历史累计 高→低",
        "历史累计 低→高",
        "英语词频 高→低",
        "英语词频 低→高",
    ]
    assert snapshot["ImportResultSearchInsideClient"] is True
    assert snapshot["ImportResultSearchButtonInsideClient"] is True
    assert snapshot["ImportResultSortInsideClient"] is True
    assert snapshot["ImportResultSearchAboveGrid"] is True
    assert snapshot["ImportResultPreviousInsideClient"] is True
    assert snapshot["ImportResultNextInsideClient"] is True
    assert snapshot["ImportResultRemoveText"] == "撤销本次导入"
    assert snapshot["ImportResultRemoveEnabled"] is True
    assert snapshot["ImportResultRemoveParent"] == "本次导入结果"
    assert snapshot["ImportResultRemoveInsideClient"] is True
    assert snapshot["DuplicateRemoveEnabled"] is False
    assert snapshot["DuplicateResultTitle"] == "历史导入结果（本次未重复累计）"
    assert "没有再次累计" in snapshot["DuplicateNotice"]
    assert snapshot["RemovedState"] == {
        "Title": "本次导入已撤销",
        "Notice": "本次导入已撤销；历史总词库已刷新。",
        "RemoveEnabled": False,
    }


if __name__ == "__main__":
    test_launcher_builds_two_tabs_and_preserves_extraction_actions()
    test_vocabulary_tab_uses_read_only_server_sorted_grid_and_fixed_page_size()
    test_vocabulary_actions_are_visible_inside_the_shown_tab_client_area()
    test_vocabulary_table_can_expand_and_restore_without_losing_layout()
    test_import_result_window_has_five_columns_pagination_and_duplicate_notice()
    print("PASS: vocabulary GUI layout tests")
