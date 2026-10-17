"""Create fixed SDL2 input envelopes using the generated Measurement binding."""
import json
from pathlib import Path
import sys
from ose.codec.runtime import TypedCodec
from measurement import Measurement


def main():
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "build/simulated-scenario")
    destination.mkdir(parents=True, exist_ok=True)
    codec, events = TypedCodec(Measurement), []
    for index, (at, value) in enumerate(((.15, 10.0), (.35, 20.0)), 1):
        filename = f"offset-{index}.s2ps"
        (destination / filename).write_bytes(codec.encode(Measurement(sequence=index, value=value)))
        events.append(dict(at=at, topic="external.offset", envelope=filename))
    scenario = destination / "scenario.json"
    scenario.write_text(json.dumps(dict(version=1, events=events), indent=2) + "\n")
    print(scenario)


if __name__ == "__main__":
    main()
