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


@dataclass(frozen=True)
class ParameterEvent:
    at: int
    index: int
    parameter: str
    path: Path
    payload: bytes


def envelope_bytes(path):
    with path.open("rb") as stream:
        body = stream.read(MAX_SIZE + 1)
    if len(body) > MAX_SIZE:
        raise ValueError("scenario envelope exceeds the component frame limit")
    return body


def read_scenario(path, topics, codec, parameters=None):
    if path is None:
        return []
    path = Path(path).resolve()
    document = load(path)
    fields(document, ("version", "events"), ("version", "events"))
    if not isinstance(document["events"], list):
        raise ValueError("scenario events must be a list")
    events = []
    for index, event in enumerate(document["events"]):
        if isinstance(event, dict) and 'parameter' in event:
            fields(event, ('at', 'parameter', 'value'), ('at', 'parameter', 'value'))
            key = event['parameter']
            if parameters is None or not isinstance(key, str) or key not in parameters.blocks:
                raise ValueError('scenario must update a declared shared parameter block')
            from ose.parameters.values import Values
            desc = parameters.blocks[key]
            payload = Values(codec.api, desc['catalogue'], desc['root']).encode(event['value'])
            parameters.validate(key, payload)
            events.append(ParameterEvent(seconds_ns(event['at']), index, key, path, payload))
            continue
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
