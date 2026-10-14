import io
import unittest
from chain_env.delivery.buffers import Buffer
from chain_env.delivery.policies import SubscriptionPolicy as Policy
from chain_env.delivery.stores import MemoryStore
from chain_env.execution.scheduler import Scheduler
from chain_env.execution.component import ComponentSession
from chain_env.execution.ipc import frame, port_data, unpack_port
from chain_env.transport.protocol import encode, decode


class ProtocolTests(unittest.TestCase):
    def test_binary_round_trip_and_limits(self):
        packet = encode({"op": "data", "id": "x"}, b"\0\xff")
        self.assertEqual(decode(packet), ({"op": "data", "id": "x"}, b"\0\xff"))
        for invalid in (b"", b"CE\x01\xff\xff\xff\xff", packet[:8]):
            with self.assertRaises((ValueError, UnicodeError)):
                decode(invalid)
        with self.assertRaises(ValueError):
            encode({"op": "data"}, b"x" * 1200, 1200)

    def test_sdk_lifecycle(self):
        reader = io.BytesIO(frame("I", b'{"parameters":{"gain":2}}') + frame("D", port_data("input", b"wire", "SDL2|")) + frame("T") + frame("X"))
        writer = io.BytesIO()
        session = ComponentSession(reader, writer)
        self.assertEqual(session.config["parameters"]["gain"], 2)
        self.assertEqual(list(session.ticks()), [{"input": ("SDL2|", b"wire")}])
        self.assertEqual(writer.getvalue(), frame("R") + frame("F"))
        with self.assertRaises(ValueError):
            unpack_port(b"\0\x04x", True)

    def test_ready_can_be_deferred_until_user_initialization_succeeds(self):
        writer = io.BytesIO()
        session = ComponentSession(io.BytesIO(frame("I", b"{}")), writer, auto_ready=False)
        self.assertEqual(writer.getvalue(), b"")
        session.ready()
        session.ready()
        self.assertEqual(writer.getvalue(), frame("R"))


class SchedulerTests(unittest.TestCase):
    def test_coalesced_latest_and_required_fifo(self):
        latest = Buffer(Policy.resolve({"buffer": "latest"}), MemoryStore())
        fifo = Buffer(Policy(), MemoryStore())
        node = {"inputs": {"state": {}, "event": {}}, "trigger": {"mode": "on_change"}}
        scheduler = Scheduler(node, {"state": latest, "event": fifo})
        rec = lambda n: {"id": str(n), "epoch": "e", "sequence": n}
        latest.admit(rec(1))
        self.assertIsNone(scheduler.select())
        fifo.admit(rec(1))
        selected = scheduler.select()
        latest.admit(rec(2)); latest.admit(rec(3))
        scheduler.complete(selected)
        self.assertIsNone(scheduler.select())
        fifo.admit(rec(2))
        self.assertEqual(scheduler.select()["state"]["sequence"], 3)

    def test_periodic_skips_missed_deadlines(self):
        now = [0.0]
        node = {"inputs": {}, "trigger": {"mode": "periodic", "period_ms": 10}}
        scheduler = Scheduler(node, {}, lambda: now[0])
        self.assertEqual(scheduler.select(), {})
        now[0] = 1
        scheduler.complete({})
        self.assertIsNone(scheduler.select())
        now[0] = 1.011
        self.assertEqual(scheduler.select(), {})

    def test_periodic_waits_for_input_before_arming_expired_timer(self):
        buffer = Buffer(Policy(), MemoryStore())
        node = {"inputs": {"input": {}}, "trigger": {"mode": "periodic", "period_ms": 10}}
        scheduler = Scheduler(node, {"input": buffer}, lambda: 1)
        self.assertIsNone(scheduler.wait_timeout())
        buffer.admit({"id": "1", "epoch": "e", "sequence": 1})
        self.assertEqual(scheduler.wait_timeout(), 0)
