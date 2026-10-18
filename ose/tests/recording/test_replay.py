"""Recording replay preflight, exact virtual timing and CLI simulation."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy
from ose.recording.replay import recording_events, run
from sdl_recording import RecordingWriter

CAT = "SDL2|Value{1:uint64:sequence;2:fl64:value;}"
OTHER = "SDL2|Value{1:uint64:sequence;2:fl64:value;3:fl64:extra;}"
SINK = '''from ose.execution.component import ComponentSession
s = ComponentSession(timed_ticks=True)
for inputs in s.ticks():
    s.publish("out", inputs["input"][1])
'''


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codec = SDL2(os.environ["SDL_ROOT"])
        self.topics = {"external.signal": dict(root="Value", catalogue=CAT)}
        self.mapping = {"source.value": "external.signal"}
        script = self.root / "sink.py"
        script.write_text(SINK)
        desc = dict(root="Value", catalogue=CAT)
        self.plan = dict(version=1, sdl_root=str(self.codec.root), build_dir=str(self.root),
            external_topics=self.topics,
            components={"sink": dict(inputs={"input": desc}, outputs={"out": desc},
                trigger={"mode": "on_change"}, parameters={}, cwd=str(self.root), env={},
                command=[sys.executable, str(script)])},
            subscriptions=[dict(id="signal", from_topic="external.signal", **{"from": "external.signal", "to": "sink.input"},
                                policy=SubscriptionPolicy.resolve({}).to_dict())])
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan))

    def capture(self, format="pcapng", *, catalogue=CAT, malformed=False, offsets=(7, 0, 7)):
        path = self.root / ("capture." + format)
        base = 1_800_000_000_000_000_000
        with RecordingWriter(path, format=format, validate_payload=not malformed) as writer:
            channel = writer.register_channel(topic="source.value", catalogue=catalogue, root_type="Value")
            ignored = writer.register_channel(topic="ignored", catalogue=CAT, root_type="Value")
            for sequence, offset in enumerate(offsets, 1):
                body = b"\x01" if malformed else b"\x01" + struct.pack(">Qd", sequence, float(sequence))
                if catalogue == OTHER:
                    body += struct.pack(">d", 3.)
                writer.write_encoded(channel, body, log_time_ns=base + offset)
            writer.write_encoded(ignored, b"\x01" + struct.pack(">Qd", 99, 99.), log_time_ns=base - 10)
        return path, base

    async def test_both_formats_preserve_payloads_nanoseconds_and_stable_ties(self):
        for format in ("pcapng", "mcap"):
            path, base = self.capture(format)
            trace = self.root / "trace.jsonl"
            reference = None
            for workers in (1, 4):
                summary = await run(path, self.plan_path, .001, mappings=["source.value=external.signal"],
                                    workers=workers, trace=trace)
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                outputs = [event for event in events if event["event"] == "publication" and event["topic"] == "sink.out"]
                self.assertEqual([event["time_ns"] for event in outputs], [0, 7, 7])
                expected = [self.codec.wrap(CAT, b"\x01" + struct.pack(">Qd", n, float(n))) for n in (2, 1, 3)]
                self.assertEqual([event["sha256"] for event in outputs],
                                 [hashlib.sha256(body).hexdigest() for body in expected])
                self.assertEqual(summary["replay"]["origin_log_time_ns"], base)
                self.assertEqual(summary["replay"]["message_count_by_topic"], {"source.value": 3})
                if reference is None:
                    reference = trace.read_bytes()
                self.assertEqual(reference, trace.read_bytes())

    async def test_range_is_half_open_and_rebases_first_selected_message(self):
        path, base = self.capture()
        with recording_events(path, self.topics, self.mapping, self.codec,
                              start_ns=base, end_ns=base + 7) as (events, selection):
            self.assertEqual([(event.at, event.index) for event in events], [(0, 0)])
            self.assertEqual(selection["message_count"], 1)
        with recording_events(path, self.topics, self.mapping, self.codec,
                              start_ns=base + 7) as (events, _):
            self.assertEqual([event.at for event in events], [0, 0])

    async def test_execution_horizon_excludes_messages_at_its_boundary(self):
        path, _ = self.capture()
        summary = await run(path, self.plan_path, 7e-9, mappings=["source.value=external.signal"])
        self.assertEqual(summary["ticks"], 1)
        self.assertEqual(summary["time_ns"], 7)
        self.assertEqual(summary["replay"]["message_count"], 3)

    async def test_repeated_inputs_have_unique_ids_and_deterministic_traces(self):
        path, _ = self.capture()
        for loops in (2, 0):
            reference = None
            for workers in (1, 4):
                trace = self.root / "loop-trace.jsonl"
                summary = await run(path, self.plan_path, 16e-9, mappings=["source.value=external.signal"],
                                    workers=workers, trace=trace, loops=loops)
                events = [json.loads(line) for line in trace.read_text().splitlines()]
                inputs = [e for e in events if e["event"] == "publication" and e["topic"] == "external.signal"]
                self.assertEqual([e["time_ns"] for e in inputs], [0, 7, 7, 8, 15, 15])
                self.assertEqual(len({e["id"] for e in inputs}), 6)
                self.assertEqual([e["sha256"] for e in inputs[:3]], [e["sha256"] for e in inputs[3:]])
                self.assertEqual(summary["completed_loops"], 2)
                self.assertEqual(summary["replay"]["loop_period_ns"], 8)
                if loops == 0:
                    self.assertEqual(summary["stop_reason"], "duration")
                if reference is None:
                    reference = trace.read_bytes()
                self.assertEqual(reference, trace.read_bytes())

    async def test_single_message_infinite_replay_advances_to_simulation_horizon(self):
        path, _ = self.capture(offsets=(0,))
        summary = await run(path, self.plan_path, 3e-9, mappings=["source.value=external.signal"], loops=0)
        self.assertEqual(summary["external_events"], 3)
        self.assertEqual(summary["completed_loops"], 3)
        self.assertEqual(summary["time_ns"], 3)
        self.assertEqual(summary["stop_reason"], "duration")

    async def test_frame_limit_is_checked_before_starting_components(self):
        path, _ = self.capture()
        with patch("ose.recording.replay.MAX_SIZE", 16), patch("ose.execution.simulation.run") as simulate:
            with self.assertRaisesRegex(ValueError, "frame limit"):
                await run(path, self.plan_path, .001, mappings=["source.value=external.signal"])
            simulate.assert_not_called()

    async def test_invalid_recordings_fail_before_starting_simulation(self):
        for kind in ("catalogue", "malformed", "truncated", "empty", "missing", "range"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                self.root = Path(directory)
                path, base = self.capture(catalogue=OTHER if kind == "catalogue" else CAT,
                                          malformed=kind == "malformed")
                if kind == "truncated":
                    path.write_bytes(path.read_bytes()[:-1])
                options = {"mappings": ["absent=external.signal" if kind == "missing" else "source.value=external.signal"]}
                if kind == "empty":
                    options["start_ns"] = base + 100
                if kind == "range":
                    options.update(start_ns=base + 8, end_ns=base)
                with patch("ose.execution.simulation.run") as simulate:
                    with self.assertRaises(ValueError):
                        await run(path, self.plan_path, .001, **options)
                    simulate.assert_not_called()

    async def test_capture_is_protected_from_diagnostic_outputs(self):
        path, _ = self.capture()
        original = path.read_bytes()
        for destination in (path, self.plan_path, self.root / "last-status.json"):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, "overwrite"):
                await run(path, self.plan_path, .001, mappings=["source.value=external.signal"], trace=destination)
        self.assertEqual(path.read_bytes(), original)

    async def test_cli_exposes_replay_and_reports_selection(self):
        path, _ = self.capture("mcap")
        result = await asyncio.to_thread(subprocess.run,
            [sys.executable, "-m", "ose.cli", "replay", str(path), "--plan", str(self.plan_path),
             "--map", "source.value=external.signal", "--duration", ".001"],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["replay"]["message_count"], 3)
