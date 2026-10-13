$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if ($env:CONDA_PREFIX -or $env:VIRTUAL_ENV) {
    $PredictorPython = (Get-Command python).Source
    & $PredictorPython -c 'import sys; assert sys.version_info >= (3,12); import numpy, scipy, xarray, netCDF4, fastapi, uvicorn, pydantic, yaml, dotenv, filelock'
    if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12+ and run: python -m pip install -r requirements.txt" }
} else {
    $PredictorEnvironment = if ($env:BALLOONPREDICTOR_VENV) { $env:BALLOONPREDICTOR_VENV } else { Join-Path $env:LOCALAPPDATA "BalloonPredictor\venv" }
    if ((Test-Path ".venv\Scripts\python.exe") -and -not $env:BALLOONPREDICTOR_VENV) { $PredictorEnvironment = Join-Path $PSScriptRoot ".venv" }
    $PredictorPython = Join-Path $PredictorEnvironment "Scripts\python.exe"
    if (-not (Test-Path $PredictorPython)) {
        $BootstrapPython = if ($env:BP_PYTHON) { $env:BP_PYTHON } else { "python" }
        & $BootstrapPython -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ is required"'
        if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12 or newer first." }
        & $BootstrapPython -m venv --copies $PredictorEnvironment
        if ($LASTEXITCODE -ne 0) { throw "Could not create the environment." }
    }
    $Ready = Join-Path $PredictorEnvironment ".balloon-predictor-1.1-ready"
    if (-not (Test-Path $Ready)) {
        & $PredictorPython -m pip install -r requirements.lock.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
        New-Item $Ready -ItemType File -Force | Out-Null
    }
}
if (-not $env:BP_DATA_DIR -and -not $env:BP_CONFIG -and -not ($args | Where-Object { $_ -eq "--config" -or $_ -like "--config=*" })) { $env:BP_DATA_DIR = Join-Path $env:LOCALAPPDATA "BalloonPredictor\data" }
Write-Host "BalloonPredictor v1.1 · default http://127.0.0.1:8000 · Ctrl+C to stop."
& $PredictorPython "$PSScriptRoot\server.py" @args
exit $LASTEXITCODE
