$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not (Test-Path (Join-Path $repositoryRoot ".git") -PathType Container)) {
    throw "当前目录不是已克隆的 OculiDoC Git 仓库。"
}

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
$python311Ready = $false
if ($null -ne $pythonLauncher) {
    & $pythonLauncher.Source -3.11 -c `
        "import sys; assert sys.version_info[:2] == (3, 11), sys.version" *> $null
    $python311Ready = $LASTEXITCODE -eq 0
}

if (-not $python311Ready) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($null -eq $winget) {
        throw "未找到 Python 3.11 或 winget，无法完成一键安装。"
    }
    & $winget.Source install --id Python.Python.3.11 -e --source winget --silent `
        --accept-source-agreements --accept-package-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.11 自动安装失败。"
    }
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [Environment]::GetEnvironmentVariable("Path", "User")
    $pythonLauncher = Get-Command py -ErrorAction Stop
}

& $pythonLauncher.Source -3.11 -c `
    "import sys; assert sys.version_info[:2] == (3, 11), sys.version"
if ($LASTEXITCODE -ne 0) {
    throw "未找到可用的 Python 3.11。"
}

Set-Location $repositoryRoot
& $pythonLauncher.Source -3.11 -m venv .venv
if ($LASTEXITCODE -ne 0) {
    throw "创建仓库 .venv 失败。"
}

$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$pythonw = Join-Path $repositoryRoot ".venv\Scripts\pythonw.exe"
& $python -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw "初始化 pip 失败。" }
& $python -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { throw "升级安装工具失败。" }
& $python -m pip install --prefer-binary -e "$repositoryRoot[dev,research]"
if ($LASTEXITCODE -ne 0) { throw "安装 OculiDoC 依赖失败。" }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw "依赖核验失败。" }
& $python -c "import PySide6, matplotlib, oculidoc.app; print('OculiDoC ready')"
if ($LASTEXITCODE -ne 0) { throw "OculiDoC 启动导入检查失败。" }

if (-not (Test-Path $pythonw -PathType Leaf)) {
    throw "虚拟环境中没有 pythonw.exe。"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "OculiDoC.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "-m oculidoc"
$shortcut.WorkingDirectory = $repositoryRoot
$shortcut.Description = "启动 OculiDoC"
$icon = Join-Path $repositoryRoot "src\oculidoc\assets\app_icon.ico"
if (Test-Path $icon -PathType Leaf) {
    $shortcut.IconLocation = "$icon,0"
}
$shortcut.Save()

Write-Host "OCULIDOC_INSTALL=PASS" -ForegroundColor Green
Write-Host "DESKTOP_SHORTCUT=$shortcutPath"
