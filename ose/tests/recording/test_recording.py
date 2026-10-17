import asyncio
import copy
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest

from ose.codec.sdl2 import SDL2
from ose.execution.broker import Broker
from ose.recording.recorder import Recorder
from ose.recording.console import Console
from ose.transport.secure import connect
from sdl_recording import RecordingReader

SDL_ROOT = os.environ["SDL_ROOT"]
CAT = "SDL2|Value{1:uint64:sequence;2:fl64:value;}"


class RecordingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.codec = SDL2(SDL_ROOT)
        port = dict(root="Value", catalogue=CAT)
        self.broker = Broker(dict(sdl_root=SDL_ROOT, components={"source": dict(inputs={}, outputs={"value": port})},
                                  subscriptions=[], max_frame=1024 * 1024, max_datagram=1200))
        await self.broker.start()
        self.broker.peers["source"] = (None, None)
        self.broker.enabled = True
        self.addAsyncCleanup(self.broker.close)
        self.recorder = Recorder("127.0.0.1", self.broker.port)
        await self.recorder.connect()
        self.addAsyncCleanup(self.recorder.close)
        self.recorder.selected["source.value"] = None

    def body(self, sequence, value=None):
        return self.codec.wrap(CAT, b"\x01" + struct.pack(">Qd", sequence, float(sequence) if value is None else value))

    async def publish(self, sequence, value=None):
        await self.broker.publish("source.value", self.body(sequence, value))

    async def until(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            if self.recorder.capture and self.recorder.capture.error:
                self.fail(self.recorder.capture.error)
            if time.monotonic() >= deadline:
                self.fail("recording condition timed out")
            await asyncio.sleep(.01)

    async def test_original_bytes_catalogues_timestamps_and_both_formats(self):
        await self.publish(99)  # A tap has no historical replay.
        for format in ("pcapng", "mcap"):
            path = self.root / ("capture." + format)
            await self.recorder.start(path, format=format)
            for n in range(5):
                await self.publish(n)
            await self.recorder.stop()
            reader = RecordingReader(path)
            reader.validate()
            records = list(reader.iter_records())
            self.assertEqual(len(records), 5)
            self.assertEqual([bytes(r.payload) for r in records], [self.body(n)[self.codec.api.ENVELOPE_HEADER_SIZE:] for n in range(5)])
            self.assertTrue(all(r.channel.topic == "source.value" and r.channel.catalogue == CAT for r in records))
            self.assertTrue(all(r.log_time_ns > 10**18 and r.publish_time_ns == r.log_time_ns for r in records))
            self.assertEqual([r.decode().fields["value"] for r in records], list(range(5)))
            self.assertEqual(self.recorder.status()["state"], "stopped")
            self.assertEqual(self.broker.recording.sessions, {})

    async def test_filters_are_typed_and_operate_on_wire_values(self):
        with self.assertRaises(ValueError):
            await self.recorder.set_filter("source.value", "missing > 0")
        await self.recorder.set_filter("source.value", "value >= 3 && sequence < 5")
        path = self.root / "filtered.pcapng"
        await self.recorder.start(path)
        for n in range(7):
            await self.publish(n)
        await self.recorder.stop()
        records = list(RecordingReader(path).iter_records())
        self.assertEqual([r.decode().fields["value"] for r in records], [3., 4.])
        self.assertEqual(self.recorder.status()["counts"]["filtered"], 5)

    async def test_inspect_live_recording_has_no_default_message_limit(self):
        path = self.root / "eight-messages.pcapng"
        await self.recorder.start(path)
        for sequence in range(8):
            await self.publish(sequence)
        await self.recorder.stop()
        tool = Path(SDL_ROOT) / "tools/recording/sdl-recording"
        for options, count in (([], 8), (["--limit", "3"], 3)):
            process = await asyncio.create_subprocess_exec(sys.executable, str(tool), "inspect", str(path),
                *options, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await process.communicate()
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual([json.loads(line)["message"]["fields"]["sequence"]
                              for line in stdout.splitlines()], list(range(count)))

    async def test_indeterminate_filter_is_counted_and_not_written(self):
        await self.recorder.set_filter("source.value", "value >= 0")
        path = self.root / "nan.pcapng"
        await self.recorder.start(path)
        await self.publish(1, float("nan"))
        await self.publish(2, 3.)
        await self.recorder.stop()
        self.assertEqual(self.recorder.status()["counts"]["indeterminate"], 1)
        self.assertEqual(len(list(RecordingReader(path).iter_records())), 1)

    async def test_encoded_quantization_is_preserved_and_controls_filter_results(self):
        api = self.codec.api
        catalogue = "SDL2|Sample{1:fl64:value@b12,q0.25;}"
        class Sample(api.SdlMessage):
            _SDL_NAME = "Sample"
            _SDL_DESCRIPTOR = catalogue
            _SDL_FIELDS = ((1, "value", "required", "fl64", ()),)
        point = dict(root="Sample", catalogue=catalogue)
        self.broker.topics["source.quantized"] = point
        self.broker.register(catalogue)
        await self.recorder.refresh()
        self.recorder.selected = {"source.quantized": None}
        await self.recorder.set_filter("source.quantized", "value > 1.23")
        message = Sample(value=1.21)  # On the wire this rounds to 1.25.
        payload = api.encode(api.prepare(catalogue, [Sample]), message)
        path = self.root / "quantized.pcapng"
        await self.recorder.start(path)
        await self.broker.publish("source.quantized", self.codec.wrap(catalogue, payload))
        await self.recorder.stop()
        records = list(RecordingReader(path).iter_records())
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].payload, payload)
        self.assertEqual(records[0].decode().fields["value"], 1.25)

    async def test_disabling_external_access_also_disables_recording(self):
        self.broker.limits["enabled"] = False
        with self.assertRaisesRegex(ValueError, "disabled"):
            await self.recorder.refresh()

    async def test_slow_disk_does_not_block_application_and_reports_loss(self):
        self.recorder.capacity = 2
        self.recorder.byte_budget = 1024
        path = self.root / "bounded.pcapng"
        capture = await self.recorder.start(path)
        original = capture.write
        def slow(*args):
            time.sleep(.03)
            original(*args)
        capture.write = slow
        started = time.monotonic()
        for n in range(100):
            await self.publish(n)
        self.assertLess(time.monotonic() - started, 1)
        await self.recorder.stop()
        self.assertGreater(self.recorder.status()["capture_counts"]["dropped"], 0)
        self.assertLessEqual(self.recorder.status()["counts"]["written"], 3)
        RecordingReader(path).validate()

    async def test_disconnect_and_disk_failure_finalize_partial_file(self):
        path = self.root / "failure.pcapng"
        capture = await self.recorder.start(path)
        await self.publish(1)
        await self.until(lambda: capture.counts["written"] == 1)
        def full(*args):
            raise OSError("disk full")
        capture.write = full
        await self.publish(2)
        await asyncio.wait_for(capture.task, 3)
        self.assertEqual(capture.state, "failed")
        self.assertIn("disk full", capture.error)
        RecordingReader(path).validate()
        self.assertEqual(self.broker.recording.sessions, {})
        await self.recorder.start(self.root / "disconnect.pcapng")
        self.broker.recording.disconnected(self.recorder.capture.feeds[0]["client"])
        await self.broker.close()
        await asyncio.wait_for(self.recorder.capture.task, 4)
        self.assertEqual(self.recorder.status()["state"], "failed")

    async def test_selection_is_frozen_and_existing_files_are_preserved(self):
        path = self.root / "existing.pcapng"
        await self.recorder.start(path)
        with self.assertRaisesRegex(ValueError, "stop"):
            await self.recorder.set_filter("source.value", "value > 0")
        await self.recorder.stop()
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            await self.recorder.start(path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.broker.recording.sessions, {})

    async def test_explicit_reconnect_after_broker_restart_starts_a_new_file(self):
        capture = await self.recorder.start(self.root / "old.pcapng")
        port, plan = self.broker.port, self.broker.plan
        await self.broker.close()
        await asyncio.wait_for(capture.task, 4)
        self.assertEqual(capture.state, "failed")
        self.broker = Broker(plan)
        self.addAsyncCleanup(self.broker.close)
        await self.broker.start(port)
        self.broker.peers["source"] = (None, None)
        self.broker.enabled = True
        await self.recorder.reconnect()
        capture = await self.recorder.start(self.root / "new.pcapng")
        await self.publish(8)
        await self.recorder.stop()
        self.assertFalse(capture.error)
        self.assertEqual(len(list(RecordingReader(capture.path).iter_records())), 1)

    async def test_interactive_console_selection_description_and_repeated_sessions(self):
        output = io.StringIO()
        console = Console(self.recorder, output)
        await console.execute("unselect *")
        await console.execute("list")
        await console.execute("select 1")
        await console.execute("show source.value")
        await console.execute("filter source.value value > 1")
        self.assertIn(CAT, output.getvalue())
        self.assertEqual(self.recorder.selected, {"source.value": "value > 1"})
        for n in range(2):
            await console.execute(f"start {self.root / str(n)} pcapng")
            await self.publish(3)
            await console.execute("stop")
        await console.execute("filter 1 off")
        self.assertIsNone(self.recorder.selected["source.value"])
        self.assertFalse(await console.execute("quit"))

    async def test_cli_script_and_sigterm_close_the_file(self):
        path = self.root / "cli.pcapng"
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "record",
            "--host", "127.0.0.1", "--port", str(self.broker.port), "--sdl-root", SDL_ROOT,
            "--command", "select *", "--command", f"start {path}", "--command", "wait 30",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async def started():
                while True:
                    line = await process.stdout.readline()
                    if b"Recording to" in line:
                        return
                    if not line:
                        self.fail((await process.stderr.read()).decode())
            await asyncio.wait_for(started(), 5)
            await self.publish(7)
            await asyncio.sleep(.1)
            process.terminate()
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            self.assertEqual(process.returncode, 130, stderr.decode() + stdout.decode())
            RecordingReader(path).validate()
            self.assertEqual(len(list(RecordingReader(path).iter_records())), 1)
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()

    async def test_cli_sdl_root_discovers_optional_services_without_pythonpath(self):
        path = self.root / "cli-filtered.pcapng"
        # A fresh interpreter must find both the filter extension and recording
        # dependencies from --sdl-root, without inheriting the test's SDL paths.
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src"))
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "record",
            "--host", "127.0.0.1", "--port", str(self.broker.port), "--sdl-root", SDL_ROOT,
            "--command", "select source.value", "--command", "filter 1 value >= 3 && sequence < 5",
            "--command", f"start {path}", "--command", "wait 30", env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            async def started():
                while True:
                    line = await process.stdout.readline()
                    if b"Recording to" in line:
                        return
                    if not line:
                        self.fail((await process.stderr.read()).decode())
            await asyncio.wait_for(started(), 5)
            for n in range(7):
                await self.publish(n)
            process.terminate()
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            self.assertEqual(process.returncode, 130, stderr.decode() + stdout.decode())
            reader = RecordingReader(path)
            reader.validate()
            self.assertEqual([r.decode().fields["value"] for r in reader.iter_records()], [3., 4.])
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()

    async def test_interactive_stdin_and_eof_or_signal_while_waiting_for_commands(self):
        for ending in ("eof", "sigterm"):
            path = self.root / (ending + ".pcapng")
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "record",
                "--host", "127.0.0.1", "--port", str(self.broker.port), "--sdl-root", SDL_ROOT,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                process.stdin.write(f"select source.*\nfilter source.value value > 0\nstart {path}\n".encode())
                await process.stdin.drain()
                async def started():
                    while True:
                        line = await process.stdout.readline()
                        if b"Recording to" in line:
                            return
                        if not line:
                            self.fail((await process.stderr.read()).decode())
                await asyncio.wait_for(started(), 5)
                await self.publish(5)
                await asyncio.sleep(.05)
                if ending == "eof":
                    process.stdin.close()
                else:
                    process.terminate()
                stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
                self.assertEqual(process.returncode, 0 if ending == "eof" else 130, stderr.decode() + stdout.decode())
                RecordingReader(path).validate()
                self.assertEqual(len(list(RecordingReader(path).iter_records())), 1)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()


