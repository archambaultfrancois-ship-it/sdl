"""Shared component lifecycle and streaming CH/1 tick driver for both engines."""
import asyncio
import json
import os
from pathlib import Path
import struct
from chain_env.execution import ipc


async def start_component(node):
    env = dict(os.environ, **node.get("env", {}))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    process = await asyncio.create_subprocess_exec(*node["command"], cwd=node["cwd"], env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    try:
        async def initialize():
            await ipc.send(process.stdin, "I", json.dumps(node).encode())
            op, body = await ipc.receive(process.stdout)
            if op != "R" or body not in (b"", ipc.TICK_CONTEXT):
                raise ValueError("component did not send a supported READY")
            return body == ipc.TICK_CONTEXT
        capable = await asyncio.wait_for(initialize(), 15)
        return process, capable
    except BaseException:
        await stop_component(process)
        raise


async def tick_events(process, node, selected, time_ns=None, tick_index=1):
    """Yield publications as they arrive; callers control their visibility."""
    for port, record in selected.items():
        body = ipc.port_data(port, b"", "") if record is None else ipc.port_data(port, record["payload"], record["catalogue"])
        await ipc.send(process.stdin, "D", body)
    await ipc.send(process.stdin, "T", b"" if time_ns is None else struct.pack(">QQ", time_ns, tick_index))
    while True:
        op, body = await ipc.receive(process.stdout)
        if op == "F" and not body:
            return
        if op != "P":
            raise ValueError("component must publish or finish a tick")
        port, payload = ipc.unpack_port(body)
        if port not in node["outputs"] or not payload:
            raise ValueError("component published unknown output or empty payload")
        yield port, payload


async def stop_component(process):
    async def discard_stdout():
        # A cancelled tick can leave a paused, full StreamReader behind. Drain it
        # while reaping so neither Process.wait() nor pipe cleanup gets stuck.
        while await process.stdout.read(65536):
            pass
    draining = asyncio.create_task(discard_stdout())
    async def stop():
        if process.returncode is None:
            await ipc.send(process.stdin, "X")
        await process.wait()
        await asyncio.shield(draining)
    try:
        # Include writer.drain() in the grace deadline, even for a full pipe.
        await asyncio.wait_for(stop(), .5)
    except (ConnectionError, asyncio.TimeoutError):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(stop_after_signal(process, draining), 1)
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.wait()
            await draining
    finally:
        process.stdin.close()
        if not draining.done():
            draining.cancel()
        await asyncio.gather(draining, return_exceptions=True)


async def stop_after_signal(process, draining):
    await process.wait()
    await asyncio.shield(draining)
