"""Integer virtual deadlines and timestamped CH/1 contexts."""
import io
import struct
import unittest
from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.component import ComponentSession
from ose.execution.ipc import frame, tick_context, TICK_CONTEXT
from ose.execution.simulation.clock import seconds_ns, VirtualClock, Schedule


class SimulationClockTests(unittest.TestCase):
    def test_decimal_rounding_range_and_invalid_times(self):
        self.assertEqual(seconds_ns(.1), 100_000_000)
        self.assertEqual(seconds_ns(.0000000005), 1)
        self.assertEqual(seconds_ns(1e-15, positive=True), 1)
        self.assertEqual(seconds_ns(0), 0)
        for value in (True, None, "1", -1, float("nan"), float("inf"), 10 ** 1000, 1e30):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                seconds_ns(value)
        with self.assertRaises(ValueError):
            seconds_ns(0, positive=True)

    def test_timestamped_session_and_legacy_ticks(self):
        data = frame("I", b"{}") + frame("T", struct.pack(">QQ", 123456789, 4)) + frame("T") + frame("X")
        writer = io.BytesIO()
        session = ComponentSession(io.BytesIO(data), writer, timed_ticks=True)
        seen = []
        for inputs in session.ticks():
            seen.append((inputs, session.time_ns, session.tick_index))
        self.assertEqual(seen, [({}, 123456789, 4), ({}, 0, 5)])
        self.assertEqual(writer.getvalue(), frame("R", TICK_CONTEXT) + frame("F") * 2)
        for malformed in (b"x", b"x" * 15, b"x" * 17, struct.pack(">QQ", 1, 0)):
            with self.assertRaises(ValueError):
                tick_context(malformed)

    def test_exact_periods_and_latest_state_quiescence(self):
        clock = VirtualClock()
        node = dict(inputs={}, outputs={}, trigger=dict(mode="periodic", period_ms=100))
        schedule = Schedule(node, {}, clock)
        for index in range(1, 1001):
            self.assertEqual(schedule.select(), {})
            schedule.tick_index += 1
            schedule.complete({})
            self.assertEqual(schedule.next_event(), index * 100_000_000)
            clock.time_ns = schedule.next_event()
        buffer = Buffer(SubscriptionPolicy.resolve(dict(buffer="latest")), MemoryStore(1), clock.seconds)
        node = dict(inputs={"state": dict(timeout=.1)}, trigger=dict(mode="on_change"))
        schedule = Schedule(node, {"state": buffer}, clock)
        buffer.admit(dict(id="1", epoch="sim", sequence=1))
        schedule.tick_index += 1
        schedule.complete(schedule.select())
        clock.time_ns += 1_000_000_000
        self.assertIsNone(schedule.select())
        self.assertIsNone(schedule.next_event())

    def test_joint_timeouts_and_untimed_input_do_not_spin(self):
        clock = VirtualClock()
        buffers = {port: Buffer(SubscriptionPolicy(), MemoryStore(), clock.seconds) for port in ("a", "b")}
        node = dict(inputs={"a": dict(timeout=.1), "b": dict(timeout=.2)}, trigger=dict(mode="on_change"))
        schedule = Schedule(node, buffers, clock)
        self.assertEqual(schedule.next_event(), 200_000_000)
        clock.time_ns = 100_000_000
        self.assertIsNone(schedule.select())
        self.assertEqual(schedule.next_event(), 200_000_000)
        clock.time_ns = 200_000_000
        self.assertEqual(schedule.select(), {"a": None, "b": None})
        node["inputs"]["b"] = {}
        schedule = Schedule(node, buffers, clock)
        clock.time_ns += 1_000_000_000
        self.assertIsNone(schedule.select())
        self.assertIsNone(schedule.next_event())
