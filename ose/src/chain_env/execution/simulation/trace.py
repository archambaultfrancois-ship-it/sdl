"""Streaming canonical logical traces: no wall clocks, process IDs or payload journal."""
import hashlib
import json


class Trace:
    def __init__(self, clock, path=None):
        self.clock, self.digest = clock, hashlib.sha256()
        self.stream = open(path, "w", encoding="utf-8") if path is not None else None

    def emit(self, event, **fields):
        line = json.dumps(dict(event=event, time_ns=self.clock.time_ns, **fields),
                          sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
        self.digest.update(line.encode())
        if self.stream:
            self.stream.write(line)

    def close(self):
        if self.stream:
            self.stream.close()
