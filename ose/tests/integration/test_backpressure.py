"""Regression coverage for control/data isolation, admission and shutdown."""
import asyncio
import copy
import os
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.transport.tcp import Client

SDL_ROOT = os.environ.get("SDL_ROOT")
CATALOGUE = "SDL2|Measurement{1:uint64:sequence;2:fl64:value;}"


async def eventually(predicate, timeout=3):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(.005)


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class BackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.codec = SDL2(SDL_ROOT)
        self.tasks, self.adapters, self.brokers = [], [], []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for broker in self.brokers:
            await broker.close()
        for adapter in self.adapters:
            for buffer in adapter.buffers.values():
                buffer.store.close()
        self.directory.cleanup()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def plan(self, policies, catalogues=None):
        catalogues = catalogues or {port: CATALOGUE for port in policies}
        ports = {port: {"root": "Measurement", "catalogue": catalogues[port],
                       "catalogue_id": self.codec.identity(catalogues[port])} for port in policies}
        return {"version": 1, "sdl_root": str(Path(SDL_ROOT).resolve()), "max_frame": 1024 * 1024,
                "max_datagram": 1200, "components": {
                    "source": {"inputs": {}, "outputs": ports},
                    "sink": {"inputs": copy.deepcopy(ports), "outputs": {},
                             "trigger": {"mode": "on_change"}, "env": {},
                             "cwd": self.directory.name, "command": [sys.executable, "-c",
                                 "from ose.execution.component import ComponentSession\n"
                                 "s=ComponentSession()\nfor inputs in s.ticks(): pass\n"]}},
                "subscriptions": [{"id": port, "from": f"source.{port}", "to": f"sink.{port}",
                                   "policy": policy.to_dict()} for port, policy in policies.items()]}

    async def test_unsubscribe_stops_even_when_a_wakeup_absorbs_cancellation(self):
        broker = Broker(self.plan({"input": Policy()}))
        engine = broker.engines["input"]
        entered = asyncio.Event()

        class RacingEvent:
            swallowed = False
            def clear(self): pass
            def set(self): pass
            async def wait(self):
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    if self.swallowed:
                        raise
                    self.swallowed = True
                    # Model the completed-wakeup cancellation race in wait_for.
                    return

        engine.store.changed = RacingEvent()
        task = self.spawn(broker._send_loop("input", engine))
        broker.external_tasks["input"] = task
        session = SimpleNamespace(subscriptions={"input"})
        await entered.wait()
        await asyncio.wait_for(broker.remove_subscription(session, "input"), .5)
        self.assertTrue(task.done())
        self.assertNotIn("input", broker.engines)
        self.assertEqual(session.subscriptions, set())

    def adapter(self, plan):
        adapter = Adapter(plan, "sink")
        self.adapters.append(adapter)
        return adapter

    async def broker(self, plan):
        broker = Broker(plan)
        await broker.start()
        self.brokers.append(broker)
        return broker

    def record(self, number):
        envelope = self.codec.wrap(CATALOGUE, b"\x01" + struct.pack(">Qd", number, float(number)))
        return {"id": f"e:{number}", "epoch": "e", "sequence": number,
                "body": envelope, "payload": envelope[9:], "catalogue": CATALOGUE}

    def packet(self, port, number):
        record = self.record(number)
        return {"op": "data", "subscription": port, "topic": f"source.{port}",
                "id": record["id"], "epoch": "e", "sequence": number}, record["body"]

    async def test_control_reply_survives_1100_message_tcp_burst(self):
        plan = self.plan({"input": Policy(capacity=2048)})
        adapter, broker = self.adapter(plan), await self.broker(plan)
        running = self.spawn(adapter.run("127.0.0.1", broker.port))
        await eventually(lambda: "sink" in broker.ready)
        peer = broker.peers["sink"][0]
        original_send = peer.send
        catalogue_requested, release_catalogue = asyncio.Event(), asyncio.Event()
        async def hold_catalogue(header, payload=b""):
            if payload == CATALOGUE.encode():
                catalogue_requested.set()
                await release_catalogue.wait()
            await original_send(header, payload)
        peer.send = hold_catalogue
        try:
            # Prepare the burst before delivery starts so building it cannot
            # consume the held catalogue request's control timeout.
            for number in range(1100):
                await broker.publish("source.input", self.record(number)["body"])
            broker.enabled = True
            await asyncio.wait_for(catalogue_requested.wait(), 3)
            await eventually(lambda: adapter.network_queues["input"].full(), timeout=10)
            self.assertTrue(adapter.client.pending)
            release_catalogue.set()
            await eventually(lambda: adapter.buffers["input"].metrics["accepted"] == 1100, timeout=30)
            self.assertFalse(running.done(), "adapter stopped during control reply starvation")
            status, _ = await asyncio.wait_for(adapter.client.request("status"), 1)
            self.assertTrue(status["enabled"])
        finally:
            release_catalogue.set()

    async def test_shutdown_kills_component_that_never_reads_stdin(self):
        plan = self.plan({})
        plan["components"]["sink"]["trigger"] = {"mode": "periodic", "period_ms": 10}
        plan["components"]["sink"]["command"] = [sys.executable, "-c",
            "import signal,sys,time\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            "sys.stdout.buffer.write(b'\\x00\\x00\\x00\\x01R');sys.stdout.buffer.flush()\n"
            "while True: time.sleep(10)\n"]
        adapter, broker = self.adapter(plan), await self.broker(plan)
        blocked = asyncio.Event()
        async def fill_pipe():
            from ose.execution import ipc
            blocked.set()
            await ipc.send(adapter.process.stdin, "D", b"x" * 900000)
        adapter._work = fill_pipe
        running = self.spawn(adapter.run("127.0.0.1", broker.port))
        try:
            await eventually(lambda: "sink" in broker.ready)
            broker.enabled = True
            await asyncio.wait_for(blocked.wait(), 2)
            await eventually(lambda: adapter.process.stdin.transport.get_write_buffer_size() > 65536)
            running.cancel()
            done, _ = await asyncio.wait({running}, timeout=2.5)
            self.assertIn(running, done, "STOP write exceeded the cleanup deadline")
            self.assertIsNotNone(adapter.process.returncode, "component survived adapter shutdown")
            with self.assertRaises(ProcessLookupError):
                os.kill(adapter.process.pid, 0)
        finally:
            # Also clean up when running this regression against the broken version.
            if hasattr(adapter, "process") and adapter.process.returncode is None:
                adapter.process.kill()
                adapter.process.stdin.transport.abort()
                await adapter.process.wait()

    async def test_entire_ram_snapshot_reserved_before_first_pipe_write(self):
        policy = Policy(capacity=1, overflow="drop_oldest")
        adapter = self.adapter(self.plan({"left": policy, "right": policy}))
        for buffer in adapter.buffers.values():
            buffer.admit(self.record(1))
        entered, release = asyncio.Event(), asyncio.Event()
        adapter.process = SimpleNamespace(stdin=object())
        async def blocked_send(writer, op, data=b""):
            if op == "D":
                entered.set()
                await release.wait()
        with patch("ose.execution.adapter.ipc.send", blocked_send):
            working = self.spawn(adapter._work())
            await asyncio.wait_for(entered.wait(), 1)
            right = adapter.buffers["right"]
            self.assertEqual(right.admit(self.record(2)), "full")
            working.cancel()
            await asyncio.gather(working, return_exceptions=True)
        self.assertEqual(right.take()["id"], "e:1")

    async def test_catalogue_cache_does_not_skip_second_input_contract(self):
        strict = "SDL2|Measurement{1:uint64:sequence;2:fl64:value;3:int32:required_extra;}"
        adapter = self.adapter(self.plan({"left": Policy(), "right": Policy()}, {"left": CATALOGUE, "right": strict}))
        async def request(op, **arguments): return {}, CATALOGUE.encode()
        adapter.client = SimpleNamespace(request=request)
        self.spawn(adapter._receiver("left"))
        self.spawn(adapter._receiver("right"))
        await adapter._tcp_data(*self.packet("left", 1))
        await eventually(lambda: adapter.buffers["left"].metrics["accepted"] == 1)
        await adapter._tcp_data(*self.packet("right", 2))
        await eventually(lambda: adapter.metrics.get("invalid", 0) == 1)
        self.assertIsNone(adapter.buffers["right"].take())
        self.assertNotIn((self.codec.identity(CATALOGUE), "right"), adapter.compatibility)

    async def test_full_input_does_not_block_other_input_admission(self):
        adapter = self.adapter(self.plan({"slow": Policy(capacity=1, overflow="wait", wait_timeout_ms=1000), "fast": Policy()}))
        adapter.buffers["slow"].admit(self.record(0))
        async def request(op, **arguments): return {}, CATALOGUE.encode()
        adapter.client = SimpleNamespace(request=request)
        self.spawn(adapter._receiver("slow")); self.spawn(adapter._receiver("fast"))
        await adapter._tcp_data(*self.packet("slow", 1))
        await eventually(lambda: adapter.buffers["slow"].metrics["refused"] > 0)
        await adapter._tcp_data(*self.packet("fast", 2))
        await eventually(lambda: adapter.buffers["fast"].metrics["accepted"] == 1, timeout=.3)
        self.assertEqual(adapter.buffers["slow"].take()["id"], "e:0")

    async def test_slow_sender_does_not_delay_fast_sender_or_retry(self):
        plan = self.plan({"slow": Policy(capacity=100), "fast": Policy.resolve({"ack": "received", "retry": {"ack_timeout_ms": 20}})})
        broker = await self.broker(plan)
        slow_entered, release = asyncio.Event(), asyncio.Event()
        sent = []
        class Peer:
            async def send(self, header, payload=b""):
                if header["subscription"] == "slow":
                    slow_entered.set()
                    # Hold the whole slow flush even when its send timeout cancels this write.
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        await release.wait()
                else:
                    sent.append(header)
        broker.peers["sink"] = (Peer(), ("127.0.0.1", 1))
        try:
            broker.engines["slow"].enqueue(dict(self.record(1), topic="source.slow"))
            broker.engines["fast"].enqueue(dict(self.record(1), topic="source.fast"))
            broker.enabled = True
            await asyncio.wait_for(slow_entered.wait(), 1)
            await eventually(lambda: len(sent) >= 1)
            broker.engines["fast"].enqueue(dict(self.record(2), topic="source.fast"))
            await eventually(lambda: any(h["id"] == "e:2" for h in sent) and sum(h["id"] == "e:1" for h in sent) >= 2)
            self.assertFalse(release.is_set())
            self.assertEqual(broker.engines["slow"].metrics["sent"], 0)
        finally:
            release.set()
