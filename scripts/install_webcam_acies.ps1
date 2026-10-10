param([string]$PythonCommand = '', [int]$CameraIndex = 0)
$ErrorActionPreference = 'Stop'
if ($CameraIndex -lt 0 -or $CameraIndex -gt 16) { throw 'Camera index must be from 0 to 16.' }
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonCommand) { $PythonCommand = Join-Path $repo '.venv\Scripts\python.exe' }
$external = Join-Path (Split-Path $repo) 'External\Webcam\ACIES'
$providerPython = Join-Path $external '.venv\Scripts\python.exe'
$model = Join-Path $external 'models\face_landmarker.task'
$configPath = Join-Path $repo 'data\runtime\webcam_providers.json'
New-Item -ItemType Directory -Path (Split-Path $model) -Force | Out-Null
if (-not (Test-Path -LiteralPath $providerPython)) {
    & $PythonCommand -m venv (Join-Path $external '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'ACIES environment creation failed.' }
}
# Generic landmark frontend only. No third-party gaze estimator is installed.
& $providerPython -m pip install 'mediapipe==1.1.0' 'numpy==1.26.4' 'opencv-contrib-python==4.11.0.86'
if ($LASTEXITCODE -ne 0) { throw 'ACIES vision dependencies installation failed.' }
& $providerPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'ACIES dependency check failed.' }
if (-not (Test-Path -LiteralPath $model)) {
    $existingModel = Join-Path (Split-Path $repo) 'External\Webcam\EyeTrax\models\face_landmarker.task'
    if (Test-Path -LiteralPath $existingModel) {
        Copy-Item -LiteralPath $existingModel -Destination ($model + '.part')
    } else {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 -Uri 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task' -OutFile ($model + '.part')
    }
    Move-Item -LiteralPath ($model + '.part') -Destination $model
}
& $providerPython -c 'import sys; sys.path.insert(0,sys.argv[1]); from acies import AciesExtractor; e=AciesExtractor(sys.argv[2]); e.close()' (Join-Path $repo 'src\oculidoc\devices') $model
if ($LASTEXITCODE -ne 0) { throw 'ACIES landmark model initialization failed.' }
$config = @{ camera_index=$CameraIndex; camera_width=1280; camera_height=720; providers=@{} }
if (Test-Path -LiteralPath $configPath) {
    $previousConfig = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $config.camera_index = $previousConfig.camera_index
    if ($previousConfig.camera_width) { $config.camera_width = $previousConfig.camera_width }
    if ($previousConfig.camera_height) { $config.camera_height = $previousConfig.camera_height }
    foreach ($property in $previousConfig.providers.PSObject.Properties) {
        $config.providers[$property.Name] = $property.Value
    }
    Copy-Item -LiteralPath $configPath -Destination ($configPath + '.before-acies-' + (Get-Date -Format 'yyyyMMddHHmmssfff') + '.json')
}
$config.providers['acies'] = @{ python=$providerPython; face_model=$model }
New-Item -ItemType Directory -Path (Split-Path $configPath) -Force | Out-Null
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath ($configPath + '.tmp') -Encoding UTF8
Move-Item -LiteralPath ($configPath + '.tmp') -Destination $configPath -Force
Get-FileHash -LiteralPath $model -Algorithm SHA256
Write-Host "ACIES installed. Configuration: $configPath"
Write-Host 'Open device settings, select ACIES, set calibration tolerance, and calibrate again.'
