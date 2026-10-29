import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from ose.codec.sdl2 import SDL2
from ose.execution.ipc import frame, unpack_port
from ose.recording.component import declaration
from tests.internal_recording import build_component

SDL_ROOT = os.environ.get("SDL_ROOT")


def check_component(test, plan, codec):
    events = frame("T") + frame("E", b"dsp.fft.intermediaire\0dsp.etat") + frame("T") + \
             frame("E", b"dsp.etat") + frame("T") + frame("E") + frame("T") + frame("X")
    node = plan["components"]["source"]
    result = subprocess.run(node["command"], input=frame("I", json.dumps(node).encode()) + events,
                            capture_output=True, timeout=15, cwd=node["cwd"])
    test.assertEqual(result.returncode, 0, result.stderr.decode())
    stream, frames = io.BytesIO(result.stdout), []
    while stream.tell() < len(result.stdout):
        size, = struct.unpack(">I", stream.read(4)); body = stream.read(size)
        frames.append((chr(body[0]), body[1:]))
    test.assertEqual([op for op, _ in frames], ["N", "N", "R", "F", "C", "C", "F", "C", "F", "F"])
    points = dict(declaration(body) for op, body in frames if op == "N")
    test.assertEqual(points["dsp.fft.intermediaire"]["description"], "Spectre avant filtrage")
    samples = []
    for op, body in frames:
        if op == "C":
            name, payload = unpack_port(body)
            value = codec.api.decode_dynamic(codec.api.prepare(points[name]["catalogue"]), payload)
            samples.append((name, value.fields["sequence"]))
    test.assertEqual(samples, [("dsp.fft.intermediaire", 2), ("dsp.etat", 2), ("dsp.etat", 3)])


@unittest.skipUnless(SDL_ROOT, "set SDL_ROOT to an independent SDL checkout")
class ComponentRecordingSdkTests(unittest.TestCase):
    def test_python_c_and_rust_port_free_diagnostic_components(self):
        with tempfile.TemporaryDirectory() as directory:
            codec = SDL2(SDL_ROOT)
            for language in ("python", "c", "rust"):
                with self.subTest(language=language):
                    plan = build_component(Path(directory) / language, language, SDL_ROOT)
                    check_component(self, plan, codec)

    def test_diagnostic_only_schema_can_be_included_without_business_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            codec = SDL2(SDL_ROOT)
            for language in ("python", "c"):
                with self.subTest(language=language):
                    plan = build_component(Path(directory) / language, language, SDL_ROOT, extra_schema=True)
                    check_component(self, plan, codec)
