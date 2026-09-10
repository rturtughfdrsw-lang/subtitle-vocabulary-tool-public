function ConvertTo-VocabularyCommandLineArgument {
    param(
        [AllowEmptyString()]
        [string]$Value
    )

    if ($null -eq $Value -or $Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }

    $builder = New-Object Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]92) {
            $backslashes++
            continue
        }
        if ($character -eq [char]34) {
            if ($backslashes -gt 0) {
                [void]$builder.Append((([string][char]92) * ($backslashes * 2)) -join '')
            }
            [void]$builder.Append([char]92)
            [void]$builder.Append([char]34)
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append((([string][char]92) * $backslashes) -join '')
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append((([string][char]92) * ($backslashes * 2)) -join '')
    }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Get-VocabularySortValue([int]$SelectedIndex) {
    $values = @(
        'word_asc',
        'word_desc',
        'count_desc',
        'count_asc',
        'frequency_desc',
        'frequency_asc'
    )
    if ($SelectedIndex -lt 0 -or $SelectedIndex -ge $values.Count) {
        return 'word_asc'
    }
    return $values[$SelectedIndex]
}

function Get-VocabularyImportSortValue([int]$SelectedIndex) {
    $values = @(
        'word_asc',
        'word_desc',
        'import_count_desc',
        'import_count_asc',
        'total_count_desc',
        'total_count_asc',
        'frequency_desc',
        'frequency_asc'
    )
    if ($SelectedIndex -lt 0 -or $SelectedIndex -ge $values.Count) {
        return 'import_count_desc'
    }
    return $values[$SelectedIndex]
}

function New-VocabularyCliArguments {
    param(
        [Parameter(Mandatory)]
        [ValidateSet('import','import-file','query','query-import','remove-import','collection-list','collection-create','collection-rename','collection-delete')]
        [string]$Action,
        [string]$TaskFolder,
        [string]$SubtitleFile,
        [int]$ImportId,
        [int]$CollectionId,
        [AllowEmptyString()]
        [string]$CollectionName = '',
        [string]$OutputJson,
        [AllowEmptyString()]
        [string]$Search = '',
        [ValidateSet('word_asc','word_desc','count_desc','count_asc','frequency_desc','frequency_asc')]
        [string]$Sort = 'word_asc',
        [ValidateSet('word_asc','word_desc','import_count_desc','import_count_asc','total_count_desc','total_count_asc','frequency_desc','frequency_asc')]
        [string]$ImportSort = 'import_count_desc',
        [int]$Limit = 200,
        [int]$Offset = 0,
        [switch]$ResetOffset
    )

    $parts = New-Object System.Collections.Generic.List[string]
    $parts.Add($Action)
    if ($Action -eq 'import') {
        if ([string]::IsNullOrWhiteSpace($TaskFolder)) {
            throw 'import 需要明确的字幕任务目录。'
        }
        $parts.Add((ConvertTo-VocabularyCommandLineArgument $TaskFolder))
    } elseif ($Action -eq 'import-file') {
        if ([string]::IsNullOrWhiteSpace($SubtitleFile)) {
            throw 'import-file 需要明确的字幕文件。'
        }
        $parts.Add((ConvertTo-VocabularyCommandLineArgument $SubtitleFile))
    } elseif ($Action -eq 'query') {
        if ($ResetOffset) { $Offset = 0 }
        $parts.Add('--search')
        $parts.Add((ConvertTo-VocabularyCommandLineArgument $Search))
        $parts.Add('--sort')
        $parts.Add($Sort)
        $parts.Add('--limit')
        $parts.Add([string]$Limit)
        $parts.Add('--offset')
        $parts.Add([string]$Offset)
    } elseif ($Action -eq 'query-import') {
        if ($ImportId -le 0) { throw 'query-import 需要有效的 import_id。' }
        if ($ResetOffset) { $Offset = 0 }
        $parts.Add('--import-id')
        $parts.Add([string]$ImportId)
        $parts.Add('--search')
        $parts.Add((ConvertTo-VocabularyCommandLineArgument $Search))
        $parts.Add('--sort')
        $parts.Add($ImportSort)
        $parts.Add('--limit')
        $parts.Add([string]$Limit)
        $parts.Add('--offset')
        $parts.Add([string]$Offset)
    } elseif ($Action -eq 'remove-import') {
        if ($ImportId -le 0) { throw 'remove-import 需要有效的 import_id。' }
        $parts.Add('--import-id')
        $parts.Add([string]$ImportId)
    } elseif ($Action -eq 'collection-create') {
        if ([string]::IsNullOrWhiteSpace($CollectionName)) { throw '词汇表名称不能为空。' }
        $parts.Add('--name')
        $parts.Add((ConvertTo-VocabularyCommandLineArgument $CollectionName))
    } elseif ($Action -eq 'collection-rename') {
        if ($CollectionId -le 0) { throw 'collection-rename 需要有效的 collection_id。' }
        if ([string]::IsNullOrWhiteSpace($CollectionName)) { throw '词汇表名称不能为空。' }
        $parts.Add('--collection-id')
        $parts.Add([string]$CollectionId)
        $parts.Add('--name')
        $parts.Add((ConvertTo-VocabularyCommandLineArgument $CollectionName))
    } elseif ($Action -eq 'collection-delete') {
        if ($CollectionId -le 0) { throw 'collection-delete 需要有效的 collection_id。' }
        $parts.Add('--collection-id')
        $parts.Add([string]$CollectionId)
    }
    if ($Action -in @('import','import-file','query','query-import','remove-import') -and $CollectionId -gt 0) {
        $parts.Add('--collection-id')
        $parts.Add([string]$CollectionId)
    }
    if ([string]::IsNullOrWhiteSpace($OutputJson)) {
        throw 'CLI 输出 JSON 路径不能为空。'
    }
    $parts.Add('--output-json')
    $parts.Add((ConvertTo-VocabularyCommandLineArgument $OutputJson))
    return $parts -join ' '
}

