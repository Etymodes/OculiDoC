param(
    [string]$BundleRoot = "",
    [string]$OutputPath = "",
    [switch]$RequireTrustedRsa
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $BundleRoot) {
    $BundleRoot = Join-Path $repositoryRoot "dist\windows\OculiDoC"
}
if (-not (Test-Path $BundleRoot -PathType Container)) {
    throw "未找到 Windows 签名清单目录：$BundleRoot"
}

$bundleRootPath = (Resolve-Path $BundleRoot).Path
if (-not $OutputPath) {
    $OutputPath = Join-Path `
        (Split-Path $bundleRootPath -Parent) `
        "OculiDoC_bundle_signing_inventory.json"
}
$outputFullPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
    $OutputPath
)
if ([System.IO.Path]::GetExtension($outputFullPath).ToLowerInvariant() -ne ".json") {
    throw "Windows 签名清单输出必须是 JSON 文件：$outputFullPath"
}

$candidates = @(
    Get-ChildItem $bundleRootPath -Recurse -File |
        Where-Object {
            @(".exe", ".dll", ".pyd", ".ps1") -contains $_.Extension.ToLowerInvariant()
        } |
        Sort-Object FullName
)
if ($candidates.Count -eq 0) {
    throw "Windows 签名清单目录中没有 EXE、DLL、PYD 或 PS1：$bundleRootPath"
}
if (
    @(
        $candidates | Where-Object {
            [System.String]::Equals(
                $_.FullName,
                $outputFullPath,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }
    ).Count -ne 0
) {
    throw "Windows 签名清单输出不能覆盖待签名文件：$outputFullPath"
}

$outputDirectory = Split-Path $outputFullPath -Parent
if (-not (Test-Path $outputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}

$files = @(
    foreach ($candidate in $candidates) {
        $signature = Get-AuthenticodeSignature -LiteralPath $candidate.FullName
        $certificate = $signature.SignerCertificate
        $publicKeyAlgorithm = $null
        $isRsa = $false

        if ($null -ne $certificate) {
            $publicKeyOid = $certificate.PublicKey.Oid
            if ($null -ne $publicKeyOid) {
                if ($publicKeyOid.FriendlyName) {
                    $publicKeyAlgorithm = $publicKeyOid.FriendlyName
                } else {
                    $publicKeyAlgorithm = $publicKeyOid.Value
                }
            }

            $rsaKey = [System.Security.Cryptography.X509Certificates.RSACertificateExtensions]::GetRSAPublicKey(
                $certificate
            )
            if ($null -ne $rsaKey) {
                $isRsa = $true
                $rsaKey.Dispose()
            }
        }

        $isValidRsa = $signature.Status.ToString() -eq "Valid" -and $isRsa
        [ordered]@{
            relative_path = [System.IO.Path]::GetRelativePath(
                $bundleRootPath,
                $candidate.FullName
            ).Replace("\", "/")
            extension = $candidate.Extension.ToLowerInvariant()
            sha256 = (Get-FileHash $candidate.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            authenticode_status = $signature.Status.ToString()
            signer_subject = if ($null -ne $certificate) { $certificate.Subject } else { $null }
            signer_thumbprint = if ($null -ne $certificate) {
                $certificate.Thumbprint
            } else {
                $null
            }
            public_key_algorithm = $publicKeyAlgorithm
            valid_rsa_signature = $isValidRsa
            needs_rsa_trusted_signature = -not $isValidRsa
        }
    }
)

$validRsaCount = @($files | Where-Object { $_.valid_rsa_signature }).Count
$needsRsaCount = $files.Count - $validRsaCount
$countsByExtension = [ordered]@{}
foreach ($extension in @(".exe", ".dll", ".pyd", ".ps1")) {
    $extensionFiles = @($files | Where-Object { $_.extension -eq $extension })
    $extensionValidRsa = @(
        $extensionFiles | Where-Object { $_.valid_rsa_signature }
    ).Count
    $countsByExtension[$extension.Substring(1)] = [ordered]@{
        candidate_files = $extensionFiles.Count
        valid_rsa_signatures = $extensionValidRsa
        needs_rsa_trusted_signature = $extensionFiles.Count - $extensionValidRsa
    }
}
$report = [ordered]@{
    schema_version = "1.0"
    generated_at_utc = [DateTimeOffset]::UtcNow.ToString("o")
    inventory_root_name = Split-Path $bundleRootPath -Leaf
    counts = [ordered]@{
        candidate_files = $files.Count
        valid_rsa_signatures = $validRsaCount
        needs_rsa_trusted_signature = $needsRsaCount
    }
    counts_by_extension = $countsByExtension
    files = $files
}

$report |
    ConvertTo-Json -Depth 6 |
    Set-Content $outputFullPath -Encoding utf8

Write-Host "WINDOWS_SIGNING_INVENTORY_PATH=$outputFullPath"
Write-Host "WINDOWS_SIGNING_CANDIDATES=$($files.Count)"
Write-Host "WINDOWS_SIGNING_VALID_RSA=$validRsaCount"
Write-Host "WINDOWS_SIGNING_NEEDS_RSA=$needsRsaCount"
foreach ($extensionName in $countsByExtension.Keys) {
    $extensionLabel = $extensionName.ToUpperInvariant()
    $extensionCounts = $countsByExtension[$extensionName]
    Write-Host (
        "WINDOWS_SIGNING_${extensionLabel}_CANDIDATES=" +
        $extensionCounts.candidate_files
    )
    Write-Host (
        "WINDOWS_SIGNING_${extensionLabel}_NEEDS_RSA=" +
        $extensionCounts.needs_rsa_trusted_signature
    )
}

if ($RequireTrustedRsa -and $needsRsaCount -ne 0) {
    throw "仍有 $needsRsaCount 个文件缺少有效、受信任的 RSA Authenticode 签名。"
}

Write-Host "WINDOWS_SIGNING_INVENTORY=PASS" -ForegroundColor Green
