"""Generic remote recording without model files or generated application classes."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import time

from ose.codec.runtime import Runtime
from ose.recording import DEFAULT_LABEL, session_label
from ose.recording.filters import prepare_filter
from ose.recording.rotation import OutputFiles, positive_setting
from ose.transport.secure import connect, loopback, finish_cleanup


class Recorder:
    def __init__(self, host, port, *, context=None, distributed=False, capacity=256,
                 byte_budget=8 * 1024 * 1024, max_frame=1024 * 1024, label=DEFAULT_LABEL,
                 max_size=None, cycle=None):
        self.host, self.port, self.context = host, port, context
        self.distributed, self.capacity, self.byte_budget = distributed, capacity, byte_budget
        self.max_frame = max_frame
        self.label = session_label(label)
        self.max_size = positive_setting(max_size, "max-size")
        self.cycle = positive_setting(cycle, "cycle")
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
        return {k: result[k] for k in ("topic", "root", "generation", "available", "endpoint",
                "kind", "component", "name", "description") if k in result} | {"catalogue": body.decode()}

    def editable(self):
        if self.capture and not self.capture.task.done():
            raise ValueError("stop the recording before changing its settings, selection or filters")

    async def set_filter(self, topic, expression):
        self.editable()
        if topic not in self.selected:
            raise ValueError("select the point first")
        if expression:
            desc = await self.describe(topic)
            predicate = prepare_filter(desc, expression)
            predicate.close()
        self.selected[topic] = expression

    def set_label(self, label):
        self.editable()
        self.label = session_label(label)

    def set_max_size(self, size):
        self.editable()
        self.max_size = positive_setting(size, "max-size")

    def set_cycle(self, count):
        self.editable()
        self.cycle = positive_setting(count, "cycle")

    async def start(self, path, *, format="pcapng", finished=None):
        self.editable()
        if not self.selected:
            raise ValueError("select at least one point")
        if self.cycle is not None and self.max_size is None:
            raise ValueError("cycle requires an active max-size")
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
            return {"state": "idle", "label": self.label, "max_size": self.max_size,
                    "cycle": self.cycle, "selected": dict(self.selected)}
        return self.capture.status()

    async def close(self):
        try:
            await self.stop()
        finally:
            if self.client:
                await self.client.close()


class Capture:
    def __init__(self, recorder, descriptions, path, format, finished):
        self.recorder, self.descriptions = recorder, descriptions
        self.max_size, self.cycle = recorder.max_size, recorder.cycle
        self.files = OutputFiles(path, numbered=self.max_size is not None, cycle=self.cycle)
        self.path, self.format = str(self.files.path), format
        self.label = recorder.label
        self.filters = dict(recorder.selected)
        self.finished = finished
        self.feeds, self.pumps = [], []
        self.counts = Counter()
        self.topic_counts = Counter()
        self.state, self.error = "starting", None
        self.writer = None
        self.channels = {}
        self.file_messages = 0
        self.file_topics = Counter()
        self.file_bytes = self.pending_bytes = 0
        # Disk writes are serialized off the IO loop; filters run at the source.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ose-recording")

    async def disk(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self.executor, function, *args)

    def prepare(self):
        try:
            from sdl_recording import RecordingWriter
        except ImportError as error:
            raise ValueError("install the optional recording dependency: pip install 'ose[recording]'; "
                             "make the SDL Python runtime (including sdl_recording) available") from error
        self.runtime = Runtime()
        stream = self.files.open_next()
        self.path = str(self.files.path)
        self.file_messages = 0
        self.file_topics.clear()
        self.file_bytes = self.pending_bytes = 0
        self.channels.clear()
        self.writer = RecordingWriter(stream, format=self.format, metadata={
            "producer": "ose-recorder", "label": self.label, "started_at_ns": str(time.time_ns()),
            "filters": json.dumps(self.filters, sort_keys=True), "filtering": "source",
            "timestamps": "recorder Unix arrival time; production time unavailable",
            "sequence": "source publication sequence modulo 2^32; generation in channel metadata",
            "base_file": str(self.files.base), "file_index": str(self.files.index),
            "max_size": str(self.max_size or "off"), "cycle": str(self.cycle or "off")})
        for topic, desc in self.descriptions.items():
            self.channels[topic] = self.writer.register_channel(topic=topic, catalogue=desc["catalogue"],
                root_type=desc["root"], metadata={"ose.generation": desc["generation"],
                    "ose.filter": self.filters[topic] or "", "ose.filter_location": "source",
                    "ose.payload": "original SDL2 bytes"})
        self.file_bytes = stream.tell()

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
            self.files.preflight()
            await self.disk(self.prepare)
            for (host, port, expected), points in groups.items():
                client = await connect(host, port, self.recorder.max_frame, self.recorder.context, expected)
                feed = {"client": client, "session": None, "stats": {}, "endpoint": f"{host}:{port}"}
                self.feeds.append(feed)
                filters = {topic: self.filters[topic] for topic in points if self.filters[topic]}
                result, _ = await client.request("recording_open", points=points,
                    filters=filters, label=self.label,
                    capacity=self.recorder.capacity, byte_budget=self.recorder.byte_budget)
                feed["session"] = result["session"]
                if filters and result.get("filtering") != "source":
                    raise ValueError("recording source does not support source filtering; upgrade the broker/worker")
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
        if self.max_size is not None and self.file_messages and self.file_bytes >= self.max_size:
            # Detect collisions while the current writer can still record the
            # failure in its closing metadata. open_next also claims atomically.
            self.files.check_next()
            self.finish_file(final=False)
            self.prepare()
        now = time.time_ns()
        self.writer.write_encoded(self.channels[topic], payload, log_time_ns=now,
                                  publish_time_ns=now, sequence=record["sequence"] % (2**32))
        self.counts["written"] += 1
        self.topic_counts[topic] += 1
        self.file_messages += 1
        self.file_topics[topic] += 1
        if self.max_size is not None:
            if self.format == "pcapng":
                self.file_bytes = self.files.stream.tell()
            else:
                # MCAP buffers whole chunks. Flush the first message to include
                # declarations, then only near the threshold; never fsync here.
                self.pending_bytes += len(payload) + 64
                if self.file_messages == 1 or self.files.stream.tell() + self.pending_bytes >= self.max_size:
                    self.writer.flush()
                    self.pending_bytes = 0
                self.file_bytes = self.files.stream.tell()

    async def pump(self, feed):
        while True:
            result, body = await feed["client"].request("recording_next", session=feed["session"])
            point_counts = {**feed["stats"].get("point_counts", {}), **result.get("point_counts", {})}
            feed["stats"] = {k: result[k] for k in ("counts", "queued", "queued_bytes", "stopped", "capture_error")}
            feed["stats"]["point_counts"] = point_counts
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

    def finish_file(self, *, final):
        writer, self.writer = self.writer, None
        if writer is not None:
            try:
                writer.write_metadata("ose.session", {"label": self.label, "error": self.error or "",
                    "final": "true" if final else "false", "status": json.dumps(self.status())})
            finally:
                try:
                    writer.close()
                finally:
                    self.files.close()
        else:
            self.files.close()

    def finish(self):
        self.finish_file(final=True)

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
        point_counts = {}
        for feed in self.feeds:
            remote.update(feed["stats"].get("counts", {}))
            for topic, counters in feed["stats"].get("point_counts", {}).items():
                point_counts.setdefault(topic, Counter()).update(counters)
        counts = self.counts.copy()
        for key in ("filtered", "indeterminate", "filter_errors"):
            if key in remote:
                counts[key] += remote[key]
        return {"state": self.state, "label": self.label, "file": self.path, "format": self.format,
                "base_file": str(self.files.base), "max_size": self.max_size, "cycle": self.cycle,
                **self.files.status(), "file_message_count": self.file_messages, "file_topics": dict(self.file_topics),
                "counts": dict(counts), "topics": dict(self.topic_counts),
                "capture_counts": dict(remote), "error": self.error,
                "point_counts": {topic: dict(counts) for topic, counts in point_counts.items()},
                "selected": dict(self.filters)}
