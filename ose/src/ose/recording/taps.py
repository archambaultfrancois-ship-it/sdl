"""Read-only publication taps with per-session filters before queue admission.

All methods run on the application's event loop. Observing a publication never
awaits a recorder: queue overflow drops the oldest capture and increments counters.
"""
import asyncio
from collections import Counter, deque
import uuid

from ose.transport.protocol import encode
from ose.codec.runtime import Runtime
from ose.recording import DEFAULT_LABEL, session_label
from ose.recording.filters import prepare_filter


OPERATIONS = {"recording_points", "recording_describe", "recording_open",
              "recording_next", "recording_stop", "recording_close"}


class TapHub:
    MAX_BYTES = 64 * 1024 * 1024
    MAX_SESSIONS = 16

    def __init__(self, points, max_frame, *, codec=None):
        self.points, self.max_frame = points, max_frame
        self.codec = codec
        self.sessions = {}

    async def request(self, peer, header):
        op = header["op"]
        if op == "recording_points":
            offset = header.get("offset", 0)
            if type(offset) is not int or offset < 0:
                raise ValueError("invalid recording point offset")
            points = sorted(self.points().values(), key=lambda point: point["topic"])
            page = [{k: v for k, v in point.items() if k != "catalogue"}
                    for point in points[offset:offset + 100]]
            return {"points": page, "next": offset + len(page) if offset + len(page) < len(points) else None,
                    "max_frame": self.max_frame}, None
        if op == "recording_describe":
            point = self.points()[header["topic"]]
            return {k: v for k, v in point.items() if k != "catalogue"}, point["catalogue"].encode()
        if op == "recording_open":
            label = session_label(header.get("label", DEFAULT_LABEL))
            selections = header.get("points")
            filters = header.get("filters", {})
            capacity, budget = header.get("capacity", 256), header.get("byte_budget", 8 * 1024 * 1024)
            if not isinstance(selections, dict) or not 1 <= len(selections) <= 128:
                raise ValueError("select 1..128 recording points per connection")
            if not isinstance(filters, dict) or any(topic not in selections or
                    not isinstance(expression, str) or not expression for topic, expression in filters.items()):
                raise ValueError("recording filters must map selected topics to nonempty expressions")
            if type(capacity) is not int or not 1 <= capacity <= 4096:
                raise ValueError("recording capacity must be in 1..4096")
            if type(budget) is not int or not 1024 <= budget <= self.MAX_BYTES:
                raise ValueError("invalid recording byte budget")
            if len(self.sessions) >= self.MAX_SESSIONS or sum(s["budget"] for s in self.sessions.values()) + budget > self.MAX_BYTES:
                raise ValueError("recording service capacity exhausted")
            points = self.points()
            for topic, expected in selections.items():
                point = points[topic]
                if not point["available"] or expected != point["generation"]:
                    raise ValueError("recording point unavailable or changed; refresh discovery")
            predicates, identities = {}, {}
            try:
                if filters and self.codec is None:
                    self.codec = Runtime()
                for topic, expression in filters.items():
                    predicates[topic] = prepare_filter(points[topic], expression,
                        sdl_root=getattr(self.codec, "root", None))
                    identities[topic] = self.codec.identity(points[topic]["catalogue"])
            except Exception:
                for predicate in predicates.values():
                    predicate.close()
                raise
            token = uuid.uuid4().hex
            self.sessions[token] = dict(peer=peer, label=label, points={t: points[t] for t in selections},
                capacity=capacity, budget=budget, queue=deque(), bytes=0, active=True,
                predicates=predicates, identities=identities,
                changed=asyncio.Event(), polling=False, error=None, counts=Counter())
            return {"session": token, "label": label, "filtering": "source"}, None
        session = self.sessions[header["session"]]
        if session["peer"] is not peer:
            raise ValueError("recording session belongs to another connection")
        if op == "recording_close":
            self.sessions.pop(header["session"])
            self.deactivate(session)
            return self.stats(session), None
        if op == "recording_stop":
            self.deactivate(session)
            return self.stats(session), None
        if op != "recording_next":
            raise ValueError("unknown recording operation")
        if session["polling"]:
            raise ValueError("one recording poll at a time")
        session["polling"] = True
        try:
            session["changed"].clear()
            if not session["queue"] and session["active"]:
                try:
                    await asyncio.wait_for(session["changed"].wait(), .25)
                except asyncio.TimeoutError:
                    pass
            stats = self.stats(session)
            if not session["queue"]:
                return {"empty": True, **stats}, None
            metadata, body = session["queue"].popleft()
            session["bytes"] -= len(body)
            session["counts"]["delivered"] += 1
            return {"record": metadata, **self.stats(session)}, body
        finally:
            session["polling"] = False

    @staticmethod
    def stats(session):
        return {"label": session["label"], "counts": dict(session["counts"]), "queued": len(session["queue"]),
                "queued_bytes": session["bytes"], "stopped": not session["active"], "capture_error": session["error"]}

    @staticmethod
    def deactivate(session):
        session["active"] = False
        for predicate in session["predicates"].values():
            predicate.close()
        session["predicates"].clear()
        session["changed"].set()

    def observe(self, topic, body, *, generation, sequence, catalogue, root):
        # Keep the hot path constant when nobody is recording.
        if not self.sessions:
            return
        metadata = {"topic": topic, "generation": generation, "sequence": sequence}
        for session in self.sessions.values():
            expected = session["points"].get(topic)
            if not session["active"] or expected is None:
                continue
            session["counts"]["observed"] += 1
            if (catalogue, root, generation) != (expected["catalogue"], expected["root"], expected["generation"]):
                session["error"] = "recording point changed; stop and refresh discovery"
                self.deactivate(session)
                continue
            predicate = session["predicates"].get(topic)
            if predicate is not None:
                try:
                    identity, payload = self.codec.api.unpack_envelope(body)
                    if identity != session["identities"][topic]:
                        raise ValueError("recording catalogue identity mismatch")
                    result = predicate.evaluate_wire(payload)
                    if result.status == -1:
                        raise ValueError(result.diagnostic)
                    if not result.matched:
                        session["counts"]["indeterminate" if result.status == 2 else "filtered"] += 1
                        continue
                except Exception as error:
                    session["counts"]["filter_errors"] += 1
                    session["error"] = f"SDL source filter evaluation failed on {topic}: {error}"
                    self.deactivate(session)
                    continue
            try:
                # Replies add control fields; reserve room for them before admission.
                encode({"op": "result", "request": 2**63, "record": metadata,
                        "label": session["label"], "counts": {}, "padding": " " * 512}, body, self.max_frame)
            except ValueError:
                session["counts"]["oversize"] += 1
                continue
            if len(body) > session["budget"]:
                session["counts"]["dropped"] += 1
                continue
            while session["queue"] and (len(session["queue"]) >= session["capacity"] or session["bytes"] + len(body) > session["budget"]):
                _, old = session["queue"].popleft()
                session["bytes"] -= len(old)
                session["counts"]["dropped"] += 1
            session["queue"].append((metadata, body))
            session["bytes"] += len(body)
            session["changed"].set()

    def disconnected(self, peer):
        for token, session in list(self.sessions.items()):
            if session["peer"] is peer:
                self.sessions.pop(token)
                self.deactivate(session)

    def close(self):
        for session in self.sessions.values():
            self.deactivate(session)
        self.sessions.clear()
