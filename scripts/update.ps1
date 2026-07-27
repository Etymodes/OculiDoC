$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$git = (Get-Command git -ErrorAction Stop).Source

if (-not (Test-Path $python -PathType Leaf)) {
    throw "未找到仓库虚拟环境。请先运行 scripts\install.ps1。"
}

$origin = (& $git -C $repositoryRoot remote get-url origin).Trim()
if ($origin -notmatch "(?i)(?:github\.com[:/])Etymodes/OculiDoC(?:\.git)?$") {
    throw "当前仓库不是官方 Etymodes/OculiDoC：$origin"
}

$branch = (& $git -C $repositoryRoot branch --show-current).Trim()
if ($branch -ne "main") {
    throw "一键更新仅支持 main 分支；当前分支为 $branch。"
}

if ((& $git -C $repositoryRoot status --porcelain).Trim()) {
    throw "仓库存在未提交修改，已停止更新以免覆盖文件。"
}

$before = (& $git -C $repositoryRoot rev-parse HEAD).Trim()
& $git -C $repositoryRoot fetch origin main
if ($LASTEXITCODE -ne 0) { throw "获取 main 更新失败。" }
& $git -C $repositoryRoot merge --ff-only origin/main
if ($LASTEXITCODE -ne 0) { throw "main 无法安全快进，未执行更新。" }
$after = (& $git -C $repositoryRoot rev-parse HEAD).Trim()

Set-Location $repositoryRoot
& $python -m pip install --prefer-binary -e "$repositoryRoot[dev,research]"
if ($LASTEXITCODE -ne 0) { throw "更新依赖失败。" }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw "更新后的依赖核验失败。" }

Write-Host "OCULIDOC_UPDATE=PASS" -ForegroundColor Green
Write-Host "BEFORE=$before"
Write-Host "AFTER=$after"
