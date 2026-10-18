"""Live rates, evidence-based alerts, colors and terminal lifecycle."""
import asyncio
import io
import json
import os
import pty
import re
import termios
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.execution.observation import TickTimings, describe
from ose.execution.scheduler import Scheduler
from ose.top import Keyboard, Observer, color_enabled, render, run
from ose.transport.secure import RPCServer


def status():
    policy = dict(buffer="fifo", capacity=10, overflow="reject_new")
    return {"epoch": "epoch", "enabled": True,
            "observation": {"version": 1, "components": {
                "sink": {"trigger": {"mode": "on_change"}, "inputs": {"input": {}},
                         "connected": True, "ready": True, "generation": "generation", "report_age_ms": 0}},
                "subscriptions": {"link": {"from": "source.output", "to": "sink.input", "policy": policy}}},
            "components": {"sink": {"running": True, "ticks": 0, "publications": 0,
                "tick_p95_ms": 2, "inputs": {"input": {"accepted": 0, "depth": 0, "age_ms": 0}}}}}


def options(**values):
    defaults = dict(endpoint=None, host="127.0.0.1", port=7000, distributed=True, insecure=True,
                    tls_ca=None, tls_cert=None, tls_key=None, interval=.1, window=5, count=2,
                    format="jsonl", color="always")
    return SimpleNamespace(**(defaults | values))


class ObserverTests(unittest.TestCase):
    def test_recent_rates_and_first_sample_unknown(self):
        observer, data = Observer(), status()
        first = observer.sample(data, now=0)["components"][0]
        self.assertIsNone(first["rates"]["in"])
        data["components"]["sink"]["inputs"]["input"]["accepted"] = 20
        data["components"]["sink"].update(ticks=10, publications=5)
        row = observer.sample(data, now=2)["components"][0]
        self.assertEqual(row["rates"], {"in": 10, "out": 2.5, "ticks": 5})
        self.assertEqual(row["state"], "ACTIVE")
        for now in (4, 6, 8, 10):
            row = observer.sample(data, now=now)["components"][0]
        self.assertEqual(row["rates"]["in"], 0)
        self.assertEqual(row["state"], "IDLE")

    def test_restarts_and_counter_resets_do_not_make_false_rates(self):
        for change in ("epoch", "generation", "counter"):
            observer, data = Observer(), status()
            data["components"]["sink"]["ticks"] = 100
            observer.sample(data, now=0)
            if change == "epoch":
                data["epoch"] = "new"
            elif change == "generation":
                data["observation"]["components"]["sink"]["generation"] = "new"
            data["components"]["sink"]["ticks"] = 1
            row = observer.sample(data, now=1)["components"][0]
            self.assertIsNone(row["rates"]["ticks"], change)

    def test_waiting_is_distinct_from_saturation_and_loss(self):
        observer, data = Observer(), status()
        data["components"]["sink"]["waiting_inputs"] = ["input"]
        self.assertEqual(observer.sample(data, now=0)["components"][0]["state"], "WAIT INPUT")
        data["components"]["sink"]["inputs"]["input"]["depth"] = 8
        row = observer.sample(data, now=1)["components"][0]
        self.assertEqual(row["severity"], "warning")
        self.assertIn("8/10", row["alerts"][0]["message"])
        data["components"]["sink"]["inputs"]["input"]["dropped_oldest"] = 3
        row = observer.sample(data, now=2)["components"][0]
        self.assertEqual(row["state"], "LOSS/ERROR")
        self.assertIn("3 dropped", row["alerts"][0]["message"])

    def test_latest_retention_is_neither_saturation_nor_loss(self):
        observer, data = Observer(), status()
        data["observation"]["subscriptions"]["link"]["policy"].update(buffer="latest", capacity=1, overflow="drop_oldest")
        data["components"]["sink"]["inputs"]["input"]["depth"] = 1
        observer.sample(data, now=0)
        data["components"]["sink"]["inputs"]["input"]["replaced"] = 99
        self.assertEqual(observer.sample(data, now=1)["components"][0]["alerts"], [])

    def test_refusal_attempts_and_retries_are_amber(self):
        observer, data = Observer(), status()
        observer.sample(data, now=0)
        data["components"]["sink"]["inputs"]["input"].update(refused=3, retries=2)
        row = observer.sample(data, now=1)["components"][0]
        self.assertEqual(row["severity"], "warning")
        self.assertTrue(any("attempts" in alert["message"] for alert in row["alerts"]))

    def test_stale_missing_and_starting_components(self):
        observer, data = Observer(), status()
        observer.sample(data, now=0)
        data["observation"]["components"]["sink"]["report_age_ms"] = 4000
        row = observer.sample(data, now=1)["components"][0]
        self.assertEqual(row["state"], "STALE")
        self.assertIsNone(row["rates"]["in"])
        self.assertIsNone(row["tick_p95_ms"])
        data["observation"]["components"]["sink"]["connected"] = False
        self.assertEqual(observer.sample(data, now=2)["components"][0]["state"], "UNAVAILABLE")
        data["enabled"] = False
        self.assertEqual(observer.sample(data, now=3)["components"][0]["state"], "STARTING")

    def test_distributed_metrics_and_outbound_direct_failures(self):
        observer, data = Observer(), status()
        data["observation"]["subscriptions"]["out"] = dict(
            data["observation"]["subscriptions"]["link"], **{"from": "sink.output", "to": "target.input", "routing": "direct"})
        data["components"]["sink"] = {"metrics": data["components"]["sink"]}
        metrics = data["components"]["sink"]["metrics"]
        metrics["direct"] = {"out": {"failed": 0, "depth": 0}}
        observer.sample(data, now=0)
        metrics["direct"]["out"]["failed"] = 1
        self.assertEqual(observer.sample(data, now=1)["components"][0]["severity"], "error")

    def test_period_overrun_and_connection_failure(self):
        observer, data = Observer(), status()
        data["observation"]["components"]["sink"]["trigger"] = dict(mode="periodic", period_ms=1)
        row = observer.sample(data, now=0)["components"][0]
        self.assertIn("exceeds period", row["alerts"][0]["message"])
        row = observer.sample(data, now=1, error="disconnected")["components"][0]
        self.assertEqual(row["state"], "UNAVAILABLE")
        self.assertIsNone(row["rates"]["ticks"])

    def test_color_detection_and_plain_text_states(self):
        output = io.StringIO()
        self.assertFalse(color_enabled("auto", output, {}))
        with patch.object(output, "isatty", return_value=True):
            self.assertTrue(color_enabled("auto", output, {}))
            self.assertFalse(color_enabled("auto", output, {"NO_COLOR": ""}))
            self.assertFalse(color_enabled("auto", output, {"TERM": "dumb"}))
            self.assertFalse(color_enabled("never", output, {}))
        self.assertTrue(color_enabled("always", output, {"NO_COLOR": ""}))
        report = Observer().sample(status(), now=0)
        self.assertNotIn("\033", render(report, color=False))
        colored = render(report, color=True)
        self.assertIn("\033[2m", colored)
        self.assertIn("IDLE", colored)
        for line in render(report, color=True, width=40).splitlines():
            self.assertLessEqual(len(re.sub(r"\x1b\[[0-9;]*m", "", line)), 40)

    def test_remote_names_and_errors_cannot_inject_terminal_controls(self):
        data = status()
        desc = data["observation"]["components"].pop("sink")
        data["observation"]["components"]["bad\033[2J"] = desc
        text = render(Observer().sample(data, error="bad\033[2J"), color=False)
        self.assertNotIn("\033", text)

    def test_observation_metadata_omits_secrets_and_commands(self):
        plan = {"components": {"a": dict(inputs={"x": dict(root="Value", catalogue="secret", timeout=1)},
                    outputs={}, trigger={"mode": "on_change"}, command=["secret"], env={"SECRET": "secret"})}, "subscriptions": []}
        self.assertNotIn("secret", json.dumps(describe(plan)))


class AsyncTopTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_details_freeze_and_quit_keep_collecting(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r")
        self.addCleanup(stream.close)
        original = termios.tcgetattr(slave)
        output, calls = io.StringIO(), [0]
        class FakeClient:
            async def request(self, *args, **kwargs):
                calls[0] += 1
                data = status()
                data["components"]["sink"]["ticks"] = calls[0]
                return data, b""
            async def close(self):
                pass
        async def connection(*args, **kwargs):
            return FakeClient()
        async def until(predicate):
            async def check():
                while not predicate():
                    await asyncio.sleep(.005)
            await asyncio.wait_for(check(), 2)
        with patch.dict(os.environ, TERM="xterm"), patch.object(output, "isatty", return_value=True), \
                patch("ose.top.connect", side_effect=connection):
            task = asyncio.create_task(run(options(format="text", count=None), output=output, stdin=stream))
            try:
                await until(lambda: calls[0] >= 1)
                os.write(master, b"\n ")
                await until(lambda: "FROZEN" in output.getvalue())
                self.assertIn("Details: sink", output.getvalue())
                frozen_at = calls[0]
                await until(lambda: calls[0] >= frozen_at + 2)
                os.write(master, b" q")
                self.assertEqual(await asyncio.wait_for(task, 2), 0)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(termios.tcgetattr(slave), original)
        self.assertTrue(output.getvalue().endswith("\033[0m\033[?25h\033[?1049l"))

    async def test_cancellation_restores_terminal_and_closes_connection(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r")
        self.addCleanup(stream.close)
        original = termios.tcgetattr(slave)
        output, entered, closed = io.StringIO(), asyncio.Event(), asyncio.Event()
        class FakeClient:
            async def request(self, *args, **kwargs):
                entered.set()
                await asyncio.Future()
            async def close(self):
                closed.set()
        async def connection(*args, **kwargs):
            return FakeClient()
        with patch.dict(os.environ, TERM="xterm"), patch.object(output, "isatty", return_value=True), \
                patch("ose.top.connect", side_effect=connection):
            task = asyncio.create_task(run(options(format="text", count=None), output=output, stdin=stream))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(closed.is_set())
        self.assertEqual(termios.tcgetattr(slave), original)
        self.assertIn("\033[?1049l", output.getvalue())

    async def test_tick_timings_exclude_publication_wait_and_expire(self):
        now = [0.0]
        timer = TickTimings(lambda: now[0])
        scheduler = Scheduler(dict(inputs={}, trigger={"mode": "periodic", "period_ms": 10}), {}, lambda: now[0])
        async def tick():
            now[0] += .002
            yield "output", b"value"
            now[0] += .003
        async for _ in timer.events(tick()):
            now[0] += 10  # Backpressure in the publisher must not enter tick p95.
        snapshot = timer.snapshot(scheduler)
        self.assertAlmostEqual(snapshot["tick_p95_ms"], 5)
        self.assertEqual(snapshot["tick_samples"], 1)
        self.assertIsNone(snapshot["busy_ms"])
        now[0] += 6
        self.assertIsNone(timer.snapshot(scheduler)["tick_p95_ms"])

    async def test_keyboard_restores_terminal_and_handles_split_arrow(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        stream = os.fdopen(slave, "r")
        self.addCleanup(stream.close)
        original = termios.tcgetattr(slave)
        with self.assertRaisesRegex(RuntimeError, "stop"):
            with Keyboard(stream) as keyboard:
                os.write(master, b"\033[")
                await asyncio.sleep(.01)
                os.write(master, b"B ")
                self.assertEqual(await asyncio.wait_for(keyboard.queue.get(), 1), b"\033[B")
                self.assertEqual(await asyncio.wait_for(keyboard.queue.get(), 1), b" ")
                raise RuntimeError("stop")
        self.assertEqual(termios.tcgetattr(slave), original)

    async def test_jsonl_uses_only_status_and_is_never_colored(self):
        operations = []
        async def handler(peer, header, body):
            operations.append(header["op"])
            return status(), None
        server = RPCServer(1024 * 1024, handler)
        port = await server.start("127.0.0.1", 0)
        self.addAsyncCleanup(server.close)
        output = io.StringIO()
        result = await run(options(port=port), output=output, stdin=io.StringIO("q\n"))
        self.assertEqual(result, 0)
        self.assertEqual(operations, ["status", "status"])
        self.assertNotIn("\033", output.getvalue())
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]["components"][0]["name"], "sink")

    async def test_finite_plain_report_stays_visible_on_a_terminal(self):
        async def handler(peer, header, body):
            return status(), None
        server = RPCServer(1024 * 1024, handler)
        port = await server.start("127.0.0.1", 0)
        self.addAsyncCleanup(server.close)
        output = io.StringIO()
        with patch.dict(os.environ, TERM="xterm"), patch.object(output, "isatty", return_value=True):
            self.assertEqual(await run(options(port=port, format="text", color="never", count=1),
                                       output=output, stdin=io.StringIO()), 0)
        self.assertNotIn("\033", output.getvalue())
        self.assertIn("COMPONENT", output.getvalue())
        self.assertIn("Details: sink", output.getvalue())

    async def test_disconnection_is_reported_and_reconnected(self):
        calls = [0]
        class FakeClient:
            async def request(self, *args, **kwargs):
                calls[0] += 1
                if calls[0] == 2:
                    raise ConnectionError("lost connection")
                return status(), b""
            async def close(self):
                pass
        async def connection(*args, **kwargs):
            return FakeClient()
        output = io.StringIO()
        with patch("ose.top.connect", side_effect=connection) as connect_mock:
            self.assertEqual(await run(options(count=3), output=output), 0)
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(reports[1]["components"][0]["state"], "UNAVAILABLE")
        self.assertIsNone(reports[2]["components"][0]["rates"]["in"])
        self.assertEqual(connect_mock.call_count, 2)

    async def test_invalid_options_fail_before_connecting(self):
        for values in (dict(interval=0), dict(interval=float("nan")), dict(window=0), dict(count=0),
                       dict(endpoint="endpoint.json"), dict(insecure=False)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                await run(options(**values), output=io.StringIO())
