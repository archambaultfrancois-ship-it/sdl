"""Timed inputs across generated containers and real broker/adapter execution."""
import asyncio
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

from chain_env.client import Client
from chain_env.codec.sdl2 import SDL2
from chain_env.execution.component import read_exact
from chain_env.execution.ipc import frame, port_data, unpack_port
from chain_env.modeling.compiler import build

SDL_ROOT = os.environ.get("SDL_ROOT")
SCHEMA = "message Measurement {1: required uint64 sequence;2: required fl64 value;}"

PYTHON_NAMED = '''from measurement import Measurement

def on_input(ctx, message: Measurement | None):
    if message is None:
        ctx.publish_result(Measurement(sequence=0, value=-1.0))
    else:
        ctx.publish_result(Measurement(sequence=message.sequence, value=message.value * 2))

def on_optional(ctx, message):
    raise RuntimeError("absent optional input was dispatched")
'''
PYTHON_PAIR = '''from measurement import Measurement

def on_tick(ctx, inputs):
    a, b = inputs["a"], inputs["b"]
    assert inputs["optional"] is None
    ctx.publish_result(Measurement(sequence=0, value=(a.value if a else -1.0) + (b.value if b else -2.0)))
'''
C_NAMED = '''#include "component.h"
static int on_input(ChainContext *ctx, const Measurement *message) {
    Measurement result = {0};
    result.sequence = message ? message->sequence : 0;
    result.value = message ? message->value * 2 : -1.0;
    return publish_result(ctx, &result);
}
static int on_optional(ChainContext *ctx, const Measurement *message) {
    (void)message; return chain_fail(ctx, "absent optional input was dispatched");
}
const ComponentCallbacks component_callbacks = { .on_input = on_input, .on_optional = on_optional };
'''
C_PAIR = '''#include "component.h"
static int on_tick(ChainContext *ctx, const ComponentInputs *inputs) {
    Measurement result = {0};
    if (inputs->optional) return 0;
    result.value = (inputs->a ? inputs->a->value : -1.0) + (inputs->b ? inputs->b->value : -2.0);
    return publish_result(ctx, &result);
}
const ComponentCallbacks component_callbacks = { .on_tick = on_tick };
'''
RUST_NAMED = '''use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, message: Option<&Measurement>) -> Result {
        let result = match message {
            Some(m) => Measurement { sequence: m.sequence, value: m.value * 2.0 },
            None => Measurement { sequence: 0, value: -1.0 },
        };
        ctx.publish_result(&result)
    }
    fn on_optional(&mut self, _ctx: &mut Context, _message: &Measurement) -> Result {
        Err("absent optional input was dispatched".into())
    }
}
'''
RUST_PAIR = '''use crate::container::{Callbacks, Context, Inputs, Result};
use crate::types::measurement::Measurement;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_tick(&mut self, ctx: &mut Context, inputs: &Inputs<'_>) -> Result {
        assert!(inputs.optional.is_none());
        ctx.publish_result(&Measurement { sequence: 0,
            value: inputs.a.map_or(-1.0, |m| m.value) + inputs.b.map_or(-2.0, |m| m.value) })
    }
}
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class TimeoutContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.codec = SDL2(SDL_ROOT)
        cls.fixtures = {}
        for language, suffix, named, pair in (("python", ".py", PYTHON_NAMED, PYTHON_PAIR),
                                               ("c", ".c", C_NAMED, C_PAIR),
                                               ("rust", ".rs", RUST_NAMED, RUST_PAIR)):
            root = cls.root / language
            root.mkdir()
            (root / "measurement.sdl").write_text(SCHEMA)
            timed = dict(root="Measurement", timeout=.05)
            optional = dict(root="Measurement", required=False)
            components = {
                "sink": dict(inputs={"input": dict(timed), "optional": dict(optional)},
                             outputs={"result": {"root": "Measurement"}}, trigger={"mode": "on_change"}),
                "pair": dict(inputs={"a": dict(timed), "b": dict(timed, timeout=.1), "optional": dict(optional)},
                             outputs={"result": {"root": "Measurement"}}, trigger={"mode": "on_change"}),
            }
            model = dict(version=1, schemas="measurement.sdl", components=components,
                         external_topics={"source": {"root": "Measurement"}}, subscriptions=[
                             {"id": "sink", "from_topic": "source", "to": "sink.input"},
                             {"id": "a", "from_topic": "source", "to": "pair.a"},
                             {"id": "b", "from_topic": "source", "to": "pair.b"},
                         ])
            deployment = dict(version=1, components={name: {"container": {"language": language, "handler": name + suffix}}
                                                       for name in components})
            (root / ("sink" + suffix)).write_text(named)
            (root / ("pair" + suffix)).write_text(pair)
            m, d = root / "model.json", root / "deployment.json"
            m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
            plan = json.loads(build(m, d, SDL_ROOT, root / "build").read_text())
            cls.fixtures[language] = plan
        cls.catalogue = cls.fixtures["python"]["external_topics"]["source"]["catalogue"]
        cls.payload = b"\x01" + struct.pack(">Qd", 7, 3.5)

    def run_node(self, language, name, events):
        node = self.fixtures[language]["components"][name]
        result = subprocess.run(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"),
                                capture_output=True, timeout=10)
        messages, stream = [], io.BytesIO(result.stdout)
        while stream.tell() < len(result.stdout):
            size = struct.unpack(">I", read_exact(stream, 4))[0]
            data = read_exact(stream, size)
            messages.append((chr(data[0]), data[1:]))
        return result, messages

    def data(self, port):
        return frame("D", port_data(port, self.payload, self.catalogue))

    def null(self, port):
        return frame("D", port_data(port, b"", ""))

    def values(self, messages):
        codec = self.codec.api.prepare(self.catalogue)
        return [self.codec.api.decode_dynamic(codec, unpack_port(body)[1]).fields["value"]
                for op, body in messages if op == "P"]

    def test_same_named_callback_handles_null_data_and_null_again(self):
        events = self.null("input") + frame("T") + self.data("input") + frame("T") + self.null("input") + frame("T")
        for language in self.fixtures:
            with self.subTest(language=language):
                result, messages = self.run_node(language, "sink", events)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([op for op, _ in messages], ["R", "P", "F", "P", "F", "P", "F"])
                self.assertEqual(self.values(messages), [-1., 7., -1.])

    def test_snapshot_callback_handles_mixed_and_all_null_inputs(self):
        events = self.data("a") + self.null("b") + frame("T") + self.null("a") + self.null("b") + frame("T")
        for language in self.fixtures:
            with self.subTest(language=language):
                result, messages = self.run_node(language, "pair", events)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual(self.values(messages), [1.5, -3.])
                self.assertEqual(sum(op == "F" for op, _ in messages), 2)

    def test_missing_duplicate_unexpected_and_malformed_nulls_are_rejected(self):
        cases = [frame("T"), self.null("input") + self.null("input") + frame("T"),
                 self.null("input") + self.data("input") + frame("T"),
                 self.null("input") + self.null("optional") + frame("T"),
                 self.null("unknown") + frame("T"),
                 frame("D", port_data("input", b"bad", "")) + frame("T")]
        for language in self.fixtures:
            for events in cases:
                with self.subTest(language=language, events=events[-12:]):
                    result, messages = self.run_node(language, "sink", events)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual([op for op, _ in messages], ["R"])

    def test_compiled_plan_preserves_timeout_seconds(self):
        for plan in self.fixtures.values():
            self.assertEqual(plan["components"]["sink"]["inputs"]["input"]["timeout"], .05)
            self.assertEqual(plan["components"]["pair"]["inputs"]["b"]["timeout"], .1)


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class TimeoutRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_timeouts_mixed_snapshots_and_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "measurement.sdl").write_text(SCHEMA)
            (root / "handler.py").write_text(PYTHON_PAIR)
            model = dict(version=1, schemas="measurement.sdl", external_topics={"a": {"root": "Measurement"}, "b": {"root": "Measurement"}},
                components={"pair": dict(inputs={"a": dict(root="Measurement", timeout=.15),
                    "b": dict(root="Measurement", timeout=.3), "optional": dict(root="Measurement", required=False)},
                    outputs={"result": {"root": "Measurement"}}, trigger={"mode": "on_change"})},
                subscriptions=[{"id": p, "from_topic": p, "to": "pair." + p, "qos": {"ack": "received"}} for p in ("a", "b")])
            deployment = dict(version=1, components={"pair": {"container": {"language": "python", "handler": "handler.py"}}})
            m, d = root / "model.json", root / "deployment.json"
            m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
            plan = await asyncio.to_thread(build, m, d, SDL_ROOT, root / "build")
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "chain_env.cli", "run", "--plan", str(plan),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            client = None
            try:
                running = json.loads(await asyncio.wait_for(process.stdout.readline(), 5))
                api = SDL2(SDL_ROOT).api
                class Measurement(api.SdlMessage):
                    _SDL_NAME = "Measurement"
                    _SDL_DESCRIPTOR = "SDL2|Measurement{1:uint64:sequence;2:fl64:value;}"
                    _SDL_FIXED_SIZE = 16
                    _SDL_FIELDS = ((1, "sequence", "required", "uint64", ()), (2, "value", "required", "fl64", ()))
                client = await asyncio.to_thread(Client.connect, port=running["port"], client_id="timeout-driver")
                values, changed = [], threading.Event()
                def receive(message, info):
                    values.append(message.value)
                    changed.set()
                await asyncio.to_thread(client.subscribe, "pair.result", Measurement, receive)
                self.assertTrue(await asyncio.to_thread(changed.wait, 2))
                self.assertEqual(values[0], -3.)
                a = await asyncio.to_thread(client.publisher, "a", Measurement)
                b = await asyncio.to_thread(client.publisher, "b", Measurement)
                changed.clear()
                await asyncio.to_thread(a.publish, Measurement(sequence=1, value=10.))
                self.assertTrue(await asyncio.to_thread(changed.wait, 2))
                self.assertIn(8., values)  # Real A with a timed-out B.
                await asyncio.to_thread(a.publish, Measurement(sequence=2, value=10.))
                await asyncio.to_thread(b.publish, Measurement(sequence=3, value=20.))
                deadline = asyncio.get_running_loop().time() + 2
                while 30. not in values and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(.01)
                self.assertIn(30., values)
                self.assertIsNone(client.failure)
            finally:
                if client:
                    await asyncio.to_thread(client.close)
                if process.returncode is None:
                    process.terminate()
                communication = asyncio.create_task(process.communicate())
                try:
                    stdout, stderr = await asyncio.wait_for(asyncio.shield(communication), 5)
                except asyncio.TimeoutError:
                    process.kill()
                    await communication
                    raise
                self.assertEqual(process.returncode, 0, stderr.decode() + stdout.decode())
