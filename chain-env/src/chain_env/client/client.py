"""Synchronous, typed API backed by a private asyncio network thread."""
import asyncio
from collections import OrderedDict
import threading
import uuid
from chain_env.codec.runtime import TypedCodec
from chain_env.transport.tcp import Client as RPC, RemoteError
from chain_env.transport.udp import UDP
from .errors import (ClientError, UnknownTopic, SchemaError, QoSError,
                     ConnectionLost, PublicationRefused)
from .qos import Fifo
from .subscription import Subscription
from .types import PublishResult

class Publisher:
    def __init__(self, client, topic, codec):
        self.client, self.topic, self.codec = client, topic, codec
        self.lock = threading.Lock()
        self.closed = False

    def publish(self, message):
        if self.closed:
            raise ClientError("publisher is closed")
        with self.lock:
            envelope = self.codec.encode(message)
        async def send():
            header, _ = await self.client._rpc("publish", envelope, topic=self.topic)
            return PublishResult(**{k: header[k] for k in ("publication_id", "epoch", "sequence", "delivery")})
        return self.client._call(send())

    def close(self):
        if not self.closed:
            self.client._call(self.client._rpc("release_publisher", topic=self.topic))
            self.closed = True
            self.client.publishers.pop(self.topic, None)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

class Client:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="sdl-client", daemon=True)
        self.thread.start()
        self.subscriptions, self.publishers, self.catalogues = {}, {}, OrderedDict()
        self.stopped, self.closed, self.failure = threading.Event(), False, None
        self.control, self.udp = None, None
        self.timeout = 15

    @classmethod
    def connect(cls, host="127.0.0.1", port=7000, client_id=None, *, max_frame=1024*1024):
        client = cls()
        try:
            client._call(client._connect(host, port, client_id or uuid.uuid4().hex, max_frame))
            return client
        except BaseException as error:
            client.close()
            if isinstance(error, (OSError, TimeoutError)):
                raise ConnectionLost(str(error)) from error
            raise

    async def _connect(self, host, port, client_id, max_frame):
        self.host, self.port, self.max_frame = host, port, max_frame
        self.udp = await UDP.bind(self._udp, limit=65507, host="0.0.0.0")
        self.control = await asyncio.wait_for(RPC.connect(host, port, limit=max_frame), 10)
        result, _ = await self._rpc("client_hello", client_id=client_id, udp_port=self.udp.address[1])
        self.session = result["session"]
        self.max_frame = min(max_frame, result["max_frame"])
        self.control.tcp.limit = self.max_frame
        self.timeout = 15 + result["max_wait_timeout_ms"] / 1000
        self.control.request_timeout = self.timeout
        self.udp.limit = result["max_datagram"]
        self.udp_endpoint = (self.control.tcp.writer.get_extra_info("peername")[0], result["udp_port"])
        self.control.task.add_done_callback(self._task_done)

    def _task_done(self, task):
        if task.cancelled() or self.closed:
            return
        error = task.exception()
        if error and self.failure is None:
            self.failure = error if isinstance(error, ClientError) else ConnectionLost(str(error))
            self.stopped.set()

    async def _rpc(self, op, body=b"", **arguments):
        try:
            return await self.control.request(op, body, **arguments)
        except RemoteError as error:
            kind = {"unknown_topic": UnknownTopic, "schema": SchemaError, "qos": QoSError,
                    "ownership": PublicationRefused}.get(error.code, ClientError)
            raise kind(str(error)) from error
        except (ConnectionError, asyncio.TimeoutError) as error:
            raise ConnectionLost(str(error) or "request deadline exceeded; publication outcome is unknown") from error

    def _call(self, coroutine):
        if self.closed:
            coroutine.close()
            raise ClientError("client is closed")
        if self.failure:
            coroutine.close()
            raise self.failure
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return future.result()

    async def _catalogue(self, identity):
        if identity not in self.catalogues:
            _, body = await self._rpc("catalogue", catalogue_id=identity)
            self.catalogues[identity] = body.decode()
            if len(self.catalogues) > 64:
                self.catalogues.popitem(last=False)
        return self.catalogues[identity]

    async def _udp(self, header, body, address):
        if address != getattr(self, "udp_endpoint", None) or header.get("op") != "data":
            return
        sub = self.subscriptions.get(header.get("subscription"))
        if sub and sub.policy.transport == "udp":
            try:
                sub.stage.put_nowait((header, body))
            except asyncio.QueueFull:
                sub.buffer.metrics["staging_dropped"] += 1

    def list_topics(self):
        async def get():
            header, _ = await self._rpc("list_topics")
            return header["topics"]
        return self._call(get())

    def publisher(self, topic, message_type):
        if topic in self.publishers:
            raise ClientError("publisher already created on this client")
        codec = TypedCodec(message_type)
        async def create():
            await self._rpc("declare_publisher", codec.catalogue.encode(), topic=topic, root=codec.root)
            publisher = Publisher(self, topic, codec)
            self.publishers[topic] = publisher
            return publisher
        return self._call(create())

    def subscribe(self, topic, message_type, on_message, *, qos=None, transport="tcp"):
        codec = TypedCodec(message_type)
        try:
            policy = (qos or Fifo()).policy(transport)
        except ValueError as error:
            raise QoSError(str(error)) from error
        if not callable(on_message):
            raise TypeError("on_message must be callable")
        async def create():
            sub = Subscription(self, codec, on_message, policy)
            try:
                result, _ = await self._rpc("subscribe", codec.catalogue.encode(), topic=topic,
                                            root=codec.root, policy=policy.to_dict())
                sub.identity = result["subscription"]
                self.subscriptions[sub.identity] = sub
                sub.start()
                if transport == "tcp":
                    sub.data = await asyncio.wait_for(RPC.connect(self.host, self.port, sub.receive, limit=self.max_frame), 10)
                    sub.data.task.add_done_callback(self._task_done)
                    await sub.data.request("attach_client", session=self.session, subscription=sub.identity)
                await self._rpc("activate_subscription", subscription=sub.identity)
                return sub
            except BaseException:
                await sub._close(notify=sub.identity is not None)
                raise
        return self._call(create())

    def run(self):
        self.stopped.wait()
        if self.failure:
            raise self.failure

    async def _close(self):
        for sub in list(self.subscriptions.values()):
            await sub._close(notify=False)
        if self.control:
            await self.control.close()
        if self.udp:
            await self.udp.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopped.set()
        try:
            asyncio.run_coroutine_threadsafe(self._close(), self.loop).result(timeout=5)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
            self.loop.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
