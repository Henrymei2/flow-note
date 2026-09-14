#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This build script is for macOS." >&2
    exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname -- "$SCRIPT_DIR")
PYTHON=${PYTHON:-python3}

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "Python 3.12 or newer is required. Install it, then run this build again." >&2
    exit 1
fi

if ! "$PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
    echo "$PYTHON is too old. Python 3.12 or newer is required." >&2
    exit 1
fi

cd "$REPO_ROOT"
"$PYTHON" -m pip install ".[dev]"
"$PYTHON" -m PyInstaller \
    --name FLStudioActivityRecorder \
    --onefile \
    --collect-all fl_activity_recorder \
    scripts/launcher.py
