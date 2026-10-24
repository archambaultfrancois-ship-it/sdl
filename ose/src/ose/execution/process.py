"""Shared component lifecycle and streaming CH/1 tick driver for both engines."""
import asyncio
import json
import os
from pathlib import Path
import struct
from ose.execution import ipc
from ose.parameters.bindings import updates, write_reply


async def start_component(node, parameters=None):
    env = dict(os.environ, **node.get("env", {}))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2]) + os.pathsep + env.get("PYTHONPATH", "")
    # Terminal Ctrl+C belongs to the supervisor. Components stop through IPC so
    # callbacks can finish and on_stop runs instead of a concurrent SIGINT.
    process = await asyncio.create_subprocess_exec(*node["command"], cwd=node["cwd"], env=env,
        start_new_session=True,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    try:
        async def initialize():
            await ipc.send(process.stdin, "I", json.dumps(node).encode())
            if node.get("parameter_bindings"):
                if parameters is None:
                    raise ValueError("shared parameter service is required")
                await updates(process, node, parameters.initial)
            op, body = await ipc.receive(process.stdout)
            while op == "S":
                await write_reply(process, body, parameters)
                op, body = await ipc.receive(process.stdout)
            if op != "R" or body not in (b"", ipc.TICK_CONTEXT, b"\x03"):
                raise ValueError("component did not send a supported READY")
            if node.get("parameter_bindings") and body != b"\x03":
                raise ValueError("shared parameters require an updated component SDK; regenerate the container")
            return body in (ipc.TICK_CONTEXT, b"\x03")
        capable = await asyncio.wait_for(initialize(), 15)
        return process, capable
    except BaseException:
        await stop_component(process)
        raise


async def tick_events(process, node, selected, time_ns=None, tick_index=1, parameters=None):
    """Yield publications as they arrive; callers control their visibility."""
    if parameters is not None:
        await updates(process, node, parameters.drain())
    for port, record in selected.items():
        body = ipc.port_data(port, b"", "") if record is None else ipc.port_data(port, record["payload"], record["catalogue"])
        await ipc.send(process.stdin, "D", body)
    await ipc.send(process.stdin, "T", b"" if time_ns is None else struct.pack(">QQ", time_ns, tick_index))
    while True:
        op, body = await ipc.receive(process.stdout)
        if op == "S":
            await write_reply(process, body, parameters)
            continue
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
