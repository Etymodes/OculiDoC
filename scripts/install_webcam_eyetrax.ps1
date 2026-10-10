param([string]$PythonCommand = '', [int]$CameraIndex = 0)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $PythonCommand) { $PythonCommand = Join-Path $repo '.venv\Scripts\python.exe' }
$external = Join-Path (Split-Path $repo) 'External\Webcam\EyeTrax'
$providerPython = Join-Path $external '.venv\Scripts\python.exe'
$model = Join-Path $external 'models\face_landmarker.task'
$configPath = Join-Path $repo 'data\runtime\webcam_providers.json'
New-Item -ItemType Directory -Path (Split-Path $model) -Force | Out-Null
if (-not (Test-Path -LiteralPath $providerPython)) {
    & $PythonCommand -m venv (Join-Path $external '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Provider environment creation failed.' }
}
& $providerPython -m pip install 'eyetrax==0.4.0' 'numpy<2' 'opencv-python==4.11.0.86' 'opencv-contrib-python==4.11.0.86'
if ($LASTEXITCODE -ne 0) { throw 'EyeTrax installation failed.' }
& $providerPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Provider dependency check failed.' }
if (-not (Test-Path -LiteralPath $model)) {
    Invoke-WebRequest -UseBasicParsing -Uri 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task' -OutFile ($model + '.part')
    Move-Item -LiteralPath ($model + '.part') -Destination $model
}
& $providerPython -c 'import sys; from eyetrax import GazeEstimator; e=GazeEstimator(face_landmarker_model=sys.argv[1]); e.close()' $model
if ($LASTEXITCODE -ne 0) { throw 'EyeTrax model initialization failed.' }
if (Test-Path -LiteralPath $configPath) {
    Write-Host "Existing provider configuration preserved: $configPath"
} else {
    New-Item -ItemType Directory -Path (Split-Path $configPath) -Force | Out-Null
    @{ camera_index=$CameraIndex; providers=@{ eyetrax=@{ python=$providerPython; face_model=$model } } } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $configPath -Encoding UTF8
}
Write-Host "EyeTrax installed. Configuration: $configPath"
Write-Host 'Next: choose webcam in device settings and calibrate the current participant on the patient screen.'
