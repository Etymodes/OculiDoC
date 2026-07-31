$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $python -PathType Leaf)) {
    throw "未找到仓库虚拟环境。请先运行 scripts\install.ps1。"
}

Set-Location $repositoryRoot
& $python -m oculidoc.api
exit $LASTEXITCODE
