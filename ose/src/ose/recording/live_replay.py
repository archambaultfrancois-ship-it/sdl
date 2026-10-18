"""Publish preflighted recordings to a live local broker or coordinator."""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import json
import math
from pathlib import Path
import sys
import time
import uuid

from ose.codec.sdl2 import SDL2
from ose.execution.simulation.clock import seconds_ns
from ose.recording.command_input import CommandInput
from ose.recording.pacing import PlaybackClock, playback_speed
from ose.recording.replay import RepeatedEvents, loop_count, recording_events, topic_mapping
from ose.transport.secure import connect, contexts, finish_cleanup
from ose.transport.udp import UDP


@asynccontextmanager
async def publisher(plan, topics, *, host, port, distributed=False, context=None):
    client, udp, token = None, None, None
    try:
        client = await connect(host, port, plan["max_frame"], context,
                               "coordinator" if distributed else None)
        client.request_timeout = 15 + max((sub["policy"]["wait_timeout_ms"] / 1000
            for sub in plan["subscriptions"] if sub["policy"]["overflow"] == "wait"), default=0)
        if distributed:
            from ose.execution.distributed import fingerprint
            result, _ = await client.request("external_open", topics=sorted(topics), plan_id=fingerprint(plan))
            token = result["token"]
            # READY precedes the first activation heartbeat. Wait until existing
            # workers actually run their schedulers, so startup does not fill
            # and overflow input queues before any callback can consume them.
            deadline = time.monotonic() + 20
            while True:
                status, _ = await client.request("status")
                if all(node.get("metrics", {}).get("running") for node in status["components"].values()):
                    break
                if time.monotonic() >= deadline:
                    raise ValueError("workers did not start processing within 20 seconds")
                await asyncio.sleep(.05)
        else:
            async def ignore(*args):
                pass
            udp = await UDP.bind(ignore)
            result, _ = await client.request("client_hello", client_id="replay-" + uuid.uuid4().hex,
                                              udp_port=udp.address[1])
            client.tcp.limit = min(client.tcp.limit, result["max_frame"])
            client.request_timeout = 15 + result["max_wait_timeout_ms"] / 1000
            for topic in topics:
                desc = plan["external_topics"][topic]
                await client.request("declare_publisher", desc["catalogue"].encode(), topic=topic, root=desc["root"])

        async def publish(topic, body):
            result, _ = (await client.request("external_publish", body, token=token, topic=topic)
                         if distributed else await client.request("publish", body, topic=topic))
            return result["delivery"]
        yield publish
    finally:
        async def close():
            if client is not None:
                try:
                    if token is not None:
                        await client.request("external_close", token=token, timeout=.5)
                except (OSError, EOFError, ValueError, asyncio.TimeoutError):
                    pass
                finally:
                    await client.close()
            if udp is not None:
                await udp.close()
        await finish_cleanup(close())


CONTROL_HELP = "Replay commands: speed N, pause, resume, wait SECONDS, status, stop (EOF stops)."


async def execute_control(line, clock, status, output):
    parts = line.split()
    if not parts or parts[0].startswith("#"):
        return True
    if parts[0] == "speed" and len(parts) == 2:
        clock.set_speed(float(parts[1]))
    elif parts == ["pause"]:
        clock.pause()
    elif parts == ["resume"]:
        clock.resume()
    elif parts[0] == "wait" and len(parts) == 2:
        seconds = float(parts[1])
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("wait requires finite nonnegative seconds")
        await asyncio.sleep(seconds)
    elif parts in (["stop"], ["quit"]):
        clock.stop()
    elif parts == ["help"]:
        print(CONTROL_HELP, file=output, flush=True)
    elif parts != ["status"]:
        raise ValueError("expected speed N, pause, resume, wait SECONDS, status or stop")
    print(json.dumps({**status, "speed": clock.speed, "paused": clock.paused}), file=output, flush=True)
    return not clock.stopped


async def controls(clock, status, output, commands=None):
    source = CommandInput(output=output) if commands is None else None
    try:
        if commands is not None:
            for line in commands:
                if not await execute_control(line, clock, status, output):
                    break
            clock.stop()
            return
        print(CONTROL_HELP, file=output, flush=True)
        while not clock.stopped:
            line = await source.readline("replay> ")
            if not line:
                clock.stop()
                break
            try:
                if not await execute_control(line, clock, status, output):
                    break
            except ValueError as error:
                print(f"replay: {error}", file=output, flush=True)
    except BaseException:
        clock.stop()
        raise
    finally:
        if source is not None:
            source.close()