from tests.integration import test_distributed as distributed_fixture
from ose.execution.distributed import Coordinator, Worker


class DistributedRecordingTests(unittest.IsolatedAsyncioTestCase):
    setUpClass = classmethod(distributed_fixture.DistributedTests.setUpClass.__func__)
    asyncSetUp = distributed_fixture.DistributedTests.asyncSetUp
    shutdown = distributed_fixture.DistributedTests.shutdown
    until = distributed_fixture.DistributedTests.until
    launch = distributed_fixture.DistributedTests.launch
    start_graph = distributed_fixture.DistributedTests.start_graph
    values = distributed_fixture.DistributedTests.values

    async def recorder(self, **options):
        recorder = Recorder("127.0.0.1", self.coordinator.port, distributed=True, **options)
        await recorder.connect()
        self.addAsyncCleanup(recorder.close)
        return recorder

    async def test_direct_and_broker_outputs_are_discovered_and_recorded_without_model_changes(self):
        await self.start_graph()
        before = copy.deepcopy(self.plan)
        recorder = await self.recorder()
        self.assertEqual(set(recorder.points), {"source.value", "middle.value"})
        recorder.selected = {topic: None for topic in recorder.points}
        path = Path(self.directory.name) / "distributed.pcapng"
        capture = await recorder.start(path)
        await self.until(lambda: all(capture.topic_counts[t] >= 3 for t in recorder.selected))
        await recorder.stop()
        reader = RecordingReader(path)
        reader.validate()
        records = list(reader.iter_records())
        self.assertEqual({r.channel.topic for r in records}, set(recorder.points))
        self.assertEqual(self.plan, before)
        self.assertNotIn("signal", self.coordinator.engines)
        for record in records:
            value = record.decode()
            self.assertEqual(value.fields["value"], value.fields["sequence"] * (2 if record.channel.topic == "middle.value" else 1))

    async def test_worker_replacement_is_explicit_and_a_new_session_can_resume(self):
        await self.start_graph()
        recorder = await self.recorder()
        recorder.selected = {"source.value": None}
        capture = await recorder.start(Path(self.directory.name) / "before.pcapng")
        await self.until(lambda: capture.counts["written"] >= 2)
        self.tasks["source"].cancel()
        await asyncio.gather(self.tasks["source"], return_exceptions=True)
        await asyncio.wait_for(capture.task, 4)
        self.assertEqual(capture.state, "failed")
        RecordingReader(capture.path).validate()
        await self.launch("source")
        await recorder.refresh()
        capture = await recorder.start(Path(self.directory.name) / "after.pcapng")
        await self.until(lambda: capture.counts["written"] >= 2)
        await recorder.stop()

    async def test_mutual_tls_recorder_authenticates_each_worker(self):
        root = Path(self.directory.name)
        await asyncio.to_thread(distributed_fixture.certificates, root, ["coordinator", "source", "middle", "sink", "recorder"])
        await self.coordinator.close()
        self.coordinator = Coordinator(self.plan, distributed_fixture.tls(root, "coordinator")[0], lease_seconds=1)
        await self.coordinator.start()
        for name in self.plan["components"]:
            server, client = distributed_fixture.tls(root, name)
            worker = Worker(self.plan, name, "127.0.0.1", self.coordinator.port,
                            server_context=server, client_context=client)
            self.workers[name] = worker
            self.tasks[name] = asyncio.create_task(worker.run())
        await self.until(lambda: self.coordinator.activated)
        recorder = await self.recorder(context=distributed_fixture.tls(root, "recorder")[1])
        recorder.selected = {"source.value": None}
        capture = await recorder.start(root / "secure.pcapng")
        await self.until(lambda: capture.counts["written"] >= 2)
        await recorder.stop()
        self.assertFalse(capture.error)
