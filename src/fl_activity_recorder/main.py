from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from .api import create_app
from .config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="FL Studio Activity Recorder")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the dashboard")
    parser.add_argument("--port", type=int, help="Local dashboard port")
    args = parser.parse_args()
    settings = Settings.from_environment()
    if args.port is not None:
        settings = Settings(
            database_path=settings.database_path,
            data_directory=settings.data_directory,
            midi_event_port=settings.midi_event_port,
            host="127.0.0.1",
            port=args.port,
            enable_midi=settings.enable_midi,
        )
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(f"http://127.0.0.1:{settings.port}",)).start()
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
