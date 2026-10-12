"""Catalogue-bound filters; native access preserves values at the evaluation point."""
from enum import IntEnum
from typing import NamedTuple
import _sdl_filter_native as _native

class Status(IntEnum):
    ERROR = -1
    NO_MATCH = 0
    MATCH = 1
    INDETERMINATE = 2

class Result(NamedTuple):
    status: Status
    diagnostic: str = ''
    @property
    def matched(self):
        return self.status == Status.MATCH

class Filter:
    def __init__(self, catalogue, root_type, expression):
        self._plan = _native.prepare(catalogue, root_type, expression)
        self.root_type = root_type
        self.expression = expression

    def evaluate(self, message):
        status, diagnostic = _native.evaluate(self._plan, message)
        return Result(Status(status), diagnostic)

    def evaluate_wire(self, data):
        status, diagnostic = _native.evaluate_wire(self._plan, data)
        return Result(Status(status), diagnostic)

    def close(self):
        self._plan = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

def encode_filtered(context, message, predicate):
    from sdl_runtime import encode
    result = predicate.evaluate(message)
    return encode(context, message), result

def decode_filtered(context, data, predicate, **options):
    from sdl_runtime import decode
    result = predicate.evaluate_wire(data)
    return decode(context, data, **options), result
