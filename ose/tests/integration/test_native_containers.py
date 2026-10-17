"""Compile and exercise C/Rust callbacks, ownership and catalogue evolution."""
import copy
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from ose.codec.sdl2 import SDL2
from ose.execution.component import read_exact
from ose.execution.ipc import frame, port_data, unpack_port
from ose.modeling.compiler import build, validate

SDL_ROOT = os.environ.get("SDL_ROOT")
SIMPLE = "message Measurement {1: required uint64 sequence; 2: required fl64 value;}"
EVOLVED = "message Alpha {1: int32 x;} message Measurement {1: required uint64 sequence; 2: required fl64 value; 3: uint32 extra;}"
C_SOURCE = r'''#include "component.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int start(ChainContext *ctx) {
    uint64_t *counter = malloc(sizeof(*counter));
    if (!counter) return 0;
    *counter = 6; ctx->user_data = counter;
    if (strstr(ctx->config_json, "FAIL_INIT")) return chain_fail(ctx, "init failure");
    if (strstr(ctx->config_json, "OUTSIDE_TICK")) {
        Measurement value = {0};
        return publish_out(ctx, &value);
    }
    return 1;
}
static int tick(ChainContext *ctx) {
    Measurement value = {0};
    if (strstr(ctx->config_json, "FAIL_TICK")) return chain_fail(ctx, "user failure");
    value.sequence = ++*(uint64_t *)ctx->user_data;
    value.value = 3.5; value.extra = 9;
    return publish_out(ctx, &value);
}
static int stop(ChainContext *ctx) {
    free(ctx->user_data);
    fprintf(stderr, "STOP %s\n", ctx->name);
    return 1;
}
const ComponentCallbacks component_callbacks = { .on_start = start, .on_stop = stop, .on_tick = tick };
'''
C_ECHO = r'''#include "component.h"
#include <stdio.h>
static int on_input(ChainContext *ctx, const Measurement *message) {
    Measurement value = *message;
    value.value *= 2;
    return publish_result(ctx, &value);
}
static int stop(ChainContext *ctx) { fprintf(stderr, "STOP %s\n", ctx->name); return 1; }
const ComponentCallbacks component_callbacks = { .on_input = on_input, .on_stop = stop };
'''
C_SNAPSHOT = r'''#include "component.h"
#include <stdio.h>
static int tick(ChainContext *ctx, const ComponentInputs *inputs) {
    Measurement value = {0};
    if (!inputs->first || !inputs->second || inputs->optional) return 0;
    value.sequence = inputs->first->sequence;
    value.value = inputs->first->value + inputs->second->value;
    return publish_result(ctx, &value);
}
static int stop(ChainContext *ctx) { fprintf(stderr, "STOP %s\n", ctx->name); return 1; }
const ComponentCallbacks component_callbacks = { .on_tick = tick, .on_stop = stop };
'''
RUST_SOURCE = r'''use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;
#[derive(Default)]
pub struct Handler { counter: u64 }
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        self.counter = 6;
        if ctx.config_json.contains("FAIL_INIT") { return Err("init failure".into()); }
        if ctx.config_json.contains("OUTSIDE_TICK") { ctx.publish_out(&Measurement::default())?; }
        Ok(())
    }
    fn on_tick(&mut self, ctx: &mut Context) -> Result {
        if ctx.config_json.contains("FAIL_TICK") { return Err("user failure".into()); }
        if ctx.config_json.contains("PANIC") { panic!("user panic"); }
        self.counter += 1;
        ctx.publish_out(&Measurement { sequence: self.counter, value: 3.5, extra: 9 })
    }
    fn on_stop(&mut self, ctx: &mut Context) -> Result { eprintln!("STOP {}", ctx.name); Ok(()) }
}
'''
RUST_ECHO = r'''use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;
#[derive(Default)]
pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, message: &Measurement) -> Result {
        let mut value = message.clone(); value.value *= 2.0;
        ctx.publish_result(&value)
    }
    fn on_stop(&mut self, ctx: &mut Context) -> Result { eprintln!("STOP {}", ctx.name); Ok(()) }
}
'''
RUST_SNAPSHOT = r'''use crate::container::{Callbacks, Context, Inputs, Result};
use crate::types::measurement::Measurement;
#[derive(Default)]
pub struct Handler;
impl Callbacks for Handler {
    fn on_tick(&mut self, ctx: &mut Context, inputs: &Inputs<'_>) -> Result {
        assert!(inputs.optional.is_none());
        let first = inputs.first.unwrap(); let second = inputs.second.unwrap();
        ctx.publish_result(&Measurement { sequence: first.sequence, value: first.value + second.value })
    }
    fn on_stop(&mut self, ctx: &mut Context) -> Result { eprintln!("STOP {}", ctx.name); Ok(()) }
}
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class NativeContainerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        cls.fixtures = {}
        cls.codec = SDL2(SDL_ROOT)
        for language, suffix, handlers in (("c", ".c", (C_SOURCE, C_ECHO, C_SNAPSHOT)),
                                           ("rust", ".rs", (RUST_SOURCE, RUST_ECHO, RUST_SNAPSHOT))):
            root = cls.root / language
            root.mkdir()
            (root / "types.sdl").write_text(EVOLVED)
            (root / "consumer").mkdir()
            (root / "consumer/measurement.sdl").write_text(SIMPLE)
            port = {"root": "Measurement", "schema": "consumer/measurement.sdl"}
            model = {"version": 1, "schemas": "types.sdl", "components": {
                "source": {"inputs": {}, "outputs": {"out": {"root": "Measurement"}}, "trigger": {"mode": "periodic", "period_ms": 10}},
                "echo": {"inputs": {"input": dict(port)}, "outputs": {"result": dict(port)}, "trigger": {"mode": "on_change"}},
                "sink": {"inputs": {"first": dict(port), "second": dict(port), "optional": dict(port, required=False)},
                         "outputs": {"result": dict(port)}, "trigger": {"mode": "on_change"}}},
                "subscriptions": [{"id": name, "from": "source.out", "to": target} for name, target in
                                  (("echo", "echo.input"), ("first", "sink.first"), ("second", "sink.second"))]}
            deployment = {"version": 1, "components": {name: {"container": {"language": language, "handler": name + suffix}}
                                                      for name in model["components"]}}
            for name, handler in zip(model["components"], handlers):
                if language == "rust" and name == "source":
                    handler = handler.replace("crate::types::measurement::Measurement", "crate::types::types::Measurement")
                (root / (name + suffix)).write_text(handler)
            m, d = root / "model.json", root / "deployment.json"
            m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
            path = build(m, d, SDL_ROOT, root / "build")
            cls.fixtures[language] = {"root": root, "model": model, "deployment": deployment, "plan": json.loads(path.read_text()), "suffix": suffix}

    def run_node(self, language, name, events=b"", probe=None):
        node = copy.deepcopy(self.fixtures[language]["plan"]["components"][name])
        if probe:
            node["parameters"] = {"probe": probe}
        result = subprocess.run(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"),
                                capture_output=True, timeout=10)
        stream, messages = io.BytesIO(result.stdout), []
        while stream.tell() < len(result.stdout):
            size = struct.unpack(">I", read_exact(stream, 4))[0]
            data = read_exact(stream, size)
            messages.append((chr(data[0]), data[1:]))
        return result, messages

    def source_payload(self, language):
        result, messages = self.run_node(language, "source", frame("T"))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return unpack_port(messages[1][1])[1]

    def test_periodic_sources_publish_typed_values_and_stop(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                result, messages = self.run_node(language, "source", frame("T") + frame("T"))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([op for op, _ in messages], ["R", "P", "F", "P", "F"])
                catalogue = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
                decoder = self.codec.api.prepare(catalogue)
                values = [self.codec.api.decode_dynamic(decoder, unpack_port(body)[1]).fields for op, body in messages if op == "P"]
                self.assertEqual([value["sequence"] for value in values], [7, 8])
                self.assertEqual(values[0]["extra"], 9)
                self.assertIn(b"STOP source", result.stderr)

    def test_named_callbacks_decode_evolved_catalogue(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                payload = self.source_payload(language)
                publisher = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
                consumer = fixture["plan"]["components"]["echo"]["outputs"]["result"]["catalogue"]
                self.assertNotEqual(self.codec.api.message_names(self.codec.api.prepare(publisher)), self.codec.api.message_names(self.codec.api.prepare(consumer)))
                result, messages = self.run_node(language, "echo", frame("D", port_data("input", payload, publisher)) + frame("T"))
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
                output = self.codec.api.decode_dynamic(self.codec.api.prepare(consumer), unpack_port(messages[1][1])[1])
                self.assertEqual(output.fields, {"sequence": 7, "value": 7.0})
                self.assertIn(b"STOP echo", result.stderr)

    def test_snapshot_callbacks_include_optional_absence(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                payload = self.source_payload(language)
                catalogue = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
                events = b"".join(frame("D", port_data(port, payload, catalogue)) for port in ("first", "second")) + frame("T")
                result, messages = self.run_node(language, "sink", events)
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
                output_catalogue = fixture["plan"]["components"]["sink"]["outputs"]["result"]["catalogue"]
                value = self.codec.api.decode_dynamic(self.codec.api.prepare(output_catalogue), unpack_port(messages[1][1])[1])
                self.assertEqual(value.fields["value"], 7.0)

    def test_incomplete_duplicate_and_invalid_inputs_never_invoke_callbacks(self):
        for language, fixture in self.fixtures.items():
            payload = self.source_payload(language)
            catalogue = fixture["plan"]["components"]["source"]["outputs"]["out"]["catalogue"]
            first = frame("D", port_data("first", payload, catalogue))
            for events in (first + frame("T"), first + first + frame("T"),
                           first + frame("D", port_data("second", b"invalid", catalogue)) + frame("T"),
                           frame("D", port_data("unknown", payload, catalogue)) + frame("T")):
                with self.subTest(language=language, events=events[-10:]):
                    result, messages = self.run_node(language, "sink", events)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual([op for op, _ in messages], ["R"])
                    self.assertIn(b"STOP sink", result.stderr)

    def test_initialization_publication_and_callback_failures(self):
        for language in self.fixtures:
            for probe, expected in (("FAIL_INIT", []), ("OUTSIDE_TICK", []), ("FAIL_TICK", ["R"])):
                with self.subTest(language=language, probe=probe):
                    result, messages = self.run_node(language, "source", frame("T"), probe)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual([op for op, _ in messages], expected)
                    self.assertIn(b"STOP source", result.stderr)
        result, messages = self.run_node("rust", "source", frame("T"), "PANIC")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([op for op, _ in messages], ["R"])
        self.assertIn(b"STOP source", result.stderr)

    def test_rebuild_preserves_native_user_files(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                root = fixture["root"]
                handler = root / ("source" + fixture["suffix"])
                original = handler.read_bytes()
                model = copy.deepcopy(fixture["model"])
                model["components"] = {"source": model["components"]["source"]}
                model["components"]["source"]["outputs"]["mirror"] = {"root": "Measurement"}
                model["subscriptions"] = []
                deployment = {"version": 1, "components": {"source": fixture["deployment"]["components"]["source"]}}
                m, d = root / "rebuild-model.json", root / "rebuild-deployment.json"
                m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
                build(m, d, SDL_ROOT, root / "rebuild")
                self.assertEqual(handler.read_bytes(), original)
                generated = root / "rebuild/components/source" / ("component.h" if language == "c" else "src/container.rs")
                self.assertIn("publish_mirror", generated.read_text())

    def test_qualified_nested_bindings_and_unicode_handler_paths(self):
        for language in self.fixtures:
            with self.subTest(language=language):
                root = self.root / (language + "-qualified")
                schemas = root / "schémas"
                schemas.mkdir(parents=True)
                (schemas / "point.sdl").write_text("package demo; message Point {1: int32 x;}")
                (schemas / "packet.sdl").write_text("package demo; message Packet {1: Point point; 2: string label;}")
                model = {"version": 1, "schemas": "schémas", "components": {
                    "source": {"inputs": {}, "outputs": {"out": {"root": "demo.Packet"}}, "trigger": {"mode": "periodic", "period_ms": 10}},
                    "echo": {"inputs": {"input": {"root": "demo.Packet"}}, "outputs": {"result": {"root": "demo.Packet"}},
                             "trigger": {"mode": "on_change"}}},
                    "subscriptions": [{"id": "link", "from": "source.out", "to": "echo.input"}]}
                suffix = self.fixtures[language]["suffix"]
                deployment = {"version": 1, "components": {name: {"container": {"language": language, "handler": name + "-é" + suffix}}
                                                          for name in model["components"]}}
                if language == "c":
                    handlers = ['#include "component.h"\nstatic int tick(ChainContext *ctx) { Output_out value = {0}; value.point.x = 12; value.label = "native"; return publish_out(ctx, &value); }\nconst ComponentCallbacks component_callbacks = { .on_tick = tick };\n',
                                '#include "component.h"\nstatic int on_input(ChainContext *ctx, const Input_input *value) { return publish_result(ctx, value); }\nconst ComponentCallbacks component_callbacks = { .on_input = on_input };\n']
                else:
                    handlers = ['use crate::container::{Callbacks, Context, Result};\nuse crate::types::demo::packet::Packet;\n#[derive(Default)] pub struct Handler;\nimpl Callbacks for Handler { fn on_tick(&mut self, ctx: &mut Context) -> Result { let mut value = Packet::default(); value.point.x = 12; value.label = "native".into(); ctx.publish_out(&value) } }\n',
                                'use crate::container::{Callbacks, Context, Result};\nuse crate::types::demo::packet::Packet;\n#[derive(Default)] pub struct Handler;\nimpl Callbacks for Handler { fn on_input(&mut self, ctx: &mut Context, value: &Packet) -> Result { ctx.publish_result(value) } }\n']
                for name, handler in zip(model["components"], handlers):
                    (root / (name + "-é" + suffix)).write_text(handler)
                m, d = root / "model.json", root / "deployment.json"
                m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
                plan = json.loads(build(m, d, SDL_ROOT, root / "build").read_text())
                def run(name, events):
                    node = plan["components"][name]
                    result = subprocess.run(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"), capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    stream, messages = io.BytesIO(result.stdout), []
                    while stream.tell() < len(result.stdout):
                        size = struct.unpack(">I", read_exact(stream, 4))[0]
                        data = read_exact(stream, size)
                        messages.append((chr(data[0]), data[1:]))
                    self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
                    return unpack_port(messages[1][1])[1]
                payload = run("source", frame("T"))
                catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
                output = run("echo", frame("D", port_data("input", payload, catalogue)) + frame("T"))
                decoded = self.codec.api.decode_dynamic(self.codec.api.prepare(catalogue), output)
                self.assertEqual(decoded.fields["point"].fields["x"], 12)
                self.assertEqual(decoded.fields["label"], "native")

    def test_validation_and_first_build_scaffold_native_handlers(self):
        for language, fixture in self.fixtures.items():
            with self.subTest(language=language):
                root = fixture["root"]
                model = copy.deepcopy(fixture["model"])
                model["components"] = {"source": model["components"]["source"]}
                model["subscriptions"] = []
                handler = root / ("new" + fixture["suffix"])
                deployment = {"version": 1, "components": {"source": {"container": {"language": language, "handler": str(handler)}}}}
                m, d = root / "new-model.json", root / "new-deployment.json"
                m.write_text(json.dumps(model)); d.write_text(json.dumps(deployment))
                validate(m, d, SDL_ROOT)
                self.assertFalse(handler.exists())
                path = build(m, d, SDL_ROOT, root / "new-build")
                self.assertIn("on_tick", handler.read_text())
                node = json.loads(path.read_text())["components"]["source"]
                result = subprocess.run(node["command"], input=frame("I", json.dumps(node).encode()) + frame("T") + frame("X"), capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(b"implement the tick callback", result.stderr)
