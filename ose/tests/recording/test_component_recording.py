import asyncio
import copy
import io
import os
from pathlib import Path
import tempfile
import time
import unittest

from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator, Worker
from ose.execution.simulation import Simulation
from ose.recording.console import Console
from ose.recording.recorder import Recorder
from sdl_recording import RecordingReader
from tests.internal_recording import build_component

SDL_ROOT = os.environ["SDL_ROOT"]
POINT = "source.record.dsp.fft.intermediaire"


class InternalCaptureTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.plan = build_component(cls.directory.name, "python", SDL_ROOT)

    async def asyncSetUp(self):
        self.files = tempfile.TemporaryDirectory(); self.addCleanup(self.files.cleanup)
        self.tasks = []

    async def until(self, condition):
        deadline = time.monotonic() + 8
        while not condition():
            for task in self.tasks:
                if task.done(): task.result()
            if time.monotonic() > deadline: self.fail("internal recording timed out")
            await asyncio.sleep(.01)

    async def launch(self, distributed):
        plan = copy.deepcopy(self.plan)
        if distributed:
            plan["execution"] = "distributed"
            self.source = Coordinator(plan, lease_seconds=2)
            await self.source.start()
            self.component = Worker(plan, "source", "127.0.0.1", self.source.port)
            task = asyncio.create_task(self.component.run())
        else:
            self.source = Broker(plan)
            await self.source.start()
            self.component = Adapter(plan, "source")
            task = asyncio.create_task(self.component.run("127.0.0.1", self.source.port))
        self.tasks.append(task)
        self.addAsyncCleanup(self.shutdown)
        if distributed:
            await self.until(lambda: self.source.activated)
            await self.until(lambda: self.component.started.is_set())
        else:
            await self.until(lambda: "source" in self.source.ready)
            self.source.enabled = True
        return task

    async def shutdown(self):
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        await self.source.close()

    async def recorder(self, distributed):
        recorder = Recorder("127.0.0.1", self.source.port, distributed=distributed)
        await recorder.connect()
        self.addAsyncCleanup(recorder.close)
        recorder.selected = {POINT: None}
        return recorder

    async def capture(self, distributed, format):
        await self.launch(distributed)
        first, second = await self.recorder(distributed), await self.recorder(distributed)
        self.assertEqual(first.points[POINT]["kind"], "internal")
        self.assertEqual(first.points[POINT]["component"], "source")
        self.assertNotIn(POINT, self.component.node["outputs"])
        out = io.StringIO(); console = Console(first, out)
        await console.execute("list --kind internal")
        self.assertIn(POINT, out.getvalue())
        await console.execute("show " + POINT)
        self.assertIn("Spectre avant filtrage", out.getvalue())
        index = self.component.metrics["ticks"] if distributed else self.component.tick_index
        cutoff = index + 20
        await first.set_filter(POINT, f"sequence > {cutoff}")
        path = Path(self.files.name) / ("first." + format)
        capture = await first.start(path, format=format)
        other = await second.start(Path(self.files.name) / ("second." + format), format=format)
        await self.until(lambda: capture.counts["written"] >= 4 and other.counts["written"] >= 4)
        await first.stop()
        written = other.counts["written"]
        await self.until(lambda: other.counts["written"] >= written + 3)
        await second.stop()
        def disabled():
            endpoint = self.component.internal_recording if distributed else self.component.component_recording.endpoint
            return not endpoint.activation()
        await self.until(disabled)
        reader = RecordingReader(path); reader.validate()
        records = list(reader.iter_records())
        self.assertTrue(all(record.channel.topic == POINT for record in records))
        self.assertTrue(all(record.decode().fields["sequence"] > cutoff for record in records))
        self.assertGreaterEqual(first.status()["point_counts"][POINT]["observed"], 4)
        self.assertGreater(first.status()["point_counts"][POINT]["filtered"], 0)
        if distributed:
            self.assertEqual(self.component.metrics["publications"], 0)
            self.assertEqual(self.component.sequence, 0)
        else:
            self.assertEqual(dict(self.source.topic_counts), {})
            self.assertEqual(self.source.sequence, 0)

    async def test_local_internal_points_pcapng(self):
        await self.capture(False, "pcapng")

    async def test_local_internal_points_mcap(self):
        await self.capture(False, "mcap")

    async def test_distributed_internal_points_and_direct_worker_capture(self):
        await self.capture(True, "pcapng")

    async def test_disconnect_invalidates_active_local_capture(self):
        task = await self.launch(False)
        recorder = await self.recorder(False)
        generation = recorder.points[POINT]["generation"]
        capture = await recorder.start(Path(self.files.name) / "disconnected.pcapng")
        await self.until(lambda: capture.counts["written"] >= 2)
        task.cancel(); await asyncio.gather(task, return_exceptions=True)
        self.tasks.remove(task)
        await asyncio.wait_for(capture.task, 5)
        self.assertEqual(capture.state, "failed")
        self.assertIn("disconnected", capture.error)
        await recorder.refresh()
        self.assertNotIn(POINT, recorder.points)
        self.component = Adapter(copy.deepcopy(self.plan), "source")
        replacement = asyncio.create_task(self.component.run("127.0.0.1", self.source.port))
        self.tasks.append(replacement)
        await self.until(lambda: "source" in self.source.ready)
        await recorder.refresh()
        self.assertNotEqual(recorder.points[POINT]["generation"], generation)
        capture = await recorder.start(Path(self.files.name) / "replacement.pcapng")
        await self.until(lambda: capture.counts["written"] >= 2)
        await recorder.stop()

    async def test_simulation_accepts_declarations_and_leaves_diagnostics_disabled(self):
        trace = Path(self.files.name) / "simulation.jsonl"
        result = await Simulation(copy.deepcopy(self.plan), .04, trace=trace).execute()
        self.assertEqual(result["stop_reason"], "duration")
        self.assertGreater(result["ticks"], 0)
        self.assertEqual(result["publications"], 0)
        self.assertNotIn('"publication"', trace.read_text())
