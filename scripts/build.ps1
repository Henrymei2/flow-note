$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 12))"
    if ($LASTEXITCODE -ne 0) {
        throw "The default Python 3 installation is too old. Python 3.12 or newer is required."
    }
    py -3 -m pip install ".[dev]"
    py -3 -m PyInstaller `
        --name FLStudioActivityRecorder `
        --onefile `
        --collect-all fl_activity_recorder `
        scripts/launcher.py
} finally {
    Pop-Location
}
