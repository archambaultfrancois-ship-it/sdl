"""Bounded parameter JSON input, shared by imports and directory sync."""
import json
import sys
from pathlib import Path
from ose.modeling.model import unique_pairs

MAX_JSON_SIZE = 16 * 1024 * 1024
MAX_TOTAL_JSON_SIZE = 64 * 1024 * 1024


def read_source(source, max_size=MAX_JSON_SIZE):
    if max_size <= 0:
        raise ValueError('maximum JSON size must be positive')
    if source == '-':
        data = sys.stdin.buffer.read(max_size + 1)
    else:
        with Path(source).open('rb') as stream:
            data = stream.read(max_size + 1)
    if len(data) > max_size:
        raise ValueError(f'parameter JSON exceeds {max_size} bytes')
    return data


def read_value(source, max_size=MAX_JSON_SIZE):
    return json.loads(read_source(source, max_size).decode('utf-8'), object_pairs_hook=unique_pairs)


def validate_import(value):
    if (not isinstance(value, dict) or len(value) > 4096 or
            any(not path or not isinstance(block, dict) for path, block in value.items())):
        raise ValueError('parameter import requires a JSON object {path: complete block}, at most 4096 blocks')