function Format-VocabularyFrequency {
    param(
        [AllowNull()]
        [Nullable[double]]$Value
    )

    if ($null -eq $Value) { return '—' }
    $number = [double]$Value
    $label = if ($number -ge 6.0) {
        '极常见'
    } elseif ($number -ge 5.0) {
        '很常见'
    } elseif ($number -ge 4.0) {
        '常见'
    } elseif ($number -ge 3.0) {
        '较少见'
    } elseif ($number -ge 2.0) {
        '少见'
    } else {
        '非常少见'
    }
    $formatted = $number.ToString('0.00',[Globalization.CultureInfo]::InvariantCulture)
    return '{0}（{1}）' -f $formatted,$label
}

function Get-VocabularyPageState {
    param(
        [int]$Total,
        [int]$Limit,
        [int]$Offset
    )

    if ($Limit -le 0) { throw 'limit 必须大于 0。' }
    $safeTotal = [Math]::Max(0,$Total)
    $safeOffset = [Math]::Max(0,$Offset)
    $pageCount = if ($safeTotal -eq 0) { 0 } else { [int][Math]::Ceiling($safeTotal / [double]$Limit) }
    $pageNumber = if ($pageCount -eq 0) { 0 } else { [int][Math]::Floor($safeOffset / [double]$Limit) + 1 }
    return [pscustomobject][ordered]@{
        PageNumber = $pageNumber
        PageCount = $pageCount
        HasPrevious = ($safeOffset -gt 0 -and $safeTotal -gt 0)
        HasNext = ($safeOffset + $Limit -lt $safeTotal)
        PageText = '第 {0} / {1} 页' -f $pageNumber,$pageCount
    }
}

