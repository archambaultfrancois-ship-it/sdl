import math
import pathlib
import struct
import unittest
from sdl_runtime import (CodecError, Complex32, Complex64, SdlMessage,
   SdlDynamicMessage, SdlDynamicEnum, display, description, prepare, encode, decode,
   decode_dynamic, catalogue_id, message_name, message_names, compare_catalogues)
from wire_example import Packet
from codec_cases import CodecCases, State, EnumRecord, EnumRecordBatch
from schema import (RootPayload, FixedItem, VarItem, FixedBoard, FixedRow,
   FixedVector, AnonymousEnvelope)
from empty_message import EmptyMessage

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / 'fixtures'


def context(*types):
   return prepare(description(types), types)


class CodecTests(unittest.TestCase):
   def test_01_codec_automatic_fixed_shared_oracle(self):
      from auto_fixed import AutoBatch, AutoRecord, AutoPoint, AutoState
      record = AutoRecord(id=0x01020304, point=AutoPoint(coordinates=[1.25,-2.5],
         code=-123, state=AutoState.NEGATIVE, enabled=True), grid=[[-1,2,-3],[4,-5,6]])
      message = AutoBatch(label='x', records=[record, record], samples=[-1,2,-3])
      ctx = context(AutoBatch)
      oracle = (FIXTURES / 'auto_fixed.bin').read_bytes()
      self.assertEqual(encode(ctx, message), oracle)
      self.assertEqual(decode(ctx, oracle), message)
      root = bytes([4]) + oracle[4:35]
      self.assertEqual(encode(ctx, record), root)
      self.assertEqual(decode(ctx, root), record)
      for offset, value in ((22,2), (21,1)):
         bad = bytearray(oracle); bad[offset] = value
         with self.assertRaises(CodecError):
            decode(ctx, bad)
      older = prepare(description(AutoBatch).replace('3:int16[2][3]:grid;', ''), AutoBatch)
      self.assertNotIn('AutoRecord', older.fixed_messages)
      self.assertEqual(decode(older, root[:20]).grid, [[0,0,0],[0,0,0]])

   def test_01_codec_largest_fixed_message(self):
      import os
      from unittest.mock import patch
      import sdl_runtime as runtime
      from bench_cases import BenchSmall, BenchRecord, BenchPose, BenchVector
      cases = [
         (BenchSmall(active=True, sequence=123, code=-2), struct.pack('>Bi h', 1, 123, -2)),
         (BenchRecord(id=7, pose=BenchPose(position=BenchVector(values=[1., 2., 3.]),
            rotation=[0., 0., 0., 1.]), measures=[4., 5.]),
            struct.pack('>i9f', 7, 1., 2., 3., 0., 0., 0., 1., 4., 5.)),
      ]
      for disabled in ('1', ''):
         with patch.dict(os.environ, {'SDL_PYTHON_NO_NATIVE': disabled}):
            for message, payload in cases:
               ctx = context(type(message))
               name = message._SDL_NAME
               self.assertIn(name, ctx.fixed_messages)
               wire = bytes([ctx.ids[name]]) + payload
               self.assertEqual(encode(ctx, message), wire)
               self.assertEqual(decode(ctx, wire), message)
               for end in range(len(wire)):
                  with self.assertRaises(CodecError):
                     decode(ctx, wire[:end])
               baseline = context(type(message))
               baseline.fixed_messages.clear()
               self.assertEqual(encode(baseline, message), wire)
               self.assertEqual(decode(baseline, wire), message)
               self.assertEqual(decode_dynamic(ctx, wire), decode_dynamic(baseline, wire))
               if isinstance(message, BenchSmall):
                  with self.assertRaises(CodecError):
                     decode(ctx, bytes([ctx.ids[name], 2]) + payload[1:])

      # A variable root still selects fixed descendants and keeps its counters.
      message = FixedBoard()
      ctx = context(FixedBoard)
      self.assertNotIn('FixedBoard', ctx.fixed_messages)
      self.assertIn('FixedRow', ctx.fixed_messages)
      wire = encode(ctx, message)
      ctx.fixed_messages.clear()
      self.assertEqual(encode(ctx, message), wire)
      self.assertEqual(decode(ctx, wire), message)

      # A local schema missing a remote field must retain evolution handling.
      class OlderSmall(SdlMessage):
         _SDL_NAME = 'BenchSmall'
         _SDL_DESCRIPTOR = 'SDL2|BenchSmall{1:bool:active;2:int32:sequence;}'
         _SDL_FIELDS = ((1, 'active', 'required', 'bool', ()),
                        (2, 'sequence', 'required', 'int32', ()))
      ctx = prepare(description(BenchSmall), OlderSmall)
      self.assertNotIn('BenchSmall', ctx.fixed_messages)
      wire = bytes([ctx.ids['BenchSmall']]) + struct.pack('>Bih', 1, 123, -2)
      value = decode(ctx, wire)
      self.assertTrue(value.active)
      self.assertEqual(value.sequence, 123)

   def test_01_codec_typed_and_dynamic_display(self):
      ctx = context(Packet)
      typed = Packet(active=True, code=None, label='a\nb', samples=[1, 2, 3])
      dynamic = decode_dynamic(ctx, encode(ctx, typed))
      expected = ('Packet {\n   active: true\n   code: null\n   label: "a\\nb"\n'
         '   samples: [\n      1,\n      2,\n      3\n   ]\n}\n')
      self.assertEqual(display(typed), expected)
      self.assertEqual(display(dynamic), expected)
      self.assertEqual(display(dynamic, max_items=1), expected.replace(
         '      2,\n      3', '      ... (2 more elements)'))
      ctx = context(EnumRecordBatch)
      typed = EnumRecordBatch(records=[EnumRecord(state=State.READY, code=1),
         EnumRecord(state=State.NEGATIVE, code=2)])
      dynamic = decode_dynamic(ctx, encode(ctx, typed))
      self.assertEqual(display(dynamic, 2), display(typed, 2))
      self.assertIn('state: READY', display(dynamic))
      self.assertIn('... (1 more elements)', display(dynamic, max_items=1))
      value = SdlDynamicMessage('Status', {'unknown': SdlDynamicEnum('State', 9),
         'point': Complex32(1, -2), 'empty': []})
      self.assertEqual(display(value),
         'Status {\n   unknown: 9\n   point: (1, -2)\n   empty: []\n}\n')

   def test_02_invalid_display_arguments(self):
      for value in (-1, True, 1.5):
         with self.subTest(value=value):
            with self.assertRaises(ValueError):
               display(Packet(), indent_width=value)
            with self.assertRaises(ValueError):
               display(Packet(), max_items=value)
      with self.assertRaises(CodecError):
         display(object())

   def test_01_codec_envelope(self):
      from sdl_runtime import pack_envelope, unpack_envelope
      for identity in (0, 0x89abcdef, 0xffffffff):
         wire = pack_envelope(identity, b'\x01\x00')
         self.assertEqual(wire, b'S2PS\x02' + identity.to_bytes(4, 'big') + b'\x01\x00')
         self.assertEqual(unpack_envelope(wire), (identity, b'\x01\x00'))
         for n in range(10):
            with self.assertRaises(CodecError): unpack_envelope(wire[:n])
         for i in range(5):
            invalid = bytearray(wire); invalid[i] ^= 1
            with self.assertRaises(CodecError): unpack_envelope(invalid)
      for identity in (-1, 0x100000000):
         with self.assertRaises(CodecError): pack_envelope(identity, b'\x01')
      with self.assertRaises(CodecError): pack_envelope(0, b'')

   def test_01_codec_directional_compatibility(self):
      folder = FIXTURES / 'compatibility'
      for line in (folder / 'cases.tsv').read_text().splitlines():
         name, p, c, root, policy, code, message, path, field_id, pt, ct, value = line.split('\t')
         with self.subTest(name=name):
            result = compare_catalogues((folder/p).read_text(), (folder/c).read_text(),
               None if root == '-' else root, require_required=policy == '1')
            expected = (code, *('' if s == '-' else s for s in (message, path)),
               int(field_id), *('' if s == '-' else s for s in (pt, ct)), int(value))
            self.assertEqual(tuple(result), expected)
            self.assertEqual(result.compatible, code == 'COMPATIBLE')

   def test_01_codec_catalogue_identity_and_inspection(self):
      for line in (FIXTURES / 'catalogue_ids.tsv').read_text().splitlines():
         raw, expected = line.split('\t')
         data = bytes.fromhex(raw)
         self.assertEqual(catalogue_id(data), int(expected, 16))
         self.assertEqual(catalogue_id(data.decode('utf-8')), int(expected, 16))
      for data in (b'\xff', '\ud800', object(), b'a' * (1024 * 1024 + 1)):
         with self.assertRaises(CodecError):
            catalogue_id(data)
      self.assertEqual(message_name(Packet), 'Packet')
      self.assertEqual(message_name(Packet()), 'Packet')
      with self.assertRaises(CodecError):
         message_name(object())
      ctx = context(Packet, EmptyMessage)
      self.assertEqual(message_names(ctx), ('EmptyMessage', 'Packet'))
      self.assertEqual(message_names(prepare(description([CodecCases]))),
         message_names(context(CodecCases)))
      self.assertNotIn('State', message_names(context(CodecCases)))
      self.assertEqual(description([Packet, EmptyMessage]), description([EmptyMessage, Packet]))

   def test_01_codec_fixture(self):
      ctx = context(Packet)
      self.assertEqual(description([Packet]), (FIXTURES/'packet.sdl2').read_text())
      message = Packet(active=True, code=-2, label='été', samples=[300, -1])
      wire = encode(ctx, message)
      self.assertEqual(wire, (FIXTURES/'packet.bin').read_bytes())
      self.assertEqual(decode(ctx, wire), message)
      dynamic = decode_dynamic(ctx, wire)
      self.assertEqual(dynamic.type_name, 'Packet')
      self.assertEqual(dynamic.fields['label'], 'été')
      self.assertEqual(dynamic.fields['samples'], [300, -1])

   def test_01_codec_empty_optional_and_strings(self):
      ctx = context(Packet)
      for code in (None, 0, -32768, 32767):
         for label in ('', 'a\0b\0', 'été', '😀'):
            value = Packet(active=False, code=code, label=label)
            self.assertEqual(decode(ctx, encode(ctx, value)), value)
      ctx = context(CodecCases)
      for value in (None, ''):
         message = CodecCases(empty_text=value)
         self.assertEqual(decode(ctx, encode(ctx, message)).empty_text, value)

   def test_01_codec_multitype_catalogue(self):
      ctx = context(Packet, EmptyMessage)
      for value in (Packet(), EmptyMessage()):
         self.assertEqual(decode(ctx, encode(ctx, value)), value)
      self.assertEqual(encode(context(EmptyMessage), EmptyMessage()), b'\x01')

   def test_01_codec_all_scalar_types_and_enums(self):
      ctx = context(CodecCases)
      value = CodecCases(tiny=-128, small=32767, signed_value=-2147483648,
         wide=-9223372036854775808, ratio=1.5, precise=-0.0,
         point=Complex32(1, -2), position=Complex64(3, 4), state=State.MINIMUM,
         required_enabled=True, optional_enabled=False, bool_flags=[True, False],
         samples=[-32768, 32767], measurements=[1.5, float('inf')], labels=['', 'a\0b'],
         points=[Complex32(2, 3)], packed_states=[State.NEGATIVE, State.MAXIMUM],
         fixed_states=[State.UNKNOWN, State.READY, State.NEGATIVE],
         packed_flags=[False, True], high_id_value=2147483647)
      copy = decode(ctx, encode(ctx, value))
      self.assertEqual(copy, value)
      self.assertEqual(math.copysign(1, copy.precise), -1)
      ctx = context(EnumRecordBatch)
      value = EnumRecordBatch(records=[EnumRecord(state=State.READY, code=7)])
      self.assertEqual(decode(ctx, encode(ctx, value)), value)

   def test_01_codec_nested_messages_and_arrays(self):
      ctx = context(RootPayload)
      value = RootPayload(header='nested', fixed_array=[FixedItem(x=1, y=2)],
         var_array=[VarItem(name=None, id=3), VarItem(name='', id=4)])
      self.assertEqual(decode(ctx, encode(ctx, value)), value)
      ctx = context(FixedBoard)
      value = FixedBoard()
      value.rows[0].vectors[0].coords = [1, 2]
      value.rows[0].vectors[0].grid = [[-1, 2, 3], [4, 5, 6]]
      value.packed_rows = [FixedRow()]
      self.assertEqual(decode(ctx, encode(ctx, value)), value)
      ctx = context(AnonymousEnvelope)
      value = AnonymousEnvelope()
      value.metadata = ctx.classes['AnonymousEnvelope$1']()
      value.metadata.code = 7
      self.assertEqual(decode(ctx, encode(ctx, value)), value)

   def test_01_packed_records_match_generic_codec(self):
      # The optimized path must preserve bytes, nested shape and public types.
      row = FixedRow()
      row.vectors[0].coords = [1, -2]
      row.vectors[1].grid = [[-32768, 0, 32767], [4, 5, 6]]
      cases = [
         RootPayload(fixed_array=[FixedItem(x=1, y=-2), FixedItem(x=3, y=4)]),
         FixedBoard(packed_rows=[row, FixedRow()]),
         EnumRecordBatch(records=[EnumRecord(state=State.MINIMUM, code=-7),
            EnumRecord(state=State.MAXIMUM, code=2147483647)]),
         CodecCases(packed_flags=[False, True], packed_states=[State.NEGATIVE]),
      ]
      for message in cases:
         with self.subTest(type=type(message).__name__):
            ctx = context(type(message))
            plans = ctx.packed_codecs
            fast_wire = encode(ctx, message)
            fast_copy = decode(ctx, fast_wire)
            ctx.packed_codecs = {}
            self.assertEqual(fast_wire, encode(ctx, message))
            self.assertEqual(fast_copy, decode(ctx, fast_wire))
            self.assertEqual(fast_copy, message)
            ctx.packed_codecs = plans
            for n in range(len(fast_wire)):
               with self.assertRaises(CodecError):
                  decode(ctx, fast_wire[:n])

   def test_01_packed_fixed_array_wire_layout(self):
      class PackedArrays(SdlMessage):
         _SDL_NAME = 'PackedArrays'
         _SDL_DESCRIPTOR = ("SDL2|PackedArrays{1+:int16[2]:pairs;}")
         _SDL_FIELDS = ((1, 'pairs', 'packed', 'int16', (2,)),)
      ctx = context(PackedArrays)
      message = PackedArrays(pairs=[[-32768, 32767], [1, -2]])
      expected = b'\x01\x02' + struct.pack('>hhhh', -32768, 32767, 1, -2)
      self.assertEqual(encode(ctx, message), expected)
      self.assertEqual(decode(ctx, expected), message)
      with self.assertRaises(CodecError):
         encode(ctx, PackedArrays(pairs=[[1]]))

   def test_01_packed_complex_wire_layout(self):
      class PackedComplex(SdlMessage):
         _SDL_NAME = 'PackedComplex'
         _SDL_DESCRIPTOR = ("SDL2|PackedComplex{1+:c32:narrow;2+:c64:wide;}")
         _SDL_FIELDS = ((1, 'narrow', 'packed', 'c32', ()),
            (2, 'wide', 'packed', 'c64', ()))
      ctx = context(PackedComplex)
      message = PackedComplex(narrow=[Complex32(1, -2), Complex32(-0.0, float('inf'))],
         wide=[Complex64(3, -4), Complex64(float('-inf'), -0.0)])
      expected = (b'\x01\x02' + struct.pack('>ffff', 1, -2, -0.0, float('inf')) +
         b'\x02' + struct.pack('>dddd', 3, -4, float('-inf'), -0.0))
      self.assertEqual(encode(ctx, message), expected)
      copy = decode(ctx, expected)
      self.assertEqual(copy, message)
      self.assertEqual(math.copysign(1, copy.narrow[1].real), -1)
      self.assertEqual(math.copysign(1, copy.wide[1].imag), -1)
      self.assertEqual(encode(ctx, PackedComplex()), b'\x01\x00\x00')

   def test_02_packed_record_enum_validation(self):
      ctx = context(EnumRecordBatch)
      wire = bytes([ctx.ids['EnumRecordBatch'], 1]) + struct.pack('>ii', 123, 7)
      with self.assertRaises(CodecError):
         decode(ctx, wire)
      with self.assertRaises(CodecError):
         encode(ctx, EnumRecordBatch(records=[EnumRecord(state=123, code=7)]))
      ctx = context(CodecCases)
      with self.assertRaises(CodecError):
         encode(ctx, CodecCases(packed_flags=[1]))
      valid = encode(ctx, CodecCases(packed_flags=[True]))
      with self.assertRaises(CodecError):
         decode(ctx, valid[:-2] + bytes([2]) + valid[-1:])

   def test_01_codec_evolution(self):
      remote = Packet._SDL_DESCRIPTOR.replace('bool:active;', 'bool:enabled;').replace(
         '4+:int16:samples;', '4*:int16:samples;5:string:extra;')
      ctx = prepare(remote, [Packet])
      wire = (FIXTURES/'packet.bin').read_bytes() + b'\x02ok'
      value = decode(ctx, wire)
      self.assertTrue(value.active)
      self.assertEqual(value.samples, [300, -1])
      self.assertEqual(decode_dynamic(ctx, wire).fields['extra'], 'ok')
      removed = "SDL2|Packet{1:bool:active;}"
      value = decode(prepare(removed, [Packet]), b'\x01\x01')
      self.assertEqual(value, Packet(active=True))
      with self.assertRaises(CodecError):
         encode(ctx, Packet())

   def test_01_codec_nested_evolution(self):
      remote = RootPayload._SDL_DESCRIPTOR.replace('fl32:x;', 'fl32:renamed_x;').replace(
         'fl32:y;', 'fl32:y;3:int32:extra;')
      ctx = prepare(remote, [RootPayload])
      wire = bytes([ctx.ids['RootPayload'], 0, 1]) + struct.pack('>ffi', 1, 2, 7) + b'\x00'
      self.assertEqual(decode(ctx, wire).fixed_array, [FixedItem(x=1, y=2)])

   def test_01_codec_floating_edges(self):
      ctx = context(CodecCases)
      for value in (float('inf'), -float('inf'), -0.0, 5e-324, float('nan')):
         copy = decode(ctx, encode(ctx, CodecCases(precise=value)))
         if math.isnan(value): self.assertTrue(math.isnan(copy.precise))
         else: self.assertEqual(copy.precise, value)

   def test_02_invalid_truncation_and_trailing(self):
      ctx = context(Packet);wire = (FIXTURES/'packet.bin').read_bytes()
      for n in range(len(wire)):
         with self.subTest(length=n), self.assertRaises(CodecError): decode(ctx, wire[:n])
      with self.assertRaises(CodecError): decode(ctx, wire+b'\0')

   def test_02_invalid_counters(self):
      ctx = context(Packet)
      for wire in (b'\0', b'\x81\0', b'\xff\xff\xff\xff\x10', b'\x80'*5, b'\x02'):
         with self.assertRaises(CodecError): decode(ctx, wire)
      wire = bytearray((FIXTURES/'packet.bin').read_bytes());wire[2] = 2
      with self.assertRaises(CodecError): decode(ctx, wire)

   def test_02_invalid_values(self):
      ctx = context(Packet);wire = bytearray((FIXTURES/'packet.bin').read_bytes())
      wire[1] = 2
      with self.assertRaises(CodecError): decode(ctx, wire)
      wire[1] = 1;wire[7] = 255
      with self.assertRaises(CodecError): decode_dynamic(ctx, wire)
      for value in (Packet(active=1), Packet(code=32768), Packet(label='\ud800')):
         with self.assertRaises(CodecError): encode(ctx, value)
      with self.assertRaises(CodecError): encode(context(CodecCases), CodecCases(state=99))

   def test_02_invalid_descriptions(self):
      descriptions = ['SDD1', 'SDL2\n', "SDL2|A{1:A:a;}",
         "SDL2|A{1:Missing:a;}",
         "SDL2|A{1+:string:a;}",
         "SDL2|A{1:bool:a;1:bool:b;}",
         "SDL2|A{}|#E{}",
         "SDL2|A{1:int32[0]:a;}",
         Packet._SDL_DESCRIPTOR.replace('bool:active;', 'int32:active;'),
         Packet._SDL_DESCRIPTOR.replace('2?:int16:code;', '2:int16:code;')]
      for text in descriptions:
         with self.subTest(text=text), self.assertRaises(CodecError): prepare(text, [Packet])
      with self.assertRaises(CodecError): prepare(b'SDL2\n\xff\n')
      with self.assertRaises(CodecError): prepare(Packet._SDL_DESCRIPTOR.replace('Packet', 'Pac\0ket'))

   def test_03_limits_shared_subtypes_and_depth(self):
      def graph(count, shared):
         declarations = []
         for i in range(count):
            fields = ['1:int8:value;'] if i == 0 else ['1?:N%03d:left;' % (i - 1)]
            if shared and i:
               fields.append('2?:N%03d:right;' % (i - 1))
            declarations.append('N%03d{' % i + ''.join(fields) + '}')
         return 'SDL2|' + '|'.join(declarations)
      prepare(graph(30, True))
      with self.assertRaises(CodecError): prepare(graph(66, False))
      with self.assertRaises(CodecError): prepare('SDL2|' + 'A'*65536 + '{}')
      with self.assertRaises(CodecError): prepare("SDL2|\ud800{}")

   def test_03_limits_counter_boundaries(self):
      ctx = context(Packet)
      for n in (0, 1, 127, 128, 16383, 16384):
         value = Packet(label='x'*n, samples=[-1]*n)
         self.assertEqual(decode(ctx, encode(ctx, value)), value)
      with self.assertRaises(CodecError): prepare('SDL2\n'+'x'*(1<<20))
      enormous = b'\x01\x00\x00\x00\xff\xff\xff\xff\x0f'
      with self.assertRaises(CodecError): decode(ctx, enormous)
