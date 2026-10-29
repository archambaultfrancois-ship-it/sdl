import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ose.execution.component import ComponentSession, RecordResult
from ose.execution.callbacks import CallbackContext
from ose.execution.ipc import frame, port_data
from ose.recording.component import ComponentRecording, RemoteRecording, MAX_TICK_BYTES, validate_name


class RecordingRegistryTests(unittest.TestCase):
    def session(self, events=b""):
        self.output = io.BytesIO()
        return ComponentSession(io.BytesIO(frame("I", b"{}") + events), self.output, auto_ready=False)

    def test_namespaced_registry_duplicate_and_lifecycle_validation(self):
        session = self.session()
        for name in ("dsp.fft.intermediaire", "dsp.etat", "_debug.v2"):
            session.declare_recording_point(name, "Diagnostic", "SDL2|Diagnostic{}")
            self.assertFalse(session.recording_enabled(name))
        with self.assertRaisesRegex(ValueError, "unique"):
            session.declare_recording_point("dsp.etat", "Diagnostic", "cat")
        for name in ("", ".dsp", "dsp.", "dsp..fft", "dsp/fft", "dsp.1fft", "é", "x" * 201):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_name(name)
        session.ready()
        with self.assertRaisesRegex(ValueError, "initialization"):
            session.declare_recording_point("late", "Diagnostic", "cat")

    def test_activation_snapshot_budget_and_no_emission_when_disabled(self):
        session = self.session(frame("T") + frame("E", b"dsp.fft") + frame("T") +
                               frame("E") + frame("T") + frame("X"))
        session.declare_recording_point("dsp.fft", "Diagnostic", "cat")
        session.ready()
        ticks = session.ticks()
        next(ticks)
        self.assertEqual(session.record("dsp.fft", b"payload"), RecordResult.DISABLED)
        next(ticks)
        self.assertTrue(session.recording_enabled("dsp.fft"))
        self.assertEqual(session.record("dsp.fft", b"a" * MAX_TICK_BYTES), RecordResult.DROPPED)
        self.assertEqual(session.record("dsp.fft", b"payload"), RecordResult.QUEUED)
        next(ticks)
        self.assertFalse(session.recording_enabled("dsp.fft"))
        with self.assertRaises(StopIteration):
            next(ticks)
        self.assertEqual(self.output.getvalue().count(frame("C", port_data("dsp.fft", b"payload"))), 1)
        with self.assertRaisesRegex(RuntimeError, "active tick"):
            session.record("dsp.fft", b"payload")

    def test_unknown_and_duplicate_activation_is_rejected(self):
        for active in (b"other", b"dsp.fft\0dsp.fft", b"dsp.fft\0"):
            session = self.session(frame("E", active) + frame("T"))
            session.declare_recording_point("dsp.fft", "Diagnostic", "cat")
            session.ready()
            with self.subTest(active=active), self.assertRaisesRegex(ValueError, "active recording"):
                next(session.ticks())

    def test_disabled_typed_point_never_encodes_and_wrong_type_is_rejected(self):
        class Diagnostic: pass
        codec = SimpleNamespace(api=SimpleNamespace(message_name=lambda cls: "Diagnostic",
            description=lambda classes: "catalogue", prepare=lambda *args: object(), encode=Mock()))
        session = self.session()
        session.config.update(inputs={}, outputs={})
        ctx = CallbackContext(session, "source", {}, {}, codec)
        ctx.declare_recording_point("dsp.fft", Diagnostic)
        ctx._active = True
        self.assertEqual(ctx.record("dsp.fft", Diagnostic()), RecordResult.DISABLED)
        codec.api.encode.assert_not_called()
        with self.assertRaises(TypeError):
            ctx.record("dsp.fft", object())


class ComponentEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_declarations_validated_before_registration_and_disabled_in_simulation(self):
        codec = SimpleNamespace(api=SimpleNamespace(prepare=lambda cat: cat,
                                                   message_names=lambda cat: ["Diagnostic"]))
        endpoint = ComponentRecording(codec)
        declaration = port_data("dsp.fft", b"Diagnostic\0description\0catalogue")
        await endpoint.declare(declaration)
        self.assertEqual(endpoint.activation(), b"")
        endpoint.emit("dsp.fft", b"value")  # Simulation consumes diagnostics without publication.
        with self.assertRaisesRegex(ValueError, "duplicate"):
            await endpoint.declare(declaration)
        with self.assertRaisesRegex(ValueError, "root"):
            await endpoint.declare(port_data("dsp.bad", b"Other\0\0catalogue"))
        self.assertNotIn("dsp.bad", endpoint.points)

    async def test_adapter_queue_is_bounded_without_awaiting_network(self):
        remote = RemoteRecording("source", None, Mock())
        for _ in range(1000):
            remote.emit("dsp.fft", {}, b"value")
        self.assertEqual(remote.queue.qsize(), 64)
        self.assertEqual(remote.bytes, 64 * 5)
        self.assertEqual(remote.counts["dropped"], 936)
