import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from ose.recording.taps import TapHub


class TapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.point = dict(topic="source.value", root="Value", catalogue="SDL2|Value{1:uint8:x;}",
                          generation="first", available=True, endpoint=None)
        self.hub = TapHub(lambda: {self.point["topic"]: dict(self.point)}, 1024 * 1024)
        self.peer = object()

    async def open(self, **options):
        result, _ = await self.hub.request(self.peer, dict(op="recording_open", points={"source.value": "first"}, **options))
        return result["session"]

    def offer(self, body=b"payload", sequence=1):
        self.hub.observe("source.value", body, generation=self.point["generation"], sequence=sequence,
                         catalogue=self.point["catalogue"], root=self.point["root"])

    async def test_fifo_overflow_is_bounded_and_stop_drains(self):
        token = await self.open(capacity=2, byte_budget=1024)
        for n in range(5):
            self.offer(sequence=n)
        self.assertEqual(self.hub.sessions[token]["counts"]["dropped"], 3)
        await self.hub.request(self.peer, dict(op="recording_stop", session=token))
        self.offer(sequence=99)
        received = []
        for _ in range(3):
            result, body = await self.hub.request(self.peer, dict(op="recording_next", session=token))
            if not result.get("empty"):
                received.append(result["record"]["sequence"])
                self.assertEqual(body, b"payload")
        self.assertEqual(received, [3, 4])
        self.assertTrue(result["stopped"])
        self.assertEqual(result["queued_bytes"], 0)

    async def test_byte_budget_and_connection_ownership(self):
        token = await self.open(capacity=100, byte_budget=1024)
        self.offer(b"a" * 700)
        self.offer(b"b" * 700)
        self.assertEqual(self.hub.sessions[token]["bytes"], 700)
        self.offer(b"c" * 1500)
        self.assertEqual(self.hub.sessions[token]["counts"]["dropped"], 2)
        with self.assertRaisesRegex(ValueError, "another connection"):
            await self.hub.request(object(), dict(op="recording_next", session=token))
        self.hub.disconnected(self.peer)
        self.assertEqual(self.hub.sessions, {})

    async def test_labels_are_informational_and_duplicate_labels_keep_separate_owners(self):
        default = await self.open()
        self.assertEqual(self.hub.sessions[default]["label"], "recorder")
        self.hub.close()
        first = await self.open(label="essai moteur")
        other_peer = object()
        opened, _ = await self.hub.request(other_peer, dict(op="recording_open",
            points={"source.value": "first"}, label="essai moteur"))
        second = opened["session"]
        self.assertNotEqual(first, second)
        self.assertEqual(opened["label"], "essai moteur")
        with self.assertRaisesRegex(ValueError, "another connection"):
            await self.hub.request(other_peer, dict(op="recording_stop", session=first, label="essai moteur"))
        stats, _ = await self.hub.request(self.peer, dict(op="recording_stop", session=first))
        self.assertEqual(stats["label"], "essai moteur")
        self.assertTrue(self.hub.sessions[second]["active"])
        self.hub.close()

    async def test_invalid_labels_are_rejected_without_allocating_sessions(self):
        for label in (None, 42, "", "  ", "é" * 129):
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "label"):
                await self.open(label=label)
        self.assertEqual(self.hub.sessions, {})

    async def test_schema_or_generation_changes_end_capture(self):
        token = await self.open()
        self.point["catalogue"] = "SDL2|Value{1:uint16:x;}"
        self.offer()
        result, _ = await self.hub.request(self.peer, dict(op="recording_next", session=token))
        self.assertTrue(result["stopped"])
        self.assertIn("changed", result["capture_error"])
        with self.assertRaisesRegex(ValueError, "changed"):
            await self.hub.request(self.peer, dict(op="recording_open", points={"source.value": "old"}))

    async def test_global_budget_and_oversize_are_explicit(self):
        token = await self.open(byte_budget=self.hub.MAX_BYTES)
        with self.assertRaisesRegex(ValueError, "capacity"):
            await self.open()
        self.hub.max_frame = 600
        self.offer(b"a" * 1000)
        self.assertEqual(self.hub.sessions[token]["counts"]["oversize"], 1)
        self.assertEqual(self.hub.sessions[token]["bytes"], 0)

    async def test_filter_configuration_is_atomic_and_validates_topics(self):
        for filters in ([], {"other": "x == 1"}, {"source.value": 1}, {"source.value": ""}):
            with self.subTest(filters=filters), self.assertRaisesRegex(ValueError, "filters"):
                await self.open(filters=filters)
        other = {**self.point, "topic": "other"}
        self.hub.points = lambda: {self.point["topic"]: self.point, "other": other}
        self.hub.codec = Mock()
        first = Mock()
        with patch("ose.recording.taps.prepare_filter", side_effect=[first, ValueError("invalid expression")]):
            with self.assertRaisesRegex(ValueError, "invalid expression"):
                await self.hub.request(self.peer, dict(op="recording_open",
                    points={"source.value": "first", "other": "first"},
                    filters={"source.value": "x == 1", "other": "missing == 2"}))
        first.close.assert_called_once()
        self.assertEqual(self.hub.sessions, {})

    async def test_filter_error_is_isolated_and_filtered_records_never_enter_queue(self):
        self.hub.codec = SimpleNamespace(identity=lambda text: 42,
            api=SimpleNamespace(unpack_envelope=lambda body: (42, body)))
        predicate = Mock()
        predicate.evaluate_wire.side_effect = [SimpleNamespace(status=0, matched=False),
            SimpleNamespace(status=2, matched=False), SimpleNamespace(status=-1, diagnostic="evaluation failed")]
        with patch("ose.recording.taps.prepare_filter", return_value=predicate):
            filtered = await self.open(filters={"source.value": "x == 1"}, capacity=1)
        other = await self.open(capacity=3)
        for sequence in range(3):
            self.offer(sequence=sequence)
        rejected, unfiltered = self.hub.sessions[filtered], self.hub.sessions[other]
        self.assertEqual(rejected["bytes"], 0)
        self.assertEqual(list(rejected["queue"]), [])
        self.assertEqual(rejected["counts"], {"observed": 3, "filtered": 1, "indeterminate": 1, "filter_errors": 1})
        self.assertIn("evaluation failed", rejected["error"])
        self.assertFalse(rejected["active"])
        self.assertTrue(unfiltered["active"])
        self.assertEqual(len(unfiltered["queue"]), 3)
        predicate.close.assert_called_once()
        self.hub.close()
        predicate.close.assert_called_once()

    async def test_source_predicates_are_released_on_stop_close_disconnect_and_shutdown(self):
        self.hub.codec = Mock()
        for operation in ("recording_stop", "recording_close", "disconnect", "shutdown"):
            with self.subTest(operation=operation):
                predicate = Mock()
                with patch("ose.recording.taps.prepare_filter", return_value=predicate):
                    token = await self.open(filters={"source.value": "x == 1"})
                if operation == "disconnect":
                    self.hub.disconnected(self.peer)
                elif operation == "shutdown":
                    self.hub.close()
                else:
                    await self.hub.request(self.peer, dict(op=operation, session=token))
                predicate.close.assert_called_once()
                self.hub.close()
                predicate.close.assert_called_once()
