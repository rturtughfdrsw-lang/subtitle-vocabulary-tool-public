$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName Microsoft.VisualBasic

$Root = if ($env:SUBTITLE_TOOL_ROOT) { $env:SUBTITLE_TOOL_ROOT } else { Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$Core = Join-Path $Root 'runtime'
$Result = Join-Path $Root '字幕结果'
$Python = Join-Path $Core 'Python312\python.exe'
$Worker = Join-Path $Core 'worker.py'
$CacheTool = Join-Path $Core 'cache_maintenance.py'
$UiHelpers = Join-Path $Core 'ui_helpers.ps1'
$VocabularyUiHelpers = Join-Path $Core 'vocabulary_ui_helpers.ps1'
$VocabularyCli = Join-Path $Core 'vocab_cli.py'
$MediaToolsResolver = Join-Path $Core 'media_tools.py'
$YtDlp = Join-Path $Core 'bin\yt-dlp.exe'
$Resolver = Join-Path $Core 'resolve_url.py'
$FailureLogs = Join-Path $Root '失败日志'
. $UiHelpers
. $VocabularyUiHelpers
New-Item -ItemType Directory -Force -Path $Result | Out-Null

function Read-JsonShared($Path) {
    $stream = $null
    $reader = $null
    try {
        $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        $stream = [IO.FileStream]::new($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,$share)
        $reader = [IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true)
        $text = $reader.ReadToEnd()
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return $text | ConvertFrom-Json
    } finally {
        if ($reader) { $reader.Dispose() }
        elseif ($stream) { $stream.Dispose() }
    }
}

function New-Label($text, $x, $y, $size, $bold=$false) {
    $item = New-Object Windows.Forms.Label
    $item.Text = $text; $item.Location = New-Object Drawing.Point($x,$y); $item.AutoSize = $true
    $style = if ($bold) { [Drawing.FontStyle]::Bold } else { [Drawing.FontStyle]::Regular }
    $item.Font = New-Object Drawing.Font('Microsoft YaHei UI',$size,$style)
    return $item
}

$form = New-Object Windows.Forms.Form
$form.Text = '网页视频处理工具'
$form.Size = New-Object Drawing.Size(800,430)
$form.StartPosition = 'CenterScreen'
$form.BackColor = [Drawing.Color]::White
$form.FormBorderStyle = 'FixedSingle'; $form.MaximizeBox = $false

$mainTabs = New-Object Windows.Forms.TabControl
$mainTabs.Location = New-Object Drawing.Point(8,8); $mainTabs.Size = New-Object Drawing.Size(764,340)
$mainTabs.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
$form.Controls.Add($mainTabs)
$extractTab = New-Object Windows.Forms.TabPage
$extractTab.Text = '字幕提取'; $extractTab.BackColor = [Drawing.Color]::White
$vocabularyTab = New-Object Windows.Forms.TabPage
$vocabularyTab.Text = '词汇统计'; $vocabularyTab.BackColor = [Drawing.Color]::White
$mainTabs.TabPages.Add($extractTab); $mainTabs.TabPages.Add($vocabularyTab)

$title = New-Label '网页视频处理' 32 24 20 $true
$extractTab.Controls.Add($title)
$hint = New-Label '粘贴视频页面地址，选择完整字幕、极速字幕、MP3 音频或 MP4 视频。' 34 68 10
$hint.ForeColor = [Drawing.Color]::FromArgb(95,99,104); $extractTab.Controls.Add($hint)
$urlLabel = New-Label '视频地址' 34 108 9 $true; $extractTab.Controls.Add($urlLabel)

$urlBox = New-Object Windows.Forms.TextBox
$urlBox.Location = New-Object Drawing.Point(34,134); $urlBox.Size = New-Object Drawing.Size(696,32)
$urlBox.Font = New-Object Drawing.Font('Microsoft YaHei UI',11); $extractTab.Controls.Add($urlBox)

$modeBox = New-Object Windows.Forms.ComboBox
$modeBox.Location = New-Object Drawing.Point(34,190); $modeBox.Size = New-Object Drawing.Size(176,34)
$modeBox.DropDownStyle = 'DropDownList'; $modeBox.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
Get-ModeItems | ForEach-Object { [void]$modeBox.Items.Add($_) }; $modeBox.SelectedIndex = 0
$extractTab.Controls.Add($modeBox)

$start = New-Object Windows.Forms.Button
$start.Text = '开始处理'; $start.Location = New-Object Drawing.Point(220,184); $start.Size = New-Object Drawing.Size(150,46)
$start.Font = New-Object Drawing.Font('Microsoft YaHei UI',11,[Drawing.FontStyle]::Bold)
$start.BackColor = [Drawing.Color]::FromArgb(26,115,232); $start.ForeColor = [Drawing.Color]::White
$start.FlatStyle = 'Flat'; $start.FlatAppearance.BorderSize = 0; $extractTab.Controls.Add($start)

$cancel = New-Object Windows.Forms.Button
$cancel.Text = '取消任务'; $cancel.Location = New-Object Drawing.Point(380,184); $cancel.Size = New-Object Drawing.Size(105,46)
$cancel.Font = New-Object Drawing.Font('Microsoft YaHei UI',10); $cancel.Enabled = $false
$extractTab.Controls.Add($cancel)

$open = New-Object Windows.Forms.Button
$open.Text = '打开结果'; $open.Location = New-Object Drawing.Point(495,184); $open.Size = New-Object Drawing.Size(105,46)
$open.Font = New-Object Drawing.Font('Microsoft YaHei UI',10); $extractTab.Controls.Add($open)

$analyze = New-Object Windows.Forms.Button
$analyze.Text = '分析词汇'; $analyze.Location = New-Object Drawing.Point(610,184); $analyze.Size = New-Object Drawing.Size(120,46)
$analyze.Font = New-Object Drawing.Font('Microsoft YaHei UI',10,[Drawing.FontStyle]::Bold)
$analyze.Enabled = $false; $extractTab.Controls.Add($analyze)

$progress = New-Object Windows.Forms.ProgressBar
$progress.Location = New-Object Drawing.Point(34,252); $progress.Size = New-Object Drawing.Size(696,18)
$progress.Minimum = 0; $progress.Maximum = 100; $progress.Style = 'Continuous'; $extractTab.Controls.Add($progress)

$elapsed = New-Label '已用时 00:00:00' 590 232 9
$elapsed.ForeColor = [Drawing.Color]::FromArgb(95,99,104); $extractTab.Controls.Add($elapsed)

$status = New-Label '正在加载…' 34 282 10 $true
$status.AutoSize = $false; $status.Size = New-Object Drawing.Size(420,28)
$status.ForeColor = [Drawing.Color]::FromArgb(60,64,67); $extractTab.Controls.Add($status)
$cleanup = New-Object Windows.Forms.Button
$cleanup.Text = '清理缓存'; $cleanup.Location = New-Object Drawing.Point(460,280); $cleanup.Size = New-Object Drawing.Size(115,30)
$cleanup.Font = New-Object Drawing.Font('Microsoft YaHei UI',9); $extractTab.Controls.Add($cleanup)
$details = New-Object Windows.Forms.Button
$details.Text = '查看详细日志 ▾'; $details.Location = New-Object Drawing.Point(590,280); $details.Size = New-Object Drawing.Size(140,30)
$details.FlatStyle = 'Flat'; $details.FlatAppearance.BorderSize = 0; $details.BackColor = [Drawing.Color]::White
$extractTab.Controls.Add($details)

$log = New-Object Windows.Forms.TextBox
$log.Location = New-Object Drawing.Point(34,330); $log.Size = New-Object Drawing.Size(696,235)
$log.Multiline = $true; $log.ScrollBars = 'Vertical'; $log.ReadOnly = $true; $log.Visible = $false
$log.Font = New-Object Drawing.Font('Consolas',9); $extractTab.Controls.Add($log)

$vocabularyCollectionLabel = New-Label '词汇表' 24 10 9 $true
$vocabularyTab.Controls.Add($vocabularyCollectionLabel)
$vocabularyCollection = New-Object Windows.Forms.ComboBox
$vocabularyCollection.Name = 'VocabularyCollection'
$vocabularyCollection.Location = New-Object Drawing.Point(88,7); $vocabularyCollection.Size = New-Object Drawing.Size(250,30)
$vocabularyCollection.DropDownStyle = 'DropDownList'; $vocabularyCollection.DisplayMember = 'Name'
$vocabularyTab.Controls.Add($vocabularyCollection)
$vocabularyCollectionCreate = New-Object Windows.Forms.Button
$vocabularyCollectionCreate.Text = '新建'; $vocabularyCollectionCreate.Location = New-Object Drawing.Point(350,5)
$vocabularyCollectionCreate.Size = New-Object Drawing.Size(70,30); $vocabularyTab.Controls.Add($vocabularyCollectionCreate)
$vocabularyCollectionRename = New-Object Windows.Forms.Button
$vocabularyCollectionRename.Text = '重命名'; $vocabularyCollectionRename.Location = New-Object Drawing.Point(430,5)
$vocabularyCollectionRename.Size = New-Object Drawing.Size(70,30); $vocabularyTab.Controls.Add($vocabularyCollectionRename)
$vocabularyCollectionDelete = New-Object Windows.Forms.Button
$vocabularyCollectionDelete.Text = '删除'; $vocabularyCollectionDelete.Location = New-Object Drawing.Point(510,5)
$vocabularyCollectionDelete.Size = New-Object Drawing.Size(70,30); $vocabularyTab.Controls.Add($vocabularyCollectionDelete)

$vocabularyImportFile = New-Object Windows.Forms.Button
$vocabularyImportFile.Text = '导入字幕文件'; $vocabularyImportFile.Location = New-Object Drawing.Point(24,44)
$vocabularyImportFile.Size = New-Object Drawing.Size(140,30)
$vocabularyImportFile.Font = New-Object Drawing.Font('Microsoft YaHei UI',9)
$vocabularyImportFile.Enabled = $false
$vocabularyTab.Controls.Add($vocabularyImportFile)

$vocabularyExpand = New-Object Windows.Forms.Button
$vocabularyExpand.Name = 'VocabularyExpand'; $vocabularyExpand.Text = '放大表格'
$vocabularyExpand.Location = New-Object Drawing.Point(584,44)
$vocabularyExpand.Size = New-Object Drawing.Size(140,30)
$vocabularyExpand.Font = New-Object Drawing.Font('Microsoft YaHei UI',9)
$vocabularyTab.Controls.Add($vocabularyExpand)

$vocabularySearchLabel = New-Label '英文单词搜索' 24 84 9 $true
$vocabularyTab.Controls.Add($vocabularySearchLabel)
$vocabularySearch = New-Object Windows.Forms.TextBox
$vocabularySearch.Location = New-Object Drawing.Point(24,108); $vocabularySearch.Size = New-Object Drawing.Size(300,30)
$vocabularySearch.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
$vocabularyTab.Controls.Add($vocabularySearch)
$vocabularySearchButton = New-Object Windows.Forms.Button
$vocabularySearchButton.Text = '搜索'; $vocabularySearchButton.Location = New-Object Drawing.Point(334,106)
$vocabularySearchButton.Size = New-Object Drawing.Size(80,34); $vocabularySearchButton.Font = New-Object Drawing.Font('Microsoft YaHei UI',9)
$vocabularyTab.Controls.Add($vocabularySearchButton)
$vocabularySortLabel = New-Label '排序' 430 84 9 $true
$vocabularyTab.Controls.Add($vocabularySortLabel)
$vocabularySort = New-Object Windows.Forms.ComboBox
$vocabularySort.Location = New-Object Drawing.Point(430,108); $vocabularySort.Size = New-Object Drawing.Size(270,30)
$vocabularySort.DropDownStyle = 'DropDownList'; $vocabularySort.Font = New-Object Drawing.Font('Microsoft YaHei UI',9)
@('单词 A-Z','单词 Z-A','累计次数 高→低','累计次数 低→高','英语词频 高→低','英语词频 低→高') |
    ForEach-Object { [void]$vocabularySort.Items.Add($_) }
$vocabularySort.SelectedIndex = 0; $vocabularyTab.Controls.Add($vocabularySort)

$vocabularyGrid = New-Object Windows.Forms.DataGridView
$vocabularyGrid.Location = New-Object Drawing.Point(24,150); $vocabularyGrid.Size = New-Object Drawing.Size(700,112)
$vocabularyGrid.ReadOnly = $true
$vocabularyGrid.AllowUserToAddRows = $false; $vocabularyGrid.AllowUserToDeleteRows = $false
$vocabularyGrid.AllowUserToResizeRows = $false; $vocabularyGrid.AutoGenerateColumns = $false
$vocabularyGrid.RowHeadersVisible = $false; $vocabularyGrid.MultiSelect = $false
$vocabularyGrid.SelectionMode = 'FullRowSelect'; $vocabularyGrid.BackgroundColor = [Drawing.Color]::White
$vocabularyGrid.BorderStyle = 'Fixed3D'; $vocabularyGrid.Font = New-Object Drawing.Font('Microsoft YaHei UI',9)
$columnSpecs = @(
    @('Word','单词',145),
    @('Meaning','中文释义',335),
    @('TotalCount','累计出现次数',110),
    @('EnglishFrequency','英语总体词频',105)
)
foreach ($spec in $columnSpecs) {
    $column = New-Object Windows.Forms.DataGridViewTextBoxColumn
    $column.Name = $spec[0]; $column.HeaderText = $spec[1]; $column.Width = $spec[2]
    $column.SortMode = [Windows.Forms.DataGridViewColumnSortMode]::NotSortable
    [void]$vocabularyGrid.Columns.Add($column)
}
$vocabularyTab.Controls.Add($vocabularyGrid)

$vocabularyPrevious = New-Object Windows.Forms.Button
$vocabularyPrevious.Text = '上一页'; $vocabularyPrevious.Location = New-Object Drawing.Point(24,272)
$vocabularyPrevious.Size = New-Object Drawing.Size(90,30)
$vocabularyPrevious.Enabled = $false; $vocabularyTab.Controls.Add($vocabularyPrevious)
$vocabularyPage = New-Label '第 0 / 0 页' 130 278 9
$vocabularyPage.AutoSize = $false; $vocabularyPage.Size = New-Object Drawing.Size(150,24)
$vocabularyTab.Controls.Add($vocabularyPage)
$vocabularyStatus = New-Label '暂无词汇记录' 300 278 9
$vocabularyStatus.AutoSize = $false; $vocabularyStatus.Size = New-Object Drawing.Size(290,24)
$vocabularyStatus.ForeColor = [Drawing.Color]::FromArgb(95,99,104); $vocabularyTab.Controls.Add($vocabularyStatus)
$vocabularyNext = New-Object Windows.Forms.Button
$vocabularyNext.Text = '下一页'; $vocabularyNext.Location = New-Object Drawing.Point(634,272)
$vocabularyNext.Size = New-Object Drawing.Size(90,30)
$vocabularyNext.Enabled = $false; $vocabularyTab.Controls.Add($vocabularyNext)

$script:CurrentProcess = $null
$script:CurrentFolder = $null
$script:CurrentLog = $null
$script:DetailsOpen = $false
$script:LastSuccessFolder = $null
$script:LastSuccessType = $null
$script:CloseAfterCancel = $false
$script:CancelRequested = $false
$script:TaskStartedAt = $null
$script:VocabularyProcess = $null
$script:VocabularyOutputPath = $null
$script:VocabularyAction = $null
$script:VocabularyBusy = $false
$script:VocabularyLimit = 200
$script:VocabularyOffset = 0
$script:VocabularyHasPrevious = $false
$script:VocabularyHasNext = $false
$script:VocabularyImportId = 0
$script:VocabularyImportOffset = 0
$script:VocabularyImportHasPrevious = $false
$script:VocabularyImportHasNext = $false
$script:VocabularyImportDuplicate = $false
$script:VocabularyImportPending = $false
$script:VocabularyImportWindow = $null
$script:VocabularyCollectionId = 0
$script:VocabularyCollectionName = ''
$script:VocabularyDefaultCollectionId = 0
$script:VocabularyCollectionsLoaded = $false
$script:VocabularyCollectionChanging = $false
$script:VocabularyPreferredCollectionId = 0
$script:VocabularyImportCollectionId = 0
$script:VocabularyImportCollectionName = ''
$script:VocabularyExpanded = $false
$script:VocabularySavedWindowState = [Windows.Forms.FormWindowState]::Normal
$script:VocabularySavedBounds = [Drawing.Rectangle]::Empty
$script:VocabularyAvailable = Test-Path -LiteralPath $VocabularyCli -PathType Leaf

function Switch-VocabularyExpandedView {
    if (!$script:VocabularyExpanded) {
        $script:VocabularySavedWindowState = $form.WindowState
        $script:VocabularySavedBounds = $form.Bounds
        $script:VocabularyExpanded = $true
        $vocabularyExpand.Text = '恢复窗口'
        $form.WindowState = [Windows.Forms.FormWindowState]::Maximized
        return
    }

    $script:VocabularyExpanded = $false
    $vocabularyExpand.Text = '放大表格'
    if ($script:VocabularySavedWindowState -eq [Windows.Forms.FormWindowState]::Normal) {
        $form.WindowState = [Windows.Forms.FormWindowState]::Normal
        if (!$script:VocabularySavedBounds.IsEmpty) {
            $form.Bounds = $script:VocabularySavedBounds
        }
    } else {
        $form.WindowState = $script:VocabularySavedWindowState
    }
}

function Update-VocabularyControls {
    $subtitleBusy = $script:CurrentProcess -and !$script:CurrentProcess.HasExited
    $canAnalyze = (
        $script:VocabularyAvailable -and
        !$script:VocabularyBusy -and
        !$subtitleBusy -and
        $script:LastSuccessFolder -and
        (Test-Path -LiteralPath $script:LastSuccessFolder -PathType Container) -and
        $script:LastSuccessType -notin @('MP3 音频','MP4 视频')
    )
    $analyze.Enabled = [bool]$canAnalyze
    $vocabularySearch.Enabled = !$script:VocabularyBusy
    $vocabularySearchButton.Enabled = !$script:VocabularyBusy
    $vocabularySort.Enabled = !$script:VocabularyBusy
    $hasCollection = ($script:VocabularyCollectionId -gt 0)
    $vocabularyCollection.Enabled = (!$script:VocabularyBusy -and $script:VocabularyCollectionsLoaded)
    $vocabularyCollectionCreate.Enabled = ($script:VocabularyAvailable -and !$script:VocabularyBusy)
    $vocabularyCollectionRename.Enabled = (!$script:VocabularyBusy -and $hasCollection)
    $vocabularyCollectionDelete.Enabled = (
        !$script:VocabularyBusy -and $hasCollection -and
        $script:VocabularyCollectionId -ne $script:VocabularyDefaultCollectionId
    )
    $vocabularyImportFile.Enabled = ($script:VocabularyAvailable -and !$script:VocabularyBusy -and $hasCollection)
    $vocabularyPrevious.Enabled = (!$script:VocabularyBusy -and $script:VocabularyHasPrevious)
    $vocabularyNext.Enabled = (!$script:VocabularyBusy -and $script:VocabularyHasNext)
    if ($script:VocabularyImportWindow -and !$script:VocabularyImportWindow.IsDisposed) {
        $importPrevious = $script:VocabularyImportWindow.Controls.Find('ImportResultPrevious',$true)[0]
        $importNext = $script:VocabularyImportWindow.Controls.Find('ImportResultNext',$true)[0]
        $importSearch = $script:VocabularyImportWindow.Controls.Find('ImportResultSearch',$true)[0]
        $importSearchButton = $script:VocabularyImportWindow.Controls.Find('ImportResultSearchButton',$true)[0]
        $importSort = $script:VocabularyImportWindow.Controls.Find('ImportResultSort',$true)[0]
        $importRemove = $script:VocabularyImportWindow.Controls.Find('ImportResultRemove',$true)[0]
        if ($importPrevious) {
            $importPrevious.Enabled = (!$script:VocabularyBusy -and $script:VocabularyImportHasPrevious)
        }
        if ($importNext) {
            $importNext.Enabled = (!$script:VocabularyBusy -and $script:VocabularyImportHasNext)
        }
        if ($importSearch) { $importSearch.Enabled = !$script:VocabularyBusy }
        if ($importSearchButton) { $importSearchButton.Enabled = !$script:VocabularyBusy }
        if ($importSort) { $importSort.Enabled = !$script:VocabularyBusy }
        if ($importRemove) {
            $importRemove.Enabled = (
                !$script:VocabularyBusy -and
                [bool]$importRemove.Tag -and
                $importRemove.Text -eq '撤销本次导入'
            )
        }
    }
}

function Show-VocabularyCollections($Payload,[int]$PreferredCollectionId = 0) {
    $view = ConvertTo-VocabularyCollectionListView $Payload $PreferredCollectionId
    $script:VocabularyCollectionChanging = $true
    try {
        $vocabularyCollection.Items.Clear()
        foreach ($item in @($view.Collections)) { [void]$vocabularyCollection.Items.Add($item) }
        $selectedIndex = -1
        for ($index = 0; $index -lt $vocabularyCollection.Items.Count; $index++) {
            if ([int]$vocabularyCollection.Items[$index].CollectionId -eq [int]$view.SelectedCollectionId) {
                $selectedIndex = $index
                break
            }
        }
        if ($selectedIndex -ge 0) { $vocabularyCollection.SelectedIndex = $selectedIndex }
        $script:VocabularyCollectionId = [int]$view.SelectedCollectionId
        $selected = @($view.Collections | Where-Object CollectionId -eq $script:VocabularyCollectionId | Select-Object -First 1)
        $script:VocabularyCollectionName = if ($selected.Count) { [string]$selected[0].Name } else { '' }
        $script:VocabularyDefaultCollectionId = [int]$view.DefaultCollectionId
        $script:VocabularyCollectionsLoaded = $true
    } finally {
        $script:VocabularyCollectionChanging = $false
    }
    Update-VocabularyControls
}

function Set-VocabularyBusy([bool]$Busy) {
    $script:VocabularyBusy = $Busy
    Update-VocabularyControls
}

function Show-VocabularyQuery($Payload) {
    $view = ConvertTo-VocabularyQueryView $Payload
    $vocabularyGrid.Rows.Clear()
    foreach ($row in @($view.Rows)) {
        [void]$vocabularyGrid.Rows.Add(
            $row.Word,
            $row.Meaning,
            $row.TotalCount,
            $row.EnglishFrequency
        )
    }
    $script:VocabularyOffset = [int]$view.Offset
    $script:VocabularyHasPrevious = [bool]$view.HasPrevious
    $script:VocabularyHasNext = [bool]$view.HasNext
    $vocabularyPage.Text = $view.PageText
    $vocabularyStatus.Text = $view.StatusText
    $vocabularyStatus.ForeColor = [Drawing.Color]::FromArgb(95,99,104)
    Update-VocabularyControls
}

function Show-VocabularyFailure($Payload) {
    $message = Get-VocabularyErrorMessage ([string]$Payload.error_code) ([string]$Payload.message)
    $vocabularyStatus.Text = '词汇操作失败'
    $vocabularyStatus.ForeColor = [Drawing.Color]::Firebrick
    [Windows.Forms.MessageBox]::Show(
        $message,
        '词汇操作失败',
        [Windows.Forms.MessageBoxButtons]::OK,
        [Windows.Forms.MessageBoxIcon]::Warning
    ) | Out-Null
}

function Set-VocabularyImportRemovedState($Window) {
    if (!$Window -or $Window.IsDisposed) { return }
    $Window.Text = '本次导入已撤销'
    $notice = $Window.Controls.Find('ImportResultNotice',$true)[0]
    $remove = $Window.Controls.Find('ImportResultRemove',$true)[0]
    if ($notice) {
        $notice.Text = '本次导入已撤销；历史总词库已刷新。'
        $notice.ForeColor = [Drawing.Color]::FromArgb(95,99,104)
    }
    if ($remove) {
        $remove.Tag = $false
        $remove.Enabled = $false
    }
}

function New-VocabularyImportResultWindow {
    param(
        [bool]$Duplicate = $false,
        [string]$CollectionName = ''
    )

    $window = New-Object Windows.Forms.Form
    $window.Text = if ($Duplicate) { '历史导入结果（本次未重复累计）' } else { '本次导入结果' }
    $window.Size = New-Object Drawing.Size(820,500)
    $window.MinimumSize = New-Object Drawing.Size(720,420)
    $window.StartPosition = 'CenterParent'
    $window.Font = New-Object Drawing.Font('Microsoft YaHei UI',9)
    $window.ShowIcon = $false

    $collectionLabel = New-Object Windows.Forms.Label
    $collectionLabel.Name = 'ImportResultCollection'
    $collectionLabel.Location = New-Object Drawing.Point(20,14)
    $collectionLabel.Size = New-Object Drawing.Size(760,22)
    $collectionLabel.Text = '词汇表：' + $CollectionName
    $collectionLabel.Font = New-Object Drawing.Font('Microsoft YaHei UI',9,[Drawing.FontStyle]::Bold)
    $window.Controls.Add($collectionLabel)

    $notice = New-Object Windows.Forms.Label
    $notice.Name = 'ImportResultNotice'
    $notice.Location = New-Object Drawing.Point(20,40)
    $notice.Size = New-Object Drawing.Size(760,24)
    $notice.Text = if ($Duplicate) {
        '该字幕此前已经统计过，以下显示原导入结果；本次没有再次累计。'
    } else {
        '以下是本次字幕导入的词汇结果；历史累计次数包含此前所有成功导入。'
    }
    $notice.ForeColor = if ($Duplicate) { [Drawing.Color]::DarkOrange } else { [Drawing.Color]::FromArgb(95,99,104) }
    $notice.Anchor = 'Top,Left,Right'
    $window.Controls.Add($notice)

    $searchLabel = New-Object Windows.Forms.Label
    $searchLabel.Text = '英文单词搜索'; $searchLabel.Location = New-Object Drawing.Point(20,72)
    $searchLabel.Size = New-Object Drawing.Size(130,20); $window.Controls.Add($searchLabel)
    $search = New-Object Windows.Forms.TextBox
    $search.Name = 'ImportResultSearch'; $search.Location = New-Object Drawing.Point(20,94)
    $search.Size = New-Object Drawing.Size(300,30); $window.Controls.Add($search)
    $searchButton = New-Object Windows.Forms.Button
    $searchButton.Name = 'ImportResultSearchButton'; $searchButton.Text = '搜索'
    $searchButton.Location = New-Object Drawing.Point(330,92); $searchButton.Size = New-Object Drawing.Size(80,34)
    $window.Controls.Add($searchButton)
    $sortLabel = New-Object Windows.Forms.Label
    $sortLabel.Text = '排序'; $sortLabel.Location = New-Object Drawing.Point(500,72)
    $sortLabel.Size = New-Object Drawing.Size(80,20); $window.Controls.Add($sortLabel)
    $sortBox = New-Object Windows.Forms.ComboBox
    $sortBox.Name = 'ImportResultSort'; $sortBox.Location = New-Object Drawing.Point(500,94)
    $sortBox.Size = New-Object Drawing.Size(284,30); $sortBox.DropDownStyle = 'DropDownList'
    @('单词 A-Z','单词 Z-A','本次次数 高→低','本次次数 低→高','历史累计 高→低','历史累计 低→高','英语词频 高→低','英语词频 低→高') |
        ForEach-Object { [void]$sortBox.Items.Add($_) }
    $sortBox.SelectedIndex = 2
    $sortBox.Anchor = 'Top,Right'; $window.Controls.Add($sortBox)

    $grid = New-Object Windows.Forms.DataGridView
    $grid.Name = 'ImportResultGrid'
    $grid.Location = New-Object Drawing.Point(20,136)
    $grid.Size = New-Object Drawing.Size(764,262)
    $grid.ReadOnly = $true
    $grid.AllowUserToAddRows = $false; $grid.AllowUserToDeleteRows = $false
    $grid.AllowUserToResizeRows = $false; $grid.AutoGenerateColumns = $false
    $grid.RowHeadersVisible = $false; $grid.MultiSelect = $false
    $grid.SelectionMode = 'FullRowSelect'; $grid.BackgroundColor = [Drawing.Color]::White
    $grid.Anchor = 'Top,Bottom,Left,Right'
    $importColumnSpecs = @(
        @('Word','单词',130),
        @('Meaning','中文释义',280),
        @('ImportCount','本次出现次数',105),
        @('TotalCount','历史累计次数',105),
        @('EnglishFrequency','英语总体词频',125)
    )
    foreach ($spec in $importColumnSpecs) {
        $column = New-Object Windows.Forms.DataGridViewTextBoxColumn
        $column.Name = $spec[0]; $column.HeaderText = $spec[1]; $column.Width = $spec[2]
        $column.SortMode = [Windows.Forms.DataGridViewColumnSortMode]::NotSortable
        [void]$grid.Columns.Add($column)
    }
    $window.Controls.Add($grid)

    $previous = New-Object Windows.Forms.Button
    $previous.Name = 'ImportResultPrevious'; $previous.Text = '上一页'
    $previous.Location = New-Object Drawing.Point(20,410); $previous.Size = New-Object Drawing.Size(90,30)
    $previous.Anchor = 'Bottom,Left'; $previous.Enabled = $false
    $window.Controls.Add($previous)
    $page = New-Object Windows.Forms.Label
    $page.Name = 'ImportResultPage'; $page.Text = '第 0 / 0 页'
    $page.Location = New-Object Drawing.Point(126,416); $page.Size = New-Object Drawing.Size(150,24)
    $page.Anchor = 'Bottom,Left'; $window.Controls.Add($page)
    $statusLabel = New-Object Windows.Forms.Label
    $statusLabel.Name = 'ImportResultStatus'; $statusLabel.Text = '正在读取本次结果…'
    $statusLabel.Location = New-Object Drawing.Point(300,416); $statusLabel.Size = New-Object Drawing.Size(230,24)
    $statusLabel.Anchor = 'Bottom,Left'; $statusLabel.ForeColor = [Drawing.Color]::FromArgb(95,99,104)
    $window.Controls.Add($statusLabel)
    $remove = New-Object Windows.Forms.Button
    $remove.Name = 'ImportResultRemove'; $remove.Text = '撤销本次导入'
    $remove.Location = New-Object Drawing.Point(548,410); $remove.Size = New-Object Drawing.Size(130,30)
    $remove.Anchor = 'Bottom,Right'; $remove.Tag = (-not $Duplicate); $remove.Enabled = (-not $Duplicate)
    $window.Controls.Add($remove)
    $next = New-Object Windows.Forms.Button
    $next.Name = 'ImportResultNext'; $next.Text = '下一页'
    $next.Location = New-Object Drawing.Point(694,410); $next.Size = New-Object Drawing.Size(90,30)
    $next.Anchor = 'Bottom,Right'; $next.Enabled = $false
    $window.Controls.Add($next)

    $previous.Add_Click({
        if (!$script:VocabularyBusy -and $script:VocabularyImportHasPrevious) {
            $script:VocabularyImportOffset = [Math]::Max(0,$script:VocabularyImportOffset - $script:VocabularyLimit)
            [void](Start-VocabularyCli -Action query-import -ImportId $script:VocabularyImportId)
        }
    })
    $next.Add_Click({
        if (!$script:VocabularyBusy -and $script:VocabularyImportHasNext) {
            $script:VocabularyImportOffset += $script:VocabularyLimit
            [void](Start-VocabularyCli -Action query-import -ImportId $script:VocabularyImportId)
        }
    })
    $searchButton.Add_Click({
        if (!$script:VocabularyBusy) {
            [void](Start-VocabularyCli -Action query-import -ImportId $script:VocabularyImportId -ResetImportOffset)
        }
    })
    $search.Add_KeyDown({
        param($sender,$eventArgs)
        if ($eventArgs.KeyCode -eq [Windows.Forms.Keys]::Enter) {
            $eventArgs.SuppressKeyPress = $true
            if (!$script:VocabularyBusy) {
                [void](Start-VocabularyCli -Action query-import -ImportId $script:VocabularyImportId -ResetImportOffset)
            }
        }
    })
    $sortBox.Add_SelectedIndexChanged({
        if ($script:VocabularyImportWindow -and !$script:VocabularyBusy) {
            [void](Start-VocabularyCli -Action query-import -ImportId $script:VocabularyImportId -ResetImportOffset)
        }
    })
    $remove.Add_Click({
        if ($script:VocabularyBusy -or ![bool]$this.Tag -or $script:VocabularyImportId -le 0) {
            return
        }
        $answer = [Windows.Forms.MessageBox]::Show(
            '确定撤销本次字幕导入吗？本次贡献的累计次数将被扣除，之后仍可重新导入同一字幕。',
            '确认撤销词汇导入',
            [Windows.Forms.MessageBoxButtons]::YesNo,
            [Windows.Forms.MessageBoxIcon]::Warning
        )
        if (Test-VocabularyRemoveConfirmation $answer) {
            [void](Start-VocabularyCli -Action remove-import -ImportId $script:VocabularyImportId)
        }
    })
    $window.Add_FormClosing({
        param($sender,$eventArgs)
        if ($script:VocabularyBusy -and $script:VocabularyAction -eq 'query-import') {
            $eventArgs.Cancel = $true
        }
    })
    return $window
}

function Show-VocabularyImportQuery($Payload) {
    $view = ConvertTo-VocabularyImportQueryView $Payload
    if (!$script:VocabularyImportWindow -or $script:VocabularyImportWindow.IsDisposed) {
        $script:VocabularyImportWindow = New-VocabularyImportResultWindow `
            -Duplicate $script:VocabularyImportDuplicate `
            -CollectionName $script:VocabularyImportCollectionName
        $script:VocabularyImportWindow.Add_FormClosed({
            $script:VocabularyImportWindow = $null
            $script:VocabularyImportHasPrevious = $false
            $script:VocabularyImportHasNext = $false
        })
    }
    $window = $script:VocabularyImportWindow
    $grid = $window.Controls.Find('ImportResultGrid',$true)[0]
    $grid.Rows.Clear()
    foreach ($row in @($view.Rows)) {
        [void]$grid.Rows.Add(
            $row.Word,
            $row.Meaning,
            $row.ImportCount,
            $row.TotalCount,
            $row.EnglishFrequency
        )
    }
    $script:VocabularyImportOffset = [int]$view.Offset
    $script:VocabularyImportHasPrevious = [bool]$view.HasPrevious
    $script:VocabularyImportHasNext = [bool]$view.HasNext
    $window.Controls.Find('ImportResultPage',$true)[0].Text = $view.PageText
    $window.Controls.Find('ImportResultStatus',$true)[0].Text = $view.StatusText
    $vocabularyStatus.Text = '词汇分析完成'
    $vocabularyStatus.ForeColor = [Drawing.Color]::FromArgb(95,99,104)
    Update-VocabularyControls
    if (!$window.Visible) { $window.Show($form) }
    $window.Activate()
}

function Start-VocabularyCli {
    param(
        [Parameter(Mandatory)]
        [ValidateSet('import','import-file','query','query-import','remove-import','collection-list','collection-create','collection-rename','collection-delete')]
        [string]$Action,
        [string]$TaskFolder,
        [string]$SubtitleFile,
        [int]$ImportId,
        [int]$CollectionId,
        [string]$CollectionName,
        [switch]$ResetOffset,
        [switch]$ResetImportOffset
    )

    if (!(Test-VocabularyActionCanStart $script:VocabularyBusy)) { return $false }
    if (!$script:VocabularyAvailable) {
        Show-VocabularyFailure ([pscustomobject]@{
            error_code = 'INTERNAL_ERROR'
            message = '找不到 runtime\vocab_cli.py。'
        })
        return $false
    }
    if ($Action -eq 'import' -and (
        [string]::IsNullOrWhiteSpace($TaskFolder) -or
        !(Test-Path -LiteralPath $TaskFolder -PathType Container)
    )) {
        [Windows.Forms.MessageBox]::Show(
            '当前没有可分析的成功字幕任务。请先完成一次字幕提取。',
            '无法分析词汇',
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        return $false
    }

    $outputPath = Join-Path ([IO.Path]::GetTempPath()) (
        'subtitle-vocabulary-' + [Guid]::NewGuid().ToString('N') + '.json'
    )
    try {
        if ($CollectionId -le 0) { $CollectionId = $script:VocabularyCollectionId }
        if ($Action -eq 'import') {
            $arguments = New-VocabularyCliArguments -Action import -TaskFolder $TaskFolder `
                -CollectionId $CollectionId -OutputJson $outputPath
        } elseif ($Action -eq 'import-file') {
            $arguments = New-VocabularyCliArguments -Action import-file -SubtitleFile $SubtitleFile `
                -CollectionId $CollectionId -OutputJson $outputPath
        } elseif ($Action -eq 'query') {
            if ($ResetOffset) { $script:VocabularyOffset = 0 }
            $sort = Get-VocabularySortValue $vocabularySort.SelectedIndex
            $arguments = New-VocabularyCliArguments -Action query `
                -Search $vocabularySearch.Text -Sort $sort `
                -Limit $script:VocabularyLimit -Offset $script:VocabularyOffset `
                -CollectionId $CollectionId -OutputJson $outputPath
        } elseif ($Action -eq 'query-import') {
            if ($ResetImportOffset) { $script:VocabularyImportOffset = 0 }
            $importSearch = ''
            $importSort = 'import_count_desc'
            if ($script:VocabularyImportWindow -and !$script:VocabularyImportWindow.IsDisposed) {
                $searchControl = $script:VocabularyImportWindow.Controls.Find('ImportResultSearch',$true)[0]
                $sortControl = $script:VocabularyImportWindow.Controls.Find('ImportResultSort',$true)[0]
                if ($searchControl) { $importSearch = $searchControl.Text }
                if ($sortControl) { $importSort = Get-VocabularyImportSortValue $sortControl.SelectedIndex }
            }
            $arguments = New-VocabularyCliArguments -Action query-import `
                -ImportId $ImportId -Search $importSearch -ImportSort $importSort `
                -Limit $script:VocabularyLimit `
                -Offset $script:VocabularyImportOffset `
                -CollectionId $script:VocabularyImportCollectionId -OutputJson $outputPath
        } elseif ($Action -eq 'remove-import') {
            $arguments = New-VocabularyCliArguments -Action remove-import `
                -ImportId $ImportId -CollectionId $script:VocabularyImportCollectionId `
                -OutputJson $outputPath
        } elseif ($Action -eq 'collection-list') {
            $arguments = New-VocabularyCliArguments -Action collection-list -OutputJson $outputPath
        } elseif ($Action -eq 'collection-create') {
            $arguments = New-VocabularyCliArguments -Action collection-create `
                -CollectionName $CollectionName -OutputJson $outputPath
        } elseif ($Action -eq 'collection-rename') {
            $arguments = New-VocabularyCliArguments -Action collection-rename `
                -CollectionId $CollectionId -CollectionName $CollectionName -OutputJson $outputPath
        } else {
            $arguments = New-VocabularyCliArguments -Action collection-delete `
                -CollectionId $CollectionId -OutputJson $outputPath
        }
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = $Python
        $info.Arguments = (ConvertTo-VocabularyCommandLineArgument $VocabularyCli) + ' ' + $arguments
        $info.WorkingDirectory = $Core
        $info.UseShellExecute = $false; $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
        $info.EnvironmentVariables['SUBTITLE_TOOL_ROOT'] = $Root
        $process = New-Object Diagnostics.Process
        $process.StartInfo = $info
        Set-VocabularyBusy $true
        [void]$process.Start()
        $process.BeginOutputReadLine(); $process.BeginErrorReadLine()
        $script:VocabularyProcess = $process
        $script:VocabularyOutputPath = $outputPath
        $script:VocabularyAction = $Action
        $vocabularyStatus.Text = if ($Action -in @('import','import-file')) {
            '正在分析字幕词汇…'
        } elseif ($Action -eq 'query-import') {
            '正在读取本次导入结果…'
        } elseif ($Action -eq 'remove-import') {
            '正在撤销本次导入…'
        } elseif ($Action -like 'collection-*') {
            '正在更新词汇表…'
        } else {
            '正在查询词汇…'
        }
        $vocabularyStatus.ForeColor = [Drawing.Color]::FromArgb(26,115,232)
        $vocabularyTimer.Start()
        return $true
    } catch {
        Remove-VocabularyOutputFile $outputPath
        $script:VocabularyProcess = $null
        $script:VocabularyOutputPath = $null
        $script:VocabularyAction = $null
        Set-VocabularyBusy $false
        Show-VocabularyFailure ([pscustomobject]@{
            error_code = 'INTERNAL_ERROR'
            message = $_.Exception.Message
        })
        return $false
    }
}

function Complete-VocabularyCli {
    $process = $script:VocabularyProcess
    $outputPath = $script:VocabularyOutputPath
    $action = $script:VocabularyAction
    $payload = $null
    $completionError = $null
    $vocabularyTimer.Stop()
    try {
        $process.WaitForExit()
        if (!(Test-Path -LiteralPath $outputPath -PathType Leaf)) {
            throw '词汇后台程序没有返回 JSON 结果。'
        }
        $payload = Read-JsonShared $outputPath
        if ($null -eq $payload) { throw '词汇后台程序返回了空 JSON。' }
    } catch {
        $completionError = $_.Exception
    } finally {
        Remove-VocabularyOutputFile $outputPath
        if ($process) { $process.Dispose() }
        $script:VocabularyProcess = $null
        $script:VocabularyOutputPath = $null
        $script:VocabularyAction = $null
        Set-VocabularyBusy $false
    }

    if ($completionError) {
        Show-VocabularyFailure ([pscustomobject]@{
            error_code = 'INTERNAL_ERROR'
            message = $completionError.Message
        })
        return
    }
    if (!$payload.ok) {
        if ($action -eq 'query' -and $script:VocabularyImportPending) {
            $script:VocabularyImportPending = $false
        }
        Show-VocabularyFailure $payload
        return
    }
    if ($action -in @('import','import-file')) {
        $outcome = Get-VocabularyImportOutcome $payload
        if (!$outcome.ShouldQuery) {
            Show-VocabularyFailure $payload
            return
        }
        if ($outcome.IsDuplicate) {
            [Windows.Forms.MessageBox]::Show(
                $outcome.Message,
                '词汇统计',
                [Windows.Forms.MessageBoxButtons]::OK,
                [Windows.Forms.MessageBoxIcon]::Information
            ) | Out-Null
        }
        if ($script:VocabularyImportWindow -and !$script:VocabularyImportWindow.IsDisposed) {
            $script:VocabularyImportWindow.Close()
            $script:VocabularyImportWindow.Dispose()
        }
        $script:VocabularyImportWindow = $null
        $script:VocabularyImportId = [int]$payload.import_id
        $script:VocabularyImportCollectionId = [int]$payload.collection_id
        $script:VocabularyImportCollectionName = [string]$payload.collection_name
        $script:VocabularyImportOffset = 0
        $script:VocabularyImportDuplicate = [bool]$outcome.IsDuplicate
        $script:VocabularyImportPending = $true
        $mainTabs.SelectedTab = $vocabularyTab
        [void](Start-VocabularyCli -Action query -ResetOffset)
    } elseif ($action -eq 'query') {
        Show-VocabularyQuery $payload
        if ($script:VocabularyImportPending) {
            $script:VocabularyImportPending = $false
            $script:VocabularyImportOffset = 0
            [void](Start-VocabularyCli -Action query-import -ImportId $script:VocabularyImportId)
        }
    } elseif ($action -eq 'query-import') {
        Show-VocabularyImportQuery $payload
    } elseif ($action -eq 'remove-import') {
        $outcome = Get-VocabularyRemoveOutcome $payload
        if (!$outcome.ShouldRefresh) {
            Show-VocabularyFailure $payload
            return
        }
        Set-VocabularyImportRemovedState $script:VocabularyImportWindow
        $vocabularyStatus.Text = $outcome.Message
        $vocabularyStatus.ForeColor = [Drawing.Color]::FromArgb(95,99,104)
        [Windows.Forms.MessageBox]::Show(
            $outcome.Message,
            '词汇统计',
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        [void](Start-VocabularyCli -Action query -ResetOffset)
    } elseif ($action -eq 'collection-list') {
        $preferred = $script:VocabularyPreferredCollectionId
        $script:VocabularyPreferredCollectionId = 0
        Show-VocabularyCollections $payload $preferred
        if ($mainTabs.SelectedTab -eq $vocabularyTab) {
            [void](Start-VocabularyCli -Action query -ResetOffset)
        }
    } else {
        $script:VocabularyPreferredCollectionId = [int]$payload.collection_id
        [void](Start-VocabularyCli -Action collection-list)
    }
}

function Set-Ready {
    $missing = @($Python,$Worker,$CacheTool,$UiHelpers,$YtDlp,$Resolver,$MediaToolsResolver) | Where-Object { !(Test-Path $_) }
    if ($missing.Count) {
        $status.Text = '组件不完整，请重新解压完整版工具。'; $start.Enabled = $false
        $status.ForeColor = [Drawing.Color]::Firebrick
    } else {
        $probe = Join-Path $Result ('.write-test-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        try {
            [IO.File]::WriteAllText($probe,'ok',[Text.Encoding]::UTF8)
            Remove-Item -LiteralPath $probe -Force
            $mediaJson = & $Python $MediaToolsResolver --json 2>$null
            if ($LASTEXITCODE -eq 0) {
                $media = $mediaJson | ConvertFrom-Json
                $status.Text = '准备就绪'; $start.Enabled = $true
                $status.ForeColor = [Drawing.Color]::FromArgb(32,33,36)
            } else {
                $status.Text = '未找到 FFmpeg。请安装 FFmpeg 并加入 PATH。'
                $status.ForeColor = [Drawing.Color]::Firebrick
                $start.Enabled = $false
            }
            $progress.Value = 0
            if (!$script:VocabularyAvailable) {
                $vocabularyStatus.Text = '词汇组件不完整，请重新解压完整版工具。'
                $vocabularyStatus.ForeColor = [Drawing.Color]::Firebrick
            }
            Update-VocabularyControls
            if ($script:VocabularyAvailable -and !$script:VocabularyCollectionsLoaded) {
                [void](Start-VocabularyCli -Action collection-list)
            }
        } catch {
            Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
            $status.Text = '结果文件夹没有写入权限'; $start.Enabled = $false
            $status.ForeColor = [Drawing.Color]::Firebrick
        }
    }
}

function Start-Task {
    if ($urlBox.Text -notmatch '^https?://') {
        [Windows.Forms.MessageBox]::Show('请粘贴完整的 http/https 视频地址。','请检查'); return
    }
    $label = 'task'
    if ($urlBox.Text -match '/vod-play/([^/]+)/([^/?#]+)') { $label = $Matches[1] + '-' + $Matches[2] }
    $folder = Join-Path $Result ($label + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Python
    $mode = Get-WorkerMode $modeBox.SelectedIndex
    $info.Arguments = '"' + $Worker + '" "' + $mode + '" "' + ($urlBox.Text -replace '"','\"') + '" "' + $folder + '"'
    $info.WorkingDirectory = $Core; $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $process = New-Object Diagnostics.Process; $process.StartInfo = $info
    try {
        [void]$process.Start()
    } catch {
        Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
        $status.Text = '后台程序启动失败'; $status.ForeColor = [Drawing.Color]::Firebrick
        [Windows.Forms.MessageBox]::Show('后台程序启动失败：' + $_.Exception.Message,'启动失败')
        return
    }
    $script:CurrentProcess = $process; $script:CurrentFolder = $folder
    $script:CurrentLog = Join-Path $folder '运行记录.txt'
    $script:CancelRequested = $false; $script:CloseAfterCancel = $false
    $script:TaskStartedAt = [DateTime]::UtcNow; $elapsed.Text = '已用时 00:00:00'
    $start.Enabled = $false; $urlBox.Enabled = $false; $modeBox.Enabled = $false; $progress.Value = 1
    $cancel.Enabled = $true; $cleanup.Enabled = $false
    $status.Text = '任务已启动…'; $status.ForeColor = [Drawing.Color]::FromArgb(26,115,232)
    Update-VocabularyControls
    $log.Clear(); $timer.Start()
}

function Request-Cancel([bool]$Ask=$true) {
    if (!$script:CurrentProcess -or $script:CurrentProcess.HasExited -or $script:CancelRequested) { return $false }
    if ($Ask) {
        $choice = [Windows.Forms.MessageBox]::Show(
            '确定取消当前任务吗？未完成的结果文件夹会被清理。',
            '取消任务',
            [Windows.Forms.MessageBoxButtons]::YesNo,
            [Windows.Forms.MessageBoxIcon]::Question)
        if ($choice -ne [Windows.Forms.DialogResult]::Yes) { return $false }
    }
    $request = Join-Path $script:CurrentFolder 'cancel.request'
    [IO.File]::WriteAllText($request,'cancel',[Text.Encoding]::UTF8)
    $script:CancelRequested = $true; $cancel.Enabled = $false
    $status.Text = '正在取消任务并清理临时文件…'
    $status.ForeColor = [Drawing.Color]::FromArgb(180,95,6)
    return $true
}

function Invoke-CacheTool($Action) {
    $output = & $Python $CacheTool $Action $Result '--older-than-hours' '24' 2>&1
    if ($LASTEXITCODE -ne 0) { throw (($output | Out-String).Trim()) }
    $line = @($output | Where-Object { $_ -and $_.ToString().Trim() })[-1]
    return ($line.ToString() | ConvertFrom-Json)
}

function Preserve-UnexpectedLog {
    if (!$script:CurrentLog -or !(Test-Path -LiteralPath $script:CurrentLog)) { return $null }
    New-Item -ItemType Directory -Force -Path $FailureLogs | Out-Null
    $safe = (Split-Path $script:CurrentFolder -Leaf) -replace '[<>:"/\\|?*]','_'
    $target = Join-Path $FailureLogs ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $safe + '.txt')
    $content = [IO.File]::ReadAllText($script:CurrentLog,[Text.Encoding]::UTF8)
    [IO.File]::WriteAllText($target,$content,[Text.UTF8Encoding]::new($true))
    Get-ChildItem -LiteralPath $FailureLogs -File -Filter '*.txt' |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 |
        Remove-Item -Force -ErrorAction SilentlyContinue
    return $target
}

function Remove-UnexpectedTaskFolder {
    if (!$script:CurrentFolder -or !(Test-Path -LiteralPath $script:CurrentFolder)) { return }
    $resultPath = [IO.Path]::GetFullPath($Result).TrimEnd('\') + '\'
    $taskPath = [IO.Path]::GetFullPath($script:CurrentFolder)
    if ($taskPath.StartsWith($resultPath,[StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $taskPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Refresh-Log {
    $logPath = $script:CurrentLog
    if (!$logPath -or !(Test-Path $logPath)) {
        $latest = Get-ChildItem -LiteralPath $Result -Recurse -File -Filter '运行记录.txt' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) { $logPath = $latest.FullName }
    }
    if ($logPath -and (Test-Path $logPath)) {
        $content = (Get-Content -Encoding UTF8 $logPath -Tail 120 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        if (!$content) { $content = '日志文件已创建，正在等待后台输出…' }
        $log.Text = $content; $log.SelectionStart = $log.TextLength; $log.ScrollToCaret()
    } else {
        $log.Text = '暂无任务日志。开始处理后，详细进度会显示在这里。'
    }
}

$timer = New-Object Windows.Forms.Timer
$timer.Interval = 600
$timer.Add_Tick({
    if ($script:TaskStartedAt -and $script:CurrentProcess -and !$script:CurrentProcess.HasExited) {
        $seconds = ([DateTime]::UtcNow - $script:TaskStartedAt).TotalSeconds
        $elapsed.Text = '已用时 ' + (Format-Elapsed $seconds)
    }
    $progressPath = Join-Path $script:CurrentFolder 'progress.json'
    if (Test-Path $progressPath) {
        try {
            $state = Read-JsonShared $progressPath
            $value = [Math]::Max(0,[Math]::Min(100,[int]$state.value))
            $progress.Value = $value; $status.Text = $state.message
        } catch {}
    }
    if ($script:DetailsOpen) { Refresh-Log }
    if ($script:CurrentProcess -and $script:CurrentProcess.HasExited) {
        $timer.Stop(); $start.Enabled = $true; $urlBox.Enabled = $true; $modeBox.Enabled = $true
        $cancel.Enabled = $false; $cleanup.Enabled = $true
        $statusPath = Join-Path $script:CurrentFolder 'status.json'
        if (!(Test-Path $statusPath)) {
            $statusPath = Join-Path (Split-Path $script:CurrentFolder -Parent) ('.' + (Split-Path $script:CurrentFolder -Leaf) + '.status.json')
        }
        if (Test-Path $statusPath) {
            $resultState = Read-JsonShared $statusPath
            $fallbackSeconds = if ($script:TaskStartedAt) { ([DateTime]::UtcNow - $script:TaskStartedAt).TotalSeconds } else { 0 }
            $totalSeconds = if ($null -ne $resultState.elapsed_seconds) { [double]$resultState.elapsed_seconds } else { $fallbackSeconds }
            $totalText = Format-Elapsed $totalSeconds
            $elapsed.Text = '总用时 ' + $totalText
            if ($resultState.ok) {
                $progress.Value = 100; $status.Text = '处理完成：' + $resultState.type
                $status.ForeColor = [Drawing.Color]::FromArgb(22,125,61)
                $script:LastSuccessFolder = $script:CurrentFolder
                $script:LastSuccessType = [string]$resultState.type
                $doneMessage = switch ([string]$resultState.type) {
                    'MP4 视频' { 'MP4 视频下载完成，结果已单独保存。' }
                    'MP3 音频' { 'MP3 音频提取完成，结果已单独保存。' }
                    default { '字幕提取完成，结果已单独保存。' }
                }
                $phaseText = Format-PhaseTimings $resultState.phase_timings
                $phaseSummary = if ($phaseText) {
                    [Environment]::NewLine + '阶段耗时：' + $phaseText
                } else { '' }
                Flash-Taskbar $form
                $openNow = [Windows.Forms.MessageBox]::Show(
                    $doneMessage + [Environment]::NewLine +
                    '总用时：' + $totalText + $phaseSummary +
                    [Environment]::NewLine + [Environment]::NewLine +
                    '是否立即打开结果文件夹？',
                    '完成',
                    [Windows.Forms.MessageBoxButtons]::YesNo,
                    [Windows.Forms.MessageBoxIcon]::Information)
                if ($openNow -eq [Windows.Forms.DialogResult]::Yes -and
                    $script:LastSuccessFolder -and
                    (Test-Path -LiteralPath $script:LastSuccessFolder)) {
                    Start-Process explorer.exe $script:LastSuccessFolder
                }
            } elseif ($resultState.cancelled) {
                $progress.Value = 0; $status.Text = '任务已取消，未保留结果文件夹'
                $status.ForeColor = [Drawing.Color]::FromArgb(95,99,104)
                if (!$script:CloseAfterCancel) {
                    [Windows.Forms.MessageBox]::Show('任务已取消，未保留结果文件夹。','已取消')
                }
            } else {
                $progress.Value = 0; $status.Text = '任务未完成，未保留文件'
                $status.ForeColor = [Drawing.Color]::Firebrick
                if ($resultState.log -and (Test-Path -LiteralPath $resultState.log)) {
                    $script:CurrentLog = $resultState.log
                }
                $extra = if ($resultState.log) { [Environment]::NewLine + '失败日志：' + $resultState.log } else { '' }
                [Windows.Forms.MessageBox]::Show($resultState.message + [Environment]::NewLine + '未生成结果文件夹。' + $extra,'处理失败')
            }
            if ((Split-Path $statusPath -Leaf).StartsWith('.')) { Remove-Item -LiteralPath $statusPath -Force -ErrorAction SilentlyContinue }
        } else {
            $fallbackSeconds = if ($script:TaskStartedAt) { ([DateTime]::UtcNow - $script:TaskStartedAt).TotalSeconds } else { 0 }
            $elapsed.Text = '总用时 ' + (Format-Elapsed $fallbackSeconds)
            $saved = Preserve-UnexpectedLog
            Remove-UnexpectedTaskFolder
            $progress.Value = 0; $status.Text = '后台异常退出'
            $status.ForeColor = [Drawing.Color]::Firebrick
            $extra = if ($saved) { [Environment]::NewLine + '日志：' + $saved } else { '' }
            [Windows.Forms.MessageBox]::Show('后台异常退出，没有返回任务状态。' + $extra,'处理失败')
        }
        $script:CurrentProcess = $null
        $script:TaskStartedAt = $null
        Update-VocabularyControls
        if ($script:CloseAfterCancel) {
            $script:CloseAfterCancel = $false
            $form.Close()
        }
    }
})

$vocabularyTimer = New-Object Windows.Forms.Timer
$vocabularyTimer.Interval = 150
$vocabularyTimer.Add_Tick({
    if ($script:VocabularyProcess -and $script:VocabularyProcess.HasExited) {
        Complete-VocabularyCli
    }
})

$start.Add_Click({ Start-Task })
$cancel.Add_Click({ [void](Request-Cancel $true) })
$cleanup.Add_Click({
    try {
        $state = Invoke-CacheTool 'scan'
        if ([int]$state.count -eq 0) {
            [Windows.Forms.MessageBox]::Show('没有发现超过 24 小时的失败下载缓存。','清理缓存'); return
        }
        $size = [Math]::Round([double]$state.total_bytes / 1MB,2)
        $choice = [Windows.Forms.MessageBox]::Show(
            "发现 $($state.count) 个旧临时文件，共 $size MB。`n只会删除 .part/.ytdl，不会删除字幕、音频、视频和日志。`n确定清理吗？",
            '清理缓存',
            [Windows.Forms.MessageBoxButtons]::YesNo,
            [Windows.Forms.MessageBoxIcon]::Question)
        if ($choice -eq [Windows.Forms.DialogResult]::Yes) {
            $removed = Invoke-CacheTool 'clean'
            $removedSize = [Math]::Round([double]$removed.total_bytes / 1MB,2)
            [Windows.Forms.MessageBox]::Show("已清理 $($removed.count) 个文件，释放 $removedSize MB。",'清理完成')
        }
    } catch {
        [Windows.Forms.MessageBox]::Show('缓存清理失败：' + $_.Exception.Message,'清理失败')
    }
})
$open.Add_Click({
    $target = if ($script:LastSuccessFolder -and (Test-Path $script:LastSuccessFolder)) { $script:LastSuccessFolder } else { $Result }
    Start-Process explorer.exe $target
})
$analyze.Add_Click({
    if ($script:VocabularyBusy) { return }
    if (
        !$script:LastSuccessFolder -or
        !(Test-Path -LiteralPath $script:LastSuccessFolder -PathType Container) -or
        $script:LastSuccessType -in @('MP3 音频','MP4 视频')
    ) {
        [Windows.Forms.MessageBox]::Show(
            '当前没有可分析的成功字幕任务。请先完成一次字幕提取。',
            '无法分析词汇',
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        return
    }
    [void](Start-VocabularyCli -Action import -TaskFolder $script:LastSuccessFolder)
})
$vocabularyImportFile.Add_Click({
    if ($script:VocabularyBusy) { return }
    $dialog = New-VocabularySubtitleOpenFileDialog
    try {
        if ($dialog.ShowDialog($form) -eq [Windows.Forms.DialogResult]::OK) {
            [void](Start-VocabularyCli -Action import-file -SubtitleFile $dialog.FileName)
        }
    } finally {
        $dialog.Dispose()
    }
})
$vocabularyCollection.Add_SelectedIndexChanged({
    if ($script:VocabularyCollectionChanging -or $script:VocabularyBusy) { return }
    $selected = $vocabularyCollection.SelectedItem
    if (!$selected) { return }
    $script:VocabularyCollectionId = [int]$selected.CollectionId
    $script:VocabularyCollectionName = [string]$selected.Name
    $script:VocabularyOffset = 0
    Update-VocabularyControls
    [void](Start-VocabularyCli -Action query -CollectionId $script:VocabularyCollectionId -ResetOffset)
})
$vocabularyCollectionCreate.Add_Click({
    if ($script:VocabularyBusy) { return }
    $name = [Microsoft.VisualBasic.Interaction]::InputBox(
        '请输入新词汇表名称。','新建词汇表',''
    )
    if (![string]::IsNullOrWhiteSpace($name)) {
        [void](Start-VocabularyCli -Action collection-create -CollectionName $name)
    }
})
$vocabularyCollectionRename.Add_Click({
    if ($script:VocabularyBusy -or $script:VocabularyCollectionId -le 0) { return }
    $name = [Microsoft.VisualBasic.Interaction]::InputBox(
        '请输入新的词汇表名称。','重命名词汇表',$script:VocabularyCollectionName
    )
    if (![string]::IsNullOrWhiteSpace($name)) {
        [void](Start-VocabularyCli -Action collection-rename `
            -CollectionId $script:VocabularyCollectionId -CollectionName $name)
    }
})
$vocabularyCollectionDelete.Add_Click({
    if (
        $script:VocabularyBusy -or $script:VocabularyCollectionId -le 0 -or
        $script:VocabularyCollectionId -eq $script:VocabularyDefaultCollectionId
    ) { return }
    $answer = [Windows.Forms.MessageBox]::Show(
        ('确定删除词汇表“{0}”吗？' -f $script:VocabularyCollectionName) + [Environment]::NewLine +
        '该词汇表中的导入历史和累计统计都会被永久删除。其他词汇表不会受到影响。',
        '确认删除词汇表',
        [Windows.Forms.MessageBoxButtons]::YesNo,
        [Windows.Forms.MessageBoxIcon]::Warning
    )
    if ($answer -eq [Windows.Forms.DialogResult]::Yes) {
        [void](Start-VocabularyCli -Action collection-delete `
            -CollectionId $script:VocabularyCollectionId)
    }
})
$vocabularyExpand.Add_Click({ Switch-VocabularyExpandedView })
$vocabularySearchButton.Add_Click({
    if (!$script:VocabularyBusy) {
        [void](Start-VocabularyCli -Action query -ResetOffset)
    }
})
$vocabularySearch.Add_KeyDown({
    param($sender,$eventArgs)
    if ($eventArgs.KeyCode -eq [Windows.Forms.Keys]::Enter) {
        $eventArgs.SuppressKeyPress = $true
        if (!$script:VocabularyBusy) {
            [void](Start-VocabularyCli -Action query -ResetOffset)
        }
    }
})
$vocabularySort.Add_SelectedIndexChanged({
    if ($mainTabs.SelectedTab -eq $vocabularyTab -and !$script:VocabularyBusy) {
        [void](Start-VocabularyCli -Action query -ResetOffset)
    }
})
$vocabularyPrevious.Add_Click({
    if (!$script:VocabularyBusy -and $script:VocabularyHasPrevious) {
        $script:VocabularyOffset = [Math]::Max(0,$script:VocabularyOffset - $script:VocabularyLimit)
        [void](Start-VocabularyCli -Action query)
    }
})
$vocabularyNext.Add_Click({
    if (!$script:VocabularyBusy -and $script:VocabularyHasNext) {
        $script:VocabularyOffset += $script:VocabularyLimit
        [void](Start-VocabularyCli -Action query)
    }
})
$mainTabs.Add_SelectedIndexChanged({
    if ($mainTabs.SelectedTab -eq $vocabularyTab -and !$script:VocabularyBusy) {
        if (!$script:VocabularyCollectionsLoaded) {
            [void](Start-VocabularyCli -Action collection-list)
        } else {
            [void](Start-VocabularyCli -Action query -ResetOffset)
        }
    }
})
$details.Add_Click({
    $script:DetailsOpen = !$script:DetailsOpen
    if ($script:DetailsOpen) {
        $form.Size = New-Object Drawing.Size(800,670); $mainTabs.Size = New-Object Drawing.Size(764,580)
        $log.Visible = $true; $details.Text = '收起详细日志 ▴'
        Refresh-Log
    } else {
        $log.Visible = $false; $form.Size = New-Object Drawing.Size(800,430)
        $mainTabs.Size = New-Object Drawing.Size(764,340); $details.Text = '查看详细日志 ▾'
    }
})
$form.Add_Shown({
    $mainTabs.PerformLayout()
    $vocabularyTab.PerformLayout()
    $mainTabs.Anchor = 'Top,Bottom,Left,Right'
    $vocabularyExpand.Anchor = 'Top,Right'
    $vocabularyCollectionDelete.Anchor = 'Top,Right'
    $vocabularySortLabel.Anchor = 'Top,Right'
    $vocabularySort.Anchor = 'Top,Right'
    $vocabularyGrid.Anchor = 'Top,Bottom,Left,Right'
    $vocabularyPrevious.Anchor = 'Bottom,Left'
    $vocabularyPage.Anchor = 'Bottom,Left'
    $vocabularyStatus.Anchor = 'Bottom,Left'
    $vocabularyNext.Anchor = 'Bottom,Right'
    $progress.Style = 'Marquee'; $progress.MarqueeAnimationSpeed = 28; $status.Text = '正在检查运行组件…'
    $startupTimer.Start()
})
$form.Add_FormClosing({
    param($sender,$eventArgs)
    if ($script:VocabularyBusy) {
        $eventArgs.Cancel = $true
        [Windows.Forms.MessageBox]::Show(
            '词汇操作正在完成，请稍候片刻再关闭窗口。',
            '词汇操作进行中',
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        return
    }
    if ($script:CurrentProcess -and !$script:CurrentProcess.HasExited) {
        $choice = [Windows.Forms.MessageBox]::Show(
            '任务仍在运行。是否先取消任务并清理临时文件，然后关闭窗口？',
            '任务正在运行',
            [Windows.Forms.MessageBoxButtons]::YesNo,
            [Windows.Forms.MessageBoxIcon]::Warning)
        $eventArgs.Cancel = $true
        if ($choice -eq [Windows.Forms.DialogResult]::Yes) {
            $script:CloseAfterCancel = $true
            [void](Request-Cancel $false)
        }
    }
})
$startupTimer = New-Object Windows.Forms.Timer
$startupTimer.Interval = 500
$startupTimer.Add_Tick({ $startupTimer.Stop(); $progress.Style = 'Continuous'; Set-Ready })

[void]$form.ShowDialog()
