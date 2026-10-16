"""Ada executable protocol tests; a missing GNAT toolchain is an explicit failure."""
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from chain_env.modeling.compiler import build
from chain_env.execution.ipc import frame, port_data, unpack_port
from chain_env.codec.sdl2 import SDL2
from generation_cases import check_generation

SDL = Path(os.environ.get('SDL_ROOT', Path(__file__).resolve().parents[3]))


class AdaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'measurement.sdl').write_text('message Measurement {1: required uint64 sequence; 2: required fl64 value;}')
        model = {'version': 1, 'schemas': 'measurement.sdl', 'components': {
            'source': {'inputs': {}, 'outputs': {'value': {'root': 'Measurement'}}, 'trigger': {'mode': 'periodic', 'period_ms': 10}},
            'sink': {'inputs': {'input': {'root': 'Measurement', 'timeout': 0.1}}, 'outputs': {'result': {'root': 'Measurement'}}, 'trigger': {'mode': 'on_change'}}},
            'subscriptions': [{'id': 'link', 'from': 'source.value', 'to': 'sink.input'}]}
        deployment = {'version': 1, 'components': {name: {'container': {'language': 'ada', 'handler': name + '_handler.adb', 'unit': name.title() + '_Handler'}} for name in model['components']}}
        for name, doc in [('model', model), ('deployment', deployment)]: (self.root / (name + '.json')).write_text(json.dumps(doc))
        build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        (self.root / 'source_handler.adb').write_text('''with SDL_measurement;
package body Source_Handler is
 overriding procedure On_Tick (Self : in out Object; Ctx : in out Chain_source.Context; Selected : Chain_source.Inputs) is
   M : SDL_measurement.T_Measurement;
 begin
   M.F_sequence := Chain_source.Tick_Index (Ctx); M.F_value := SDL_Runtime.Float64 (Chain_source.Now (Ctx));
   Chain_source.Publish_value (Ctx, M);
 end;
end Source_Handler;
'''.replace('with SDL_measurement;', 'with SDL_measurement; with SDL_Runtime;'))
        (self.root / 'sink_handler.adb').write_text('''with SDL_measurement; with Ada.Text_IO;
package body Sink_Handler is
 overriding procedure On_input (Self : in out Object; Ctx : in out Chain_sink.Context; Message : Chain_sink.Input_input) is
   use type Chain_sink.Input_input;
   M : SDL_measurement.T_Measurement;
 begin
   if Message /= null then M := Message.all; end if;
   Chain_sink.Publish_result (Ctx, M);
 end;
end Sink_Handler;
''')
        path = build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        self.plan = json.loads(path.read_text()); self.codec = SDL2(SDL)

    def run_node(self, name, events):
        result = subprocess.run(self.plan['components'][name]['command'], input=frame('I', b'{}') + events + frame('X'), capture_output=True, timeout=15)
        stream = io.BytesIO(result.stdout); frames = []
        while stream.tell() < len(result.stdout):
            size, = struct.unpack('>I', stream.read(4)); value = stream.read(size); frames.append((chr(value[0]), value[1:]))
        return result, frames

    def test_typed_ticks_timeout_and_rejection(self):
        result, events = self.run_node('source', frame('T', struct.pack('>QQ', 2000000000, 2**64 - 1)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([op for op, _ in events], ['R', 'P', 'F'])
        self.assertEqual(events[0][1], b'\1')
        payload = unpack_port(events[1][1])[1]; cat = self.plan['components']['source']['outputs']['value']['catalogue']
        value = self.codec.api.decode_dynamic(self.codec.api.prepare(cat), payload)
        self.assertEqual(value.fields, {'sequence': 2**64-1, 'value': 2.0})
        for data in (port_data('input', payload, cat), port_data('input', b'', '')):
            result, frames = self.run_node('sink', frame('D', data) + frame('T'))
            self.assertEqual(result.returncode, 0, result.stderr); self.assertEqual([op for op, _ in frames], ['R', 'P', 'F'])
        null = frame('D', port_data('input', b'', ''))
        for events in (frame('T'), null + null + frame('T'), frame('D', b'bad'), frame('T', b'bad')):
            result, frames = self.run_node('sink', events)
            self.assertNotEqual(result.returncode, 0); self.assertEqual([op for op, _ in frames], ['R'])

    def test_snapshot_override_and_failed_callback_do_not_finish(self):
        model = json.loads((self.root / 'model.json').read_text())
        model['components']['sink']['inputs'].update(second={'root': 'Measurement'}, optional={'root': 'Measurement', 'required': False})
        model['subscriptions'].append({'id': 'second', 'from': 'source.value', 'to': 'sink.second'})
        (self.root / 'model.json').write_text(json.dumps(model))
        (self.root / 'sink_handler.ads').write_text("""with Chain_sink;
package Sink_Handler is
 type Object is new Chain_sink.Handler with null record;
 overriding procedure On_Tick (Self : in out Object; Ctx : in out Chain_sink.Context; Selected : Chain_sink.Inputs);
end Sink_Handler;
""")
        (self.root / 'sink_handler.adb').write_text("""package body Sink_Handler is
 overriding procedure On_Tick (Self : in out Object; Ctx : in out Chain_sink.Context; Selected : Chain_sink.Inputs) is
  use type Chain_sink.Input_input;
  use type Chain_sink.Input_second;
  use type Chain_sink.Input_optional;
 begin
  if Selected.optional /= null or Selected.second = null then raise Program_Error with "invalid snapshot"; end if;
  Chain_sink.Publish_result (Ctx, Selected.second.all);
  if Selected.input = null then raise Program_Error with "expected callback failure"; end if;
 end;
end Sink_Handler;
""")
        path = build(self.root / 'model.json', self.root / 'deployment.json', SDL, self.root / 'build')
        self.plan = json.loads(path.read_text())
        _, source = self.run_node('source', frame('T'))
        payload = unpack_port(source[1][1])[1]; cat = self.plan['components']['source']['outputs']['value']['catalogue']
        for timed_out in (False, True):
            first = port_data('input', b'', '') if timed_out else port_data('input', payload, cat)
            result, frames = self.run_node('sink', frame('D', first) + frame('D', port_data('second', payload, cat)) + frame('T'))
            self.assertEqual(result.returncode == 0, not timed_out, result.stderr)
            self.assertEqual([op for op, _ in frames], ['R', 'P'] if timed_out else ['R', 'P', 'F'])


class AdaGenerationTests(unittest.TestCase):
    def test_child_units_multiple_schemas_and_space_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            check_generation(self, directory, SDL, 'ada')
