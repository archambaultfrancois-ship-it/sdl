"""Generic remote recording without model files or generated application classes."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

from chain_env.codec.runtime import Runtime
from chain_env.transport.secure import connect, loopback, finish_cleanup


class Recorder:
    def __init__(self, host, port, *, context=None, distributed=False, capacity=256,
                 byte_budget=8 * 1024 * 1024, max_frame=1024 * 1024):
        self.host, self.port, self.context = host, port, context
        self.distributed, self.capacity, self.byte_budget = distributed, capacity, byte_budget
        self.max_frame = max_frame
        self.points, self.selected = {}, {}
        self.capture = None
        self.client = None

    async def connect(self):
        self.client = await connect(self.host, self.port, self.max_frame, self.context,
                                    "coordinator" if self.distributed else None)
        await self.refresh()

    async def refresh(self):
        points, offset = {}, 0
        while True:
            page, _ = await self.client.request("recording_points", offset=offset)
            self.max_frame = min(self.max_frame, page["max_frame"])
            points.update((point["topic"], point) for point in page["points"])
            if page["next"] is None:
                break
            offset = page["next"]
        self.points = points
        return points

    async def reconnect(self):
        await self.stop()
        if self.client:
            await self.client.close()
        await self.connect()

    async def describe(self, topic):
        result, body = await self.client.request("recording_describe", topic=topic)
        return {k: result[k] for k in ("topic", "root", "generation", "available", "endpoint")} | {"catalogue": body.decode()}

    def editable(self):
        if self.capture and not self.capture.task.done():
            raise ValueError("stop the recording before changing its selection or filters")

    async def set_filter(self, topic, expression):
        self.editable()
        if topic not in self.selected:
            raise ValueError("select the point first")
        if expression:
            desc = await self.describe(topic)
            predicate = prepare_filter(desc, expression)
            predicate.close()
        self.selected[topic] = expression

    async def start(self, path, *, format="pcapng", finished=None):
        self.editable()
        if not self.selected:
            raise ValueError("select at least one point")
        descriptions = {topic: await self.describe(topic) for topic in self.selected}
        capture = Capture(self, descriptions, path, format, finished)
        await capture.open()
        self.capture = capture
        capture.task = asyncio.create_task(capture.run())
        return capture

    async def stop(self):
        if self.capture:
            await self.capture.stop()
        return self.status()

    def status(self):
        if not self.capture:
            return {"state": "idle", "selected": dict(self.selected)}
        return self.capture.status()

    async def close(self):
        try:
            await self.stop()
        finally:
            if self.client:
                await self.client.close()


def prepare_filter(desc, expression):
    try:
        from sdl_filter import Filter
    except ImportError as error:
        raise ValueError("SDL filters are optional: build `make -C <SDL_ROOT> filter-python` "
                         "and add <SDL_ROOT>/runtime/filter/python and <SDL_ROOT>/build/filter/release to PYTHONPATH") from error
    return Filter(desc["catalogue"], desc["root"], expression)


class Capture:
    def __init__(self, recorder, descriptions, path, format, finished):
        self.recorder, self.descriptions = recorder, descriptions
        self.path, self.format = str(Path(path).resolve()), format
        self.filters = dict(recorder.selected)
        self.finished = finished
        self.feeds, self.pumps = [], []
        self.counts = Counter()
        self.topic_counts = Counter()
        self.state, self.error = "starting", None
        self.writer = None
        self.predicates, self.channels = {}, {}
        # All disk writes and predicate evaluation are serialized off the IO loop.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ose-recording")

    async def disk(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

    def prepare(self):
        try:
            from sdl_recording import RecordingWriter
        except ImportError as error:
            raise ValueError("install the optional recording dependency: pip install 'sdl-chain[recording]'; "
                             "make the SDL Python runtime (including sdl_recording) available") from error
        for topic, expression in self.filters.items():
            if expression:
                self.predicates[topic] = prepare_filter(self.descriptions[topic], expression)
        self.runtime = Runtime()
        self.writer = RecordingWriter(self.path, format=self.format, metadata={
            "producer": "ose-recorder", "started_at_ns": str(time.time_ns()),
            "filters": json.dumps(self.filters, sort_keys=True),
            "timestamps": "recorder Unix arrival time; production time unavailable",
            "sequence": "source publication sequence modulo 2^32; generation in channel metadata"})
        for topic, desc in self.descriptions.items():
            self.channels[topic] = self.writer.register_channel(topic=topic, catalogue=desc["catalogue"],
                root_type=desc["root"], metadata={"ose.generation": desc["generation"],
                    "ose.filter": self.filters[topic] or "", "ose.payload": "original SDL2 bytes"})

    async def open(self):
        try:
            groups = {}
            for topic, desc in self.descriptions.items():
                if not desc["available"]:
                    raise ValueError(f"recording point unavailable: {topic}")
                endpoint = desc["endpoint"] or {"host": self.recorder.host, "port": self.recorder.port,
                                               "identity": "coordinator" if self.recorder.distributed else None}
                if self.recorder.distributed and self.recorder.context is None and not loopback(endpoint["host"]):
                    raise ValueError("insecure recording requires loopback worker endpoints")
                key = (endpoint["host"], endpoint["port"], endpoint["identity"])
                groups.setdefault(key, {})[topic] = desc["generation"]
            if len(groups) > 32:
                raise ValueError("at most 32 recording endpoints per session")
            # Refuse an existing path before opening remote taps; never overwrite.
            if Path(self.path).exists():
                raise FileExistsError(self.path)
            await self.disk(self.prepare)
            for (host, port, expected), points in groups.items():
                client = await connect(host, port, self.recorder.max_frame, self.recorder.context, expected)
                feed = {"client": client, "session": None, "stats": {}, "endpoint": f"{host}:{port}"}
                self.feeds.append(feed)
                result, _ = await client.request("recording_open", points=points,
                    capacity=self.recorder.capacity, byte_budget=self.recorder.byte_budget)
                feed["session"] = result["session"]
            self.state = "recording"
        except BaseException:
            self.error = "recording startup failed"
            await finish_cleanup(self.cleanup())
            raise

    def write(self, record, envelope):
        topic = record["topic"]
        desc = self.descriptions[topic]
        if record["generation"] != desc["generation"]:
            raise ValueError("recording source generation changed")
        identity, payload = self.runtime.api.unpack_envelope(envelope)
        if identity != self.runtime.identity(desc["catalogue"]):
            raise ValueError("recording catalogue changed")
        self.counts["received"] += 1
        predicate = self.predicates.get(topic)
        if predicate:
            result = predicate.evaluate_wire(payload)
            if result.status == -1:
                raise ValueError("SDL filter evaluation failed: " + result.diagnostic)
            if not result.matched:
                self.counts["indeterminate" if result.status == 2 else "filtered"] += 1
                return
        now = time.time_ns()
        self.writer.write_encoded(self.channels[topic], payload, log_time_ns=now,
                                  publish_time_ns=now, sequence=record["sequence"] % (2**32))
        self.counts["written"] += 1
        self.topic_counts[topic] += 1

    async def pump(self, feed):
        while True:
            result, body = await feed["client"].request("recording_next", session=feed["session"])
            feed["stats"] = {k: result[k] for k in ("counts", "queued", "queued_bytes", "stopped", "capture_error")}
            if result["capture_error"]:
                raise ValueError(result["capture_error"])
            if result.get("empty"):
                if result["stopped"]:
                    return
            else:
                await self.disk(self.write, result["record"], body)

    async def run(self):
        self.pumps = [asyncio.create_task(self.pump(feed)) for feed in self.feeds]
        try:
            await asyncio.gather(*self.pumps)
        except asyncio.CancelledError:
            self.error = self.error or "recording interrupted before draining"
        except Exception as error:
            self.error = str(error) or type(error).__name__
        finally:
            for pump in self.pumps:
                pump.cancel()
            await asyncio.gather(*self.pumps, return_exceptions=True)
            await finish_cleanup(self.cleanup())
            self.state = "failed" if self.error else "stopped"
            if self.finished:
                self.finished(self.status())

    def finish(self):
        try:
            if self.writer:
                try:
                    self.writer.write_metadata("ose.session", {"error": self.error or "", "status": json.dumps(self.status())})
                finally:
                    self.writer.close()
        finally:
            for predicate in self.predicates.values():
                predicate.close()

    async def cleanup(self):
        for feed in self.feeds:
            try:
                if feed["session"] is not None:
                    stats, _ = await feed["client"].request("recording_close", session=feed["session"], timeout=2)
                    feed["stats"] = stats
            except Exception as error:
                self.error = self.error or f"capture statistics incomplete: {error}"
            finally:
                try:
                    await feed["client"].close()
                except Exception as error:
                    self.error = self.error or f"capture connection close failed: {error}"
        self.state = "failed" if self.error else "stopped"
        try:
            await self.disk(self.finish)
        except Exception as error:
            self.error = self.error or f"recording finalization failed: {error}"
        finally:
            self.executor.shutdown(wait=True)

    async def stop(self):
        if self.task.done():
            await self.task
            return
        self.state = "stopping"
        try:
            for feed in self.feeds:
                await feed["client"].request("recording_stop", session=feed["session"])
        except Exception as error:
            self.error = str(error) or type(error).__name__
            self.task.cancel()
        await self.task

    def status(self):
        remote = Counter()
        for feed in self.feeds:
            remote.update(feed["stats"].get("counts", {}))
        return {"state": self.state, "file": self.path, "format": self.format,
                "counts": dict(self.counts), "topics": dict(self.topic_counts),
                "capture_counts": dict(remote), "error": self.error,
                "selected": dict(self.filters)}
