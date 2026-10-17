"""Launch isolated adapter/component processes and coordinate readiness."""
import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time
from ose.execution.broker import Broker


async def run(plan_path, duration=None, port=0):
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text())
    broker = Broker(plan)
    children = []
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await broker.start(port)
        endpoint = {"host": "127.0.0.1", "port": broker.port}
        (Path(plan["build_dir"]) / "endpoint.json").write_text(json.dumps(endpoint))
        env = dict(os.environ)
        source_root = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
        for name in plan["components"]:
            child = await asyncio.create_subprocess_exec(sys.executable, "-m", "ose.cli", "adapter",
                "--plan", str(plan_path), "--node", name, "--port", str(broker.port), env=env)
            children.append(child)
        deadline = time.monotonic() + 20
        while broker.ready != set(plan["components"]):
            if stop.is_set():
                return
            if any(child.returncode is not None for child in children):
                raise RuntimeError("component failed during startup")
            if time.monotonic() > deadline:
                raise RuntimeError("component readiness timeout")
            await asyncio.sleep(.02)
        broker.enabled = True
        print(json.dumps({"running": True, **endpoint}), flush=True)
        deadline = time.monotonic() + duration if duration is not None else float("inf")
        while not stop.is_set() and time.monotonic() < deadline:
            if any(child.returncode is not None for child in children):
                raise RuntimeError("component failed; stopping the chain")
            await asyncio.sleep(.02)
    finally:
        broker.enabled = False
        for child in children:
            if child.returncode is None:
                child.terminate()
        for child in children:
            try:
                await asyncio.wait_for(child.wait(), 3)
            except asyncio.TimeoutError:
                child.kill()
                await child.wait()
        snapshot = broker.snapshot() if hasattr(broker, "udp") else {}
        (Path(plan["build_dir"]) / "last-status.json").write_text(json.dumps(snapshot, indent=2))
        if hasattr(broker, "server"):
            await broker.close()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
