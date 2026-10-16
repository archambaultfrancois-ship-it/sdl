"""Bounded RAM queues with direct lookups and an indexed retry schedule."""
from collections import OrderedDict
import asyncio
import heapq


class MemoryStore:
    def __init__(self, capacity=1024, seen_limit=4096):
        self.capacity, self.seen_limit = capacity, seen_limit
        self.items = OrderedDict()
        self.seen = OrderedDict()
        self.meta = {}
        self._deadlines = []
        self._versions = {}
        self._order = {}
        self._serial = 0
        self.changed = asyncio.Event()
        self.on_change = None

    def records(self):
        return list(self.items.values())

    def get(self, identity):
        return self.items.get(identity)

    def due(self, now, limit=None):
        selected, entries = [], []
        while self._deadlines and self._deadlines[0][0] <= now:
            entry = heapq.heappop(self._deadlines)
            _, _, version, identity = entry
            if self._versions.get(identity) != version:
                continue
            entries.append(entry)
            selected.append(self.items[identity])
            if limit is not None and len(selected) >= limit:
                break
        # Reading due records does not consume them: a blocked TCP write stays pending.
        for entry in entries:
            heapq.heappush(self._deadlines, entry)
        return selected

    def has_pending(self, identity):
        return identity in self.items

    def next_deadline(self):
        while self._deadlines and self._versions.get(self._deadlines[0][3]) != self._deadlines[0][2]:
            heapq.heappop(self._deadlines)
        return self._deadlines[0][0] if self._deadlines else None

    def notify(self):
        self.changed.set()
        if self.on_change is not None:
            self.on_change()

    def size(self):
        return len(self.items)

    def first(self, exclude=()):
        return next((record for identity, record in self.items.items() if identity not in exclude), None)

    def contains(self, identity):
        return identity in self.items or identity in self.seen

    def put(self, record):
        identity = record["id"]
        if identity not in self.items:
            if len(self.items) >= self.capacity:
                raise ValueError("RAM queue capacity exceeded")
            self._order[identity] = self._serial
        self.items[identity] = record
        self._schedule(record)
        self.notify()

    def update(self, record):
        self.put(record)

    def _schedule(self, record):
        identity = record["id"]
        self._serial += 1
        if "deadline" in record:
            self._versions[identity] = self._serial
            heapq.heappush(self._deadlines, (record["deadline"], self._order[identity], self._serial, identity))
        else:
            self._versions.pop(identity, None)
        self._compact()

    def _compact(self):
        # Stale timer entries never retain payloads or grow with lifetime traffic.
        if len(self._deadlines) > max(64, 2 * len(self.items)):
            self._deadlines = [(record["deadline"], self._order[identity], self._versions[identity], identity)
                               for identity, record in self.items.items() if identity in self._versions]
            heapq.heapify(self._deadlines)

    def remove(self, identity):
        self.items.pop(identity, None)
        self._versions.pop(identity, None)
        self._order.pop(identity, None)
        self._compact()
        self.notify()

    def remember(self, identity):
        self.seen[identity] = None
        if len(self.seen) > self.seen_limit:
            self.seen.popitem(last=False)

    def close(self):
        self.items.clear()
        self.seen.clear()
        self.meta.clear()
        self._deadlines.clear()
        self._versions.clear()
        self._order.clear()
        self.on_change = None
        self.changed.clear()
