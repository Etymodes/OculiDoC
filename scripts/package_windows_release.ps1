param(
    [string]$PythonCommand = "python"
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bundleRoot = Join-Path $repositoryRoot "dist\windows\OculiDoC"
$releaseRoot = Join-Path $repositoryRoot "dist\release"
$verificationSource = Join-Path $repositoryRoot `
    "dist\windows\OculiDoC_build_verification.json"

if (-not (Test-Path (Join-Path $bundleRoot "OculiDoC.exe") -PathType Leaf)) {
    throw "未找到已验证的 Windows 程序目录。"
}
if (-not (Test-Path $verificationSource -PathType Leaf)) {
    throw "未找到 Windows 构建核验报告。"
}

Set-Location $repositoryRoot
$version = (& $PythonCommand -c `
    "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
).Trim()
if ($LASTEXITCODE -ne 0 -or $version -notmatch "^[0-9]+\.[0-9]+\.[0-9]+$") {
    throw "无法读取正式版本号。"
}

if (Test-Path $releaseRoot) {
    Remove-Item $releaseRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null

$zipName = "OculiDoC-v$version-windows-x64-portable.zip"
$zipPath = Join-Path $releaseRoot $zipName
Compress-Archive -Path $bundleRoot -DestinationPath $zipPath -CompressionLevel Optimal

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    $exeCount = @(
        $archive.Entries | Where-Object { $_.FullName -match "(^|/)OculiDoC\.exe$" }
    ).Count
    $stimulusCount = @(
        $archive.Entries | Where-Object {
            $_.FullName -match "/assets/stimuli/.+\.png$"
        }
    ).Count
} finally {
    $archive.Dispose()
}

if ($exeCount -ne 1) {
    throw "便携 ZIP 内 OculiDoC.exe 数量异常：$exeCount"
}
if ($stimulusCount -ne 76) {
    throw "便携 ZIP 内刺激图数量异常：$stimulusCount"
}

$zipHash = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$hashPath = "$zipPath.sha256"
"$zipHash *$zipName" | Set-Content $hashPath -Encoding ascii

$installerSource = Join-Path $repositoryRoot "scripts\install_portable.ps1"
$installerPath = Join-Path $releaseRoot "Install-OculiDoC.ps1"
Copy-Item $installerSource $installerPath
$installerHash = (Get-FileHash $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$installerHash *Install-OculiDoC.ps1" |
    Set-Content "$installerPath.sha256" -Encoding ascii

Copy-Item $verificationSource `
    (Join-Path $releaseRoot "OculiDoC_build_verification.json")

$manifest = [ordered]@{
    schema_version = "1.0"
    version = $version
    package = $zipName
    package_sha256 = $zipHash
    package_size_bytes = (Get-Item $zipPath).Length
    executable_count = $exeCount
    reviewed_stimulus_png_count = $stimulusCount
}
$manifest | ConvertTo-Json -Depth 4 |
    Set-Content (Join-Path $releaseRoot "OculiDoC_release_manifest.json") -Encoding utf8

Write-Host "RELEASE_PACKAGE=$zipPath"
Write-Host "RELEASE_SHA256=$zipHash"
Write-Host "WINDOWS_RELEASE_PACKAGE=PASS" -ForegroundColor Green
