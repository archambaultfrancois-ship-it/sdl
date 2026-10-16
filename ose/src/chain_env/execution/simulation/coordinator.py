"""Canonical logical waves, bounded parallel subprocess calculations and virtual time."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
import heapq
import json
from pathlib import Path
import signal
from chain_env.codec.sdl2 import SDL2
from chain_env.execution.process import start_component, tick_events, stop_component
from chain_env.execution.scheduler import reserve_snapshot
from chain_env.execution.simulation.clock import MAX_NS, VirtualClock, Schedule, seconds_ns
from chain_env.execution.simulation.router import Router
from chain_env.execution.simulation.scenario import read_scenario
from chain_env.execution.simulation.trace import Trace

DEFAULT_OUTPUT_LIMIT = 16 * 1024 * 1024


def topology(plan):
    parents = {name: set() for name in plan["components"]}
    for sub in plan["subscriptions"]:
        if "from_topic" not in sub:
            parents[sub["to"].split(".")[0]].add(sub["from"].split(".")[0])
    remaining, ordered, ancestors = {name: set(values) for name, values in parents.items()}, [], {}
    available = [name for name in parents if not parents[name]]
    heapq.heapify(available)
    while available:
        name = heapq.heappop(available)
        ordered.append(name)
        ancestors[name] = set().union(*(ancestors[p] | {p} for p in parents[name]))
        for child in sorted(remaining):
            if name in remaining[child]:
                remaining[child].remove(name)
                if not remaining[child]:
                    heapq.heappush(available, child)
    if len(ordered) != len(parents):
        raise ValueError("simulation graph contains a cycle")
    return ordered, ancestors


@dataclass
class PendingTick:
    selected: dict
    outputs: deque = field(default_factory=deque)
    publication: object = None


class Simulation:
    def __init__(self, plan, duration, *, workers=1, scenario=None, trace=None,
                 max_tick_output_bytes=DEFAULT_OUTPUT_LIMIT):
        if type(workers) is not int or workers <= 0:
            raise ValueError("workers must be a positive integer")
        if type(max_tick_output_bytes) is not int or max_tick_output_bytes <= 0:
            raise ValueError("max_tick_output_bytes must be a positive integer")
        self.plan, self.workers, self.limit = plan, workers, max_tick_output_bytes
        self.horizon, self.clock = seconds_ns(duration, positive=True), VirtualClock()
        self.order, self.ancestors = topology(plan)
        self.codec = SDL2(plan["sdl_root"])
        self.events = read_scenario(scenario, plan.get("external_topics", {}), self.codec)
        protected = {event.path for event in self.events}
        if scenario is not None:
            protected.add(Path(scenario).resolve())
        status_path = (Path(plan["build_dir"]) / "last-status.json").resolve()
        if status_path in protected:
            raise ValueError("scenario inputs cannot use the last-status.json diagnostic path")
        protected.add(status_path)
        if trace is not None and Path(trace).resolve() in protected:
            raise ValueError("trace cannot overwrite scenario inputs")
        # Validate virtual deadlines before opening diagnostics or spawning processes.
        for node in plan["components"].values():
            Schedule(node, {}, self.clock)
        for sub in plan["subscriptions"]:
            if sub["policy"]["overflow"] == "wait" and sub["policy"]["wait_timeout_ms"] * 1_000_000 > MAX_NS:
                raise ValueError("simulation admission wait exceeds uint64 nanoseconds")
        self.trace = Trace(self.clock, trace)
        try:
            self.router = Router(plan, self.codec, self.clock, self.trace)
        except BaseException:
            self.trace.close()
            raise
        self.schedules = {name: Schedule(plan["components"][name], self.router.buffers[name], self.clock)
                          for name in self.order}
        self.processes, self.busy, self.external = {}, {}, []
        self.external_bytes = 0
        self.cursor, self.waves, self.ticks = 0, 0, 0
        self.reason, self.summary = "failed", None

    async def _start(self, name):
        process, capable = await start_component(self.plan["components"][name])
        self.processes[name] = process
        if not capable:
            raise ValueError(f"{name}: simulated execution requires timestamped ticks; rebuild the container or update its SDK")

    async def _calculate(self, name, selected, semaphore):
        async with semaphore:
            outputs, size = deque(), 0
            schedule = self.schedules[name]
            async for port, payload in tick_events(self.processes[name], schedule.node, selected,
                                                  self.clock.time_ns, schedule.tick_index):
                size += len(payload) + len(port.encode()) + 3
                if size > self.limit:
                    raise ValueError(f"{name}: tick outputs exceed max_tick_output_bytes={self.limit}")
                desc = schedule.node["outputs"][port]
                body = self.codec.wrap(desc["catalogue"], payload)
                self.codec.validate(desc["catalogue"], desc["root"], body)
                outputs.append((port, body))
            return outputs

    async def _wave(self, selected):
        names = [name for name in self.order if name in selected and not (self.ancestors[name] & selected.keys())]
        self.waves += 1
        self.trace.emit("wave", components=names)
        for name in names:
            reserve_snapshot(self.router.buffers[name], selected[name])
            self.schedules[name].tick_index += 1
            self.trace.emit("tick", component=name, tick_index=self.schedules[name].tick_index,
                            inputs={port: None if record is None else record["id"] for port, record in selected[name].items()})
        semaphore = asyncio.Semaphore(self.workers)
        tasks = [asyncio.create_task(self._calculate(name, selected[name], semaphore)) for name in names]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        # No callback output becomes visible until every calculation in the wave succeeded.
        for name, outputs in zip(names, results):
            self.busy[name] = PendingTick(selected[name], deque(enumerate(outputs, 1)))

    async def _pump(self):
        changed = False
        processed = 0
        for publication in list(self.external):
            changed |= self.router.advance(publication)
            if not publication.pending:
                self.external.remove(publication)
                self.external_bytes -= len(publication.record["body"])
        for name in self.order:
            tick = self.busy.get(name)
            if tick is None:
                continue
            while True:
                if tick.publication is None and tick.outputs:
                    index, (port, body) = tick.outputs.popleft()
                    identity = f"{name}:{self.schedules[name].tick_index}:{index}"
                    tick.publication = self.router.publish(f"{name}.{port}", body, identity)
                    changed = True
                    processed += 1
                    if processed % 64 == 0:
                        await asyncio.sleep(0)
                if tick.publication is not None:
                    changed |= self.router.advance(tick.publication)
                    if tick.publication.pending:
                        break
                    tick.publication = None
                if not tick.outputs:
                    self.schedules[name].complete(tick.selected)
                    del self.busy[name]
                    self.ticks += 1
                    self.trace.emit("finished", component=name, tick_index=self.schedules[name].tick_index)
                    changed = True
                    break
        return changed

    async def _inject(self):
        while self.cursor < len(self.events) and self.events[self.cursor].at == self.clock.time_ns:
            event = self.events[self.cursor]
            publication = self.router.publish(event.topic, event.read(), f"scenario:{event.index}")
            self.router.advance(publication)
            if publication.pending:
                self.external_bytes += len(publication.record["body"])
                if self.external_bytes > self.limit:
                    raise ValueError("pending scenario publications exceed max_tick_output_bytes")
                self.external.append(publication)
            self.cursor += 1
            if self.cursor % 64 == 0:
                await asyncio.sleep(0)

    def _next_event(self):
        times = []
        if self.cursor < len(self.events):
            times.append(self.events[self.cursor].at)
        for name, schedule in self.schedules.items():
            if name not in self.busy and (timestamp := schedule.next_event()) is not None:
                times.append(timestamp)
        publications = self.external + [tick.publication for tick in self.busy.values() if tick.publication is not None]
        for publication in publications:
            times.extend(publication.pending.values())
        return min(times) if times else None

    def _snapshot(self):
        waiting = [f"{name}.{port}" for name, schedule in self.schedules.items()
                   for port, desc in schedule.node["inputs"].items()
                   if desc.get("required", True) and (port not in schedule.buffers or schedule.buffers[port].take() is None)]
        return dict(mode="simulated", functional=True, transport_simulation=False, running=False,
                    time_ns=self.clock.time_ns, ticks=self.ticks, waves=self.waves, publications=self.router.sequence,
                    stop_reason=self.reason, waiting_inputs=waiting, pending_ticks=len(self.busy),
                    pending_external=len(self.external),
                    inputs={name: {port: buffer.snapshot() for port, buffer in buffers.items()}
                            for name, buffers in self.router.buffers.items()}, trace_sha256=self.trace.digest.hexdigest())

    async def execute(self):
        starting = []
        try:
            starting = [asyncio.create_task(self._start(name)) for name in self.order]
            await asyncio.wait_for(asyncio.gather(*starting), 20)
            self.trace.emit("start", duration_ns=self.horizon, functional=True)
            while self.clock.time_ns < self.horizon:
                await self._inject()
                while await self._pump():
                    await asyncio.sleep(0)
                for name, process in self.processes.items():
                    if process.returncode is not None:
                        raise RuntimeError(f"component {name} stopped unexpectedly")
                selected = {name: snapshot for name, schedule in self.schedules.items()
                            if name not in self.busy and (snapshot := schedule.select()) is not None}
                if selected:
                    await self._wave(selected)
                    continue
                timestamp = self._next_event()
                if timestamp is None:
                    self.reason = "quiescent"
                    break
                if timestamp <= self.clock.time_ns:
                    raise RuntimeError("simulation made no logical progress")
                self.clock.time_ns = min(timestamp, self.horizon)
                await asyncio.sleep(0)  # Allow cancellation even in long, callback-free scenarios.
            else:
                self.reason = "duration"
        except asyncio.CancelledError:
            self.reason = "interrupted"
            raise
        finally:
            for task in starting:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*starting, return_exceptions=True)
            cleanup = await asyncio.gather(*(stop_component(p) for p in self.processes.values()), return_exceptions=True)
            cleanup_failed = any(isinstance(result, BaseException) for result in cleanup)
            if cleanup_failed:
                self.reason = "failed"
            self.trace.emit("stop", reason=self.reason)
            self.summary = self._snapshot()
            try:
                (Path(self.plan["build_dir"]) / "last-status.json").write_text(json.dumps(self.summary, indent=2) + "\n")
            finally:
                self.trace.close()
                self.router.close()
                self.busy.clear()
                self.external.clear()
                self.external_bytes = 0
            if cleanup_failed:
                raise RuntimeError("component cleanup failed")
        return self.summary


async def run(plan_path, duration, **options):
    plan_path = Path(plan_path).resolve()
    if options.get("trace") is not None and Path(options["trace"]).resolve() == plan_path:
        raise ValueError("trace cannot overwrite the compiled plan")
    engine = Simulation(json.loads(plan_path.read_text()), duration, **options)
    loop, interrupted = asyncio.get_running_loop(), False
    task = asyncio.create_task(engine.execute())
    def stop():
        nonlocal interrupted
        interrupted = True
        task.cancel()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop)
    try:
        return await task
    except asyncio.CancelledError:
        if not interrupted:
            raise
        return engine.summary
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