async def play(events, publish, *, speed=1, duration=None, loops=1, interactive=False, commands=None, output=None):
    output = sys.stderr if output is None else output
    clock = PlaybackClock(speed)
    horizon = seconds_ns(duration, positive=True) if duration is not None else None
    repeated = RepeatedEvents(events, loops)
    status = dict(published=0, refused=0, loops=loops, current_loop=1, completed_loops=0,
                  last_lag_seconds=0.0, max_lag_seconds=0.0)
    deliveries, started, last_report = Counter(), time.monotonic(), time.monotonic()
    control = (asyncio.create_task(controls(clock, status, output, commands))
               if interactive or commands is not None else None)
    reason = "complete"
    try:
        if commands is not None:
            # Apply leading speed/pause commands before the first publication.
            await asyncio.sleep(0)
        for index, event in enumerate(repeated):
            status["current_loop"] = index // len(events) + 1
            if horizon is not None and event.at >= horizon:
                reason = "duration"
                break
            lag = await clock.wait(event.at)
            if lag is None:
                reason = "stopped"
                break
            status["last_lag_seconds"] = lag
            status["max_lag_seconds"] = max(lag, status["max_lag_seconds"])
            deliveries.update((await publish(event.topic, event.read())).values())
            status["published"] += 1
            if (index + 1) % len(events) == 0:
                status["completed_loops"] += 1
            status["refused"] = sum(count for result, count in deliveries.items()
                                    if result not in ("accepted", "duplicate", "stale"))
            if lag > .05 and time.monotonic() - last_report >= 1:
                print(f"replay: lag {lag:.3f}s; published {status['published']}; refused {status['refused']}",
                      file=output, flush=True)
                last_report = time.monotonic()
            if (index + 1) % len(events) == 0:
                # Even a one-message, unlimited-speed replay must yield to
                # scripts, interactive commands and cancellation at each loop.
                await asyncio.sleep(0)
        if control is not None and control.done():
            control.result()
        return {"mode": "realtime", "stop_reason": reason, **status, "speed": clock.speed,
                "delivery_results": dict(deliveries), "elapsed_seconds": time.monotonic() - started}
    finally:
        if control is not None:
            control.cancel()
            await asyncio.gather(control, return_exceptions=True)


async def run(path, plan_path, duration=None, *, mappings, speed=1, start_ns=None, end_ns=None,
              endpoint=None, host=None, port=None, distributed=False, tls_ca=None, tls_cert=None,
              tls_key=None, insecure=False, interactive=False, commands=None, loops=1):
    playback_speed(speed)
    loop_count(loops)
    if duration is not None:
        seconds_ns(duration, positive=True)
    plan_path, path = Path(plan_path).resolve(), Path(path).resolve()
    plan = json.loads(plan_path.read_text())
    if path in (plan_path, (Path(plan["build_dir"]) / "last-status.json").resolve()):
        raise ValueError("recording cannot use the compiled plan or last-status.json path")
    topics = plan.get("external_topics", {})
    mapping = topic_mapping(mappings, topics)
    codec = SDL2(plan["sdl_root"])
    distributed = distributed or plan.get("execution") == "distributed" or insecure or any((tls_ca, tls_cert, tls_key))
    if endpoint is not None and (host is not None or port is not None):
        raise ValueError("choose --endpoint or --host/--port")
    default_endpoint = Path(plan["build_dir"]) / "endpoint.json"
    if endpoint is not None or (not distributed and host is None and port is None and default_endpoint.is_file()):
        address = json.loads(Path(endpoint or default_endpoint).read_text())
        host, port = address["host"], address["port"]
    host, port = host or "127.0.0.1", 7000 if port is None else port
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError("port must be 1..65535")
    context = contexts(tls_ca, tls_cert, tls_key, insecure=insecure, hosts=[host])[1] if distributed else None
    with recording_events(path, topics, mapping, codec, start_ns=start_ns, end_ns=end_ns) as (events, selection):
        async with publisher(plan, mapping.values(), host=host, port=port,
                             distributed=distributed, context=context) as publish:
            result = await play(events, publish, speed=speed, duration=duration, loops=loops,
                                interactive=interactive, commands=commands)
        selection.update(loops=loops, loop_period_ns=events[-1].at + 1)
    result["replay"] = selection
    return result
