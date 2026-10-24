"""Real component processes, network RPCs, replacement, fencing and mTLS."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from ose.execution.distributed import Coordinator, Worker, fingerprint
from ose.modeling.compiler import build
from ose.transport.secure import connect, contexts

SDL_ROOT = os.environ.get("SDL_ROOT", str(Path(__file__).resolve().parents[3] / "sdl"))


def certificates(root, names):
    def openssl(*args):
        subprocess.run(["openssl", *args], cwd=root, check=True, capture_output=True)
    openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-days", "1",
            "-subj", "/CN=ose-test-ca", "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign", "-keyout", "ca.key", "-out", "ca.crt")
    (root / "leaf.ext").write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth,clientAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n")
    for name in names:
        openssl("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-subj", "/CN=" + name,
                "-keyout", name + ".key", "-out", name + ".csr")
        openssl("x509", "-req", "-in", name + ".csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial",
                "-days", "1", "-extfile", "leaf.ext", "-out", name + ".crt")


def tls(root, name):
    return contexts(str(root / "ca.crt"), str(root / (name + ".crt")), str(root / (name + ".key")))


class DistributedTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        (cls.root / "measurement.sdl").write_text("message Measurement {uint64 sequence; fl64 value;}")
        (cls.root / "source.py").write_text("from measurement import Measurement\ndef on_tick(ctx):\n ctx.publish_value(Measurement(sequence=ctx.tick_index, value=float(ctx.tick_index)))\n")
        (cls.root / "middle.py").write_text("from measurement import Measurement\ndef on_input(ctx, message):\n ctx.publish_value(Measurement(sequence=message.sequence, value=message.value*2))\n")
        (cls.root / "sink.py").write_text("import json\ndef on_input(ctx, message):\n with open(ctx.parameters['path'], 'a') as stream: stream.write(json.dumps([message.sequence, message.value])+'\\n')\n")
        desc = {"root": "Measurement"}
        model = dict(version=1, schemas="measurement.sdl", components={
            "source": dict(inputs={}, outputs={"value": desc}, trigger=dict(mode="periodic", period_ms=20)),
            "middle": dict(inputs={"input": desc}, outputs={"value": desc}, trigger=dict(mode="on_change")),
            "sink": dict(inputs={"input": desc}, outputs={}, trigger=dict(mode="on_change"), parameters={"path": str(cls.root / "observations")}),
        }, subscriptions=[dict(id="signal", **{"from": "source.value", "to": "middle.input"}, qos={"capacity": 4, "ack": "received", "retry": {}}),
                          dict(id="events", **{"from": "middle.value", "to": "sink.input"}, qos={"capacity": 16, "ack": "received", "retry": {}})])
        deployment = dict(version=1, execution="distributed", components={
            name: dict(container=dict(language="python", handler=name + ".py")) for name in model["components"]},
            subscriptions={"signal": {"routing": "direct"}})
        (cls.root / "model.json").write_text(json.dumps(model))
        (cls.root / "deployment.json").write_text(json.dumps(deployment))
        plan_path = build(cls.root / "model.json", cls.root / "deployment.json", SDL_ROOT, cls.root / "build")
        cls.template = json.loads(plan_path.read_text())

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "observations"
        self.plan = copy.deepcopy(self.template)
        self.plan["components"]["sink"]["parameters"]["path"] = str(self.path)
        self.coordinator = Coordinator(self.plan, lease_seconds=1)
        await self.coordinator.start()
        self.workers, self.tasks = {}, {}
        self.addAsyncCleanup(self.shutdown)

    async def shutdown(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        await self.coordinator.close()

    async def until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while not predicate():
            for task in self.tasks.values():
                if task.done():
                    task.result()
                    self.fail("worker stopped")
            if time.monotonic() > deadline:
                self.fail("distributed condition timed out")
            await asyncio.sleep(.02)

    async def launch(self, name):
        worker = Worker(self.plan, name, "127.0.0.1", self.coordinator.port)
        self.workers[name] = worker
        self.tasks[name] = asyncio.create_task(worker.run())
        await self.until(worker.started.is_set)
        return worker

    async def start_graph(self):
        for name in self.plan["components"]:
            await self.launch(name)
        await self.until(lambda: self.coordinator.activated)

    def values(self):
        return [json.loads(line) for line in self.path.read_text().splitlines()] if self.path.exists() else []

    async def test_mixed_routes_deliver_typed_values_and_bypass_broker(self):
        await self.start_graph()
        await self.until(lambda: len(self.values()) >= 8)
        values = self.values()
        self.assertTrue(all(value == sequence * 2 for sequence, value in values))
        self.assertEqual(len({sequence for sequence, _ in values}), len(values))
        self.assertGreater(self.workers["middle"].metrics["direct_bytes_received"], 0)
        self.assertNotIn("signal", self.coordinator.engines)
        self.assertGreater(self.coordinator.metrics["broker_bytes"], 0)
        self.assertLessEqual(self.workers["source"].engines["signal"].store.size(), 4)

    async def test_top_covers_direct_broker_and_missing_workers_without_tokens(self):
        import io
        from types import SimpleNamespace
        from ose.top import Observer, run
        await self.start_graph()
        await self.until(lambda: all(self.coordinator.sessions[name]["metrics"].get("completed_ticks", 0) > 2
                                     for name in self.plan["components"]))
        output = io.StringIO()
        args = SimpleNamespace(endpoint=None, host="127.0.0.1", port=self.coordinator.port,
            distributed=True, insecure=True, tls_ca=None, tls_cert=None, tls_key=None,
            interval=.5, window=5, count=3, format="jsonl", color="always")
        self.assertEqual(await run(args, output=output), 0)
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        rows = {row["name"]: row for row in reports[-1]["components"]}
        self.assertEqual(set(rows), set(self.plan["components"]))
        self.assertGreater(rows["source"]["rates"]["out"], 0)
        self.assertGreater(rows["middle"]["rates"]["in"], 0)
        self.assertIsNotNone(rows["middle"]["tick_p95_ms"])
        self.assertTrue(any(queue["name"] == "signal" for queue in rows["source"]["queues"]))
        self.assertTrue(any(queue["name"] == "events" for queue in rows["middle"]["queues"]))
        self.assertNotIn(self.workers["source"].token, output.getvalue())
        self.tasks["source"].cancel()
        await asyncio.gather(self.tasks["source"], return_exceptions=True)
        observer = Observer()
        report = observer.sample(self.coordinator.snapshot())
        self.assertEqual(next(row for row in report["components"] if row["name"] == "source")["state"], "UNAVAILABLE")

    async def test_broker_route_uses_same_handler(self):
        await self.coordinator.close()
        self.plan["subscriptions"][0]["routing"] = "broker"
        self.coordinator = Coordinator(self.plan, lease_seconds=1)
        await self.coordinator.start()
        await self.start_graph()
        await self.until(lambda: len(self.values()) >= 6)
        self.assertTrue(all(value == sequence * 2 for sequence, value in self.values()))
        self.assertEqual(self.workers["middle"].metrics["direct_bytes_received"], 0)

    async def test_shutdown_stops_when_wakeup_absorbs_cancellation(self):
        class RacingEvent:
            def __init__(self):
                self.entered = asyncio.Event()
                self.swallowed = False

            def clear(self): pass
            def set(self): pass

            async def wait(self):
                self.entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    if self.swallowed:
                        raise
                    # Model Python 3.10 wait_for returning a completed wakeup
                    # or RPC result instead of propagating cancellation.
                    self.swallowed = True

        cases = [("_send", "source", "signal", True),
                 ("_poll", "middle", "signal", True),
                 ("_heartbeat", "source", None, True),
                 ("_metrics", "source", None, True),
                 ("_work", "sink", None, True),
                 ("_work", "sink", None, False)]
        for method, name, key, enabled in cases:
            with self.subTest(method=method, enabled=enabled):
                worker = Worker(self.plan, name, "127.0.0.1", self.coordinator.port)
                racing = RacingEvent()
                worker.directory["enabled"] = enabled
                worker.epoch, worker.lease_seconds = "test", 1

                async def control(op, body=b"", **kwargs):
                    await racing.wait()
                    return {"epoch": worker.epoch, "empty": True}, None

                worker.control = control
                if method == "_send":
                    worker.engines[key].store.changed = racing
                elif method == "_work":
                    worker.activity = racing
                coroutine = getattr(worker, method)(key) if key is not None else getattr(worker, method)()
                task = asyncio.create_task(coroutine)
                self.tasks[f"{method}:{enabled}"] = task
                await asyncio.wait_for(racing.entered.wait(), 1)
                await asyncio.wait_for(worker.close([task]), 1)
                self.assertTrue(racing.swallowed)
                self.assertTrue(task.done())

    async def test_replacement_preserves_survivors_and_fences_old_direct_sender(self):
        await self.start_graph()
        await self.until(lambda: len(self.values()) >= 5)
        old_token = self.workers["source"].token
        survivor_pid = self.workers["middle"].process.pid
        survivor_token = self.workers["sink"].token
        self.tasks["source"].cancel()
        await asyncio.gather(self.tasks["source"], return_exceptions=True)
        count = len(self.values())
        await self.launch("source")
        await self.until(lambda: len(self.values()) >= count + 5)
        self.assertEqual(self.workers["middle"].process.pid, survivor_pid)
        self.assertEqual(self.workers["sink"].token, survivor_token)
        self.assertNotEqual(self.workers["source"].token, old_token)
        with self.assertRaisesRegex(ValueError, "obsolete"):
            self.coordinator.authorize(self.plan["subscriptions"][0], old_token, self.workers["middle"].token)
        desc = self.plan["components"]["source"]["outputs"]["value"]
        import struct
        envelope = self.workers["source"].codec.wrap(desc["catalogue"], b"\x01" + struct.pack(">Qd", 99, 99))
        target = self.coordinator.sessions["middle"]
        client = await connect(target["host"], target["port"], self.plan["max_frame"])
        try:
            with self.assertRaises(ValueError):
                await client.request("deliver", envelope, subscription="signal", source="source", topic="source.value",
                    source_token=old_token, target_token=target["token"], epoch=self.coordinator.epoch, id=old_token + ":99", sequence=99)
        finally:
            await client.close()

    async def test_lost_heartbeat_expires_live_control_connection(self):
        client = await connect("127.0.0.1", self.coordinator.port, self.plan["max_frame"])
        self.addAsyncCleanup(client.close)
        result, _ = await client.request("register", node="source", host="127.0.0.1", port=12345, plan_id=fingerprint(self.plan))
        self.assertIn("source", self.coordinator.sessions)
        await self.until(lambda: "source" not in self.coordinator.sessions, timeout=3)
        with self.assertRaises(ValueError):
            self.coordinator.current("source", result["token"])

    async def test_plan_mismatch_and_duplicate_generation_are_refused(self):
        await self.launch("source")
        client = await connect("127.0.0.1", self.coordinator.port, self.plan["max_frame"])
        self.addAsyncCleanup(client.close)
        for plan_id in ("different-application", fingerprint(self.plan)):
            with self.assertRaises(ValueError):
                await client.request("register", node="source", host="127.0.0.1", port=12345, plan_id=plan_id)

    async def test_coordinator_losing_authority_refuses_direct_admission(self):
        await self.start_graph()
        await self.until(lambda: len(self.values()) >= 3)
        def expired():
            raise ValueError("coordinator leadership authorization expired")
        self.coordinator.authority = expired
        desc = self.plan["components"]["source"]["outputs"]["value"]
        import struct
        body = self.workers["source"].codec.wrap(desc["catalogue"], b"\x01" + struct.pack(">Qd", 999, 999))
        target = self.coordinator.sessions["middle"]
        client = None
        try:
            token = self.workers["source"].token
            # Heartbeat retirement can close the receiver before its error reply;
            # both rejection paths must prevent the publication reaching business code.
            with self.assertRaises((ValueError, ConnectionError, EOFError)):
                client = await connect(target["host"], target["port"], self.plan["max_frame"])
                await client.request("deliver", body, subscription="signal", source="source", topic="source.value",
                    source_token=token, target_token=target["token"], epoch=self.coordinator.epoch, id=token + ":999", sequence=999)
            self.assertFalse(any(sequence == 999 for sequence, _ in self.values()))
        finally:
            if client is not None:
                await client.close()

    async def test_complete_mixed_graph_over_mutual_tls(self):
        root = Path(self.directory.name)
        certificates(root, ["coordinator", "observer", *self.plan["components"]])
        await self.coordinator.close()
        self.coordinator = Coordinator(self.plan, tls(root, "coordinator")[0], lease_seconds=2)
        await self.coordinator.start()
        for name in self.plan["components"]:
            server, client = tls(root, name)
            worker = Worker(self.plan, name, "127.0.0.1", self.coordinator.port,
                            server_context=server, client_context=client)
            self.workers[name] = worker
            self.tasks[name] = asyncio.create_task(worker.run())
            await self.until(worker.started.is_set)
        await self.until(lambda: len(self.values()) >= 5)
        self.assertTrue(all(value == sequence * 2 for sequence, value in self.values()))
        self.assertGreater(self.workers["middle"].metrics["direct_bytes_received"], 0)
        import io
        from types import SimpleNamespace
        from ose.top import run
        output = io.StringIO()
        args = SimpleNamespace(endpoint=None, host="127.0.0.1", port=self.coordinator.port,
            distributed=True, insecure=False, tls_ca=str(root / "ca.crt"),
            tls_cert=str(root / "observer.crt"), tls_key=str(root / "observer.key"),
            interval=.1, window=5, count=1, format="jsonl", color="auto")
        self.assertEqual(await run(args, output=output), 0)
        self.assertEqual(len(json.loads(output.getvalue())["components"]), 3)

    async def test_latest_accepts_sequence_reset_after_source_replacement(self):
        await self.coordinator.close()
        self.plan["subscriptions"][0]["policy"].update(buffer="latest", capacity=1, overflow="drop_oldest")
        self.coordinator = Coordinator(self.plan, lease_seconds=1)
        await self.coordinator.start()
        await self.start_graph()
        await self.until(lambda: len(self.values()) >= 7)
        self.tasks["source"].cancel()
        await asyncio.gather(self.tasks["source"], return_exceptions=True)
        count = len(self.values())
        await self.launch("source")
        await self.until(lambda: any(sequence <= 3 for sequence, _ in self.values()[count:]))

    async def test_worker_cli_cold_reconnects_after_coordinator_replacement(self):
        root = Path(self.directory.name)
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(self.plan))
        source_root = Path(__file__).resolve().parents[2] / "src"
        children = []
        try:
            for name in self.plan["components"]:
                children.append(subprocess.Popen([os.sys.executable, "-m", "ose.cli", "worker", "--plan", str(plan_path),
                    "--node", name, "--port", str(self.coordinator.port), "--insecure"],
                    env=dict(os.environ, PYTHONPATH=str(source_root)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            await self.until(lambda: len(self.values()) >= 5)
            old_epoch, port = self.coordinator.epoch, self.coordinator.port
            await self.coordinator.close()
            self.coordinator = Coordinator(self.plan, lease_seconds=1)
            await self.coordinator.start(port=port)
            count = len(self.values())
            await self.until(lambda: self.coordinator.activated and len(self.values()) >= count + 5, timeout=12)
            self.assertNotEqual(old_epoch, self.coordinator.epoch)
            self.assertTrue(all(child.poll() is None for child in children))
        finally:
            for child in children:
                child.terminate()
            for child in children:
                await asyncio.to_thread(child.wait, timeout=8)


class TLSIdentityTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        certificates(cls.root, ("coordinator", "source", "intruder"))

    def tls(self, name):
        return tls(self.root, name)

    async def test_mutual_tls_checks_application_identity(self):
        from ose.transport.secure import RPCServer, identity
        async def handler(peer, header, body):
            if identity(peer.writer) != header["node"]:
                raise ValueError("wrong component identity")
            return {"ok": True}, None
        server = RPCServer(4096, handler, self.tls("coordinator")[0])
        port = await server.start("127.0.0.1", 0)
        self.addAsyncCleanup(server.close)
        client = await connect("127.0.0.1", port, 4096, self.tls("source")[1], "coordinator")
        self.addAsyncCleanup(client.close)
        result, _ = await client.request("hello", node="source")
        self.assertTrue(result["ok"])
        with self.assertRaises(ValueError):
            await client.request("hello", node="other-component")
        with self.assertRaises(ValueError):
            await connect("127.0.0.1", port, 4096, self.tls("source")[1], "not-the-coordinator")

    def test_plaintext_requires_explicit_loopback_mode(self):
        with self.assertRaises(ValueError):
            contexts()
        with self.assertRaises(ValueError):
            contexts(insecure=True, hosts=["0.0.0.0"])
        self.assertEqual(contexts(insecure=True, hosts=["127.0.0.1", "localhost"]), (None, None))
