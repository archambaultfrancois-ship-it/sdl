"""Exercise generated executables through their real SDL and binary IPC boundary."""
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from chain_env.codec.sdl2 import SDL2
from chain_env.execution.component import read_exact
from chain_env.execution.ipc import frame, port_data, unpack_port
from chain_env.modeling.compiler import build, compile_plan, validate

SDL_ROOT = os.environ.get("SDL_ROOT")
SCHEMA = "message Measurement {1: required uint64 sequence; 2: required fl64 value;}"
SOURCE = '''from measurement import Measurement
def on_tick(ctx):
    ctx.publish_out(Measurement(sequence=7, value=3.5))
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class ContainerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "measurement.sdl").write_text(SCHEMA)
        self.codec = SDL2(SDL_ROOT)
        self.model = {"version": 1, "schemas": "measurement.sdl", "components": {
            "source": {"inputs": {}, "outputs": {"out": {"root": "Measurement"}},
                       "trigger": {"mode": "periodic", "period_ms": 10}},
            "sink": {"inputs": {"input": {"root": "Measurement"}}, "outputs": {"result": {"root": "Measurement"}},
                     "trigger": {"mode": "on_change"}}},
            "subscriptions": [{"id": "link", "from": "source.out", "to": "sink.input"}]}
        self.deployment = {"version": 1, "components": {
            name: {"container": {"language": "python", "handler": f"{name}.py"}}
            for name in self.model["components"]}}
        self.model_path, self.deployment_path = self.root / "model.json", self.root / "deployment.json"
        self.destination = self.root / "build"
        self.env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))

    def documents(self):
        self.model_path.write_text(json.dumps(self.model))
        self.deployment_path.write_text(json.dumps(self.deployment))

    def compile(self):
        self.documents()
        return compile_plan(self.model_path, self.deployment_path, SDL_ROOT, self.destination)

    def handler(self, name, source):
        (self.root / f"{name}.py").write_text(source)

    def run_node(self, plan, name, events=b""):
        node = plan["components"][name]
        result = subprocess.run(node["command"], input=frame("I", json.dumps(node).encode()) + events + frame("X"),
                                cwd=node["cwd"], env=self.env, capture_output=True, timeout=10)
        stream, messages = io.BytesIO(result.stdout), []
        while stream.tell() < len(result.stdout):
            size = struct.unpack(">I", read_exact(stream, 4))[0]
            body = read_exact(stream, size)
            messages.append((chr(body[0]), body[1:]))
        return result, messages

    def source_payload(self, plan):
        self.handler("source", SOURCE)
        result, messages = self.run_node(plan, "source", frame("T"))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
        return unpack_port(messages[1][1])[1]

    def test_generated_port_callback_and_typed_publication(self):
        plan = self.compile()
        payload = self.source_payload(plan)
        self.handler("sink", '''from measurement import Measurement
def on_start(ctx):
    assert ctx.name == "sink"
def on_input(ctx, message: Measurement):
    assert isinstance(message, Measurement)
    ctx.publish_result(Measurement(sequence=message.sequence, value=message.value * 2))
def on_stop(ctx):
    import sys
    print("stopped", file=sys.stderr)
''')
        catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
        result, messages = self.run_node(plan, "sink", frame("D", port_data("input", payload, catalogue)) + frame("T"))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
        port, wire = unpack_port(messages[1][1])
        self.assertEqual(port, "result")
        value = self.codec.api.decode_dynamic(self.codec.api.prepare(catalogue), wire)
        self.assertEqual(value.fields, {"sequence": 7, "value": 7.0})
        self.assertIn(b"stopped", result.stderr)

    def test_snapshot_has_all_required_inputs_and_absent_optional_input(self):
        sink = self.model["components"]["sink"]
        sink["inputs"] = {"first": {"root": "Measurement"}, "second": {"root": "Measurement"},
                          "optional": {"root": "Measurement", "required": False}}
        self.model["subscriptions"] = [{"id": port, "from": "source.out", "to": f"sink.{port}"} for port in ("first", "second")]
        plan = self.compile()
        payload = self.source_payload(plan)
        self.handler("sink", '''from measurement import Measurement
def on_first(ctx, message):
    raise AssertionError("on_tick takes precedence")
def on_tick(ctx, inputs):
    assert inputs["optional"] is None
    ctx.publish_result(Measurement(sequence=1, value=inputs["first"].value + inputs["second"].value))
