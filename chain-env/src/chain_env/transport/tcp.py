import asyncio
import json
import struct
from chain_env.transport.protocol import encode, decode


class TCP:
    def __init__(self, reader, writer, limit=1024 * 1024):
        self.reader, self.writer, self.limit = reader, writer, limit
        self.lock = asyncio.Lock()

    async def send(self, header, payload=b""):
        packet = encode(header, payload, self.limit)
        async with self.lock:
            self.writer.write(struct.pack(">I", len(packet)) + packet)
            await self.writer.drain()

    async def receive(self):
        size = struct.unpack(">I", await self.reader.readexactly(4))[0]
        if not 7 <= size <= self.limit:
            raise ValueError("invalid TCP frame length")
        return decode(await self.reader.readexactly(size), self.limit)

    async def close(self):
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), .5)
        except asyncio.TimeoutError:
            # A peer that no longer reads must not keep shutdown flushing forever.
            self.writer.transport.abort()
        except ConnectionError:
            pass  # A peer reset is already a completed close.


class Client:
    def __init__(self, tcp, on_data=None, request_timeout=15):
        self.tcp, self.on_data = tcp, on_data
        self.request_timeout = request_timeout
        self.pending, self.sequence = {}, 0
        self.task = asyncio.create_task(self._read())

    @classmethod
    async def connect(cls, host, port, on_data=None, limit=1024 * 1024, request_timeout=15):
        reader, writer = await asyncio.open_connection(host, port)
        return cls(TCP(reader, writer, limit), on_data, request_timeout)

    async def request(self, op, payload=b"", *, timeout=None, **arguments):
        self.sequence += 1
        identity = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[identity] = future
        try:
            await self.tcp.send({"op": op, "request": identity, **arguments}, payload)
            header, body = await asyncio.wait_for(future, self.request_timeout if timeout is None else timeout)
            if "error" in header:
                raise ValueError(header["error"])
            if header.get("content") == "json":
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("invalid JSON control result")
                header = {**result, **header}
                body = b""
            return header, body
        finally:
            self.pending.pop(identity, None)

    async def _read(self):
        try:
            while True:
                header, body = await self.tcp.receive()
                if header["op"] == "result":
                    future = self.pending.get(header["request"])
                    if future and not future.done():
                        future.set_result((header, body))
                elif header["op"] == "data" and self.on_data:
                    await self.on_data(header, body)
        except (asyncio.IncompleteReadError, ConnectionError, ValueError) as error:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError(str(error)))
            raise

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await self.tcp.close()
