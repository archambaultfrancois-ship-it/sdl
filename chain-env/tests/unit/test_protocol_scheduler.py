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

    def test_explicit_null_input_and_duplicate_detection(self):
        null = frame("D", port_data("input", b"", ""))
        writer = io.BytesIO()
        session = ComponentSession(io.BytesIO(frame("I", b"{}") + null + frame("T") + frame("X")), writer)
        self.assertEqual(list(session.ticks()), [{"input": None}])
        self.assertEqual(writer.getvalue(), frame("R") + frame("F"))
        for second in (null, frame("D", port_data("input", b"payload", "SDL2|"))):
            session = ComponentSession(io.BytesIO(frame("I", b"{}") + null + second + frame("T")), io.BytesIO())
            with self.assertRaisesRegex(ValueError, "duplicate"):
                list(session.ticks())


class SchedulerTests(unittest.TestCase):
    def timed(self, inputs, trigger=None):
        now = [0.0]
        buffers = {port: Buffer(Policy(), MemoryStore()) for port in inputs}
        scheduler = Scheduler(dict(inputs=inputs, trigger=trigger or {"mode": "on_change"}), buffers, lambda: now[0])
        return scheduler, buffers, now

    def test_all_required_inputs_must_have_a_value_or_expire(self):
        scheduler, buffers, now = self.timed({"a": {"timeout": .2}, "b": {"timeout": .5}})
        self.assertIsNone(scheduler.select())
        self.assertEqual(scheduler.wait_timeout(), .5)
        now[0] = .2
        self.assertIsNone(scheduler.select())
        self.assertAlmostEqual(scheduler.wait_timeout(), .3)
        now[0] = .5
        selected = scheduler.select()
        self.assertEqual(selected, {"a": None, "b": None})
        now[0] = 2.0  # A slow callback does not consume the next wait budget.
        scheduler.complete(selected)
        self.assertIsNone(scheduler.select())
        self.assertAlmostEqual(scheduler.wait_timeout(), .5)
        now[0] = 2.5
        self.assertEqual(scheduler.select(), {"a": None, "b": None})

    def test_data_wins_over_expired_timeout_and_only_real_fifo_records_are_consumed(self):
        scheduler, buffers, now = self.timed({"a": {}, "b": {"timeout": .5}})
        record = lambda n: dict(id=str(n), epoch="e", sequence=n)
        buffers["a"].admit(record(1))
        buffers["a"].admit(record(2))
        now[0] = .5
        selected = scheduler.select()
        self.assertEqual(selected["a"]["id"], "1")
        self.assertIsNone(selected["b"])
        # Arrival during a null tick is kept for the following tick.
        buffers["b"].admit(record(3))
        scheduler.complete(selected)
        selected = scheduler.select()
        self.assertEqual((selected["a"]["id"], selected["b"]["id"]), ("2", "3"))
        scheduler.complete(selected)
        self.assertIsNone(buffers["a"].take())
        self.assertIsNone(buffers["b"].take())

    def test_available_latest_state_never_expires(self):
        scheduler, buffers, now = self.timed({"state": {"timeout": .1}})
        buffers["state"] = Buffer(Policy.resolve({"buffer": "latest"}), MemoryStore())
        buffers["state"].admit(dict(id="1", epoch="e", sequence=1))
        scheduler.complete(scheduler.select())
        now[0] = 10
        self.assertIsNone(scheduler.select())
        self.assertIsNone(scheduler.wait_timeout())
        self.assertEqual(buffers["state"].take()["id"], "1")

    def test_untimed_required_input_still_waits_without_busy_loop(self):
        scheduler, buffers, now = self.timed({"a": {}, "b": {"timeout": .1}})
        now[0] = 10
        self.assertIsNone(scheduler.select())
        self.assertIsNone(scheduler.wait_timeout())
        buffers["a"].admit(dict(id="1", epoch="e", sequence=1))
        self.assertEqual(scheduler.wait_timeout(), 0)
        self.assertIsNone(scheduler.select()["b"])

    def test_periodic_timeout_uses_both_period_and_input_readiness(self):
        scheduler, buffers, now = self.timed({"input": {"timeout": .5}}, {"mode": "periodic", "period_ms": 1000})
        self.assertEqual(scheduler.wait_timeout(), .5)
        now[0] = .5
        scheduler.complete(scheduler.select())
        now[0] = 1
        self.assertIsNone(scheduler.select())
        self.assertEqual(scheduler.wait_timeout(), .5)
        now[0] = 1.5
        self.assertEqual(scheduler.select(), {"input": None})

    def test_start_rearms_after_component_initialization(self):
        scheduler, buffers, now = self.timed({"input": {"timeout": .5}, "optional": {"required": False}})
        now[0] = 100
        scheduler.start()
        self.assertIsNone(scheduler.select())
        self.assertEqual(scheduler.wait_timeout(), .5)
        now[0] = 100.5
        self.assertEqual(scheduler.select(), {"input": None})

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