''')
        catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
        events = b"".join(frame("D", port_data(port, payload, catalogue)) for port in ("first", "second")) + frame("T")
        result, messages = self.run_node(plan, "sink", events)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "P", "F"])
        # Every selected input is decoded before any user callback runs.
        bad = frame("D", port_data("first", payload, catalogue)) + frame("D", port_data("second", b"invalid", catalogue)) + frame("T")
        result, messages = self.run_node(plan, "sink", bad)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([op for op, _ in messages], ["R"])

    def test_callback_failures_never_finish_a_tick(self):
        plan = self.compile()
        self.handler("source", 'def on_tick(ctx):\n    raise RuntimeError("user failure")\n')
        result, messages = self.run_node(plan, "source", frame("T"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"user failure", result.stderr)
        self.assertEqual([op for op, _ in messages], ["R"])

    def test_invalid_callbacks_and_initialization_fail_before_ready(self):
        plan = self.compile()
        for source, error in [('def on_tick(): pass\n', b"invalid callback signature"),
                              ('async def on_tick(ctx): pass\n', b"synchronous on_tick"),
                              ('def on_start(ctx): raise RuntimeError("init failure")\ndef on_tick(ctx): pass\n', b"init failure")]:
            with self.subTest(error=error):
                self.handler("source", source)
                result, messages = self.run_node(plan, "source")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertEqual(messages, [])
        self.handler("sink", "# Missing on_input\n")
        result, messages = self.run_node(plan, "sink")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"synchronous on_input", result.stderr)
        self.assertEqual(messages, [])

    def test_publication_is_forbidden_outside_tick_and_checks_output_type(self):
        plan = self.compile()
        for source, expected in [('from measurement import Measurement\ndef on_start(ctx): ctx.publish_out(Measurement())\ndef on_tick(ctx): pass\n', []),
                                 ('def on_tick(ctx): ctx.publish_out(object())\n', ["R"])]:
            with self.subTest(expected=expected):
                self.handler("source", source)
                result, messages = self.run_node(plan, "source", frame("T"))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual([op for op, _ in messages], expected)

    def test_validation_does_not_create_handlers_and_build_never_overwrites_them(self):
        self.documents()
        validate(self.model_path, self.deployment_path, SDL_ROOT)
        self.assertFalse((self.root / "source.py").exists())
        self.assertFalse((self.root / "sink.py").exists())
        build(self.model_path, self.deployment_path, SDL_ROOT, self.destination)
        self.assertIn("def on_tick(ctx)", (self.root / "source.py").read_text())
        self.assertIn("def on_input(ctx, message: InputMessage)", (self.root / "sink.py").read_text())
        self.handler("sink", "# user code to keep\ndef on_input(ctx, message): pass\n")
        original = (self.root / "sink.py").read_bytes()
        self.model["components"]["sink"]["outputs"]["extra"] = {"root": "Measurement"}
        self.documents()
        build(self.model_path, self.deployment_path, SDL_ROOT, self.destination)
        self.assertEqual((self.root / "sink.py").read_bytes(), original)
        self.assertIn("def publish_extra", (self.destination / "components/sink/container.py").read_text())

    def test_handler_in_generated_directory_is_refused_before_generation(self):
        handler = self.destination / "generated/shared/python/measurement.py"
        handler.parent.mkdir(parents=True)
        handler.write_text("# user code to preserve\n")
        self.deployment["components"]["source"]["container"]["handler"] = str(handler)
        with self.assertRaisesRegex(ValueError, "outside the generated build directory"):
            self.compile()
        self.assertEqual(handler.read_text(), "# user code to preserve\n")

    def test_received_catalogue_drives_typed_decode_after_schema_evolution(self):
        (self.root / "measurement.sdl").write_text("message Alpha {1: int32 x;} " +
            "message Measurement {1: required uint64 sequence; 2: required fl64 value; 3: optional string note;}")
        (self.root / "consumer").mkdir()
        (self.root / "consumer/measurement.sdl").write_text(SCHEMA)
        self.model["components"]["sink"]["inputs"]["input"]["schema"] = "consumer/measurement.sdl"
        self.model["components"]["sink"]["outputs"] = {}
        plan = self.compile()
        self.handler("source", SOURCE.replace("value=3.5", 'value=3.5, note="extra"'))
        result, messages = self.run_node(plan, "source", frame("T"))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        payload = unpack_port(messages[1][1])[1]
        self.handler("sink", '''from measurement import Measurement
def on_input(ctx, message):
    assert isinstance(message, Measurement)
    assert message.sequence == 7 and message.value == 3.5
''')
        catalogue = plan["components"]["source"]["outputs"]["out"]["catalogue"]
        result, messages = self.run_node(plan, "sink", frame("D", port_data("input", payload, catalogue)) + frame("T"))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual([op for op, _ in messages], ["R", "F"])

    def test_conflicting_python_modules_are_reported_during_compilation(self):
        (self.root / "other").mkdir()
        (self.root / "other/measurement.sdl").write_text(SCHEMA + "message Extra {1: int32 x;}")
        self.model["components"]["sink"]["outputs"]["result"]["schema"] = "other/measurement.sdl"
        with self.assertRaisesRegex(ValueError, "conflicting generated module"):
            self.compile()

    def test_python_stdlib_module_collision_is_reported_during_compilation(self):
        (self.root / "types.sdl").write_text(SCHEMA)
        self.model["schemas"] = "types.sdl"
        with self.assertRaisesRegex(ValueError, "shadows the standard library"):
            self.compile()
