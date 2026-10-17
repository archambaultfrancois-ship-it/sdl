import asyncio
import copy
import os
from pathlib import Path
import struct
import tempfile
import unittest
from ose.codec.sdl2 import SDL2
from ose.delivery.buffers import Buffer
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.delivery.stores import MemoryStore
from ose.execution.broker import Broker
from ose.transport.tcp import Client
from ose.transport.udp import UDP

SDL_ROOT = os.environ.get("SDL_ROOT")
CATALOGUE = "SDL2|Measurement{1:uint64:sequence;2:fl64:value;}"


async def eventually(predicate, timeout=2):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(.01)


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.codec = SDL2(SDL_ROOT)
        port = {"root": "Measurement", "catalogue": CATALOGUE, "catalogue_id": self.codec.identity(CATALOGUE)}
        self.plan = {"version": 1, "sdl_root": str(Path(SDL_ROOT).resolve()), "max_frame": 1024*1024,
            "max_datagram": 1200, "components": {"source": {"inputs": {}, "outputs": {"output": port}},
                "sink": {"inputs": {"input": port}, "outputs": {}}},
            "subscriptions": [{"id": "link", "from": "source.output", "to": "sink.input", "policy": Policy().to_dict()}]}
        self.clients, self.udps = [], []
        self.broker = None

    async def asyncTearDown(self):
        for client in self.clients:
            await client.close()
        for udp in self.udps:
            await udp.close()
        if self.broker:
            await self.broker.close()
        self.directory.cleanup()

    def envelope(self, n):
        return self.codec.wrap(CATALOGUE, b"\x01" + struct.pack(">Qd", n, float(n)))

    async def start(self, policy):
        self.plan["subscriptions"][0]["policy"] = policy.to_dict()
        self.broker = Broker(self.plan)
        await self.broker.start()
        self.broker.enabled = True

    async def consumer(self, handler):
        udp = await UDP.bind(handler)
        self.udps.append(udp)
        async def tcp_handler(header, body):
            await handler(header, body, None)
        client = await Client.connect("127.0.0.1", self.broker.port, tcp_handler)
        self.clients.append(client)
        await client.request("hello", node="sink", udp_port=udp.address[1])
        return client, udp

    async def test_tcp_fifo_and_catalogue_resolution(self):
        await self.start(Policy())
        records = []
        async def receive(header, body, address):
            records.append((header, body))
        client, _ = await self.consumer(receive)
        for n in (3, 1, 2):
            await self.broker.publish("source.output", self.envelope(n))
        await eventually(lambda: len(records) == 3)
        self.assertEqual([self.codec.validate(CATALOGUE, "Measurement", body).fields["sequence"] for _, body in records], [3, 1, 2])
        _, text = await client.request("catalogue", catalogue_id=self.codec.identity(CATALOGUE))
        self.assertEqual(text.decode(), CATALOGUE)

    async def test_udp_lost_data_and_lost_ack_deduplicate(self):
        policy = Policy.resolve({"ack": "received", "retry": {"ack_timeout_ms": 20}}, "udp")
        await self.start(policy)
        inbox = Buffer(policy, MemoryStore())
        deliveries = []
        sender = self.broker.udp.send
        sends = [0]
        async def lose_first(header, payload=b"", address=None):
            sends[0] += 1
            if sends[0] > 1:
                await sender(header, payload, address)
        self.broker.udp.send = lose_first
        async def receive(header, body, address):
            deliveries.append(header)
            inbox.admit({**header, "body": body})
            # Drop the first ACK too. The second reception is a recognized duplicate.
            if len(deliveries) > 1:
                await udp.send({"op": "ack", "subscription": "link", "id": header["id"]}, address=address)
        _, udp = await self.consumer(receive)
        await self.broker.publish("source.output", self.envelope(1))
        await eventually(lambda: self.broker.engines["link"].metrics["acknowledged"] == 1)
        self.assertEqual(len(inbox.store.records()), 1)
        self.assertEqual(inbox.metrics["duplicates"], 1)
        self.assertEqual(self.broker.engines["link"].metrics["retries"], 2)

    async def test_retry_exhaustion_and_no_retry(self):
        await self.start(Policy.resolve({"ack": "received", "retry": {"max_retries": 1, "ack_timeout_ms": 10}}, "udp"))
        received = []
        async def receive(header, body, address): received.append(header)
        await self.consumer(receive)
        await self.broker.publish("source.output", self.envelope(1))
        await eventually(lambda: self.broker.engines["link"].metrics["failed"] == 1)
        self.assertEqual(len(received), 2)

    async def test_udp_without_retry_loses_once(self):
        await self.start(Policy.resolve({}, "udp"))
        received = []
        async def receive(header, body, address): received.append(header)
        await self.consumer(receive)
        sends = []
        async def drop(header, payload=b"", address=None): sends.append(header)
        self.broker.udp.send = drop
        await self.broker.publish("source.output", self.envelope(1))
        await eventually(lambda: len(sends) == 1)
        await asyncio.sleep(.05)
        self.assertEqual(received, [])
        self.assertEqual(len(sends), 1)
        self.assertEqual(self.broker.engines["link"].store.records(), [])

    async def test_wait_is_bounded_and_does_not_block_other_subscribers(self):
        self.plan["components"]["slow"] = copy.deepcopy(self.plan["components"]["sink"])
        self.plan["subscriptions"][0]["to"] = "slow.input"
        self.plan["subscriptions"].append({"id": "fast", "from": "source.output", "to": "sink.input", "policy": Policy().to_dict()})
        await self.start(Policy(capacity=1, overflow="wait", wait_timeout_ms=40))
        records = []
        async def receive(header, body, address): records.append(header)
        await self.consumer(receive)
        await self.broker.publish("source.output", self.envelope(1))
        await eventually(lambda: len(records) == 1)
        publishing = asyncio.create_task(self.broker.publish("source.output", self.envelope(2)))
        await eventually(lambda: len(records) == 2)
        result = await publishing
        self.assertEqual(result["delivery"]["link"], "full")
        self.assertEqual(result["delivery"]["fast"], "accepted")

    async def test_latest_late_subscriber_and_oversize_udp(self):
        await self.start(Policy.resolve({"buffer": "latest"}, "udp"))
        for n in (1, 2, 3):
            await self.broker.publish("source.output", self.envelope(n))
        records = []
        async def receive(header, body, address): records.append((header, body))
        await self.consumer(receive)
        await eventually(lambda: len(records) == 1)
        self.assertEqual(self.codec.validate(CATALOGUE, "Measurement", records[0][1]).fields["sequence"], 3)
        with self.assertRaises(ValueError):
            await self.broker.udp.send({"op": "data"}, b"x" * 1200, self.udps[0].address)

    async def test_malformed_payload_collision_and_independent_saturation(self):
        await self.start(Policy(capacity=1))
        await self.broker.publish("source.output", self.envelope(1))
        result = await self.broker.publish("source.output", self.envelope(2))
        self.assertEqual(result["delivery"]["link"], "full")
        with self.assertRaises(ValueError):
            await self.broker.publish("source.output", b"bad")
        original = self.codec.identity(CATALOGUE)
        self.broker.codec.identity = lambda text: original
        with self.assertRaisesRegex(ValueError, "COLLISION"):
            self.broker.register("SDL2|Other{1:int32:x;}")
