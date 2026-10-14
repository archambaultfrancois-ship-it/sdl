"""Multi-topic broker with independent, bounded subscription delivery."""
import asyncio
from collections import Counter
import json
import time
import uuid
from chain_env.codec.sdl2 import SDL2
from chain_env.delivery.engine import DeliveryEngine
from chain_env.delivery.policies import SubscriptionPolicy
from chain_env.delivery.stores import MemoryStore
from chain_env.transport.tcp import TCP
from chain_env.transport.udp import UDP
from chain_env.transport.protocol import encode


class Broker:
    def __init__(self, plan):
        self.plan, self.codec = plan, SDL2(plan["sdl_root"])
        self.catalogues, self.topics, self.engines = {}, {}, {}
        self.epoch, self.sequence = uuid.uuid4().hex, 0
        self.retained, self.peers, self.ready, self.metrics = {}, {}, set(), Counter()
        self.data_peers, self.dedicated_data = {}, set()
        self._enabled = False
        self.errors = []
        self.connections = set()
        self.started_at = time.time()
        self.topic_counts = Counter()
        for name, node in plan["components"].items():
            for direction in ("inputs", "outputs"):
                for port_name, port in node[direction].items():
                    self.register(port["catalogue"])
                    if direction == "outputs":
                        self.topics[f"{name}.{port_name}"] = port
        self.subscriptions = {sub["id"]: sub for sub in plan["subscriptions"]}
        for identity, sub in self.subscriptions.items():
            policy = SubscriptionPolicy.from_dict(sub["policy"])
            store = MemoryStore(policy.capacity)
            self.engines[identity] = DeliveryEngine(policy, store)

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = value
        for engine in self.engines.values():
            engine.store.notify()

    def _notify_node(self, node):
        for identity, sub in self.subscriptions.items():
            if sub["to"].split(".")[0] == node:
                self.engines[identity].store.notify()

    def register(self, text):
        self.codec.api.prepare(text)
        identity = self.codec.identity(text)
        if identity in self.catalogues and self.catalogues[identity] != text:
            raise ValueError("CATALOGUE_COLLISION")
        self.catalogues[identity] = text
        return identity

    async def start(self, port=0):
        self.udp = await UDP.bind(self._udp, self.plan["max_datagram"])
        self.server = await asyncio.start_server(self._connection, "127.0.0.1", port)
        self.port = self.server.sockets[0].getsockname()[1]
        self.send_tasks = [asyncio.create_task(self._send_loop(identity, engine))
                           for identity, engine in self.engines.items()]
        return self.port

    async def _connection(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        peer, node = TCP(reader, writer, self.plan["max_frame"]), None
        data_subscription = None
        publications = set()
        try:
            while True:
                header, body = await peer.receive()
                op = header["op"]
                if op == "ack":
                    self._ack(node, header)
                    continue
                if op == "publish":
                    # Admission may wait for seconds; keep reading ACKs and control RPCs.
                    if len(publications) >= 32:
                        await self._reply(peer, header.get("request"), error="too many pending publications")
                    else:
                        publication = asyncio.create_task(self._publish_reply(peer, node, header, body))
                        publications.add(publication)
                        publication.add_done_callback(publications.discard)
                    continue
                try:
                    result = {}
                    response_body = b""
                    attach_subscription = None
                    if op == "hello":
                        node = header["node"]
                        if node not in self.plan["components"] or node in self.peers:
                            raise ValueError("unknown or duplicate component")
                        udp_port = header.get("udp_port")
                        if type(udp_port) is not int or not 0 < udp_port < 65536:
                            raise ValueError("invalid UDP endpoint")
                        self.peers[node] = (peer, ("127.0.0.1", udp_port))
                        if header.get("data_channels", False):
                            self.dedicated_data.add(node)
                        self._notify_node(node)
                        result = {"udp_port": self.udp.address[1], "epoch": self.epoch}
                        for identity, sub in self.subscriptions.items():
                            if sub["to"].split(".")[0] == node and sub["policy"]["buffer"] == "latest" and sub["from"] in self.retained:
                                retained = self.retained[sub["from"]]
                                if self._fits(identity, retained, retained["body"]):
                                    self.engines[identity].store.meta.clear()
                                    self.engines[identity].enqueue(retained)
                    elif op == "attach":
                        target = header["node"]
                        identity = header["subscription"]
                        sub = self.subscriptions[identity]
                        if node is not None or data_subscription is not None or target not in self.peers or target not in self.dedicated_data:
                            raise ValueError("invalid data channel attachment")
                        if sub["to"].split(".")[0] != target or sub["policy"]["transport"] != "tcp" or identity in self.data_peers:
                            raise ValueError("invalid subscription data channel")
                        attach_subscription = identity
                    elif op == "ready":
                        if node is None:
                            raise ValueError("hello required")
                        self.ready.add(node)
                    elif op == "status":
                        result = self.snapshot()
                    elif op == "state":
                        result = {"enabled": self.enabled}
                    elif op == "catalogue":
                        response_body = self.catalogues[header["catalogue_id"]].encode()
                    elif op == "metrics":
                        if node is None:
                            raise ValueError("hello required")
                        metrics = json.loads(body) if body else header["metrics"]
                        if not isinstance(metrics, dict):
                            raise ValueError("metrics must be an object")
                        self.metrics[node] = metrics
                    else:
                        raise ValueError("unknown operation")
                    await self._reply(peer, header["request"], result, binary=response_body if op == "catalogue" else None)
                    # Send the handshake reply before permitting DATA on this channel.
                    if attach_subscription is not None:
                        data_subscription = attach_subscription
                        self.data_peers[data_subscription] = peer
                        self.engines[data_subscription].store.notify()
                except (ValueError, KeyError) as error:
                    await self._reply(peer, header.get("request"), error=str(error))
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            pending = list(publications)
            for publication in pending:
                publication.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if node is not None and self.peers.get(node, (None,))[0] is peer:
                self.peers.pop(node, None)
                self.ready.discard(node)
                self.dedicated_data.discard(node)
                self._notify_node(node)
            if data_subscription is not None and self.data_peers.get(data_subscription) is peer:
                self.data_peers.pop(data_subscription, None)
                self.engines[data_subscription].store.notify()
            self.connections.discard(task)
            await peer.close()

    async def _reply(self, peer, request, result=None, *, binary=None, error=None):
        header = {"op": "result", "request": request}
        if error is not None:
            await peer.send({**header, "error": error})
        elif binary is not None:
            await peer.send(header, binary)
        else:
            await peer.send({**header, "content": "json"}, json.dumps(result or {}, separators=(",", ":")).encode())

    async def _publish_reply(self, peer, node, header, body):
        try:
            try:
                if not self.enabled or node is None or not header["topic"].startswith(node + "."):
                    raise ValueError("publication is not enabled for this component")
                result = await self.publish(header["topic"], body)
                await self._reply(peer, header["request"], result)
            except (ValueError, KeyError) as error:
                await self._reply(peer, header.get("request"), error=str(error))
        except ConnectionError:
            pass  # The connection's owner cancels any remaining admission waits.

    def _ack(self, node, header):
        sub = self.subscriptions[header["subscription"]]
        if sub["to"].split(".")[0] != node:
            raise ValueError("acknowledgement from wrong consumer")
        self.engines[sub["id"]].acknowledge(header["id"])

    async def _udp(self, header, body, address):
        if header["op"] != "ack" or body:
            raise ValueError("expected UDP ACK")
        for node, (_, endpoint) in self.peers.items():
            if endpoint == address:
                self._ack(node, header)
                return
        raise ValueError("unknown UDP peer")

    async def publish(self, topic, envelope):
        port = self.topics[topic]
        self.codec.validate(port["catalogue"], port["root"], envelope)
        self.sequence += 1
        record = {"id": f"{self.epoch}:{self.sequence}", "epoch": self.epoch, "sequence": self.sequence,
                  "topic": topic, "body": bytes(envelope), "published_at": time.time()}
        self.retained[topic] = record
        self.topic_counts[topic] += 1
        results = {}
        # Each admission has its own bounded wait; concurrent fan-out avoids coupling subscribers.
        async def admit(identity, sub):
            engine = self.engines[identity]
            if not self._fits(identity, record, envelope):
                return identity, "oversize"
            deadline = time.monotonic() + engine.policy.wait_timeout_ms / 1000
            while True:
                engine.store.changed.clear()
                result = engine.enqueue(record)
                if result != "full" or engine.policy.overflow != "wait" or time.monotonic() >= deadline:
                    return identity, result
                try:
                    await asyncio.wait_for(engine.store.changed.wait(), max(0, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    return identity, "full"
        matching = [(identity, sub) for identity, sub in self.subscriptions.items() if sub["from"] == topic]
        for identity, result in await asyncio.gather(*(admit(*pair) for pair in matching)):
            results[identity] = result
        return {"delivery": results, "sequence": self.sequence}

    def _header(self, identity, record):
        return {"op": "data", "subscription": identity, "id": record["id"], "epoch": record["epoch"],
                "sequence": record["sequence"], "topic": record["topic"]}

    def _fits(self, identity, record, envelope):
        engine = self.engines[identity]
        limit = self.plan["max_datagram"] if engine.policy.transport == "udp" else self.plan["max_frame"]
        try:
            encode(self._header(identity, record), envelope, limit)
        except ValueError:
            engine.metrics["failed"] += 1
            return False
        return True

    async def _send_loop(self, identity, engine):
        node = self.subscriptions[identity]["to"].split(".")[0]
        while True:
            engine.store.changed.clear()
            available, failed = False, False
            if self.enabled and node in self.peers:
                control, udp_address = self.peers[node]
                tcp = self.data_peers.get(identity) if node in self.dedicated_data else control
                if engine.policy.transport == "udp" or tcp is not None:
                    available = True
                    for record in list(engine.due(limit=32)):
                        if not engine.store.has_pending(record["id"]):
                            continue
                        try:
                            header, body = self._header(identity, record), record["body"]
                            if engine.policy.transport == "tcp":
                                # Preserve the one pending write while drain is backpressured.
                                # Cancelling and rewriting would append duplicates to the transport buffer.
                                await tcp.send(header, body)
                            else:
                                await self.udp.send(header, body, udp_address)
                            engine.sent(record["id"])
                            await asyncio.sleep(0)
                        except (ConnectionError, asyncio.TimeoutError):
                            failed = True
                            break
                        except ValueError as error:
                            engine.fail(record["id"], str(error))
            deadline = engine.store.next_deadline() if available and not failed else None
            timeout = max(0, deadline - engine.clock()) if deadline is not None else None
            try:
                await asyncio.wait_for(engine.store.changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass

    def snapshot(self):
        return {"enabled": self.enabled, "ready": sorted(self.ready), "subscriptions": {key: engine.snapshot() for key, engine in self.engines.items()},
                "components": dict(self.metrics), "udp_errors": self.udp.errors, "errors": self.errors,
                "topics": {topic: {"root": port["root"], "publications": self.topic_counts[topic],
                    "frequency_hz": self.topic_counts[topic] / max(time.time() - self.started_at, .001),
                    "age_ms": (time.time() - self.retained[topic]["published_at"]) * 1000 if topic in self.retained else None}
                    for topic, port in self.topics.items()}}

    async def close(self):
        self.enabled = False
        for task in self.send_tasks:
            task.cancel()
        await asyncio.gather(*self.send_tasks, return_exceptions=True)
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.connections):
            task.cancel()
        await asyncio.gather(*self.connections, return_exceptions=True)
        await self.udp.close()
        for engine in self.engines.values():
            engine.store.close()
        self.retained.clear()
        self.catalogues.clear()
