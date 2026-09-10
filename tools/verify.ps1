[CmdletBinding()]
param(
    [ValidateSet('fast', 'full')]
    [string]$Mode = 'fast'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeRoot = Join-Path $projectRoot 'runtime'
$testsRoot = Join-Path $projectRoot 'tests'
$pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
if ($null -eq $pythonLauncher) {
    Write-Error 'Source-only verification requires the Python launcher on PATH.'
    exit 2
}
$python = (& $pythonLauncher.Source -3 -c 'import sys; print(sys.executable)').Trim()
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Write-Error 'The Python launcher did not return a usable interpreter.'
    exit 2
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$script:passed = 0
$script:failed = 0
$script:skipped = 0
$script:unavailable = 0

function Write-Result {
    param(
        [Parameter(Mandatory)][ValidateSet('PASS', 'FAIL', 'SKIP', 'UNAVAILABLE')][string]$Status,
        [Parameter(Mandatory)][string]$Name,
        [string]$Detail = ''
    )
    switch ($Status) {
        'PASS' { $script:passed++ }
        'FAIL' { $script:failed++ }
        'SKIP' { $script:skipped++ }
        'UNAVAILABLE' { $script:unavailable++ }
    }
    if ($Detail) { Write-Host ('[{0}] {1} - {2}' -f $Status, $Name, $Detail) }
    else { Write-Host ('[{0}] {1}' -f $Status, $Name) }
}

function Test-PythonSyntax {
    $sourceFiles = @(
        Get-ChildItem -LiteralPath $runtimeRoot -File -Filter '*.py'
        Get-ChildItem -LiteralPath $testsRoot -File -Filter '*.py'
    ) | Sort-Object FullName -Unique
    foreach ($file in $sourceFiles) {
        & $python -c "import ast, pathlib, sys; p=pathlib.Path(sys.argv[1]); ast.parse(p.read_text(encoding='utf-8-sig'), filename=str(p))" $file.FullName
        if ($LASTEXITCODE -eq 0) { Write-Result PASS ('Python syntax: {0}' -f $file.FullName.Substring($projectRoot.Length + 1)) }
        else { Write-Result FAIL ('Python syntax: {0}' -f $file.FullName.Substring($projectRoot.Length + 1)) }
    }
}

function Test-PowerShellSyntax {
    $sourceFiles = @(
        Get-ChildItem -LiteralPath $runtimeRoot -File -Filter '*.ps1'
        Get-ChildItem -LiteralPath $PSScriptRoot -File -Filter '*.ps1'
    ) | Sort-Object FullName -Unique
    foreach ($file in $sourceFiles) {
        $tokens = $null; $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$parseErrors)
        if ($parseErrors.Count -eq 0) { Write-Result PASS ('PowerShell syntax: {0}' -f $file.FullName.Substring($projectRoot.Length + 1)) }
        else { Write-Result FAIL ('PowerShell syntax: {0}' -f $file.FullName.Substring($projectRoot.Length + 1)) (($parseErrors | ForEach-Object Message) -join '; ') }
    }
}

function Invoke-TestFile {
    param([Parameter(Mandatory)][string]$FileName)
    $testPath = Join-Path $testsRoot $FileName
    if (-not (Test-Path -LiteralPath $testPath -PathType Leaf)) { Write-Result UNAVAILABLE "source test: $FileName" 'test file missing'; return }
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    & $python $testPath
    $exitCode = $LASTEXITCODE
    $stopwatch.Stop()
    if ($exitCode -eq 0) { Write-Result PASS "source test: $FileName" ('{0:N2}s' -f $stopwatch.Elapsed.TotalSeconds) }
    else { Write-Result FAIL "source test: $FileName" "exit=$exitCode" }
}

$sourceTests = @(
    'test_build_dictionary_resource.py', 'test_cache_maintenance.py',
    'test_dictionary_lookup.py', 'test_frequency_lookup.py',
    'test_launcher_resilience.py',
    'test_media_tools.py', 'test_ocr_dialogue_filter.py',
    'test_progress_file_lock.py',
    'test_subtitle_text_reader.py',
    'test_task_runtime.py', 'test_tokenizer.py',
    'test_vocabulary_collections.py', 'test_vocabulary_db.py',
    'test_vocabulary_duplicate.py',
    'test_vocabulary_enrichment.py', 'test_vocabulary_filter.py',
    'test_vocabulary_gui_layout.py', 'test_vocabulary_import.py',
    'test_vocabulary_query.py', 'test_vocabulary_quality.py',
    'test_vocabulary_resources.py', 'test_vocabulary_ui_helpers.py'
)

Write-Host "Verification mode: $Mode"
Write-Host "Project root: $projectRoot"
Write-Host "Python: $python"
Write-Host ''
Test-PythonSyntax
Test-PowerShellSyntax
foreach ($test in $sourceTests) { Invoke-TestFile -FileName $test }
Write-Result SKIP 'runtime/integration tests' 'source-only tree intentionally excludes bundled runtime, models, FFmpeg, and binary release outputs'
Write-Host ''
Write-Host ('SUMMARY mode={0} passed={1} failed={2} skipped={3} unavailable={4}' -f $Mode, $script:passed, $script:failed, $script:skipped, $script:unavailable)
if ($script:failed -gt 0 -or $script:unavailable -gt 0) { exit 1 }
exit 0
