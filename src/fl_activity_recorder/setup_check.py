from __future__ import annotations

import sys

import mido  # type: ignore[import-untyped]

CONTROL_PORT = "ActivityRecorder Control"
EVENT_PORT = "ActivityRecorder Events"


def main() -> int:
    inputs = list(mido.get_input_names())
    outputs = list(mido.get_output_names())
    event_visible = any(EVENT_PORT.casefold() in name.casefold() for name in inputs)
    control_visible = any(CONTROL_PORT.casefold() in name.casefold() for name in outputs)
    print("FL Studio Activity Recorder setup check")
    print(f"  {EVENT_PORT}: {'found' if event_visible else 'missing'}")
    print(f"  {CONTROL_PORT}: {'found' if control_visible else 'missing'}")
    if not event_visible or not control_visible:
        print("\nCreate both ports in loopMIDI, then run this check again.")
        return 1
    print(
        "\nVirtual MIDI ports are ready. Complete the FL Studio MIDI settings steps in README.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
