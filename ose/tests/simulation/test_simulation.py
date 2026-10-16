"""Functional simulation across native containers, queue policies and worker counts."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from chain_env.codec.sdl2 import SDL2
from chain_env.delivery.policies import SubscriptionPolicy as Policy
from chain_env.execution.simulation import Simulation
from chain_env.execution.ipc import frame, port_data
from chain_env.modeling.compiler import build

SDL_ROOT = os.environ.get("SDL_ROOT")
SCHEMA = "message Measurement {1: required uint64 sequence;2: required fl64 value;}"
SOURCE = '''from measurement import Measurement
def on_tick(ctx):
    ctx.publish_out(Measurement(sequence=ctx.tick_index, value=ctx.now()))
'''
LEFT = '''#define _POSIX_C_SOURCE 200809L
#include "component.h"
#include <stdlib.h>
#include <time.h>
static int on_input(ChainContext *ctx, const Measurement *message) {
    const char *delay = getenv("TEST_DELAY_NS");
    if (delay) { struct timespec wait = {0, atol(delay)}; nanosleep(&wait, NULL); }
    if (message->sequence != chain_tick_index(ctx)) return chain_fail(ctx, "wrong tick index");
    if ((double)chain_time_ns(ctx) / 1e9 != chain_now(ctx)) return chain_fail(ctx, "wrong timestamp");
    Measurement result = {0};
    result.sequence = chain_tick_index(ctx);
    result.value = message->value + 1.0 + chain_now(ctx);
    return publish_out(ctx, &result);
}
const ComponentCallbacks component_callbacks = { .on_input = on_input };
'''
RIGHT = '''use crate::container::{Callbacks, Context, Result};
use crate::types::measurement::Measurement;
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_input(&mut self, ctx: &mut Context, message: &Measurement) -> Result {
        if let Ok(delay) = std::env::var("TEST_DELAY_NS") {
            std::thread::sleep(std::time::Duration::from_nanos(delay.parse()?));
        }
        assert_eq!(message.sequence, ctx.tick_index());
        assert_eq!(ctx.time_ns() as f64 / 1e9, ctx.now());
        ctx.publish_out(&Measurement { sequence: ctx.tick_index(), value: message.value + 2.0 + ctx.now() })
    }
}
'''
JOIN = '''from measurement import Measurement
def on_tick(ctx, inputs):
    assert inputs["left"].sequence == inputs["right"].sequence == ctx.tick_index
    ctx.publish_out(Measurement(sequence=ctx.tick_index, value=inputs["left"].value + inputs["right"].value))
'''
RAW = '''import os, signal, struct, time
from chain_env.execution.component import ComponentSession
s = ComponentSession(auto_ready=False, timed_ticks=True)
p = s.config.get("parameters", {})
if p.get("stderr_path"):
    with open(p["stderr_path"], "wb") as errors: os.dup2(errors.fileno(), 2)
if p.get("legacy"): s.timed_ticks = False
if p.get("hang"): signal.signal(signal.SIGTERM, signal.SIG_IGN)
if p.get("init_fail"): raise RuntimeError("injected initialization failure")
s.ready()
for inputs in s.ticks():
    if p.get("hang"):
        while True: time.sleep(1)
    if p.get("delay"): time.sleep(p["delay"])
    if inputs:
        values = [(-10.0 if raw is None else struct.unpack(">Qd", raw[1][1:])[1]) for raw in inputs.values()]
        messages = [(s.tick_index, sum(values))]
    else:
        messages = [(i, float(i)) for i in p.get("values", [s.tick_index])]
    for port in s.config["outputs"]:
        if p.get("flood"):
            while True: s.publish(port, b"\\x01" + struct.pack(">Qd", 1, 1.))
        for sequence, value in messages:
            s.publish(port, b"\\x01" + struct.pack(">Qd", sequence, value))
    if p.get("fail"): raise RuntimeError("injected callback failure")
'''


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT")
class SimulationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name)
        (cls.root / "measurement.sdl").write_text(SCHEMA)
        for filename, source in (("source.py", SOURCE), ("left.c", LEFT), ("right.rs", RIGHT), ("join.py", JOIN), ("raw.py", RAW)):
            (cls.root / filename).write_text(source)
        desc = {"root": "Measurement"}
        nodes = {
            "source": dict(inputs={}, outputs={"out": dict(desc)}, trigger=dict(mode="periodic", period_ms=100)),
            "left": dict(inputs={"input": dict(desc)}, outputs={"out": dict(desc)}, trigger=dict(mode="on_change")),
            "right": dict(inputs={"input": dict(desc)}, outputs={"out": dict(desc)}, trigger=dict(mode="on_change")),
            "join": dict(inputs={"left": dict(desc), "right": dict(desc)}, outputs={"out": dict(desc)}, trigger=dict(mode="on_change")),
        }
        model = dict(version=1, schemas="measurement.sdl", components=nodes, subscriptions=[
            dict(id="source-left", **{"from": "source.out", "to": "left.input"}),
            dict(id="source-right", **{"from": "source.out", "to": "right.input"}),
            dict(id="left-join", **{"from": "left.out", "to": "join.left"}),
            dict(id="right-join", **{"from": "right.out", "to": "join.right"}),
        ])
        deployment = dict(version=1, components={name: dict(container=dict(language=language, handler=name + suffix))
            for name, language, suffix in (("source", "python", ".py"), ("left", "c", ".c"), ("right", "rust", ".rs"), ("join", "python", ".py"))})
        (cls.root / "model.json").write_text(json.dumps(model))
        (cls.root / "deployment.json").write_text(json.dumps(deployment))
        cls.plan_path = build(cls.root / "model.json", cls.root / "deployment.json", SDL_ROOT, cls.root / "build")
        cls.diamond = json.loads(cls.plan_path.read_text())
        cls.desc = cls.diamond["components"]["left"]["inputs"]["input"]
        cls.codec = SDL2(SDL_ROOT)

    def node(self, inputs=(), *, source=False, parameters=None, period=None, outputs=True):
        ports = inputs if isinstance(inputs, dict) else {port: {} for port in inputs}
        return dict(inputs={port: dict(self.desc, **desc) for port, desc in ports.items()},
                    outputs={"out": dict(self.desc)} if outputs else {},
                    trigger=dict(mode="periodic", period_ms=period or 1000) if source or period else dict(mode="on_change"),
                    parameters=parameters or {}, cwd=str(self.root), env={}, command=[sys.executable, str(self.root / "raw.py")])

    def plan(self, nodes, links=(), external=()):
        result = {key: copy.deepcopy(value) for key, value in self.diamond.items() if key not in ("components", "subscriptions", "external_topics")}
        result["components"] = nodes
        result["external_topics"] = {name: dict(self.desc) for name in external}
        result["subscriptions"] = []
        for index, entry in enumerate(links):
            source, target, *settings = entry
            policy = Policy.resolve(settings[0] if settings else {})
            sub = dict(id=f"s{index}", **{"from": source, "to": target}, policy=policy.to_dict())
            if source in external:
                sub["from_topic"] = source
            result["subscriptions"].append(sub)
        return result

    async def simulate(self, plan, duration=.31, **options):
        trace = self.root / "trace.jsonl"
        engine = Simulation(plan, duration, trace=trace, **options)
        summary = await engine.execute()
        self.assertTrue(all(p.returncode is not None for p in engine.processes.values()))
        self.assertTrue(all(p.stdout.at_eof() and p.stdin.is_closing() for p in engine.processes.values()))
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(summary["trace_sha256"], hashlib.sha256(trace.read_bytes()).hexdigest())
        return summary, events

    def scenario(self, events):
        path = self.root / "scenario.json"
        records = []
        for index, (at, topic, value) in enumerate(events):
            name = f"event-{index}.s2ps"
            body = self.codec.wrap(self.desc["catalogue"], b"\x01" + struct.pack(">Qd", index + 1, value))
            (self.root / name).write_bytes(body)
            records.append(dict(at=at, topic=topic, envelope=name))
        path.write_text(json.dumps(dict(version=1, events=records)))
        return path

    async def test_native_diamond_trace_identical_across_workers_and_completion_delays(self):
        reference = None
        for workers, slow in ((1, "left"), (2, "left"), (8, "right"), (2, "right")):
            plan = copy.deepcopy(self.diamond)
            plan["components"][slow]["env"]["TEST_DELAY_NS"] = "10000000"
            summary, events = await self.simulate(plan, workers=workers)
            self.assertEqual(summary["ticks"], 16)
            self.assertEqual(summary["waves"], 12)
            self.assertEqual(summary["stop_reason"], "duration")
            self.assertEqual([e["components"] for e in events if e["event"] == "wave"][:3], [["source"], ["left", "right"], ["join"]])
            if reference is None:
                reference = events
            self.assertEqual(events, reference)
            pubs = [e for e in events if e["event"] == "publication" and e["topic"] == "join.out"]
            for index, event in enumerate(pubs, 1):
                t = (index - 1) / 10
                value = (t + 1 + t) + (t + 2 + t)
                expected = self.codec.wrap(self.desc["catalogue"], b"\x01" + struct.pack(">Qd", index, value))
                self.assertEqual(event["sha256"], hashlib.sha256(expected).hexdigest())

    async def test_workers_bound_real_concurrency_without_changing_logical_waves(self):
        plan = self.plan({name: self.node(source=True, outputs=False) for name in ("a", "b", "c")})
        reference = None
        for workers in (1, 2, 8):
            active, peak = 0, 0
            async def calculations(process, node, selected, time_ns, tick_index):
                nonlocal active, peak
                active += 1
                peak = max(active, peak)
                try:
                    await asyncio.sleep(.01)
                    if False:
                        yield None
                finally:
                    active -= 1
            with patch("chain_env.execution.simulation.coordinator.tick_events", calculations):
                summary, events = await self.simulate(plan, .1, workers=workers)
            self.assertEqual(peak, min(3, workers))
            self.assertEqual(summary["waves"], 1)
            if reference is None:
                reference = events
            self.assertEqual(events, reference)

    async def test_scenario_data_wins_at_timeout_and_horizon_is_exclusive(self):
        plan = self.plan({"sink": self.node({"input": dict(timeout=.1)})}, [("external", "sink.input")], ["external"])
        scenario = self.scenario([(.3, "external", 3.), (.1, "external", 1.)])
        summary, events = await self.simulate(plan, .3, scenario=scenario)
        ticks = [e for e in events if e["event"] == "tick"]
        self.assertEqual([e["time_ns"] for e in ticks], [100_000_000, 200_000_000])
        self.assertEqual(ticks[0]["inputs"], {"input": "scenario:1"})
        self.assertEqual(ticks[1]["inputs"], {"input": None})
        self.assertEqual(summary["time_ns"], 300_000_000)
        self.assertEqual(sum(e["event"] == "publication" and e["topic"] == "external" for e in events), 1)

    async def test_multiple_required_timeouts_and_mixed_snapshot(self):
        plan = self.plan({"sink": self.node({"a": dict(timeout=.1), "b": dict(timeout=.2), "optional": dict(required=False)})},
                         [("a", "sink.a"), ("b", "sink.b")], ["a", "b"])
        scenario = self.scenario([(.1, "a", 5.)])
        _, events = await self.simulate(plan, .5, scenario=scenario)
        ticks = [e for e in events if e["event"] == "tick"]
        self.assertEqual([e["time_ns"] for e in ticks], [200_000_000, 400_000_000])
        self.assertEqual(ticks[0]["inputs"], {"a": "scenario:0", "b": None})
        self.assertEqual(ticks[1]["inputs"], {"a": None, "b": None})

    async def test_latest_retained_state_and_quiescent_waiting_inputs(self):
        plan = self.plan({"sink": self.node({"input": dict(timeout=.1)})},
                         [("external", "sink.input", dict(buffer="latest"))], ["external"])
        summary, _ = await self.simulate(plan, 1000, scenario=self.scenario([(.1, "external", 1.)]))
        self.assertEqual(summary["ticks"], 1)
        self.assertEqual(summary["stop_reason"], "quiescent")
        self.assertEqual(summary["inputs"]["sink"]["input"]["depth"], 1)
        untimed = self.plan({"sink": self.node(["input"])}, [("external", "sink.input")], ["external"])
        summary, _ = await self.simulate(untimed, 1000)
        self.assertEqual(summary["waiting_inputs"], ["sink.input"])
        self.assertEqual(summary["ticks"], 0)

    async def test_fifo_overflow_policies_and_multiple_publications(self):
        for overflow, expected in (("reject_new", ["source:1:1", "source:1:2"]),
                                   ("drop_oldest", ["source:1:2", "source:1:3"])):
            plan = self.plan({"source": self.node(source=True, parameters=dict(values=[1, 2, 3])), "sink": self.node(["input"])},
                [("source.out", "sink.input", dict(capacity=2, overflow=overflow))])
            summary, events = await self.simulate(plan)
            ticks = [e["inputs"]["input"] for e in events if e["event"] == "tick" and e["component"] == "sink"]
            self.assertEqual(ticks, expected)
            self.assertEqual(summary["ticks"], 3)

    async def test_virtual_wait_unblocks_and_expires_without_blocking_consumers(self):
        plan = self.plan({"source": self.node(source=True, parameters=dict(values=[1, 2, 3])), "sink": self.node(["input"], period=200)},
                         [("source.out", "sink.input", dict(capacity=1, overflow="wait", wait_timeout_ms=100))])
        reference = None
        for workers in (1, 8):
            summary, events = await self.simulate(plan, workers=workers)
            admissions = [e for e in events if e["event"] == "admission"]
            self.assertEqual([(e["result"], e["time_ns"]) for e in admissions], [("accepted", 0), ("accepted", 0), ("full", 100_000_000)])
            self.assertEqual(summary["ticks"], 3)
            if reference is None:
                reference = events
            self.assertEqual(events, reference)

    async def test_wait_fanout_does_not_block_fast_branch(self):
        plan = self.plan({"source": self.node(source=True, parameters=dict(values=[1, 2])), "slow": self.node(["a", "b"]), "fast": self.node(["input"])},
            [("source.out", "slow.a", dict(capacity=1, overflow="wait", wait_timeout_ms=100)),
             ("source.out", "fast.input"), ("external", "slow.b")], ["external"])
        _, events = await self.simulate(plan)
        ticks = [e for e in events if e["event"] == "tick" and e["component"] == "fast"]
        self.assertEqual([e["time_ns"] for e in ticks], [0, 0])
        self.assertTrue(any(e["event"] == "admission" and e["result"] == "full" and e["time_ns"] == 100_000_000 for e in events))

    async def test_input_data_from_ancestor_wins_at_timeout(self):
        plan = self.plan({"source": self.node(source=True, period=100), "sink": self.node({"input": dict(timeout=.1)})}, [("source.out", "sink.input")])
        _, events = await self.simulate(plan, .31)
        ticks = [e for e in events if e["event"] == "tick" and e["component"] == "sink"]
        self.assertEqual(len(ticks), 4)
        self.assertTrue(all(e["inputs"]["input"] is not None for e in ticks))

    async def test_wave_failure_discards_all_outputs_and_reaps_every_child(self):
        errors = self.root / "expected-callback-error.log"
        plan = self.plan({"a": self.node(source=True), "b": self.node(source=True,
            parameters=dict(fail=True, stderr_path=str(errors)))})
        trace = self.root / "failed.jsonl"
        engine = Simulation(plan, .1, workers=2, trace=trace)
        with self.assertRaises((asyncio.IncompleteReadError, RuntimeError, ValueError)):
            await engine.execute()
        self.assertTrue(all(p.returncode is not None for p in engine.processes.values()))
        self.assertIn("RuntimeError: injected callback failure", errors.read_text())
        self.assertNotIn('"event":"publication"', trace.read_text())
        self.assertEqual(engine.summary["stop_reason"], "failed")

    async def test_legacy_component_is_refused_and_cleaned_up(self):
        engine = Simulation(self.plan({"source": self.node(source=True, parameters=dict(legacy=True))}), .1)
        with self.assertRaisesRegex(ValueError, "requires timestamped ticks"):
            await engine.execute()
        self.assertTrue(all(p.returncode is not None for p in engine.processes.values()))

    async def test_flooded_tick_fails_with_bounded_output_collection(self):
        engine = Simulation(self.plan({"source": self.node(source=True, parameters=dict(flood=True))}), .1,
                            max_tick_output_bytes=128)
        with self.assertRaisesRegex(ValueError, "tick outputs exceed"):
            await engine.execute()
        self.assertEqual(engine.summary["publications"], 0)
        self.assertTrue(all(p.returncode is not None for p in engine.processes.values()))

    async def test_cancellation_reaps_a_noncooperative_callback(self):
        engine = Simulation(self.plan({"source": self.node(source=True, parameters=dict(hang=True))}), .1)
        task = asyncio.create_task(engine.execute())
        try:
            async def wait_for_tick():
                while not engine.schedules["source"].tick_index:
                    await asyncio.sleep(.01)
            await asyncio.wait_for(wait_for_tick(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            self.assertTrue(all(p.returncode is not None for p in engine.processes.values()))
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_scenario_preflight_rejects_invalid_contracts_and_trace_overwrites(self):
        plan = self.plan({"sink": self.node(["input"])}, [("external", "sink.input")], ["external"])
        path = self.scenario([(0, "external", 1.)])
        original = json.loads(path.read_text())
        cases = [dict(version=2, events=[]), dict(version=1, events=[dict(at=-1, topic="external", envelope="event-0.s2ps")]),
                 dict(version=1, events=[dict(at=0, topic="unknown", envelope="event-0.s2ps")])]
        for document in cases:
            path.write_text(json.dumps(document))
            with self.assertRaises(ValueError):
                Simulation(plan, .1, scenario=path)
        path.write_text(json.dumps(original))
        with self.assertRaisesRegex(ValueError, "overwrite"):
            Simulation(plan, .1, scenario=path, trace=path)
        envelope = self.root / "event-0.s2ps"
        envelope.write_bytes(b"invalid")
        with self.assertRaises(ValueError):
            Simulation(plan, .1, scenario=path)

    async def test_cli_validation_and_last_status(self):
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
        for args in (("--mode", "simulated"), ("--workers", "2"), ("--mode", "simulated", "--duration", "1", "--port", "0"),
                     ("--duration", "nan")):
            result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "chain_env.cli", "run", *args],
                                             env=environment, capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn(b"Traceback", result.stderr)
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "chain_env.cli", "run", "--plan", str(self.plan_path),
            "--mode", "simulated", "--duration", ".1", "--workers", "2"], env=environment, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        summary = json.loads(result.stdout)
        saved = json.loads((self.root / "build/last-status.json").read_text())
        self.assertEqual(summary, saved)
        self.assertEqual(summary["mode"], "simulated")

    async def test_malformed_tick_contexts_are_rejected_by_all_generated_languages(self):
        for name in ("source", "left", "right"):
            node = self.diamond["components"][name]
            inputs = b"" if name == "source" else frame("D", port_data("input", b"\x01" + struct.pack(">Qd", 1, 0.), self.desc["catalogue"]))
            for body in (b"x", struct.pack(">QQ", 1, 0)):
                with self.subTest(component=name, body=body):
                    result = await asyncio.to_thread(subprocess.run, node["command"],
                        input=frame("I", json.dumps(node).encode()) + inputs + frame("T", body) + frame("X"),
                        capture_output=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)

    async def test_initialization_failure_cleans_up_other_ready_children(self):
        errors = self.root / "expected-initialization-error.log"
        engine = Simulation(self.plan({"a": self.node(source=True), "b": self.node(source=True,
            parameters=dict(init_fail=True, stderr_path=str(errors)))}), .1)
        with self.assertRaises(asyncio.IncompleteReadError):
            await engine.execute()
        self.assertTrue(all(p.returncode is not None for p in engine.processes.values()))
        self.assertIn("RuntimeError: injected initialization failure", errors.read_text())
        self.assertEqual(engine.summary["stop_reason"], "failed")

    async def test_changed_scenario_input_fails_instead_of_changing_the_run(self):
        plan = self.plan({"sink": self.node(["input"])}, [("external", "sink.input")], ["external"])
        scenario = self.scenario([(0, "external", 1.)])
        engine = Simulation(plan, .1, scenario=scenario)
        (self.root / "event-0.s2ps").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed during simulation"):
            await engine.execute()
        self.assertTrue(all(p.returncode is not None for p in engine.processes.values()))

    async def test_pending_external_payload_budget_is_enforced_incrementally(self):
        plan = self.plan({"sink": self.node(["a", "b"])},
                         [("external", "sink.a", dict(capacity=1, overflow="wait")), ("other", "sink.b")], ["external", "other"])
        scenario = self.scenario([(0, "external", float(index)) for index in range(20)])
        engine = Simulation(plan, .1, scenario=scenario, max_tick_output_bytes=48)
        with self.assertRaisesRegex(ValueError, "pending scenario publications exceed"):
            await engine.execute()
        self.assertEqual(engine.summary["publications"], 3)
        self.assertEqual(engine.summary["stop_reason"], "failed")

    async def test_diagnostics_cannot_overwrite_each_other_or_inputs(self):
        plan = self.plan({"source": self.node(source=True)})
        status = self.root / "build/last-status.json"
        with self.assertRaisesRegex(ValueError, "overwrite"):
            Simulation(plan, .1, trace=status)

    async def test_deadline_ranges_are_checked_before_launching_components(self):
        plan = self.plan({"source": self.node(source=True), "sink": self.node(["input"])},
                         [("source.out", "sink.input", dict(overflow="wait", wait_timeout_ms=10 ** 20))])
        with self.assertRaisesRegex(ValueError, "uint64"):
            Simulation(plan, .1)
        plan["subscriptions"][0]["policy"]["wait_timeout_ms"] = 100
        plan["components"]["sink"]["inputs"]["input"]["timeout"] = 1e30
        with self.assertRaisesRegex(ValueError, "uint64"):
            Simulation(plan, .1)

    async def test_extracted_project_example_and_scripted_inputs(self):
        project = self.root / "standalone"
        original = Path(__file__).resolve().parents[2]
        shutil.copytree(original, project, ignore=shutil.ignore_patterns("build", "__pycache__", "target", "*.egg-info"))
        environment = dict(os.environ, PYTHONPATH=str(project / "src"))
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "chain_env.cli", "build",
            "--model", "examples/simulated/model.json", "--deployment", "examples/simulated/deployment.json", "--sdl-root", SDL_ROOT],
            cwd=project, env=environment, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        environment["PYTHONPATH"] = os.pathsep.join((str(project / "src"), str(project / "build/generated/shared/python"), str(Path(SDL_ROOT) / "runtime/python")))
        result = await asyncio.to_thread(subprocess.run, [sys.executable, "examples/simulated/make_scenario.py"],
            cwd=project, env=environment, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        for workers in (1, 4):
            result = await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "chain_env.cli", "run", "--mode", "simulated",
                "--duration", ".5", "--workers", str(workers), "--scenario", "build/simulated-scenario/scenario.json", "--trace", f"build/w{workers}.jsonl"],
                cwd=project, env=environment, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout)["ticks"], 20)
        self.assertEqual((project / "build/w1.jsonl").read_bytes(), (project / "build/w4.jsonl").read_bytes())
        self.assertFalse((project / "build/endpoint.json").exists())
