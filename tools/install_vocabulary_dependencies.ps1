[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'runtime\Python312\python.exe'
$resources = Join-Path $projectRoot 'runtime\vocabulary_resources'
$requirements = Join-Path $resources 'wordfreq-requirements.txt'
$vendor = Join-Path $resources 'vendor'

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "缺少便携 Python：$python"
}
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "缺少锁定依赖文件：$requirements"
}
if (Test-Path -LiteralPath $vendor) {
    throw "vendor 已存在；为避免混入旧版本，本脚本只安装到空目标：$vendor"
}

$temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) (
    'subtitle-vocabulary-wordfreq-' + [Guid]::NewGuid().ToString('N')
)
$wheels = Join-Path $temporaryRoot 'wheels'
$staging = Join-Path $temporaryRoot 'vendor'
[void](New-Item -ItemType Directory -Path $wheels -Force)
[void](New-Item -ItemType Directory -Path $staging -Force)

try {
    & $python -m pip download `
        --only-binary=:all: `
        --dest $wheels `
        --requirement $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "下载 wordfreq 锁定 wheels 失败，exit=$LASTEXITCODE"
    }

    & $python -m pip install `
        --no-index `
        --no-deps `
        --find-links $wheels `
        --target $staging `
        --requirement $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "安装 wordfreq vendor 失败，exit=$LASTEXITCODE"
    }

    $validationScript = @'
import importlib
import importlib.metadata
import pathlib
import sys

vendor = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(vendor))
package_name = sys.argv[2]
expected_version = sys.argv[3]
probe_word = sys.argv[4]
language = sys.argv[5]
module = importlib.import_module(package_name)

assert importlib.metadata.version(package_name) == expected_version
assert module.zipf_frequency(probe_word, language) > 0.0
'@
    & $python -c $validationScript $staging 'wordfreq' '3.1.1' 'the' 'en'
    if ($LASTEXITCODE -ne 0) {
        throw "wordfreq vendor 自检失败，exit=$LASTEXITCODE"
    }

    [void](New-Item -ItemType Directory -Path $resources -Force)
    Move-Item -LiteralPath $staging -Destination $vendor
    Write-Host "wordfreq vendor 安装完成：$vendor"
} finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedTemp = (Resolve-Path -LiteralPath $temporaryRoot).Path
        $systemTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedTemp.StartsWith($systemTemp, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
        }
    }
}
