param(
    [switch]$InstallDependencies,
    [switch]$ValidateOnly,
    [string]$PythonCommand = "python"
)

& {
    $ErrorActionPreference = "Stop"

    $scriptDirectory = $PSScriptRoot
    $repositoryRoot = Resolve-Path (
        Join-Path `
            $scriptDirectory `
            ".."
    )
    $specPath = Join-Path `
        $repositoryRoot `
        "packaging\windows\OculiDoC.spec"
    $iconPath = Join-Path `
        $repositoryRoot `
        "src\oculidoc\assets\app_icon.ico"
    $versionGenerator = Join-Path `
        $repositoryRoot `
        "scripts\generate_windows_version_info.py"
    $workRoot = Join-Path `
        $repositoryRoot `
        "build\pyinstaller"
    $distRoot = Join-Path `
        $repositoryRoot `
        "dist\windows"
    $versionFile = Join-Path `
        $workRoot `
        "OculiDoC_version_info.txt"
    $executablePath = Join-Path `
        $distRoot `
        "OculiDoC\OculiDoC.exe"

    foreach ($path in @(
        $specPath,
        $iconPath,
        $versionGenerator
    )) {
        if (-not (Test-Path $path -PathType Leaf)) {
            throw "缺少 Windows 构建文件：$path"
        }
    }

    & $PythonCommand `
        $versionGenerator `
        --pyproject (
            Join-Path `
                $repositoryRoot `
                "pyproject.toml"
        ) `
        --output $versionFile

    if ($LASTEXITCODE -ne 0) {
        throw "Windows 版本资源生成失败。"
    }

    if ($ValidateOnly) {
        Write-Host "SPEC=$specPath"
        Write-Host "ICON=$iconPath"
        Write-Host "VERSION_FILE=$versionFile"
        Write-Host "WINDOWS_BUILD_CONFIG_VALID=PASS"
        return
    }

    if ($env:OS -ne "Windows_NT") {
        throw "Windows EXE 构建只能在 Windows 上运行。"
    }

    & $PythonCommand `
        -m `
        PyInstaller `
        --version *> $null

    if ($LASTEXITCODE -ne 0) {
        if (-not $InstallDependencies) {
            throw (
                "当前环境没有 PyInstaller。" + "请使用 -InstallDependencies 重新执行。"
            )
        }

        $editableTarget = (
            "$repositoryRoot[build]"
        )
        & $PythonCommand `
            -m `
            pip `
            install `
            -e `
            $editableTarget

        if ($LASTEXITCODE -ne 0) {
            throw "构建依赖安装失败。"
        }
    }

    foreach ($path in @(
        $workRoot,
        $distRoot
    )) {
        if (Test-Path $path) {
            Remove-Item `
                $path `
                -Recurse `
                -Force
        }
    }

    New-Item `
        -ItemType Directory `
        -Path $workRoot `
        -Force | Out-Null
    New-Item `
        -ItemType Directory `
        -Path $distRoot `
        -Force | Out-Null

    & $PythonCommand `
        $versionGenerator `
        --pyproject (
            Join-Path `
                $repositoryRoot `
                "pyproject.toml"
        ) `
        --output $versionFile

    if ($LASTEXITCODE -ne 0) {
        throw "Windows 版本资源重新生成失败。"
    }

    $savedVersionFile = $env:OCULIDOC_VERSION_FILE
    $savedQtPlatform = $env:QT_QPA_PLATFORM

    try {
        $env:OCULIDOC_VERSION_FILE = $versionFile

        & $PythonCommand `
            -m `
            PyInstaller `
            --noconfirm `
            --clean `
            --workpath $workRoot `
            --distpath $distRoot `
            $specPath

        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller 构建失败。"
        }

        if (-not (Test-Path $executablePath -PathType Leaf)) {
            throw "未生成预期 EXE：$executablePath"
        }

        $requiredAssets = @(
            "app_icon.ico",
            "app_icon.png",
            "brand_mark_blue.png",
            "brand_mark_white.png",
            "brand_wordmark_blue.png"
        )

        foreach ($name in $requiredAssets) {
            $matches = @(
                Get-ChildItem `
                    (Split-Path $executablePath -Parent) `
                    -Recurse `
                    -File `
                    -Filter $name
            )

            if ($matches.Count -ne 1) {
                throw (
                    "冻结包内资源数量异常：" + "$name = $($matches.Count)"
                )
            }

            Write-Host (
                "BUNDLED_ASSET=" + $matches[0].FullName
            )
        }

        $smokePath = Join-Path `
            $env:TEMP `
            "OculiDoC_frozen_package_smoke.json"

        if (Test-Path $smokePath) {
            Remove-Item `
                $smokePath `
                -Force
        }

        $env:QT_QPA_PLATFORM = "offscreen"
        $process = Start-Process `
            -FilePath $executablePath `
            -ArgumentList @(
                "--package-smoke",
                $smokePath
            ) `
            -Wait `
            -PassThru

        if ($process.ExitCode -ne 0) {
            throw (
                "冻结程序自检退出码：" + $process.ExitCode
            )
        }

        if (-not (Test-Path $smokePath -PathType Leaf)) {
            throw "冻结程序没有生成自检报告。"
        }

        $smoke = Get-Content `
            $smokePath `
            -Raw |
            ConvertFrom-Json

        if ($smoke.ok -ne $true) {
            throw (
                "冻结程序自检失败：" + (
                    $smoke |
                        ConvertTo-Json `
                            -Depth 8
                )
            )
        }

        if ($smoke.frozen -ne $true) {
            throw "冻结程序自检未识别 frozen 状态。"
        }

        Add-Type `
            -AssemblyName `
            System.Drawing
        $embeddedIcon = (
            [System.Drawing.Icon]::ExtractAssociatedIcon(
                $executablePath
            )
        )

        if ($null -eq $embeddedIcon) {
            throw "无法从 EXE 提取内嵌图标。"
        }

        $embeddedIcon.Dispose()

        $versionInfo = (
            Get-Item $executablePath
        ).VersionInfo

        if ($versionInfo.ProductName -ne "OculiDoC") {
            throw (
                "EXE ProductName 异常：" + $versionInfo.ProductName
            )
        }

        $verification = [ordered]@{
            schema_version = "1.0"
            verified_at = (
                [DateTimeOffset]::Now.ToString("o")
            )
            executable = $executablePath
            executable_sha256 = (
                Get-FileHash `
                    $executablePath `
                    -Algorithm SHA256
            ).Hash
            executable_size_bytes = (
                Get-Item $executablePath
            ).Length
            product_name = $versionInfo.ProductName
            product_version = $versionInfo.ProductVersion
            file_version = $versionInfo.FileVersion
            embedded_icon = $true
            frozen_smoke_report = $smokePath
            frozen_smoke_ok = $true
        }
        $verificationPath = Join-Path `
            $distRoot `
            "OculiDoC_build_verification.json"
        $verification |
            ConvertTo-Json `
                -Depth 8 |
            Set-Content `
                $verificationPath `
                -Encoding utf8

        Write-Host "EXE=$executablePath"
        Write-Host "BUILD_VERIFICATION=$verificationPath"
        Write-Host "WINDOWS_EXE_BUILD_VERIFIED=PASS"
    } finally {
        if ($null -eq $savedVersionFile) {
            Remove-Item `
                Env:OCULIDOC_VERSION_FILE `
                -ErrorAction SilentlyContinue
        } else {
            $env:OCULIDOC_VERSION_FILE = $savedVersionFile
        }

        if ($null -eq $savedQtPlatform) {
            Remove-Item `
                Env:QT_QPA_PLATFORM `
                -ErrorAction SilentlyContinue
        } else {
            $env:QT_QPA_PLATFORM = $savedQtPlatform
        }
    }
}
