param(
    [string]$PythonCommand = "python",
    [string]$InnoCompiler = ""
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bundleRoot = Join-Path $repositoryRoot "dist\windows\OculiDoC"
$releaseRoot = Join-Path $repositoryRoot "dist\release"
$verificationSource = Join-Path $repositoryRoot `
    "dist\windows\OculiDoC_build_verification.json"
$installerDefinition = Join-Path $repositoryRoot `
    "packaging\windows\OculiDoC.iss"

if (-not (Test-Path (Join-Path $bundleRoot "OculiDoC.exe") -PathType Leaf)) {
    throw "未找到已验证的 Windows 程序目录。"
}
if (-not (Test-Path $verificationSource -PathType Leaf)) {
    throw "未找到 Windows 构建核验报告。"
}
if (-not (Test-Path $installerDefinition -PathType Leaf)) {
    throw "未找到 Windows 安装器定义。"
}

$requiredBundleDocuments = @(
    "LICENSE-v0.1.1.txt",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "THIRD_PARTY_LICENSES.json",
    "QT_SOURCE_OFFER.md"
)
foreach ($name in $requiredBundleDocuments) {
    $path = Join-Path $bundleRoot $name
    if (-not (Test-Path $path -PathType Leaf)) {
        throw "冻结包缺少发行许可文件：$name"
    }
}

$bundleLicenseRoot = Join-Path $bundleRoot "licenses"
$bundleLicenseFiles = @(
    Get-ChildItem $bundleLicenseRoot -Recurse -File -Filter "*.txt"
)
if ($bundleLicenseFiles.Count -lt 41) {
    throw "冻结包内完整许可文本数量不足：$($bundleLicenseFiles.Count)"
}

$thirdPartyLicenseReport = Join-Path $bundleRoot "THIRD_PARTY_LICENSES.json"
$thirdPartyLicenseRecords = @(
    Get-Content $thirdPartyLicenseReport -Raw |
        ConvertFrom-Json
)
if ($thirdPartyLicenseRecords.Count -lt 1) {
    throw "冻结包内第三方许可证清单为空。"
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

foreach ($name in $requiredBundleDocuments) {
    Copy-Item `
        (Join-Path $bundleRoot $name) `
        (Join-Path $releaseRoot $name) `
        -Force
}

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
    $requiredDocumentCounts = [ordered]@{}
    foreach ($name in $requiredBundleDocuments) {
        $requiredDocumentCounts[$name] = @(
            $archive.Entries | Where-Object {
                $_.FullName -match (
                    "(^|/)" + [regex]::Escape($name) + "$"
                )
            }
        ).Count
    }
    $licenseTextCount = @(
        $archive.Entries | Where-Object {
            $_.FullName -match "/licenses/.+\.txt$"
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
foreach ($name in $requiredDocumentCounts.Keys) {
    if ($requiredDocumentCounts[$name] -ne 1) {
        throw (
            "便携 ZIP 内发行许可文件数量异常：" +
            "$name = $($requiredDocumentCounts[$name])"
        )
    }
}
if ($licenseTextCount -lt 41) {
    throw "便携 ZIP 内完整许可文本数量不足：$licenseTextCount"
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
    release_document_count = $requiredBundleDocuments.Count
    third_party_license_record_count = (
        $thirdPartyLicenseRecords.Count
    )
    complete_license_text_count = $licenseTextCount
}
$manifest | ConvertTo-Json -Depth 4 |
    Set-Content (Join-Path $releaseRoot "OculiDoC_release_manifest.json") -Encoding utf8

if (-not $InnoCompiler) {
    $compilerCandidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    $InnoCompiler = $compilerCandidates |
        Where-Object { $_ -and (Test-Path $_ -PathType Leaf) } |
        Select-Object -First 1
}
if (-not $InnoCompiler -or -not (Test-Path $InnoCompiler -PathType Leaf)) {
    throw "未找到 Inno Setup 6 编译器 ISCC.exe。"
}

& $InnoCompiler `
    "/DAppVersion=$version" `
    "/DSourceDir=$bundleRoot" `
    "/DOutputDir=$releaseRoot" `
    $installerDefinition
if ($LASTEXITCODE -ne 0) {
    throw "Windows 安装器构建失败。"
}

$setupPath = Join-Path $releaseRoot "OculiDoC-Setup.exe"
if (-not (Test-Path $setupPath -PathType Leaf)) {
    throw "未生成 OculiDoC-Setup.exe。"
}
$setupHash = (Get-FileHash $setupPath -Algorithm SHA256).Hash.ToLowerInvariant()
"$setupHash *OculiDoC-Setup.exe" |
    Set-Content "$setupPath.sha256" -Encoding ascii

Write-Host "RELEASE_PACKAGE=$zipPath"
Write-Host "RELEASE_SHA256=$zipHash"
Write-Host "INSTALLER=$setupPath"
Write-Host "INSTALLER_SHA256=$setupHash"
Write-Host "WINDOWS_RELEASE_PACKAGE=PASS" -ForegroundColor Green
