import unittest
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
