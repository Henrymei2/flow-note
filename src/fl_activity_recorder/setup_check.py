from __future__ import annotations

import sys
from dataclasses import dataclass

import mido  # type: ignore[import-untyped]

CONTROL_PORT = "ActivityRecorder Control"
EVENT_PORT = "ActivityRecorder Events"


@dataclass(frozen=True, slots=True)
class PortStatus:
    event_visible: bool
    control_visible: bool

    @property
    def ready(self) -> bool:
        return self.event_visible and self.control_visible


def check_ports(inputs: list[str], outputs: list[str]) -> PortStatus:
    return PortStatus(
        event_visible=any(EVENT_PORT.casefold() in name.casefold() for name in inputs),
        control_visible=any(CONTROL_PORT.casefold() in name.casefold() for name in outputs),
    )


def setup_hint(platform: str) -> str:
    if platform == "darwin":
        return (
            "Open Audio MIDI Setup, show MIDI Studio, enable the IAC Driver, "
            "and create buses named ActivityRecorder Control and ActivityRecorder Events."
        )
    if platform == "win32":
        return "Create both ports in loopMIDI, then run this check again."
    return "Create both virtual MIDI ports, then run this check again."


def main() -> int:
    inputs = list(mido.get_input_names())
    outputs = list(mido.get_output_names())
    status = check_ports(inputs, outputs)
    print("FL Studio Activity Recorder setup check")
    print(f"  {EVENT_PORT}: {'found' if status.event_visible else 'missing'}")
    print(f"  {CONTROL_PORT}: {'found' if status.control_visible else 'missing'}")
    if not status.ready:
        print(f"\n{setup_hint(sys.platform)}")
        return 1
    print(
        "\nVirtual MIDI ports are ready. Complete the FL Studio MIDI settings steps in README.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
