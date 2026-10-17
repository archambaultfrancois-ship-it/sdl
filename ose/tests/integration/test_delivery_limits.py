"""Delivery regressions for blocked TCP, sizing and time budgets."""
import asyncio
import copy
import json
import os
from pathlib import Path
import struct
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.execution.adapter import Adapter
from ose.execution.broker import Broker
from ose.transport.protocol import encode
from ose.transport.tcp import Client, TCP

SDL_ROOT = os.environ.get("SDL_ROOT")
CATALOGUE = "SDL2|Measurement{1:uint64:sequence;2:fl64:value;}"


async def eventually(predicate, timeout=3):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(.005)


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class DeliveryLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.codec = SDL2(SDL_ROOT)
        self.clients, self.tasks, self.brokers = [], [], []
        port = {"root": "Measurement", "catalogue": CATALOGUE, "catalogue_id": self.codec.identity(CATALOGUE)}
        self.plan = {"version": 1, "sdl_root": str(Path(SDL_ROOT).resolve()), "max_frame": 1024 * 1024,
            "max_datagram": 1200, "components": {
                "source": {"inputs": {}, "outputs": {"output": port}, "env": {}, "cwd": self.directory.name,
                    "trigger": {"mode": "periodic", "period_ms": 10},
                    "command": [sys.executable, "-c", "import struct\nfrom ose.execution.component import ComponentSession\n"
                        "s=ComponentSession()\nfor inputs in s.ticks(): s.publish('output',b'\\x01'+struct.pack('>Qd',1,1.0))\n"]},
                "sink": {"inputs": {"input": copy.deepcopy(port)}, "outputs": {}}},
            "subscriptions": [{"id": "link", "from": "source.output", "to": "sink.input", "policy": Policy().to_dict()}]}

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for client in self.clients:
            await client.close()
        for broker in self.brokers:
            await broker.close()
        self.directory.cleanup()

    def envelope(self, number):
        return self.codec.wrap(CATALOGUE, b"\x01" + struct.pack(">Qd", number, float(number)))

    async def start(self):
        broker = Broker(self.plan)
        await broker.start()
        self.brokers.append(broker)
        return broker

    async def client(self, broker, **options):
        client = await Client.connect("127.0.0.1", broker.port, **options)
        self.clients.append(client)
        return client

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def test_blocked_tcp_drain_never_rewrites_pending_frame(self):
        broker = await self.start()
        entered, release = asyncio.Event(), asyncio.Event()
        class Writer:
            def __init__(self): self.writes = []
            def write(self, packet): self.writes.append(packet)
            async def drain(self):
                entered.set()
                await release.wait()
        writer = Writer()
        broker.peers["sink"] = (TCP(None, writer), ("127.0.0.1", 1))
        broker.enabled = True
        await broker.publish("source.output", self.envelope(1))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(.35)  # More than three of the former 100 ms drain timeouts.
            self.assertEqual(len(writer.writes), 1)
            self.assertEqual(broker.engines["link"].metrics["sent"], 0)
            self.assertEqual(broker.engines["link"].store.size(), 1)
            release.set()
            await eventually(lambda: broker.engines["link"].store.size() == 0)
            self.assertEqual(len(writer.writes), 1)
            self.assertEqual(broker.engines["link"].metrics["retries"], 0)
        finally:
            release.set()

    async def test_real_nonreading_tcp_peer_keeps_transport_buffer_bounded(self):
        text = "SDL2|Large{1:uint8[200000]:data;}"
        port = {"root": "Large", "catalogue": text, "catalogue_id": self.codec.identity(text)}
        self.plan["components"]["source"]["outputs"]["output"] = port
        self.plan["components"]["sink"]["inputs"]["input"] = copy.deepcopy(port)
        broker = await self.start()
        entered, release = asyncio.Event(), asyncio.Event()
        async def do_not_read(header, body):
            entered.set()
            await release.wait()
        client = await self.client(broker, on_data=do_not_read)
        await client.request("hello", node="sink", udp_port=1)
        peer = broker.peers["sink"][0]
        peer.writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        envelope = self.codec.wrap(text, b"\x01" + b"x" * 200000)
        self.codec.validate(text, "Large", envelope)
        for number in range(10):
            broker.engines["link"].enqueue({"id": f"{broker.epoch}:{number}", "epoch": broker.epoch,
                "sequence": number, "topic": "source.output", "body": envelope})
        broker.enabled = True
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await eventually(lambda: peer.writer.transport.get_write_buffer_size() > 65536)
            await asyncio.sleep(.4)
            _, high_water = peer.writer.transport.get_write_buffer_limits()
            self.assertLessEqual(peer.writer.transport.get_write_buffer_size(), len(envelope) + high_water + 1024)
            self.assertEqual(broker.engines["link"].metrics["retries"], 0)
        finally:
            release.set()

    async def test_tcp_publish_fits_but_oversize_data_is_refused_before_admission(self):
        self.plan["max_frame"] = 256
        text = "SDL2|Large{1:fl64[12]:values;}"
        port = {"root": "Large", "catalogue": text, "catalogue_id": self.codec.identity(text)}
        self.plan["components"]["source"]["outputs"]["output"] = port
        self.plan["components"]["sink"]["inputs"]["input"] = copy.deepcopy(port)
        self.plan["components"]["sink"]["inputs"]["state"] = copy.deepcopy(port)
        self.plan["subscriptions"].append({"id": "last", "from": "source.output", "to": "sink.state",
                                           "policy": Policy.resolve({"buffer": "latest"}).to_dict()})
        broker = await self.start()
        broker.enabled = True
        client = await self.client(broker, limit=256)
        await client.request("hello", node="source", udp_port=1)
        envelope = self.codec.wrap(text, b"\x01" + struct.pack(">12d", *range(12)))
        encode({"op": "publish", "request": 2, "topic": "source.output"}, envelope, 256)
        response, _ = await client.request("publish", envelope, topic="source.output")
        self.assertEqual(response["delivery"], {"link": "oversize", "last": "oversize"})
        self.assertEqual(broker.engines["link"].store.size(), 0)
        self.assertEqual(broker.engines["link"].buffer.metrics["accepted"], 0)
        late = await self.client(broker, limit=256)
        await late.request("hello", node="sink", udp_port=1)
        self.assertEqual(broker.engines["last"].store.size(), 0)
        self.assertEqual(broker.engines["last"].buffer.metrics["accepted"], 0)

    async def test_twenty_second_admission_wait_does_not_stop_adapter(self):
        policy = Policy(capacity=1, overflow="wait", wait_timeout_ms=20000)
        self.plan["subscriptions"][0]["policy"] = policy.to_dict()
        broker = await self.start()
        await broker.publish("source.output", self.envelope(0))  # Fill the unconnected sink's outbox.
        adapter = Adapter(self.plan, "source")
        self.assertGreater(adapter.request_timeout, 20)
        running = self.spawn(adapter.run("127.0.0.1", broker.port))
        await eventually(lambda: "source" in broker.ready)
        broker.enabled = True
        await eventually(lambda: broker.engines["link"].buffer.metrics["refused"] > 0)
        state, _ = await asyncio.wait_for(adapter.client.request("state"), 1)
        self.assertTrue(state["enabled"], "control replies blocked behind admission wait")
        await asyncio.sleep(15.2)
        self.assertFalse(running.done(), "the previous 15-second deadline stopped the adapter")
        await eventually(lambda: adapter.metrics.get("publications", 0) >= 1, timeout=7)
        self.assertEqual(adapter.metrics["delivery_refused"], 1)
        self.assertFalse(running.done())

    async def test_large_status_body_and_small_startup_state(self):
        source = self.plan["components"]["source"]
        source["outputs"] = {f"output_{number:04d}": copy.deepcopy(source["outputs"]["output"]) for number in range(1000)}
        source["command"] = [sys.executable, "-c", "from ose.execution.component import ComponentSession\n"
                            "s=ComponentSession()\nfor inputs in s.ticks(): pass\n"]
        self.plan["components"] = {"source": source}
        self.plan["subscriptions"] = []
        broker = await self.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", broker.port)
        tcp = TCP(reader, writer)
        try:
            await tcp.send({"op": "status", "request": 1})
            header, body = await tcp.receive()
            self.assertEqual(header["content"], "json")
            self.assertGreater(len(body), 65536)
            self.assertEqual(len(json.loads(body)["topics"]), 1000)
        finally:
            await tcp.close()
        client = await self.client(broker)
        status, _ = await client.request("status")
        self.assertEqual(len(status["topics"]), 1000)
        adapter = Adapter(self.plan, "source")
        running = self.spawn(adapter.run("127.0.0.1", broker.port))
        await eventually(lambda: "source" in broker.ready)
        broker.enabled = True
        await eventually(lambda: adapter.metrics.get("ticks", 0) >= 1)
        self.assertFalse(running.done())

    async def test_large_metrics_use_body_instead_of_metadata(self):
        broker = await self.start()
        client = await self.client(broker)
        await client.request("hello", node="source", udp_port=1)
        metrics = {"inputs": {f"input_{number:04d}": {"accepted": 100, "refused": 20, "duplicates": 1,
                    "depth": 10, "age_ms": 300, "replaced": 0} for number in range(1000)}}
        body = json.dumps(metrics).encode()
        self.assertGreater(len(body), 65536)
        await client.request("metrics", body)
        status, _ = await client.request("status")
        self.assertEqual(status["components"]["source"], metrics)
