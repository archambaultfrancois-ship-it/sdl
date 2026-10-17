"""RAM-only delivery: binary sharing, receipt ACKs, wakeups and volatility."""
import asyncio
import copy
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from ose.codec.sdl2 import SDL2
from ose.delivery.policies import SubscriptionPolicy as Policy
from ose.execution.adapter import Adapter
from ose.execution.broker import Broker

SDL_ROOT = os.environ.get("SDL_ROOT")
CATALOGUE = "SDL2|Measurement{1:uint64:sequence;2:fl64:value;}"


async def eventually(predicate, timeout=2):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(.001)


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class RamTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.codec = SDL2(SDL_ROOT)
        port = {"root": "Measurement", "catalogue": CATALOGUE, "catalogue_id": self.codec.identity(CATALOGUE)}
        self.plan = {"version": 1, "sdl_root": str(Path(SDL_ROOT).resolve()), "max_frame": 1024 * 1024,
                     "max_datagram": 1200, "components": {
                         "source": {"inputs": {}, "outputs": {"output": port}},
                         "sink": {"inputs": {"left": copy.deepcopy(port), "right": copy.deepcopy(port)},
                                  "outputs": {}, "trigger": {"mode": "on_change"}}},
                     "subscriptions": [{"id": p, "from": "source.output", "to": "sink." + p,
                                        "policy": Policy(capacity=2, ack="received").to_dict()} for p in ("left", "right")]}
        self.tasks, self.brokers, self.adapters = [], [], []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for broker in self.brokers:
            await broker.close()
        for adapter in self.adapters:
            for buffer in adapter.buffers.values():
                buffer.store.close()

    async def broker(self, plan=None):
        broker = Broker(plan or self.plan)
        await broker.start()
        self.brokers.append(broker)
        return broker

    def envelope(self, n):
        return self.codec.wrap(CATALOGUE, b"\x01" + struct.pack(">Qd", n, float(n)))

    async def test_delivery_and_catalogue_registration_do_not_write_files(self):
        broker = await self.broker()
        envelope = self.envelope(1)
        with patch("builtins.open", side_effect=AssertionError("delivery attempted file I/O")), \
             patch.object(Path, "mkdir", side_effect=AssertionError("delivery created a state directory")):
            broker.register(CATALOGUE)
            result = await broker.publish("source.output", envelope)
            adapter = Adapter(self.plan, "sink")
            self.adapters.append(adapter)
        self.assertEqual(result["delivery"], {"left": "accepted", "right": "accepted"})
        self.assertIs(broker.engines["left"].store.first()["body"], envelope)
        self.assertIs(broker.engines["right"].store.first()["body"], envelope)

    async def test_ack_means_admitted_in_ram_and_payload_is_a_view(self):
        adapter = Adapter(self.plan, "sink")
        self.adapters.append(adapter)
        acknowledgements = []
        async def request(op, **args): return {}, CATALOGUE.encode()
        async def acknowledge(header, payload=b""): acknowledgements.append(header)
        adapter.client = SimpleNamespace(request=request, tcp=SimpleNamespace(send=acknowledge))
        task = asyncio.create_task(adapter._receiver("left")); self.tasks.append(task)
        body = self.envelope(1)
        header = {"op": "data", "subscription": "left", "topic": "source.output", "epoch": "e", "sequence": 1, "id": "e:1"}
        await adapter._tcp_data(header, body)
        await eventually(lambda: len(acknowledgements) == 1)
        pending = adapter.buffers["left"].take()
        self.assertEqual(pending["id"], "e:1")
        self.assertIs(pending["body"], body)
        self.assertIs(pending["payload"].obj, body)
        self.assertEqual(bytes(pending["payload"]), body[9:])

    async def test_restart_drops_pending_state_and_extra_catalogues(self):
        broker = await self.broker()
        broker.register("SDL2|Extra{1:int32:x;}")
        await broker.publish("source.output", self.envelope(1))
        old_epoch = broker.epoch
        await broker.close(); self.brokers.remove(broker)
        fresh = await self.broker()
        self.assertNotEqual(fresh.epoch, old_epoch)
        self.assertFalse(fresh.retained)
        self.assertTrue(all(engine.store.size() == 0 for engine in fresh.engines.values()))
        self.assertEqual(set(fresh.catalogues.values()), {CATALOGUE})

    async def test_admission_wakes_when_space_is_freed(self):
        plan = copy.deepcopy(self.plan)
        for sub in plan["subscriptions"]:
            sub["policy"] = Policy(capacity=1, overflow="wait", wait_timeout_ms=1000).to_dict()
        broker = await self.broker(plan)
        first = await broker.publish("source.output", self.envelope(1))
        publishing = asyncio.create_task(broker.publish("source.output", self.envelope(2)))
        self.tasks.append(publishing)
        await eventually(lambda: broker.engines["left"].buffer.metrics["refused"] > 0)
        for engine in broker.engines.values():
            engine.acknowledge(f"{broker.epoch}:{first['sequence']}")
        result = await asyncio.wait_for(publishing, .2)
        self.assertEqual(result["delivery"], {"left": "accepted", "right": "accepted"})

    async def test_disk_plans_fail_explicitly_at_startup(self):
        self.plan["subscriptions"][0]["policy"]["durability"] = "disk"
        with self.assertRaisesRegex(ValueError, "RAM-only"):
            Broker(self.plan)
        with self.assertRaisesRegex(ValueError, "RAM-only"):
            Adapter(self.plan, "sink")
