import math
import pathlib
import struct
import unittest
from sdl_runtime import (CodecError, Complex32, Complex64, SdlMessage,
   description, prepare, encode, decode, decode_dynamic)
from wire_example import Packet
from codec_cases import CodecCases, State, EnumRecord, EnumRecordBatch
from schema import (RootPayload, FixedItem, VarItem, FixedBoard, FixedRow,
   FixedVector, AnonymousEnvelope)
from empty_message import EmptyMessage

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / 'fixtures'


def context(*types):
   return prepare(description(types), types)


class CodecTests(unittest.TestCase):
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
         _SDL_DESCRIPTOR = ('SDL2\nmessage PackedArrays {\n'
            '  1: packed int16[2] pairs;\n}\n')
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
         _SDL_DESCRIPTOR = ('SDL2\nmessage PackedComplex {\n'
            '  1: packed c32 narrow;\n  2: packed c64 wide;\n}\n')
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
      remote = Packet._SDL_DESCRIPTOR.replace('bool active;', 'bool enabled;').replace(
         'packed int16 samples;', 'repeated int16 samples;\n  5: required string extra;')
      ctx = prepare(remote, [Packet])
      wire = (FIXTURES/'packet.bin').read_bytes() + b'\x02ok'
      value = decode(ctx, wire)
      self.assertTrue(value.active)
      self.assertEqual(value.samples, [300, -1])
      self.assertEqual(decode_dynamic(ctx, wire).fields['extra'], 'ok')
      removed = 'SDL2\nmessage Packet {\n  1: required bool active;\n}\n'
      value = decode(prepare(removed, [Packet]), b'\x01\x01')
      self.assertEqual(value, Packet(active=True))
      with self.assertRaises(CodecError):
         encode(ctx, Packet())

   def test_01_codec_nested_evolution(self):
      remote = RootPayload._SDL_DESCRIPTOR.replace('fl32 x;', 'fl32 renamed_x;').replace(
         'fl32 y;', 'fl32 y;\n  3: required int32 extra;')
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
      descriptions = ['SDD1', 'SDL2\n', 'SDL2\nmessage A {\n  1: required A a;\n}\n',
         'SDL2\nmessage A {\n  1: required Missing a;\n}\n',
         'SDL2\nmessage A {\n  1: packed string a;\n}\n',
         'SDL2\nmessage A {\n  1: required bool a;\n  1: required bool b;\n}\n',
         'SDL2\nmessage A {\n}\nenum E {\n}\n',
         'SDL2\nmessage A {\n  1: required int32[0] a;\n}\n',
         Packet._SDL_DESCRIPTOR.replace('bool active;', 'int32 active;'),
         Packet._SDL_DESCRIPTOR.replace('optional int16 code;', 'required int16 code;')]
      for text in descriptions:
         with self.subTest(text=text), self.assertRaises(CodecError): prepare(text, [Packet])
      with self.assertRaises(CodecError): prepare(b'SDL2\n\xff\n')
      with self.assertRaises(CodecError): prepare(Packet._SDL_DESCRIPTOR.replace('Packet', 'Pac\0ket'))

   def test_03_limits_shared_subtypes_and_depth(self):
      def graph(count, shared):
         declarations = ['SDL2']
         for i in range(count):
            fields = ['  1: required int8 value;'] if i == 0 else [
               '  1: optional N%03d left;' % (i - 1)]
            if shared and i:
               fields.append('  2: optional N%03d right;' % (i - 1))
            declarations.extend(['message N%03d {' % i, *fields, '}'])
         return '\n'.join(declarations) + '\n'
      prepare(graph(30, True))
      with self.assertRaises(CodecError): prepare(graph(66, False))
      with self.assertRaises(CodecError): prepare('SDL2\nmessage ' + 'A'*65536 + ' {\n}\n')
      with self.assertRaises(CodecError): prepare('SDL2\nmessage \ud800 {\n}\n')

   def test_03_limits_counter_boundaries(self):
      ctx = context(Packet)
      for n in (0, 1, 127, 128, 16383, 16384):
         value = Packet(label='x'*n, samples=[-1]*n)
         self.assertEqual(decode(ctx, encode(ctx, value)), value)
      with self.assertRaises(CodecError): prepare('SDL2\n'+'x'*(1<<20))
      enormous = b'\x01\x00\x00\x00\xff\xff\xff\xff\x0f'
      with self.assertRaises(CodecError): decode(ctx, enormous)
