"""Per-component process: transport delivery, scheduling and bounded IPC."""
import asyncio
import json
import sys
import time
from chain_env.codec.sdl2 import SDL2
from chain_env.delivery.buffers import Buffer
from chain_env.delivery.policies import SubscriptionPolicy
from chain_env.delivery.stores import MemoryStore
from chain_env.execution import ipc
from chain_env.execution.process import start_component, tick_events, stop_component
from chain_env.execution.scheduler import Scheduler, reserve_snapshot
from chain_env.transport.tcp import Client
from chain_env.transport.udp import UDP


class Adapter:
    def __init__(self, plan, name):
        self.plan, self.name, self.node = plan, name, plan["components"][name]
        self.codec = SDL2(plan["sdl_root"])
        self.subscriptions = {s["id"]: s for s in plan["subscriptions"] if s["to"].split(".")[0] == name}
        self.buffers, self.catalogues, self.metrics = {}, {}, {}
        self.compatibility = set()
        self.network_queues = {identity: asyncio.Queue(maxsize=1024) for identity in self.subscriptions}
        self.data_clients = {}
        self.activity = asyncio.Event()
        max_wait = max((sub["policy"]["wait_timeout_ms"] for sub in plan["subscriptions"]
                        if sub["from"].split(".")[0] == name and sub["policy"]["overflow"] == "wait"), default=0)
        self.request_timeout = 15 + max_wait / 1000
        for identity, sub in self.subscriptions.items():
            policy = SubscriptionPolicy.from_dict(sub["policy"])
            store = MemoryStore(policy.capacity)
            store.on_change = self.activity.set
            self.buffers[sub["to"].split(".")[1]] = Buffer(policy, store)
        self.scheduler = Scheduler(self.node, self.buffers)
        self.running = True
        self.started_at = time.monotonic()
        self.timed_ticks, self.tick_index, self.activated_at_ns = False, 0, 0

    async def _tcp_data(self, header, body):
        # This callback runs on the subscription's data connection, never the control reader.
        await self.network_queues[header["subscription"]].put((header, body, None))

    async def _udp_data(self, header, body, address):
        if address != self.broker_udp or header["op"] != "data":
            raise ValueError("unexpected UDP peer/event")
        try:
            self.network_queues[header["subscription"]].put_nowait((header, body, address))
        except asyncio.QueueFull:
            self.metrics["network_refused"] = self.metrics.get("network_refused", 0) + 1

    async def _receiver(self, subscription):
        queue = self.network_queues[subscription]
        while True:
            header, body, address = await queue.get()
            try:
                if header["subscription"] != subscription:
                    raise ValueError("wrong subscription")
                sub = self.subscriptions[subscription]
                if header["topic"] != sub["from"]:
                    raise ValueError("wrong topic")
                port = sub["to"].split(".")[1]
                buffer = self.buffers[port]
                if (address is not None) != (buffer.policy.transport == "udp"):
                    raise ValueError("wrong data transport")
                if buffer.policy.buffer == "latest" and header["epoch"] != self.broker_epoch:
                    raise ValueError("obsolete broker epoch")
                identity = self.codec.api.unpack_envelope(body)[0]
                if identity not in self.catalogues:
                    _, raw_catalogue = await self.client.request("catalogue", catalogue_id=identity)
                    text = raw_catalogue.decode()
                    if self.codec.identity(text) != identity:
                        raise ValueError("catalogue identity mismatch")
                    if len(self.catalogues) >= 64:
                        evicted = next(iter(self.catalogues))
                        self.catalogues.pop(evicted)
                        self.compatibility = {key for key in self.compatibility if key[0] != evicted}
                    self.catalogues[identity] = text
                text = self.catalogues[identity]
                key = (identity, port)
                if key not in self.compatibility:
                    self.codec.compare(text, self.node["inputs"][port]["catalogue"], self.node["inputs"][port]["root"])
                    self.compatibility.add(key)
                self.codec.validate(text, self.node["inputs"][port]["root"], body)
                record = {"id": header["id"], "epoch": header["epoch"], "sequence": header["sequence"],
                          "body": body, "payload": memoryview(body)[self.codec.api.ENVELOPE_HEADER_SIZE:], "catalogue": text}
                deadline = time.monotonic() + buffer.policy.wait_timeout_ms / 1000
                while True:
                    buffer.store.changed.clear()
                    result = buffer.admit(record)
                    if result != "full" or buffer.policy.overflow != "wait" or time.monotonic() >= deadline:
                        break
                    try:
                        await asyncio.wait_for(buffer.store.changed.wait(), max(0, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                if result in ("accepted", "duplicate", "stale") and buffer.policy.ack == "received":
                    ack = {"op": "ack", "subscription": sub["id"], "id": record["id"]}
                    if address is None:
                        await self.client.tcp.send(ack)
                    else:
                        await self.udp.send(ack, address=address)
            except (ValueError, KeyError) as error:
                self.metrics["invalid"] = self.metrics.get("invalid", 0) + 1
                print(f"{self.name}: rejected data: {error}", file=sys.stderr)
            # Cached catalogue/in-memory admissions may otherwise run without any suspension.
            await asyncio.sleep(0)

    async def _report(self):
        while True:
            elapsed = max(time.monotonic() - self.started_at, .001)
            metrics = {"inputs": {p: b.snapshot() for p, b in self.buffers.items()},
                "ticks_hz": self.metrics.get("ticks", 0) / elapsed,
                "publications_hz": self.metrics.get("publications", 0) / elapsed, **self.metrics}
            await self.client.request("metrics", json.dumps(metrics).encode())
            await asyncio.sleep(.1)

    async def _work(self):
        while True:
            self.activity.clear()
            selected = self.scheduler.select()
            if selected is None:
                try:
                    await asyncio.wait_for(self.activity.wait(), self.scheduler.wait_timeout())
                except asyncio.TimeoutError:
                    pass
                continue
            # Reserve the complete snapshot atomically before any pipe write can suspend.
            reserve_snapshot(self.buffers, selected)
            self.tick_index += 1
            timestamp = time.monotonic_ns() - self.activated_at_ns if self.timed_ticks else None
            async for port, payload in tick_events(self.process, self.node, selected, timestamp, self.tick_index):
                desc = self.node["outputs"][port]
                envelope = self.codec.wrap(desc["catalogue"], payload)
                result, _ = await self.client.request("publish", envelope, timeout=self.request_timeout, topic=f"{self.name}.{port}")
                self.metrics["publications"] = self.metrics.get("publications", 0) + 1
                refused = {key: value for key, value in result["delivery"].items() if value not in ("accepted", "stale")}
                if refused:
                    self.metrics["delivery_refused"] = self.metrics.get("delivery_refused", 0) + len(refused)
                    print(f"{self.name}: delivery refused {refused}", file=sys.stderr)
            self.scheduler.complete(selected)
            self.metrics["ticks"] = self.metrics.get("ticks", 0) + 1

    async def run(self, host, port):
        tasks = []
        try:
            self.udp = await UDP.bind(self._udp_data, self.plan["max_datagram"])
            self.client = await Client.connect(host, port, limit=self.plan["max_frame"], request_timeout=self.request_timeout)
            result, _ = await self.client.request("hello", node=self.name, udp_port=self.udp.address[1], data_channels=True)
            self.broker_udp = (host, result["udp_port"])
            self.broker_epoch = result["epoch"]
            for identity, sub in self.subscriptions.items():
                if sub["policy"]["transport"] == "tcp":
                    async def on_data(header, body, expected=identity):
                        if header["subscription"] != expected:
                            raise ValueError("wrong TCP data channel")
                        await self._tcp_data(header, body)
                    client = await Client.connect(host, port, on_data, self.plan["max_frame"])
                    self.data_clients[identity] = client
                    await client.request("attach", node=self.name, subscription=identity)
            self.process, self.timed_ticks = await start_component(self.node)
            await self.client.request("ready")
            # Supervisor activates delivery only after every component is ready.
            while True:
                status, _ = await self.client.request("state")
                if status["enabled"]:
                    break
                await asyncio.sleep(.02)
            self.scheduler.start()
            self.activated_at_ns = time.monotonic_ns()
            tasks = [*(asyncio.create_task(self._receiver(identity)) for identity in self.subscriptions),
                     asyncio.create_task(self._work()), asyncio.create_task(self._report()), self.client.task,
                     *(client.task for client in self.data_clients.values()), asyncio.create_task(self.process.wait())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise RuntimeError(f"component {self.name} stopped unexpectedly")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if hasattr(self, "process") and self.process.returncode is None:
                await self._stop_component()
            for client in self.data_clients.values():
                await client.close()
            if hasattr(self, "client"):
                await self.client.close()
            if hasattr(self, "udp"):
                await self.udp.close()
            for buffer in self.buffers.values():
                buffer.store.close()
            self.catalogues.clear()
            self.compatibility.clear()
            self.network_queues.clear()

    async def _stop_component(self):
        await stop_component(self.process)
