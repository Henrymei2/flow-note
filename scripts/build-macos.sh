#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This build script is for macOS." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$SCRIPT_DIR")
PYTHON=${PYTHON:-python3.12}

cd "$REPO_ROOT"
"$PYTHON" -m pip install ".[dev]"
"$PYTHON" -m PyInstaller \
    --name FLStudioActivityRecorder \
    --onefile \
    --collect-all fl_activity_recorder \
    scripts/launcher.py
