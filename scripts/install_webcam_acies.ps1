param(
    [string]$PythonCommand = '',
    [int]$CameraIndex = 0,
    [string]$DataDir = '',
    [string]$ConfigPath = '',
    [string]$RuntimeRoot = '',
    [string]$AciesSourceDir = ''
)
# Python/Qt launched from PowerShell 7 can pass Core module paths to 5.1.
# Use this install process's bundled modules; retain proxy and other environment settings.
$env:PSModulePath = [IO.Path]::Combine($PSHOME, 'Modules')
$ErrorActionPreference = 'Stop'
Import-Module ([IO.Path]::Combine($PSHOME, 'Modules', 'Microsoft.PowerShell.Utility', 'Microsoft.PowerShell.Utility.psd1')) -Force
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if ($CameraIndex -lt 0 -or $CameraIndex -gt 16) { throw 'Camera index must be from 0 to 16.' }
if (-not [Environment]::Is64BitOperatingSystem) { throw 'ACIES requires 64-bit Windows.' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $DataDir) { $DataDir = Join-Path $repo 'data' }
if (-not $ConfigPath) { $ConfigPath = Join-Path $DataDir 'runtime\webcam_providers.json' }
if (-not $RuntimeRoot) { $RuntimeRoot = Join-Path (Split-Path $repo) 'External\Webcam\ACIES' }
if (-not $AciesSourceDir) { $AciesSourceDir = Join-Path $repo 'src\oculidoc\devices' }
$ConfigPath = [IO.Path]::GetFullPath($ConfigPath)
$RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot)
if (-not (Test-Path -LiteralPath (Join-Path $AciesSourceDir 'acies.py'))) {
    throw 'ACIES source resource is missing. Install a complete OculiDoC package.'
}
# Validate an existing configuration before touching runtimes or provider settings.
$config = @{ camera_index=$CameraIndex; camera_width=1280; camera_height=720; providers=@{} }
if (Test-Path -LiteralPath $ConfigPath) {
    $previous = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($previous -isnot [PSCustomObject] -or $previous.providers -isnot [PSCustomObject]) {
        throw 'Existing webcam configuration is invalid (providers must be an object); preserved unchanged.'
    }
    if ($null -ne $previous.PSObject.Properties['camera_index'] -and (
        $previous.camera_index -isnot [int] -or $previous.camera_index -lt 0 -or $previous.camera_index -gt 16
    )) { throw 'Existing camera index is invalid; configuration preserved unchanged.' }
    foreach ($size in @(@('camera_width',3840), @('camera_height',2160))) {
        $value = $previous.($size[0])
        if ($null -ne $previous.PSObject.Properties[$size[0]] -and ($value -isnot [int] -or $value -lt 160 -or $value -gt $size[1])) {
            throw 'Existing camera dimensions are invalid; configuration preserved unchanged.'
        }
    }
    # Omitted defaults are part of an old calibration identity; do not add keys.
    $config = @{}
    foreach ($property in $previous.PSObject.Properties) { $config[$property.Name] = $property.Value }
    $config.providers = @{}
    foreach ($property in $previous.providers.PSObject.Properties) { $config.providers[$property.Name] = $property.Value }
    $previousCamera = 0
    if ($null -ne $previous.PSObject.Properties['camera_index']) { $previousCamera = $previous.camera_index }
    if ($CameraIndex -ne $previousCamera) { $config.camera_index = $CameraIndex }
}
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
$env:PIP_CACHE_DIR = Join-Path $RuntimeRoot 'pip-cache'
$env:TEMP = Join-Path $RuntimeRoot 'tmp'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
function Test-AciesRuntime([string]$Python, [string]$Model) {
    if (-not $Python -or -not $Model -or -not (Test-Path -LiteralPath $Python) -or -not (Test-Path -LiteralPath $Model)) { return $false }
    try {
        & $Python -c 'import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize(chr(80))==8; sys.path.insert(0,sys.argv[1]); import cv2,numpy; from acies import AciesExtractor; e=AciesExtractor(sys.argv[2]); e.close()' $AciesSourceDir $Model | Out-Host
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
function Test-OfficialFaceModel([string]$Model) {
    if (-not $Model -or -not (Test-Path -LiteralPath $Model -PathType Leaf)) { return $false }
    try {
        return ((Get-FileHash -LiteralPath $Model -Algorithm SHA256).Hash -eq '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff')
    } catch { return $false }
}
$providerPython = ''
$model = ''
# Only reuse files that really exist and initialize on this computer.
$existing = $config.providers['acies']
if ($existing -and (Test-AciesRuntime $existing.python $existing.face_model)) {
    $providerPython = $existing.python
    $model = $existing.face_model
}
$localExternal = Join-Path (Split-Path $repo) 'External\Webcam\ACIES'
if (-not $providerPython) {
    $localPython = Join-Path $localExternal '.venv\Scripts\python.exe'
    $localModel = Join-Path $localExternal 'models\face_landmarker.task'
    if (Test-AciesRuntime $localPython $localModel) { $providerPython = $localPython; $model = $localModel }
}
if (-not $providerPython) {
    # Keep native-wheel DLL paths below Windows MAX_PATH on ordinary desktops.
    $installation = Join-Path $RuntimeRoot ('p-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
    if (Test-Path -LiteralPath $installation) { throw 'Private runtime path collision; retry installation.' }
    New-Item -ItemType Directory -Path $installation -Force | Out-Null
    $downloads = Join-Path $RuntimeRoot 'downloads'
    New-Item -ItemType Directory -Path $downloads -Force | Out-Null
    if (-not $PythonCommand) {
        $repoPython = Join-Path $repo '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $repoPython) { $PythonCommand = $repoPython }
    }
    $baseAvailable = $false
    if ($PythonCommand) {
        try {
            & $PythonCommand -c 'import sys,struct; assert sys.version_info[:2]==(3,11) and struct.calcsize(chr(80))==8' | Out-Host
            $baseAvailable = ($LASTEXITCODE -eq 0)
        } catch { $baseAvailable = $false }
    }
    if ($baseAvailable) {
        Write-Host 'Creating private Python 3.11 virtual environment...'
        & $PythonCommand -m venv $installation
        if ($LASTEXITCODE -ne 0) { throw 'ACIES environment creation failed.' }
        $providerPython = Join-Path $installation 'Scripts\python.exe'
    } else {
        Write-Host 'Downloading official private Python 3.11.9 (no system installation)...'
        $archive = Join-Path $downloads 'python-3.11.9-embed-amd64.zip'
        $pythonSha256 = '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
        if (-not (Test-Path -LiteralPath $archive) -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $pythonSha256) {
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -OutFile ($archive + '.part')
            if ((Get-FileHash -LiteralPath ($archive + '.part') -Algorithm SHA256).Hash -ne $pythonSha256) {
                throw 'Official Python archive SHA256 verification failed. Configuration was not changed.'
            }
            Move-Item -LiteralPath ($archive + '.part') -Destination $archive -Force
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $installation
        # The private embedded runtime uses an explicit isolated package search path.
        @('python311.zip', '.', 'Lib\site-packages', 'import site') | Set-Content -LiteralPath (Join-Path $installation 'python311._pth') -Encoding ASCII
        $providerPython = Join-Path $installation 'python.exe'
        $getPip = Join-Path $downloads 'get-pip.py'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile ($getPip + '.part')
        Move-Item -LiteralPath ($getPip + '.part') -Destination $getPip -Force
        & $providerPython $getPip --no-warn-script-location
        if ($LASTEXITCODE -ne 0) { throw 'Private Python pip bootstrap failed.' }
    }
    Write-Host 'Installing ACIES landmark dependencies...'
    & $providerPython -m pip install --disable-pip-version-check --no-warn-script-location 'mediapipe==1.1.0' 'numpy==1.26.4' 'opencv-contrib-python==4.11.0.86'
    if ($LASTEXITCODE -ne 0) { throw 'ACIES vision dependencies installation failed. Existing provider configuration was not changed.' }
    & $providerPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'ACIES dependency check failed. Existing provider configuration was not changed.' }
    $model = Join-Path $installation 'models\face_landmarker.task'
    New-Item -ItemType Directory -Path (Split-Path $model) -Force | Out-Null
    $modelCandidates = @(
        (Join-Path $localExternal 'models\face_landmarker.task'),
        (Join-Path (Split-Path $repo) 'External\Webcam\EyeTrax\models\face_landmarker.task')
    )
    $existingModel = $modelCandidates | Where-Object { Test-OfficialFaceModel $_ } | Select-Object -First 1
    if ($existingModel) { Copy-Item -LiteralPath $existingModel -Destination ($model + '.part') }
    else {
        Write-Host 'Downloading official Face Landmarker model...'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 180 -Uri 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task' -OutFile ($model + '.part')
    }
    if (-not (Test-OfficialFaceModel ($model + '.part'))) {
        throw 'Official Face Landmarker model SHA256 verification failed. Configuration was not changed.'
    }
    Move-Item -LiteralPath ($model + '.part') -Destination $model
    if (-not (Test-AciesRuntime $providerPython $model)) { throw 'ACIES model initialization failed. Existing provider configuration was not changed.' }
}
$modelHash = Get-FileHash -LiteralPath $model -Algorithm SHA256
if ($config.providers.ContainsKey('acies') -and -not $config.ContainsKey('calibration_identity_providers')) {
    $identityProviders = @{}
    foreach ($key in $config.providers.Keys) { $identityProviders[$key] = $config.providers[$key] }
    $config['calibration_identity_providers'] = $identityProviders
}
$config.providers['acies'] = @{ python=[IO.Path]::GetFullPath($providerPython); face_model=[IO.Path]::GetFullPath($model) }
New-Item -ItemType Directory -Path (Split-Path $ConfigPath) -Force | Out-Null
$temporary = $ConfigPath + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
try {
    $config | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $temporary -Encoding UTF8
    if (Test-Path -LiteralPath $ConfigPath) {
        $backup = $ConfigPath + '.before-acies-' + (Get-Date -Format 'yyyyMMddHHmmssfff') + '.json'
        [IO.File]::Replace($temporary, $ConfigPath, $backup)
    } else { [IO.File]::Move($temporary, $ConfigPath) }
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
$modelHash | Format-List
Write-Host "ACIES installed and verified. Configuration: $ConfigPath"
Write-Host 'Open device settings, select ACIES, and calibrate the current participant.'
