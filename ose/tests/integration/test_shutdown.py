"""Terminal interrupts must let supervision stop components through the IPC protocol."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest

from ose.modeling.compiler import build


SDL_ROOT = os.environ.get("SDL_ROOT")


@unittest.skipUnless(SDL_ROOT and hasattr(os, "killpg"), "requires SDL_ROOT and POSIX process groups")
class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "value.sdl").write_text("message Value {uint64 sequence;}")
        (self.root / "handler.py").write_text('''import json, os, time
from pathlib import Path
from value import Value

def mark(ctx, event):
    with open(ctx.parameters["events"], "a") as stream:
        stream.write(json.dumps([ctx.name, event, os.getpid()]) + "\\n")

def on_start(ctx):
    mark(ctx, "start")
    if ctx.parameters.get("pause"):
        while not Path(ctx.parameters["resume"]).exists():
            time.sleep(.005)

def on_tick(ctx, inputs=None):
    if ctx.name == "source":
        ctx.publish("value", Value(sequence=ctx.tick_index))

def on_stop(ctx):
    mark(ctx, "stop")
''')
        port = {"root": "Value"}
        model = dict(version=1, schemas="value.sdl", components={
            "source": dict(inputs={}, outputs={"value": port},
                           trigger=dict(mode="periodic", period_ms=10)),
            "sink": dict(inputs={"input": port}, outputs={}, trigger=dict(mode="on_change")),
        }, subscriptions=[dict(id="link", **{"from": "source.value", "to": "sink.input"})])
        deployment = dict(version=1, components={name: dict(container=dict(language="python", handler="handler.py"))
                                                for name in model["components"]})
        (self.root / "model.json").write_text(json.dumps(model))
        (self.root / "deployment.json").write_text(json.dumps(deployment))
        self.plan_path = build(self.root / "model.json", self.root / "deployment.json", SDL_ROOT, self.root / "build")
        self.plan = json.loads(self.plan_path.read_text())
        for node in self.plan["components"].values():
            node["parameters"] = dict(events=str(self.root / "events"), resume=str(self.root / "resume"))

    def events(self):
        path = self.root / "events"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def interrupt(self, *, during_startup=False):
        if during_startup:
            for node in self.plan["components"].values():
                node["parameters"]["pause"] = True
        self.plan_path.write_text(json.dumps(self.plan))
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "run",
            "--plan", str(self.plan_path), env=env, start_new_session=True,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            if during_startup:
                async def started():
                    while len(self.events()) < 2:
                        if process.returncode is not None:
                            self.fail((await process.stderr.read()).decode())
                        await asyncio.sleep(.01)
                await asyncio.wait_for(started(), 10)
            else:
                line = await asyncio.wait_for(process.stdout.readline(), 10)
                self.assertTrue(line, (await process.stderr.read()).decode() if not line else "")
                self.assertTrue(json.loads(line)["running"])
            # Model terminal Ctrl+C, which signals the entire foreground group.
            os.killpg(process.pid, signal.SIGINT)
            (self.root / "resume").touch()
            stdout, stderr = await asyncio.wait_for(process.communicate(), 8)
            self.assertEqual(process.returncode, 0, stderr.decode() + stdout.decode())
            self.assertNotIn(b"Traceback", stderr)
            self.assertNotIn(b"KeyboardInterrupt", stderr)
            events = self.events()
            self.assertEqual(sorted((name, event) for name, event, _ in events),
                             [("sink", "start"), ("sink", "stop"), ("source", "start"), ("source", "stop")])
            for _, _, pid in events:
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
        finally:
            if process.returncode is None:
                process.terminate()
                await asyncio.wait_for(process.communicate(), 8)

    async def test_ctrl_c_stops_running_chain_without_tracebacks(self):
        await self.interrupt()

    async def test_ctrl_c_during_initialization_runs_stop_callbacks_without_tracebacks(self):
        await self.interrupt(during_startup=True)
