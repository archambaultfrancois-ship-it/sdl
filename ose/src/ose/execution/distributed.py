"""Distributed real-time execution with cold replacement and direct TCP routes.

The coordinator authorizes every admission. Leases alone do not fence a partitioned
sender; direct receivers check the current generation with the coordinator too.
"""
import asyncio
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import signal
import time
import uuid

from ose.codec.sdl2 import SDL2
from ose.config import EXTERNAL_LIMITS
from ose.delivery.buffers import Buffer
from ose.delivery.engine import DeliveryEngine
from ose.delivery.policies import SubscriptionPolicy
from ose.delivery.stores import MemoryStore
from ose.execution.leadership import lease_clock
from ose.execution.process import start_component, stop_component, tick_events
from ose.execution.scheduler import Scheduler, reserve_snapshot
from ose.execution.observation import TickTimings, describe
from ose.transport.protocol import encode
from ose.transport.secure import RPCServer, connect, finish_cleanup, identity, loopback
from ose.recording.taps import TapHub, OPERATIONS as RECORDING_OPERATIONS


def fingerprint(plan):
    logical = {"components": {name: {key: node.get(key) for key in
                ("inputs", "outputs", "trigger", "parameters", "env")} for name, node in plan["components"].items()},
               "subscriptions": plan["subscriptions"], "max_frame": plan["max_frame"],
               "external_topics": plan.get("external_topics", {}),
               "external_limits": plan.get("broker", {}).get("external", {})}
    return hashlib.sha256(json.dumps(logical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_plan(plan):
    if plan.get("execution") != "distributed":
        raise ValueError("use a deployment with execution=distributed")
    if set(plan["components"]) & {"coordinator", "replay"}:
        raise ValueError("coordinator and replay are reserved distributed identities")
    for sub in plan["subscriptions"]:
        if sub["policy"]["transport"] != "tcp":
            raise ValueError("distributed execution currently requires TCP")
        if sub.get("routing", "broker") not in ("broker", "direct"):
            raise ValueError("routing must be broker or direct")
        if "from_topic" in sub and sub.get("routing", "broker") != "broker":
            raise ValueError("external inputs require broker routing")


async def admit(buffer, record, valid=lambda: None):
    deadline = time.monotonic() + buffer.policy.wait_timeout_ms / 1000
    while True:
        valid()
        buffer.store.changed.clear()
        result = buffer.admit(record) if isinstance(buffer, Buffer) else buffer.enqueue(record)
        if result != "full" or buffer.policy.overflow != "wait" or time.monotonic() >= deadline:
            return result
        try:
            await asyncio.wait_for(buffer.store.changed.wait(), max(0, deadline - time.monotonic()))
        except asyncio.TimeoutError:
            return "full"


def engine(sub):
    policy = SubscriptionPolicy.from_dict(sub["policy"])
    return DeliveryEngine(policy, MemoryStore(policy.capacity))


class Coordinator:
    def __init__(self, plan, context=None, lease_seconds=6, authority=lambda: None):
        check_plan(plan)
        if not math.isfinite(lease_seconds) or lease_seconds < .3:
            raise ValueError("lease must be finite and at least 0.3 seconds")
        self.plan, self.codec = plan, SDL2(plan["sdl_root"])
        self.lease_seconds = lease_seconds
        self.authority = authority
        self.epoch, self.plan_id = uuid.uuid4().hex, fingerprint(plan)
        self.sessions, self.metrics = {}, Counter()
        self.observation = describe(plan)
        self.external_peers, self.external_owners, self.external_generations = {}, {}, {}
        self.external_limits = {**EXTERNAL_LIMITS, **plan.get("broker", {}).get("external", {})}
        self.subscriptions = {s["id"]: s for s in plan["subscriptions"]}
        self.engines = {key: engine(sub) for key, sub in self.subscriptions.items()
                        if sub.get("routing", "broker") == "broker"}
        self.activated = False
        self.recording = TapHub(self.recording_points, plan["max_frame"])
        self.rpc = RPCServer(plan["max_frame"], self.request, context, self.disconnected)

    async def start(self, host="127.0.0.1", port=0):
        self.port = await self.rpc.start(host, port)
        self.monitor = asyncio.create_task(self._monitor())
        return self.port

    def current(self, name, token=None, *, ready=False):
        self.authority()
        session = self.sessions.get(name)
        if session is None or lease_clock() - session["seen"] >= self.lease_seconds:
            raise ValueError("component lease expired")
        if token is not None and session["token"] != token:
            raise ValueError("obsolete component generation")
        if ready and not session["ready"]:
            raise ValueError("component is not ready")
        return session

    def retire(self, name):
        session = self.sessions.pop(name, None)
        if not session:
            return
        self.metrics["retired"] += 1
        # No input replay or sender state survives a component generation change.
        for key, delivery in self.engines.items():
            sub = self.subscriptions[key]
            if name == sub["to"].split(".")[0] or ("from_topic" not in sub and name == sub["from"].split(".")[0]):
                delivery.store.close()
                delivery.store.notify()

    def disconnected(self, peer):
        self.release_external(peer)
        for name, session in list(self.sessions.items()):
            if session["peer"] is peer:
                self.retire(name)

    async def _monitor(self):
        while True:
            await asyncio.sleep(self.lease_seconds / 3)
            for name, session in list(self.sessions.items()):
                if lease_clock() - session["seen"] >= self.lease_seconds:
                    self.retire(name)
                    session["peer"].writer.close()

    def directory(self):
        nodes = {}
        for name in list(self.sessions):
            try:
                session = self.current(name, ready=True)
            except ValueError:
                continue
            nodes[name] = {key: session[key] for key in ("host", "port", "token")}
        return {"epoch": self.epoch, "enabled": self.activated, "nodes": nodes,
                "lease_seconds": self.lease_seconds}

    def recording_points(self):
        nodes = self.directory()["nodes"]
        return {f"{name}.{port}": dict(topic=f"{name}.{port}", root=desc["root"], catalogue=desc["catalogue"],
                    available=self.activated and name in nodes,
                    generation=nodes.get(name, {}).get("token"),
                    endpoint={"host": nodes[name]["host"], "port": nodes[name]["port"], "identity": name}
                             if name in nodes else None)
                for name, node in self.plan["components"].items() for port, desc in node["outputs"].items()}

    def authorize(self, sub, source_token, target_token):
        if not self.activated:
            raise ValueError("graph is not activated")
        if "from_topic" in sub:
            self.authority()
            if source_token is None or self.external_generations.get(sub["from_topic"]) != source_token:
                raise ValueError("obsolete external publisher generation")
        else:
            self.current(sub["from"].split(".")[0], source_token, ready=True)
        self.current(sub["to"].split(".")[0], target_token, ready=True)

    def release_external(self, peer):
        session = self.external_peers.pop(peer, None)
        if session:
            for topic in session["topics"]:
                if self.external_owners.get(topic) is peer:
                    del self.external_owners[topic]
        # Accepted records may drain after disconnect. A new publisher replaces
        # the topic generation and fences any remaining old broker records.

    async def external_request(self, peer, header, body, caller):
        if caller is not None and caller != "replay":
            raise ValueError("external publication requires a replay certificate identity")
        if not self.external_limits["enabled"]:
            raise ValueError("external publishers are disabled")
        op = header["op"]
        if op == "external_open":
            if peer in self.external_peers or any(s["peer"] is peer for s in self.sessions.values()):
                raise ValueError("connection already identified")
            if header.get("plan_id") != self.plan_id:
                raise ValueError("application plan mismatch")
            topics = header.get("topics")
            if (not isinstance(topics, list) or not topics or any(not isinstance(t, str) for t in topics)
                    or len(set(topics)) != len(topics) or set(topics) - self.plan.get("external_topics", {}).keys()):
                raise ValueError("select declared external topics")
            if len(self.external_peers) >= self.external_limits["max_clients"]:
                raise ValueError("too many external publishers")
            if any(topic in self.external_owners for topic in topics):
                raise ValueError("external topic already has an active publisher")
            if not self.activated:
                raise ValueError("graph is not activated")
            token = uuid.uuid4().hex
            self.external_peers[peer] = dict(topics=set(topics), token=token, sequence=0)
            for topic in topics:
                self.external_owners[topic] = peer
                self.external_generations[topic] = token
                for key, delivery in self.engines.items():
                    if self.subscriptions[key].get("from_topic") == topic:
                        delivery.store.close()
                        delivery.store.notify()
            return {"token": token, "epoch": self.epoch}, None
        session = self.external_peers.get(peer)
        if session is None or session["token"] != header.get("token"):
            raise ValueError("unknown external publisher session")
        if op == "external_close":
            self.release_external(peer)
            return {}, None
        topic = header.get("topic")
        if topic not in session["topics"] or self.external_owners.get(topic) is not peer:
            raise ValueError("publication from an unowned external topic")
        if not self.activated:
            raise ValueError("graph is not activated")
        desc = self.plan["external_topics"][topic]
        self.codec.validate(desc["catalogue"], desc["root"], body)
        session["sequence"] += 1
        sequence, token = session["sequence"], session["token"]
        record = dict(id=f"{token}:{sequence}", epoch=self.epoch, sequence=sequence,
                      source_token=token, source=topic, body=body, topic=topic)
        result = await self.publish_record(record)
        self.metrics["external_publications"] += 1
        return result, None

    async def publish_record(self, record):
        async def enqueue(key, delivery):
            sub = self.subscriptions[key]
            if sub["from"] != record["topic"]:
                return None
            try:
                target = self.current(sub["to"].split(".")[0], ready=True)
            except ValueError:
                return key, "unavailable"
            metadata = {k: record[k] for k in ("id", "epoch", "sequence", "source_token", "source", "topic")}
            try:
                encode({"op": "result", "request": 2**63 - 1, "record": metadata}, record["body"], self.plan["max_frame"])
            except ValueError:
                return key, "oversize"
            valid = lambda: self.authorize(sub, record["source_token"], target["token"])
            try:
                return key, await admit(delivery, record, valid)
            except ValueError:
                return key, "unavailable"
        results = dict(pair for pair in await asyncio.gather(*(enqueue(key, delivery)
                       for key, delivery in self.engines.items())) if pair is not None)
        self.metrics["broker_publications"] += 1
        self.metrics["broker_bytes"] += len(record["body"])
        return {"delivery": results}

    async def request(self, peer, header, body):
        self.authority()
        op = header["op"]
        caller = identity(peer.writer)
        if op in ("recording_points", "recording_describe"):
            return await self.recording.request(peer, header)
        if op == "status":
            return self.snapshot(), None
        if op in ("external_open", "external_publish", "external_close"):
            return await self.external_request(peer, header, body, caller)
        if peer in self.external_peers:
            raise ValueError("external publisher cannot act as a worker")
        name = header["node"]
        if caller is not None and caller != name:
            raise ValueError("certificate does not identify this component")
        if name not in self.plan["components"]:
            raise ValueError("unknown component")
        if op == "register":
            if header.get("plan_id") != self.plan_id:
                raise ValueError("application plan mismatch")
            if name in self.sessions:
                try:
                    self.current(name)
                except ValueError:
                    self.retire(name)
                else:
                    raise ValueError("component already has a live generation")
            host, port = header["host"], header["port"]
            if not isinstance(host, str) or not host or len(host) > 253 or type(port) is not int or not 0 < port < 65536:
                raise ValueError("invalid component endpoint")
            if self.rpc.context is None and not loopback(host):
                raise ValueError("insecure endpoints must be loopback")
            token = uuid.uuid4().hex
            self.sessions[name] = dict(token=token, host=host, port=port, peer=peer,
                                       ready=False, seen=lease_clock(), metrics={},
                                       observation_generation=uuid.uuid4().hex, metric_time=None)
            self.metrics["registrations"] += 1
            return {"token": token, **self.directory()}, None
        session = self.current(name, header["token"])
        if session["peer"] is not peer:
            raise ValueError("generation belongs to another control connection")
        if op == "recording_authorize":
            self.current(name, header["token"], ready=True)
            if not self.activated:
                raise ValueError("graph is not activated")
            return {}, None
        if op == "heartbeat":
            session["seen"] = lease_clock()
            return self.directory(), None
        if op == "unregister":
            self.retire(name)
            return {}, None
        if op == "ready":
            session["ready"] = True
            if set(self.sessions) == set(self.plan["components"]) and all(s["ready"] for s in self.sessions.values()):
                self.activated = True
            return self.directory(), None
        if op == "metrics":
            value = json.loads(body)
            if not isinstance(value, dict):
                raise ValueError("metrics must be an object")
            session["metrics"] = value
            session["metric_time"] = time.monotonic()
            return {}, None
        if op == "authorize":
            sub = self.subscriptions[header["subscription"]]
            if sub["to"].split(".")[0] != name:
                raise ValueError("authorization must come from the recipient")
            self.authorize(sub, header["source_token"], session["token"])
            return {"epoch": self.epoch}, None
        if op == "publish":
            self.current(name, header["token"], ready=True)
            if not self.activated:
                raise ValueError("graph is not activated")
            topic = header["topic"]
            source, port = topic.split(".")
            if source != name or port not in self.plan["components"][name]["outputs"]:
                raise ValueError("publication from wrong component")
            desc = self.plan["components"][name]["outputs"][port]
            self.codec.validate(desc["catalogue"], desc["root"], body)
            sequence = header["sequence"]
            if type(sequence) is not int or sequence < 1:
                raise ValueError("invalid publication sequence")
            record = dict(id=f"{session['token']}:{sequence}", epoch=self.epoch, sequence=sequence,
                          source_token=session["token"], source=name, body=body, topic=topic)
            return await self.publish_record(record), None
        if op in ("poll", "ack"):
            key = header["subscription"]
            sub = self.subscriptions[key]
            if sub["to"].split(".")[0] != name or key not in self.engines:
                raise ValueError("invalid broker subscription")
            delivery = self.engines[key]
            if op == "ack":
                delivery.acknowledge(header["id"])
                return {}, None
            # Short long-polls bound idle traffic and share the control connection.
            deadline = time.monotonic() + .25
            while True:
                self.current(name, header["token"], ready=True)
                delivery.store.changed.clear()
                for record in list(delivery.due(limit=1)):
                    try:
                        self.authorize(sub, record["source_token"], session["token"])
                    except ValueError:
                        delivery.fail(record["id"], "obsolete_generation")
                        continue
                    delivery.sent(record["id"])
                    return {"record": {key: value for key, value in record.items()
                                       if key in ("id", "epoch", "sequence", "source_token", "source", "topic")}}, record["body"]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"empty": True}, None
                next_due = delivery.store.next_deadline()
                if next_due is not None:
                    remaining = min(remaining, max(.001, next_due - time.monotonic()))
                try:
                    await asyncio.wait_for(delivery.store.changed.wait(), remaining)
                except asyncio.TimeoutError:
                    pass
        raise ValueError("unknown distributed operation")

    def snapshot(self):
        observation = {**self.observation, "components": {name: {
            **desc, "connected": name in self.sessions,
            "ready": self.sessions.get(name, {}).get("ready", False),
            "generation": self.sessions.get(name, {}).get("observation_generation"),
            "report_age_ms": (time.monotonic() - self.sessions[name]["metric_time"]) * 1000
                if self.sessions.get(name, {}).get("metric_time") is not None else None}
            for name, desc in self.observation["components"].items()}}
        return {"epoch": self.epoch, "observation": observation,
                "enabled": self.activated, "metrics": dict(self.metrics),
                "external_publishers": len(self.external_peers),
                "components": {name: {"ready": session["ready"], "host": session["host"],
                    "port": session["port"], "metrics": session["metrics"]} for name, session in self.sessions.items()},
                "subscriptions": {key: delivery.snapshot() for key, delivery in self.engines.items()}}

    async def close(self):
        if hasattr(self, "monitor"):
            self.monitor.cancel()
            await asyncio.gather(self.monitor, return_exceptions=True)
        await self.rpc.close()
        for delivery in self.engines.values():
            delivery.store.close()


class Worker:
    def __init__(self, plan, name, host, port, *, listen_host="127.0.0.1", listen_port=0,
                 advertise_host="127.0.0.1", server_context=None, client_context=None):
        check_plan(plan)
        self.plan, self.name, self.node = plan, name, plan["components"][name]
        self.host, self.port = host, port
        self.listen_host, self.listen_port, self.advertise_host = listen_host, listen_port, advertise_host
        self.context = client_context
        self.codec = SDL2(plan["sdl_root"])
        self.subscriptions = {s["id"]: s for s in plan["subscriptions"]}
        self.incoming = {key: s for key, s in self.subscriptions.items() if s["to"].split(".")[0] == name}
        self.outgoing = {key: s for key, s in self.subscriptions.items()
                         if s["from"].split(".")[0] == name and s.get("routing", "broker") == "direct"}
        self.activity = asyncio.Event()
        self.buffers, self.locks = {}, {}
        for key, sub in self.incoming.items():
            policy = SubscriptionPolicy.from_dict(sub["policy"])
            store = MemoryStore(policy.capacity)
            store.on_change = self.activity.set
            self.buffers[sub["to"].split(".")[1]] = Buffer(policy, store)
            self.locks[key] = asyncio.Lock()
        self.engines = {key: engine(sub) for key, sub in self.outgoing.items()}
        self.scheduler = Scheduler(self.node, self.buffers)
        self.timings = TickTimings()
        self.recording = TapHub(self.recording_points, plan["max_frame"], codec=self.codec)
        self.rpc = RPCServer(plan["max_frame"], self.receive, server_context, self.recording.disconnected)
        self.metrics = Counter()
        self.directory = {"nodes": {}, "enabled": False}
        self.started = asyncio.Event()
        self.started_at = 0
        self.sequence = 0
        self.direct_clients = {}
        self.closing = False

    def recording_points(self):
        return {f"{self.name}.{port}": dict(topic=f"{self.name}.{port}", root=desc["root"], catalogue=desc["catalogue"],
                    generation=getattr(self, "token", None), available=self.started.is_set(), endpoint=None)
                for port, desc in self.node["outputs"].items()}

    async def control(self, op, body=b"", **kwargs):
        return await self.client.request(op, body, node=self.name, token=self.token, **kwargs)

    async def run(self):
        tasks = []
        try:
            port = await self.rpc.start(self.listen_host, self.listen_port)
            self.client = await connect(self.host, self.port, self.plan["max_frame"], self.context, "coordinator")
            result, _ = await self.client.request("register", node=self.name, host=self.advertise_host,
                port=port, plan_id=fingerprint(self.plan))
            self.token, self.epoch = result["token"], result["epoch"]
            self.lease_seconds = result["lease_seconds"]
            self.last_contact = lease_clock()
            # Renew during native/user initialization too.
            tasks = [asyncio.create_task(self._heartbeat()), asyncio.create_task(self._watchdog()), self.client.task]
            initialization = asyncio.create_task(start_component(self.node))
            tasks.append(initialization)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if initialization not in done:
                initialization.cancel()
                await asyncio.gather(initialization, return_exceptions=True)
                for task in done:
                    task.result()
                raise RuntimeError("control connection failed during initialization")
            self.process, self.timed_ticks = initialization.result()
            tasks.remove(initialization)
            if not self.timed_ticks:
                raise ValueError("distributed components require timestamped ticks; regenerate containers")
            self.directory, _ = await self.control("ready")
            self.started.set()
            tasks += [asyncio.create_task(self._work()), asyncio.create_task(self._metrics()),
                      asyncio.create_task(self.process.wait()),
                      *(asyncio.create_task(self._poll(key)) for key, sub in self.incoming.items()
                        if sub.get("routing", "broker") == "broker"),
                      *(asyncio.create_task(self._send(key)) for key in self.outgoing)]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            raise RuntimeError(f"component {self.name} stopped")
        finally:
            await finish_cleanup(self.close(tasks))

    async def close(self, tasks):
        # Python 3.10 wait_for can absorb cancellation when its child has just
        # completed. Wakeups and explicit state stop those loops anyway.
        self.closing = True
        self.activity.set()
        for delivery in self.engines.values():
            delivery.store.notify()
        self.recording.close()
        reader = getattr(getattr(self, "client", None), "task", None)
        for task in tasks:
            if task is not reader:
                task.cancel()
        await asyncio.gather(*(task for task in tasks if task is not reader), return_exceptions=True)
        await self.rpc.close()
        if hasattr(self, "process"):
            await stop_component(self.process)
        if hasattr(self, "token"):
            try:
                await asyncio.wait_for(self.control("unregister"), .5)
            except (OSError, EOFError, ValueError, asyncio.TimeoutError):
                pass
        for _, client in self.direct_clients.values():
            await client.close()
        if hasattr(self, "client"):
            await self.client.close()
        for buffer in self.buffers.values():
            buffer.store.close()
        for delivery in self.engines.values():
            delivery.store.close()

    async def _heartbeat(self):
        while not self.closing:
            result, _ = await self.control("heartbeat")
            if self.closing:
                return
            if result["epoch"] != self.epoch:
                raise ValueError("coordinator generation changed")
            self.directory = result
            self.last_contact = lease_clock()
            self.activity.set()
            for delivery in self.engines.values():
                delivery.store.notify()
            await asyncio.sleep(self.lease_seconds / 4)

    async def _watchdog(self):
        while not self.closing:
            await asyncio.sleep(self.lease_seconds / 4)
            if lease_clock() - self.last_contact >= self.lease_seconds:
                raise RuntimeError("coordinator authorization expired")

    async def _metrics(self):
        while not self.closing:
            metrics = {**self.metrics, "running": bool(self.started_at),
                       **self.timings.snapshot(self.scheduler),
                       "inputs": {key: value.snapshot() for key, value in self.buffers.items()},
                       "direct": {key: value.snapshot() for key, value in self.engines.items()}}
            await self.control("metrics", json.dumps(metrics).encode())
            if self.closing:
                return
            await asyncio.sleep(.5)

    async def receive(self, peer, header, body):
        if not hasattr(self, "token"):
            raise ValueError("recipient is not registered")
        if header["op"] in RECORDING_OPERATIONS:
            await self.control("recording_authorize")
            return await self.recording.request(peer, header)
        key = header["subscription"]
        sub = self.incoming[key]
        source = sub["from"].split(".")[0]
        caller = identity(peer.writer)
        if header["op"] != "deliver" or sub.get("routing") != "direct" or (caller is not None and caller != source):
            raise ValueError("invalid direct sender or route")
        if header.get("target_token") != self.token:
            raise ValueError("obsolete target generation")
        record = {key: header[key] for key in ("id", "epoch", "sequence", "source_token", "source", "topic")}
        result = await self._admit(key, record, body)
        self.metrics["direct_bytes_received"] += len(body)
        return {"admission": result}, None

    async def _admit(self, key, record, body):
        sub = self.incoming[key]
        source = sub["from_topic"] if "from_topic" in sub else sub["from"].split(".")[0]
        if record["epoch"] != self.epoch or record["topic"] != sub["from"] or record["source"] != source:
            raise ValueError("wrong publication identity")
        if type(record["sequence"]) is not int or record["sequence"] < 1 or record["id"] != f"{record['source_token']}:{record['sequence']}":
            raise ValueError("invalid publication sequence")
        if "from_topic" in sub:
            desc = self.plan["external_topics"][sub["from_topic"]]
        else:
            source_name, source_port = sub["from"].split(".")
            desc = self.plan["components"][source_name]["outputs"][source_port]
        port = sub["to"].split(".")[1]
        self.codec.validate(desc["catalogue"], desc["root"], body)
        value = dict(record, body=body, payload=body[self.codec.api.ENVELOPE_HEADER_SIZE:], catalogue=desc["catalogue"])
        # Latest sequence numbers belong to a source generation, not a whole run.
        value["epoch"] = record["source_token"]
        buffer = self.buffers[port]
        async with self.locks[key]:
            deadline = time.monotonic() + buffer.policy.wait_timeout_ms / 1000
            while True:
                buffer.store.changed.clear()
                await self.control("authorize", subscription=key, source_token=record["source_token"])
                result = buffer.admit(value)
                if result != "full" or buffer.policy.overflow != "wait" or time.monotonic() >= deadline:
                    return result
                try:
                    await asyncio.wait_for(buffer.store.changed.wait(), max(0, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    return "full"

    async def _poll(self, key):
        while not self.closing:
            result, body = await self.control("poll", subscription=key)
            if self.closing:
                return
            if result.get("empty"):
                continue
            record = result["record"]
            try:
                admission = await self._admit(key, record, body)
            except ValueError:
                self.metrics["obsolete_received"] += 1
                continue
            if admission in ("accepted", "duplicate", "stale") and self.subscriptions[key]["policy"]["ack"] == "received":
                await self.control("ack", subscription=key, id=record["id"])

    async def _work(self):
        while not self.closing and not self.directory["enabled"]:
            self.activity.clear()
            if not self.directory["enabled"]:
                await self.activity.wait()
        if self.closing:
            return
        self.scheduler.start()
        self.started_at = time.monotonic_ns()
        while not self.closing:
            self.activity.clear()
            selected = self.scheduler.select()
            if selected is None:
                try:
                    await asyncio.wait_for(self.activity.wait(), self.scheduler.wait_timeout())
                except asyncio.TimeoutError:
                    pass
                continue
            reserve_snapshot(self.buffers, selected)
            self.metrics["ticks"] += 1
            async for port, payload in self.timings.events(tick_events(self.process, self.node, selected,
                    time.monotonic_ns() - self.started_at, self.metrics["ticks"])):
                desc = self.node["outputs"][port]
                await self.publish(f"{self.name}.{port}", self.codec.wrap(desc["catalogue"], payload))
            self.scheduler.complete(selected)
            self.metrics["completed_ticks"] += 1

    async def publish(self, topic, body):
        self.sequence += 1
        if self.recording.sessions:
            desc = self.node["outputs"][topic.split(".")[1]]
            self.recording.observe(topic, body, generation=self.token, sequence=self.sequence,
                                   catalogue=desc["catalogue"], root=desc["root"])
        record = dict(id=f"{self.token}:{self.sequence}", epoch=self.epoch, sequence=self.sequence,
                      source_token=self.token, source=self.name, body=body, topic=topic)
        broker = any(s["from"] == topic and s.get("routing", "broker") == "broker"
                     for s in self.subscriptions.values())
        if broker:
            result, _ = await self.control("publish", body, topic=topic, sequence=self.sequence)
            self.metrics["refused"] += sum(v not in ("accepted", "stale") for v in result["delivery"].values())
        for key, sub in self.outgoing.items():
            if sub["from"] != topic:
                continue
            target = self.directory["nodes"].get(sub["to"].split(".")[0])
            if target is None:
                self.metrics["unavailable"] += 1
                continue
            value = dict(record, target_token=target["token"])
            # Refuse oversize DATA before queue admission, including framing metadata.
            encode(self._header(key, value), body, self.plan["max_frame"])
            admission = await admit(self.engines[key], value)
            if admission not in ("accepted", "stale"):
                self.metrics["refused"] += 1
        self.metrics["publications"] += 1

    def _header(self, key, record):
        return {"op": "deliver", "request": 1, "subscription": key,
                **{key: record[key] for key in ("id", "epoch", "sequence", "source_token", "target_token", "source", "topic")}}

    async def _send(self, key):
        sub, delivery = self.outgoing[key], self.engines[key]
        name = sub["to"].split(".")[0]
        while not self.closing:
            delivery.store.changed.clear()
            target = self.directory["nodes"].get(name)
            for record in list(delivery.due(limit=32)):
                if self.closing:
                    return
                if target is None or record["target_token"] != target["token"]:
                    delivery.fail(record["id"], "target_generation_changed")
                    continue
                try:
                    cached = self.direct_clients.get(key)
                    if cached is not None and (cached[0] != target["token"] or cached[1].task.done()):
                        await cached[1].close()
                        self.direct_clients.pop(key)
                        cached = None
                    if cached is None:
                        client = await connect(target["host"], target["port"], self.plan["max_frame"], self.context, name,
                                               timeout=max(1, delivery.policy.wait_timeout_ms / 1000 + 1))
                        self.direct_clients[key] = (target["token"], client)
                    else:
                        client = cached[1]
                    header = self._header(key, record)
                    result, _ = await client.request("deliver", record["body"], **{k: v for k, v in header.items() if k not in ("op", "request")})
                    delivery.sent(record["id"])
                    if result["admission"] in ("accepted", "duplicate", "stale") and delivery.policy.ack == "received":
                        delivery.acknowledge(record["id"])
                    self.metrics["direct_bytes_sent"] += len(record["body"])
                except (OSError, ValueError, asyncio.TimeoutError):
                    # A failed attempt still consumes retry budget, with bounded storage.
                    delivery.sent(record["id"])
            if self.closing:
                return
            deadline = delivery.store.next_deadline()
            timeout = max(.001, deadline - time.monotonic()) if deadline is not None else None
            try:
                await asyncio.wait_for(delivery.store.changed.wait(), timeout)
            except asyncio.TimeoutError:
                pass


async def serve(plan_path, host, port, server_context=None, lease_seconds=6, kubernetes_lease=None, namespace="default"):
    from ose.execution.leadership import KubernetesLease
    leader = KubernetesLease(kubernetes_lease, namespace) if kubernetes_lease else None
    coordinator = Coordinator(json.loads(Path(plan_path).read_text()), server_context, lease_seconds,
                              leader.check if leader else lambda: None)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    stopping = asyncio.create_task(stop.wait())
    acquiring = None
    try:
        if leader:
            acquiring = asyncio.create_task(leader.acquire())
            done, _ = await asyncio.wait([acquiring, stopping], return_when=asyncio.FIRST_COMPLETED)
            if stopping in done:
                return
            acquiring.result()
        await coordinator.start(host, port)
        print(json.dumps({"running": True, "host": host, "port": coordinator.port}), flush=True)
        if leader:
            done, _ = await asyncio.wait([stopping, leader.task], return_when=asyncio.FIRST_COMPLETED)
            if leader.task in done:
                leader.task.result()
        else:
            await stopping
    finally:
        await coordinator.close()
        if acquiring:
            acquiring.cancel()
            await asyncio.gather(acquiring, return_exceptions=True)
        if leader:
            await leader.close()
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


async def work(plan_path, name, host, port, **kwargs):
    plan = json.loads(Path(plan_path).read_text())
    check_plan(plan)
    if name not in plan["components"]:
        raise ValueError("unknown component")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    waiting = asyncio.create_task(stop.wait())
    try:
        # Reconnection is a cold restart; no application or delivery state is restored.
        while not stop.is_set():
            worker = Worker(plan, name, host, port, **kwargs)
            running = asyncio.create_task(worker.run())
            done, _ = await asyncio.wait([running, waiting], return_when=asyncio.FIRST_COMPLETED)
            if waiting in done:
                running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                if not stop.is_set():
                    raise
            except (OSError, EOFError, ValueError, RuntimeError, asyncio.TimeoutError) as error:
                import sys
                print(f"{name}: cold restart: {error}", file=sys.stderr, flush=True)
            if not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), 1)
                except asyncio.TimeoutError:
                    pass
    finally:
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
