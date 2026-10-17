import asyncio
from ose.transport.protocol import encode, decode


class UDP(asyncio.DatagramProtocol):
    def __init__(self, callback, limit=1200):
        self.callback, self.limit = callback, limit
        self.transport = None
        self.queue = asyncio.Queue(maxsize=1024)
        self.errors = 0
        self.task = None

    def connection_made(self, transport):
        self.transport = transport
        self.task = asyncio.create_task(self._dispatch())

    def datagram_received(self, packet, address):
        try:
            header, body = decode(packet, self.limit)
            self.queue.put_nowait((header, body, address))
        except (ValueError, asyncio.QueueFull, UnicodeError):
            self.errors += 1

    async def _dispatch(self):
        while True:
            header, body, address = await self.queue.get()
            try:
                await self.callback(header, body, address)
            except (ValueError, KeyError, ConnectionError):
                self.errors += 1

    async def send(self, header, payload=b"", address=None):
        self.transport.sendto(encode(header, payload, self.limit), address)

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        self.transport.close()

    @classmethod
    async def bind(cls, callback, limit=1200, host="127.0.0.1", port=0):
        _, protocol = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: cls(callback, limit), local_addr=(host, port))
        return protocol

    @property
    def address(self):
        return self.transport.get_extra_info("sockname")
