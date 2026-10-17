"""Preflight timestamped external SDL2 envelopes and detect changes during a run."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from ose.modeling.model import load, fields
from ose.execution.ipc import MAX_SIZE
from ose.execution.simulation.clock import seconds_ns


@dataclass(frozen=True)
class Event:
    at: int
    index: int
    topic: str
    path: Path
    digest: str

    def read(self):
        body = envelope_bytes(self.path)
        if hashlib.sha256(body).hexdigest() != self.digest:
            raise ValueError(f"scenario envelope changed during simulation: {self.path}")
        return body


def envelope_bytes(path):
    with path.open("rb") as stream:
        body = stream.read(MAX_SIZE + 1)
    if len(body) > MAX_SIZE:
        raise ValueError("scenario envelope exceeds the component frame limit")
    return body


def read_scenario(path, topics, codec):
    if path is None:
        return []
    path = Path(path).resolve()
    document = load(path)
    fields(document, ("version", "events"), ("version", "events"))
    if not isinstance(document["events"], list):
        raise ValueError("scenario events must be a list")
    events = []
    for index, event in enumerate(document["events"]):
        fields(event, ("at", "topic", "envelope"), ("at", "topic", "envelope"))
        if not isinstance(event["topic"], str) or event["topic"] not in topics:
            raise ValueError("scenario must publish to a declared external topic")
        if not isinstance(event["envelope"], str) or not event["envelope"]:
            raise ValueError("scenario envelope must be a file path")
        source = (path.parent / event["envelope"]).resolve()
        if source.stat().st_size > MAX_SIZE:
            raise ValueError("scenario envelope exceeds the component frame limit")
        body = envelope_bytes(source)
        desc = topics[event["topic"]]
        codec.validate(desc["catalogue"], desc["root"], body)
        events.append(Event(seconds_ns(event["at"]), index, event["topic"], source, hashlib.sha256(body).hexdigest()))
    return sorted(events, key=lambda event: (event.at, event.index))
