import asyncio
import io
from types import SimpleNamespace
import unittest

from ose.recording.console import Console
from ose.recording.recorder import Recorder


class ConsoleSelectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.recorder = Recorder("127.0.0.1", 7000)
        self.recorder.points = {
            "sensor_1.signal": dict(root="SignalBlock", available=True),
            "sensor_2.signal": dict(root="SignalBlock", available=False),
            "analyzer_1.result": dict(root="Analysis", available=True),
            "alarm.event": dict(root="Alarm", available=True),
        }
        self.console = Console(self.recorder, io.StringIO())

    async def test_exact_message_type_selects_every_producer(self):
        await self.console.execute("select SignalBlock")
        self.assertEqual(self.recorder.selected, {"sensor_1.signal": None, "sensor_2.signal": None})

    async def test_type_globs_and_mixed_point_names_patterns_and_numbers(self):
        await self.console.execute('select "Signal*" Analys?s alarm.* 1')
        self.assertEqual(set(self.recorder.selected), set(self.recorder.points))
        await self.console.execute("unselect Signal[B]lock")
        self.assertEqual(self.recorder.selected, {"analyzer_1.result": None, "alarm.event": None})

    async def test_all_keeps_existing_filters_and_unselect_all_clears_selection(self):
        self.recorder.selected = {"alarm.event": "state == 1"}
        await self.console.execute("select all")
        self.assertEqual(set(self.recorder.selected), set(self.recorder.points))
        self.assertEqual(self.recorder.selected["alarm.event"], "state == 1")
        await self.console.execute("unselect all")
        self.assertEqual(self.recorder.selected, {})

    async def test_point_name_and_type_matches_are_combined_without_duplicates(self):
        self.recorder.points["Alarm"] = dict(root="Analysis", available=True)
        await self.console.execute("select Alarm A* alarm.event")
        self.assertEqual(set(self.recorder.selected), {"Alarm", "alarm.event", "analyzer_1.result"})

    async def test_no_match_and_case_mismatch_leave_selection_unchanged(self):
        self.recorder.selected = {"alarm.event": "state == 1"}
        for command in ("select signalblock", "select Signal* Missing*", "unselect Signal*"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "no recording point matches"):
                await self.console.execute(command)
            self.assertEqual(self.recorder.selected, {"alarm.event": "state == 1"})

    async def test_all_and_type_selection_are_frozen_during_capture(self):
        self.recorder.capture = SimpleNamespace(task=asyncio.get_running_loop().create_future())
        for command in ("select all", "select Signal*", "unselect all", "unselect Signal*"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "stop the recording"):
                await self.console.execute(command)
        self.assertEqual(self.recorder.selected, {})

    async def test_show_and_filter_still_resolve_only_point_names_and_numbers(self):
        for command in ("show SignalBlock", "filter SignalBlock samples > 0"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "no recording point matches"):
                await self.console.execute(command)
        self.assertEqual(self.console.names(["1"]), ["alarm.event"])
        self.assertEqual(self.console.names(["sensor_*.signal"]), ["sensor_1.signal", "sensor_2.signal"])


class ConsoleStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.recorder = Recorder("127.0.0.1", 7000)
        self.output = io.StringIO()
        self.console = Console(self.recorder, self.output)

    async def test_idle_status_is_readable_with_disabled_settings_and_empty_selection(self):
        await self.console.execute("status")
        self.assertEqual(self.output.getvalue(),
                         "State: idle\nLabel: recorder\nMax size: off\nCycle: off\n"
                         "Selected points (0):\n  none\nError: none\n")

    async def test_idle_status_includes_settings_and_filters(self):
        self.recorder.set_label("essai moteur 1")
        self.recorder.set_max_size(100_000_000)
        self.recorder.set_cycle(3)
        self.recorder.selected = {"sensor.signal": None, "alarm.event": "state == 1"}
        await self.console.execute("status")
        output = self.output.getvalue()
        for text in ("Label: essai moteur 1", "Max size: 100000000 B", "Cycle: 3 files",
                     "Selected points (2):", "alarm.event — filter: state == 1", "sensor.signal — filter: off"):
            self.assertIn(text, output)

    def capture_status(self, state="recording", error=None):
        return dict(state=state, label="recorder", max_size=1_000_000, cycle=3,
                    file="capture_004.pcapng", format="pcapng", base_file="capture.pcapng",
                    file_index=4, file_count=3, rotations=3, file_message_count=2,
                    counts=dict(received=12, written=11, filtered=5),
                    capture_counts=dict(observed=21, delivered=12, filtered=5, dropped=3, oversize=1),
                    selected={"sensor.signal": None}, topics={"sensor.signal": 11},
                    file_topics={"sensor.signal": 2}, error=error)

    async def test_recording_and_stopping_status_show_files_counts_and_losses(self):
        for state in ("recording", "stopping"):
            with self.subTest(state=state):
                self.output.seek(0)
                self.output.truncate()
                status = self.capture_status(state)
                self.recorder.capture = SimpleNamespace(status=lambda: status)
                await self.console.execute("status")
                output = self.output.getvalue()
                for text in (f"State: {state}", "File: capture_004.pcapng", "Format: pcapng",
                             "Base file: capture.pcapng", "File index: 4", "Files retained: 3",
                             "Rotations: 3", "Messages in current file: 2", "Received: 12", "Written: 11",
                             "Filtered: 5", "Indeterminate: 0", "Filter errors: 0", "Observed: 21",
                             "Delivered: 12", "Dropped: 3", "Oversize: 1", "Written: 11; in current file: 2"):
                    self.assertIn(text, output)

    async def test_completion_uses_text_and_preserves_failure_reporting(self):
        for state, error in (("stopped", None), ("failed", "disk full")):
            with self.subTest(state=state):
                self.output.seek(0)
                self.output.truncate()
                self.console.completed(self.capture_status(state, error))
                self.assertIn(f"State: {state}\n", self.output.getvalue())
                self.assertIn(f"Error: {error or 'none'}\n", self.output.getvalue())
                self.assertEqual(self.console.failed, bool(error))
