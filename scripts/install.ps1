[CmdletBinding()]
param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\FLActivityRecorder"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Get-Command py -ErrorAction SilentlyContinue
if (-not $Python) {
    throw "Python 3.12 is required. Install it from python.org, then run this installer again."
}

& py -3.12 --version | Out-Host
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
& py -3.12 -m venv "$InstallRoot\.venv"
$VenvPython = "$InstallRoot\.venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install $RepoRoot

$HardwareRoot = "$env:USERPROFILE\Documents\Image-Line\FL Studio\Settings\Hardware\ActivityRecorder"
New-Item -ItemType Directory -Force -Path $HardwareRoot | Out-Null
Copy-Item "$RepoRoot\fl_studio\ActivityRecorder\device_ActivityRecorder.py" "$HardwareRoot\device_ActivityRecorder.py" -Force

Write-Host ""
Write-Host "The companion and FL Studio observer are installed." -ForegroundColor Green
Write-Host "This installer does not install loopMIDI or change FL Studio settings."
Write-Host ""
& $VenvPython -m fl_activity_recorder.setup_check
if ($LASTEXITCODE -ne 0) {
    Write-Host "Download loopMIDI from https://www.tobias-erichsen.de/software/loopmidi.html" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Next: follow the MIDI Settings steps in README.md."
Write-Host "Run the dashboard with:"
Write-Host "  $InstallRoot\.venv\Scripts\fl-activity-recorder.exe"

