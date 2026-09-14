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
PYTHON=${PYTHON:-python3}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.12 or newer is required. Install it, then run this installer again." >&2
    exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    echo "$PYTHON is too old. Python 3.12 or newer is required." >&2
    exit 1
fi

"$PYTHON" --version
mkdir -p "$INSTALL_ROOT"
# Recreate the environment so stale interpreter links from an older Python cannot survive.
"$PYTHON" -m venv --clear "$INSTALL_ROOT/.venv"
VENV_PYTHON="$INSTALL_ROOT/.venv/bin/python"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install --upgrade "$REPO_ROOT"
INSTALLED_CONTRACT=$("$VENV_PYTHON" -c 'from fl_activity_recorder import __version__; from fl_activity_recorder.observer_contract import OBSERVER_BUILD, OBSERVER_SCHEMA; print("companion={} schema={} build={}".format(__version__, OBSERVER_SCHEMA, OBSERVER_BUILD))')

HARDWARE_ROOT="$FL_USER_DATA_ROOT/Settings/Hardware/ActivityRecorder"
mkdir -p "$HARDWARE_ROOT"
cp "$REPO_ROOT/fl_studio/ActivityRecorder/device_ActivityRecorder.py" \
    "$HARDWARE_ROOT/device_ActivityRecorder.py"

SOURCE_HASH=$(shasum -a 256 "$REPO_ROOT/fl_studio/ActivityRecorder/device_ActivityRecorder.py" | awk '{print $1}')
INSTALLED_HASH=$(shasum -a 256 "$HARDWARE_ROOT/device_ActivityRecorder.py" | awk '{print $1}')
if [ "$SOURCE_HASH" != "$INSTALLED_HASH" ]; then
    echo "Observer checksum verification failed." >&2
    exit 1
fi

echo
echo "The companion and FL Studio observer are installed."
echo "Companion contract: $INSTALLED_CONTRACT"
echo "Observer checksum: $INSTALLED_HASH"
echo "This installer does not change Audio MIDI Setup or FL Studio settings."
echo
if ! "$VENV_PYTHON" -m fl_activity_recorder.setup_check; then
    echo
    echo "Enable the IAC Driver and create the two buses described in README.md."
fi
echo
echo "Next: follow the macOS MIDI Settings steps in README.md."
echo "If FL Studio is open, reload Activity Recorder in View > Script output or restart FL Studio."
echo "Stop any dashboard process that was already running, then start it again with the command below."
echo "Run the dashboard with:"
echo "  $INSTALL_ROOT/.venv/bin/fl-activity-recorder"
