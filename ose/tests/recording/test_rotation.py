"""Real recorder rotation: standalone files, bounded retention and collisions."""
import asyncio
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from ose.codec.sdl2 import SDL2
from ose.execution.broker import Broker
from ose.recording.console import Console
from ose.recording.recorder import Recorder
from sdl_recording import RecordingReader


CAT = "SDL2|Value{1:uint64:sequence;2:fl64:value;}"


class RotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.codec = SDL2(os.environ["SDL_ROOT"])
        self.broker = Broker(dict(sdl_root=os.environ["SDL_ROOT"],
            components={"source": dict(inputs={}, outputs={"value": dict(root="Value", catalogue=CAT)})},
            subscriptions=[], max_frame=1048576, max_datagram=1200))
        await self.broker.start()
        self.broker.peers["source"] = (None, None)
        self.broker.enabled = True
        self.addAsyncCleanup(self.broker.close)
        self.recorder = Recorder("127.0.0.1", self.broker.port)
        await self.recorder.connect()
        self.addAsyncCleanup(self.recorder.close)
        self.recorder.selected = {"source.value": None}

    async def publish(self, number):
        payload = b"\x01" + struct.pack(">Qd", number, float(number))
        await self.broker.publish("source.value", self.codec.wrap(CAT, payload))
        return payload

    def check_file(self, path, index):
        reader = RecordingReader(path)
        reader.validate()
        records = list(reader.iter_records())
        info = reader.info()
        initial = next(item["data"] for item in info["metadata"] if item["name"] == "sdl.recording")
        final = next(item["data"] for item in info["metadata"] if item["name"] == "ose.session")
        status = json.loads(final["status"])
        self.assertEqual(initial["file_index"], str(index))
        self.assertEqual(initial["label"], "recorder")
        self.assertEqual(status["file_message_count"], len(records))
        self.assertEqual(status["file_index"], index)
        return records, final

    async def test_rotation_preserves_every_message_across_complete_files_in_both_formats(self):
        self.recorder.set_max_size(2048)
        for format in ("pcapng", "mcap"):
            base = self.root / ("all." + format)
            capture = await self.recorder.start(base, format=format)
            session = capture.feeds[0]["session"]
            payloads = [await self.publish(number) for number in range(100)]
            await self.recorder.stop()
            self.assertIsNone(capture.error)
            paths = sorted(self.root.glob("all_*." + format))
            self.assertGreater(len(paths), 1)
            actual = []
            for index, path in enumerate(paths, 1):
                records, final = self.check_file(path, index)
                self.assertTrue(records)
                actual.extend(record.payload for record in records)
                self.assertEqual(final["final"], "true" if index == len(paths) else "false")
                if index < len(paths):
                    self.assertGreaterEqual(path.stat().st_size, 2048)
            self.assertEqual(actual, payloads)
            self.assertFalse(base.exists())
            status = capture.status()
            self.assertEqual(status["counts"]["written"], 100)
            self.assertEqual(status["rotations"], len(paths) - 1)
            self.assertEqual(status["file_count"], len(paths))
            self.assertEqual(capture.feeds[0]["session"], session)

    async def test_cycle_retains_latest_files_with_continuously_increasing_indices(self):
        self.recorder.set_max_size(1)
        for format in ("pcapng", "mcap"):
            for limit in (1, 3):
                self.recorder.set_cycle(limit)
                stem = f"cycle-{limit}"
                capture = await self.recorder.start(self.root / (stem + "." + format), format=format)
                for number in range(7):
                    await self.publish(number)
                await self.recorder.stop()
                self.assertIsNone(capture.error)
                paths = sorted(self.root.glob(stem + "_*." + format))
                self.assertEqual([path.name for path in paths],
                    [f"{stem}_{index:03d}.{format}" for index in range(8 - limit, 8)])
                for index, path in zip(range(8 - limit, 8), paths):
                    records, _ = self.check_file(path, index)
                    self.assertEqual([record.decode().fields["sequence"] for record in records], [index - 1])
                self.assertEqual(capture.status()["file_index"], 7)
                self.assertEqual(capture.status()["rotations"], 6)
                self.assertEqual(capture.status()["file_count"], limit)
                self.assertEqual(capture.status()["counts"]["written"], 7)

    async def test_empty_rotating_capture_creates_one_valid_file(self):
        self.recorder.set_max_size(1)
        self.recorder.set_cycle(2)
        for format in ("pcapng", "mcap"):
            capture = await self.recorder.start(self.root / ("empty." + format), format=format)
            await self.recorder.stop()
            records, final = self.check_file(self.root / ("empty_001." + format), 1)
            self.assertEqual(records, [])
            self.assertEqual(final["final"], "true")
            self.assertEqual(capture.status()["rotations"], 0)

    async def test_existing_series_and_late_collisions_leave_unrelated_files_untouched(self):
        self.recorder.set_max_size(1)
        self.recorder.set_cycle(2)
        for format in ("pcapng", "mcap"):
            protected = self.root / ("existing_009." + format)
            protected.write_bytes(b"unrelated")
            with self.assertRaises(FileExistsError):
                await self.recorder.start(self.root / ("existing." + format), format=format)
            self.assertEqual(protected.read_bytes(), b"unrelated")
            self.assertEqual(self.broker.recording.sessions, {})
            capture = await self.recorder.start(self.root / ("late." + format), format=format)
            protected = self.root / ("late_003." + format)
            protected.write_bytes(b"unrelated")
            for number in range(3):
                await self.publish(number)
            await asyncio.wait_for(capture.task, 5)
            self.assertIsNotNone(capture.error)
            self.assertEqual(protected.read_bytes(), b"unrelated")
            self.check_file(self.root / ("late_001." + format), 1)
            _, final = self.check_file(self.root / ("late_002." + format), 2)
            self.assertEqual(final["final"], "true")
            self.assertTrue(final["error"])
            self.assertEqual(self.broker.recording.sessions, {})

    async def test_interactive_settings_query_disable_and_freeze(self):
        output = io.StringIO()
        console = Console(self.recorder, output)
        for command in ("max-size", "cycle", "max-size 1.5KiB", "cycle 3", "max-size", "cycle"):
            await console.execute(command)
        self.assertEqual(self.recorder.max_size, 1536)
        self.assertEqual(self.recorder.cycle, 3)
        self.assertIn('"max-size": 1536', output.getvalue())
        capture = await self.recorder.start(self.root / "settings.pcapng")
        for command in ("max-size off", "cycle off"):
            with self.assertRaisesRegex(ValueError, "stop"):
                await console.execute(command)
        await self.recorder.stop()
        await console.execute("max-size off")
        with self.assertRaisesRegex(ValueError, "cycle requires"):
            await self.recorder.start(self.root / "invalid.pcapng")
        await console.execute("cycle off")
        for command in ("cycle 0", "cycle -1", "cycle 1.5", "max-size 0", "max-size 1.1B"):
            with self.assertRaises(ValueError):
                await console.execute(command)
        self.assertEqual(capture.status()["max_size"], 1536)
        plain = self.root / "plain.pcapng"
        await self.recorder.start(plain)
        await self.recorder.stop()
        self.assertTrue(plain.exists())
        self.assertFalse((self.root / "plain_001.pcapng").exists())
