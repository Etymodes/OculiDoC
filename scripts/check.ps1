$ErrorActionPreference = "Stop"

$python = Join-Path $env:USERPROFILE "Envs\ops\Scripts\python.exe"

if (-not (Test-Path $python)) {
    throw "没有找到 ops Python：$python"
}

Write-Host "1/3 Ruff format"
& $python -m ruff format --check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "2/3 Ruff lint"
& $python -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "3/3 Pytest"
& $python -m pytest
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "OCULIDOC_CHECKS=PASS"