function ConvertTo-VocabularyQueryView($Payload) {
    $page = Get-VocabularyPageState -Total ([int]$Payload.total) -Limit ([int]$Payload.limit) -Offset ([int]$Payload.offset)
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($row in @($Payload.rows)) {
        $meaning = if ($null -eq $row.meaning) { '未收录' } else { [string]$row.meaning }
        $frequency = Format-VocabularyFrequency $row.english_frequency
        $rows.Add([pscustomobject][ordered]@{
            Word = [string]$row.word
            Meaning = $meaning
            TotalCount = [int]$row.total_count
            EnglishFrequency = $frequency
        })
    }
    $isEmpty = ([int]$Payload.total -eq 0)
    return [pscustomobject][ordered]@{
        Total = [int]$Payload.total
        Limit = [int]$Payload.limit
        Offset = [int]$Payload.offset
        Rows = $rows.ToArray()
        PageNumber = $page.PageNumber
        PageCount = $page.PageCount
        HasPrevious = $page.HasPrevious
        HasNext = $page.HasNext
        PageText = $page.PageText
        IsEmpty = $isEmpty
        StatusText = if ($isEmpty) { '暂无词汇记录' } else { '共 {0} 条词汇记录' -f [int]$Payload.total }
    }
}

function ConvertTo-VocabularyImportQueryView($Payload) {
    $page = Get-VocabularyPageState -Total ([int]$Payload.total) -Limit ([int]$Payload.limit) -Offset ([int]$Payload.offset)
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($row in @($Payload.rows)) {
        $rows.Add([pscustomobject][ordered]@{
            Word = [string]$row.word
            Meaning = if ($null -eq $row.meaning) { '未收录' } else { [string]$row.meaning }
            ImportCount = [int]$row.import_count
            TotalCount = [int]$row.total_count
            EnglishFrequency = Format-VocabularyFrequency $row.english_frequency
        })
    }
    $isEmpty = ([int]$Payload.total -eq 0)
    return [pscustomobject][ordered]@{
        ImportId = [int]$Payload.import_id
        Total = [int]$Payload.total
        Limit = [int]$Payload.limit
        Offset = [int]$Payload.offset
        Rows = $rows.ToArray()
        HasPrevious = $page.HasPrevious
        HasNext = $page.HasNext
        PageText = $page.PageText
        IsEmpty = $isEmpty
        StatusText = if ($isEmpty) { '本次没有可显示的单词' } else { '本次共 {0} 个单词' -f [int]$Payload.total }
    }
}

function ConvertTo-VocabularyCollectionListView($Payload,[int]$PreferredCollectionId = 0) {
    $collections = New-Object System.Collections.Generic.List[object]
    $defaultId = 0
    foreach ($item in @($Payload.collections)) {
        $entry = [pscustomobject][ordered]@{
            CollectionId = [int]$item.collection_id
            Name = [string]$item.name
            IsDefault = [bool]$item.is_default
        }
        if ($entry.IsDefault) { $defaultId = $entry.CollectionId }
        $collections.Add($entry)
    }
    $selectedId = if ($PreferredCollectionId -gt 0 -and @($collections | Where-Object CollectionId -eq $PreferredCollectionId).Count -gt 0) {
        $PreferredCollectionId
    } else {
        $defaultId
    }
    $selected = @($collections | Where-Object CollectionId -eq $selectedId | Select-Object -First 1)
    return [pscustomobject][ordered]@{
        DefaultCollectionId = $defaultId
        SelectedCollectionId = $selectedId
        Collections = $collections.ToArray()
        CanDeleteSelected = ($selected.Count -gt 0 -and !$selected[0].IsDefault)
    }
}

