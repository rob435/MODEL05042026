param(
    [string]$PythonExe = "py",
    [string]$EnvOutput = ".env",
    [string]$SqlitePath = "signals.sqlite3",
    [string]$CachePath = ".cache/backtest_candles.sqlite3",
    [string]$CacheBundlePath = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "Creating Python virtual environment..."
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    if ($PythonExe -eq "py") {
        & py -3 -m venv .venv
    } else {
        & $PythonExe -m venv .venv
    }
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment python not found at $VenvPython"
}

Write-Host "Installing Python dependencies..."
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt

Write-Host "Preparing local research env..."
& $VenvPython deploy/prepare_local_env.py --output $EnvOutput --sqlite-path $SqlitePath --cache-path $CachePath

if ($CacheBundlePath) {
    Write-Host "Restoring backtest cache from $CacheBundlePath ..."
    & $VenvPython deploy/cache_bundle.py unpack --archive $CacheBundlePath --destination $CachePath --overwrite
}

Write-Host "Running test suite..."
& $VenvPython -m pytest -q

Write-Host ""
Write-Host "PC setup complete."
Write-Host "Next steps:"
Write-Host "  1. Review $EnvOutput and only re-enable execution if you actually want local trading."
Write-Host "  2. Warm or import the backtest cache before large sweeps."
Write-Host "  3. Run a smoke backtest:"
Write-Host "     .venv\\Scripts\\python.exe backtest.py --cycles 96 --research-fast --variant-workers 4"
