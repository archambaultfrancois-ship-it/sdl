"""Executable Java container boundary tests; toolchain failures are failures."""
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from chain_env.modeling.compiler import build, compile_plan
from chain_env.execution.ipc import frame, port_data, unpack_port
from chain_env.codec.sdl2 import SDL2
from generation_cases import check_generation

SDL = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[3]))


class JavaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'measurement.sdl').write_text('message Measurement {1: required uint64 sequence; 2: required fl64 value;}')
        self.model = {'version': 1, 'schemas': 'measurement.sdl', 'components': {
            'source': {'inputs': {}, 'outputs': {'out': {'root': 'Measurement'}}, 'trigger': {'mode': 'periodic', 'period_ms': 10}},
            'sink': {'inputs': {'input': {'root': 'Measurement', 'timeout': 0.1}}, 'outputs': {'result': {'root': 'Measurement'}}, 'trigger': {'mode': 'on_change'}}},
            'subscriptions': [{'id': 'link', 'from': 'source.out', 'to': 'sink.input'}]}
        self.deployment = {'version': 1, 'components': {name: {'container': {'language': 'java', 'handler': name.title() + '.java', 'class': name.title()}} for name in self.model['components']}}
        (self.root / 'model.json').write_text(json.dumps(self.model)); (self.root / 'deployment.json').write_text(json.dumps(self.deployment))
        (self.root / 'Source.java').write_text('''import org.sdlchain.generated.source.API;
public class Source extends API.Handler {
 public void onTick(API.Context ctx, API.Inputs inputs) throws Exception {
  sdl.globaltypes.measurement.Measurement m = new sdl.globaltypes.measurement.Measurement();
  m.sequence = ctx.tickIndex().longValue(); m.value = ctx.now(); ctx.publish_out(m);
 }
}''')
        (self.root / 'Sink.java').write_text('''import org.sdlchain.generated.sink.API;
public class Sink extends API.Handler {
 public void on_input(API.Context ctx, sdl.globaltypes.measurement.Measurement m) throws Exception {
  if (m == null) { m = new sdl.globaltypes.measurement.Measurement(); m.sequence = -1; m.value = -1; }
  ctx.publish_result(m);
 }
 public void onStop(API.Context ctx) { System.err.println("stopped"); }
}''')
        path = build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        self.plan = json.loads(path.read_text()); self.codec = SDL2(SDL)

    def run_node(self, name, data, init=b'{}'):
        result = subprocess.run(self.plan['components'][name]['command'], input=frame('I', init) + data + frame('X'), capture_output=True, timeout=15)
        stream = io.BytesIO(result.stdout); events = []
        while stream.tell() < len(result.stdout):
            size, = struct.unpack('>I', stream.read(4)); value = stream.read(size); events.append((chr(value[0]), value[1:]))
        return result, events

    def test_typed_tick_roundtrip_and_timeout(self):
        result, events = self.run_node('source', frame('T', struct.pack('>QQ', 2000000000, 2**64 - 1)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([op for op, _ in events], ['R', 'P', 'F']); self.assertEqual(events[0][1], b'\1')
        payload = unpack_port(events[1][1])[1]
        cat = self.plan['components']['source']['outputs']['out']['catalogue']
        value = self.codec.api.decode_dynamic(self.codec.api.prepare(cat), payload)
        self.assertEqual(value.fields, {'sequence': 2**64 - 1, 'value': 2.0})
        for data in (port_data('input', payload, cat), port_data('input', b'', '')):
            result, events = self.run_node('sink', frame('D', data) + frame('T'))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([op for op, _ in events], ['R', 'P', 'F']); self.assertIn(b'stopped', result.stderr)

    def test_invalid_protocol_never_completes(self):
        null = frame('D', port_data('input', b'', ''))
        cases = [frame('T'), null + null + frame('T'), frame('D', b'bad'), frame('T', b'bad'), frame('D', port_data('unknown', b'', ''))]
        for data in cases:
            result, events = self.run_node('sink', data)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual([op for op, _ in events], ['R'])
        result, events = self.run_node('source', b'', b'{invalid')
        self.assertNotEqual(result.returncode, 0); self.assertEqual(events, [])

    def test_rebuild_preserves_handler(self):
        before = (self.root / 'Source.java').read_bytes()
        build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        self.assertEqual((self.root / 'Source.java').read_bytes(), before)

    def test_snapshot_override_and_failed_callback_do_not_finish(self):
        sink = self.model['components']['sink']
        sink['inputs'].update(second={'root': 'Measurement'}, optional={'root': 'Measurement', 'required': False})
        self.model['subscriptions'].append({'id': 'second', 'from': 'source.out', 'to': 'sink.second'})
        (self.root / 'model.json').write_text(json.dumps(self.model))
        (self.root / 'Sink.java').write_text("""import org.sdlchain.generated.sink.API;
public class Sink extends API.Handler {
 public void onTick(API.Context ctx, API.Inputs inputs) throws Exception {
  if (inputs.optional != null || inputs.second == null) throw new AssertionError("invalid snapshot");
  ctx.publish_result(inputs.second);
  if (inputs.input == null) throw new Exception("expected callback failure");
 }
 public void onStop(API.Context ctx) { System.err.println("stopped"); }
}""")
        path = build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        self.plan = json.loads(path.read_text())
        _, source = self.run_node('source', frame('T'))
        payload = unpack_port(source[1][1])[1]; cat = self.plan['components']['source']['outputs']['out']['catalogue']
        for timed_out in (False, True):
            first = port_data('input', b'', '') if timed_out else port_data('input', payload, cat)
            events = frame('D', first) + frame('D', port_data('second', payload, cat)) + frame('T')
            result, frames = self.run_node('sink', events)
            self.assertEqual(result.returncode == 0, not timed_out, result.stderr)
            self.assertEqual([op for op, _ in frames], ['R', 'P'] if timed_out else ['R', 'P', 'F'])
            self.assertIn(b'stopped', result.stderr)


class JavaGenerationTests(unittest.TestCase):
    def test_packaged_scaffolds_multiple_schemas_and_space_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            check_generation(self, directory, SDL, 'java')