function Get-VocabularyErrorMessage {
    param(
        [string]$ErrorCode,
        [string]$FallbackMessage = ''
    )

    $message = switch ($ErrorCode) {
        'INVALID_ARGUMENT' { '词汇操作参数无效。' }
        'TASK_NOT_FOUND' { '找不到要分析的字幕任务目录。' }
        'SUBTITLE_NOT_FOUND' { '该任务中没有可分析的最终字幕。' }
        'SUBTITLE_FILE_NOT_FOUND' { '找不到所选字幕文件。' }
        'SUBTITLE_FORMAT_UNSUPPORTED' { '不支持该字幕格式，请选择 SRT、VTT、ASS 或 TXT 文件。' }
        'SUBTITLE_CONTENT_EMPTY' { '字幕文件中没有有效的字幕正文。' }
        'SUBTITLE_NO_ENGLISH' { '字幕正文中没有检测到可统计的英文单词。' }
        'SUBTITLE_SOURCE_AMBIGUOUS' { '任务中存在多个字幕来源，无法确定要分析哪一份。' }
        'DICTIONARY_RESOURCE_ERROR' { '离线英汉词典资源缺失或损坏，请重新解压完整版工具。' }
        'FREQUENCY_RESOURCE_ERROR' { '英语词频资源缺失或损坏，请重新解压完整版工具。' }
        'IMPORT_NOT_FOUND' { '找不到本次字幕对应的词汇导入记录。' }
        'COLLECTION_NOT_FOUND' { '找不到所选词汇表，请刷新后重试。' }
        'COLLECTION_NAME_CONFLICT' { '该词汇表名称已经存在。' }
        'DEFAULT_COLLECTION_PROTECTED' { '默认词汇表不允许删除。' }
        'COLLECTION_IMPORT_MISMATCH' { '该导入记录不属于当前词汇表。' }
        'DATABASE_ERROR' { '词汇数据库暂时无法访问，请检查磁盘空间和目录权限。' }
        default { '词汇功能发生内部错误。' }
    }
    if ($FallbackMessage) { return $message + [Environment]::NewLine + $FallbackMessage }
    return $message
}

function Get-VocabularyImportOutcome($Payload) {
    if ($Payload.ok) {
        $duplicate = [bool]$Payload.duplicate
        return [pscustomobject][ordered]@{
            ShouldQuery = $true
            IsDuplicate = $duplicate
            Message = if ($duplicate) {
                '词汇表“{0}”中已统计过该字幕，本次没有重复累计。' -f [string]$Payload.collection_name
            } else {
                '词汇已导入“{0}”。' -f [string]$Payload.collection_name
            }
        }
    }
    return [pscustomobject][ordered]@{
        ShouldQuery = $false
        IsDuplicate = $false
        Message = Get-VocabularyErrorMessage ([string]$Payload.error_code) ([string]$Payload.message)
    }
}

function Get-VocabularyRemoveOutcome($Payload) {
    if ($Payload.ok) {
        return [pscustomobject][ordered]@{
            ShouldRefresh = $true
            ImportId = [int]$Payload.import_id
            Message = '本次导入已撤销（{0} 个单词，共 {1} 次出现）。' -f `
                [int]$Payload.removed_word_count,[int]$Payload.removed_token_count
        }
    }
    return [pscustomobject][ordered]@{
        ShouldRefresh = $false
        ImportId = 0
        Message = Get-VocabularyErrorMessage ([string]$Payload.error_code) ([string]$Payload.message)
    }
}

function Test-VocabularyRemoveConfirmation($DialogResult) {
    return ([string]$DialogResult -eq 'Yes')
}

function Test-VocabularyActionCanStart([bool]$IsBusy) {
    return -not $IsBusy
}

function Remove-VocabularyOutputFile([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
}

function New-VocabularySubtitleOpenFileDialog {
    $dialog = New-Object Windows.Forms.OpenFileDialog
    $dialog.Title = '选择要统计的字幕文件'
    $dialog.Filter = '字幕文件 (*.srt;*.vtt;*.ass;*.txt)|*.srt;*.vtt;*.ass;*.txt'
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $false
    return $dialog
}
