#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This installer is for macOS." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$SCRIPT_DIR")
INSTALL_ROOT=${FL_ACTIVITY_INSTALL_ROOT:-"$HOME/Library/Application Support/FLActivityRecorder"}
FL_USER_DATA_ROOT=${FL_STUDIO_USER_DATA_ROOT:-"$HOME/Documents/Image-Line/FL Studio"}
PYTHON=${PYTHON:-python3.12}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.12 is required. Install it, then run this installer again." >&2
    exit 1
fi

"$PYTHON" --version
mkdir -p "$INSTALL_ROOT"
"$PYTHON" -m venv "$INSTALL_ROOT/.venv"
VENV_PYTHON="$INSTALL_ROOT/.venv/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install "$REPO_ROOT"

HARDWARE_ROOT="$FL_USER_DATA_ROOT/Settings/Hardware/ActivityRecorder"
mkdir -p "$HARDWARE_ROOT"
cp "$REPO_ROOT/fl_studio/ActivityRecorder/device_ActivityRecorder.py" \
    "$HARDWARE_ROOT/device_ActivityRecorder.py"

echo
echo "The companion and FL Studio observer are installed."
echo "This installer does not change Audio MIDI Setup or FL Studio settings."
echo
if ! "$VENV_PYTHON" -m fl_activity_recorder.setup_check; then
    echo
    echo "Enable the IAC Driver and create the two buses described in README.md."
fi
echo
echo "Next: follow the macOS MIDI Settings steps in README.md."
echo "Run the dashboard with:"
echo "  $INSTALL_ROOT/.venv/bin/fl-activity-recorder"
