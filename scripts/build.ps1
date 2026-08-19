$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $RepoRoot
try {
    py -3.12 -m pip install ".[dev]"
    py -3.12 -m PyInstaller `
        --name FLStudioActivityRecorder `
        --onefile `
        --collect-all fl_activity_recorder `
        scripts/launcher.py
} finally {
    Pop-Location
}
