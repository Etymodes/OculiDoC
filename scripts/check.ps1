$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$smokeReport = Join-Path $env:TEMP "OculiDoC_source_package_smoke.json"

if (-not (Test-Path $python -PathType Leaf)) {
    throw "未找到仓库虚拟环境。请先运行 scripts\install.ps1。"
}

Set-Location $repositoryRoot
$env:OCULIDOC_ENVIRONMENT = "test"
$env:OCULIDOC_GAZE_SOURCE = "mock"
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONUTF8 = "1"

Write-Host "1/6 Python 3.11 与依赖"
& $python -c "import sys; assert sys.version_info[:2] == (3, 11), sys.version"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "2/6 包内资源自检"
& $python -m oculidoc --package-smoke $smokeReport
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "3/6 Ruff format"
& $python -m ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "4/6 Ruff lint"
& $python -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "5/6 Pytest"
& $python -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "6/6 Compile"
& $python -m compileall -q src tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "OCULIDOC_CHECKS=PASS" -ForegroundColor Green
