"""Independent network admission and sequential callback execution per subscription."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from ose.delivery.buffers import Buffer
from ose.delivery.stores import MemoryStore
from .errors import CallbackFailed, SchemaError
from .types import MessageInfo

class Subscription:
    def __init__(self, client, codec, callback, policy):
        self.client, self.codec, self.callback, self.policy = client, codec, callback, policy
        self.identity, self.data = None, None
        self.buffer = Buffer(policy, MemoryStore(policy.capacity))
        self.stage = asyncio.Queue(maxsize=policy.capacity)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdl-callback")
        self.tasks = []
        self.closed = False

    def start(self):
        self.tasks = [asyncio.create_task(self._admit()), asyncio.create_task(self._dispatch())]
        for task in self.tasks:
            task.add_done_callback(self.client._task_done)

    async def receive(self, header, body):
        await self.stage.put((header, body))

    async def _admit(self):
        while True:
            header, body = await self.stage.get()
            try:
                identity, _ = self.codec.runtime.api.unpack_envelope(body)
                text = await self.client._catalogue(identity)
                value = self.codec.decode(text, body)
            except (ValueError, TypeError) as error:
                raise SchemaError(str(error)) from error
            record = dict(header, body=value)
            deadline = asyncio.get_running_loop().time() + self.policy.wait_timeout_ms / 1000
            while True:
                self.buffer.store.changed.clear()
                result = self.buffer.admit(record)
                if result != "full" or self.policy.overflow != "wait":
                    break
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self.buffer.store.changed.wait(), remaining)
                except asyncio.TimeoutError:
                    break
            if result in ("accepted", "duplicate", "stale") and self.policy.ack == "received":
                ack = dict(op="ack", subscription=self.identity, id=header["id"])
                if self.policy.transport == "tcp":
                    await self.client.control.tcp.send(ack)
                else:
                    await self.client.udp.send(ack, address=self.client.udp_endpoint)

    async def _dispatch(self):
        while True:
            self.buffer.store.changed.clear()
            record = self.buffer.take()
            if record is None:
                await self.buffer.store.changed.wait()
                continue
            # A FIFO record remains reserved and counted until its callback completes.
            if self.policy.buffer == "fifo":
                self.buffer.reserved.add(record["id"])
            info = MessageInfo(record["id"], record["topic"], record["epoch"], record["sequence"])
            try:
                await asyncio.get_running_loop().run_in_executor(self.executor, self.callback, record["body"], info)
            except Exception as error:
                raise CallbackFailed(f"{self.identity}: {error}") from error
            self.buffer.consumed(record["id"])

    def stats(self):
        async def get():
            header, _ = await self.client._rpc("subscription_stats", subscription=self.identity)
            return {"broker": {k: v for k, v in header.items() if k not in ("op", "request", "content")},
                    "client": self.buffer.snapshot(), "staging_depth": self.stage.qsize()}
        return self.client._call(get())

    async def _close(self, notify=True):
        if self.closed:
            return
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.data:
            await self.data.close()
        if notify and self.identity:
            await self.client._rpc("unsubscribe", subscription=self.identity)
        self.client.subscriptions.pop(self.identity, None)
        while not self.stage.empty():
            self.stage.get_nowait()
        self.buffer.store.close()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def close(self):
        return self.client._call(self._close())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
