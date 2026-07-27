$ErrorActionPreference = "Stop"

$apiUrl = "https://api.github.com/repos/Etymodes/OculiDoC/releases/latest"
$installParent = Join-Path $env:LOCALAPPDATA "Programs"
$installRoot = Join-Path $installParent "OculiDoC"
$temporaryRoot = Join-Path $env:TEMP ("OculiDoC-install-" + [guid]::NewGuid())
$downloadRoot = Join-Path $temporaryRoot "download"
$extractRoot = Join-Path $temporaryRoot "extract"
$previousRoot = Join-Path $installParent ("OculiDoC.previous-" + [guid]::NewGuid())
$smokeReport = Join-Path $temporaryRoot "package-smoke.json"

New-Item -ItemType Directory -Force -Path $downloadRoot, $extractRoot, $installParent |
    Out-Null

try {
    $headers = @{
        "Accept" = "application/vnd.github+json"
        "User-Agent" = "OculiDoC-Installer"
        "X-GitHub-Api-Version" = "2022-11-28"
    }
    $release = Invoke-RestMethod -Uri $apiUrl -Headers $headers
    $zipAssets = @(
        $release.assets | Where-Object {
            $_.name -match "^OculiDoC-v[0-9.]+-windows-x64-portable\.zip$"
        }
    )
    if ($zipAssets.Count -ne 1) {
        throw "最新 Release 中便携包数量异常：$($zipAssets.Count)"
    }
    $zipAsset = $zipAssets[0]
    $hashAssets = @(
        $release.assets | Where-Object {
            $_.name -eq ($zipAsset.name + ".sha256")
        }
    )
    if ($hashAssets.Count -ne 1) {
        throw "最新 Release 中缺少唯一 SHA-256 文件。"
    }

    $zipPath = Join-Path $downloadRoot $zipAsset.name
    $hashPath = Join-Path $downloadRoot $hashAssets[0].name
    Invoke-WebRequest $zipAsset.browser_download_url -OutFile $zipPath
    Invoke-WebRequest $hashAssets[0].browser_download_url -OutFile $hashPath

    $hashText = (Get-Content $hashPath -Raw).Trim()
    if ($hashText -notmatch "^(?<hash>[0-9a-fA-F]{64})\s+\*?.+$") {
        throw "Release SHA-256 文件格式无效。"
    }
    $expectedHash = $Matches.hash.ToLowerInvariant()
    $actualHash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "便携包 SHA-256 不匹配。"
    }

    Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force
    $executables = @(
        Get-ChildItem $extractRoot -Recurse -File -Filter "OculiDoC.exe"
    )
    if ($executables.Count -ne 1) {
        throw "便携包内 OculiDoC.exe 数量异常：$($executables.Count)"
    }
    $newRoot = $executables[0].Directory.FullName

    $env:QT_QPA_PLATFORM = "offscreen"
    $smoke = Start-Process -FilePath $executables[0].FullName `
        -ArgumentList @("--package-smoke", "`"$smokeReport`"") -Wait -PassThru
    if ($smoke.ExitCode -ne 0 -or -not (Test-Path $smokeReport -PathType Leaf)) {
        throw "新便携包自检失败。"
    }

    if (Test-Path $installRoot) {
        Move-Item $installRoot $previousRoot
    }

    try {
        Move-Item $newRoot $installRoot
        $oldData = Join-Path $previousRoot "data"
        if (Test-Path $oldData -PathType Container) {
            $newData = Join-Path $installRoot "data"
            if (Test-Path $newData) {
                Remove-Item $newData -Recurse -Force
            }
            Copy-Item $oldData $newData -Recurse -Force
        }

        $installedExe = Join-Path $installRoot "OculiDoC.exe"
        if (-not (Test-Path $installedExe -PathType Leaf)) {
            throw "安装后未找到 OculiDoC.exe。"
        }
        $desktop = [Environment]::GetFolderPath("Desktop")
        $shortcutPath = Join-Path $desktop "OculiDoC.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $installedExe
        $shortcut.WorkingDirectory = $installRoot
        $shortcut.Description = "启动 OculiDoC"
        $shortcut.IconLocation = "$installedExe,0"
        $shortcut.Save()
    } catch {
        if (Test-Path $installRoot) {
            Remove-Item $installRoot -Recurse -Force
        }
        if (Test-Path $previousRoot) {
            Move-Item $previousRoot $installRoot
        }
        throw
    }

    if (Test-Path $previousRoot) {
        Remove-Item $previousRoot -Recurse -Force
    }

    Write-Host "OCULIDOC_PORTABLE_INSTALL=PASS" -ForegroundColor Green
    Write-Host "VERSION=$($release.tag_name)"
    Write-Host "INSTALL_ROOT=$installRoot"
} finally {
    if (Test-Path $temporaryRoot) {
        Remove-Item $temporaryRoot -Recurse -Force
    }
}
