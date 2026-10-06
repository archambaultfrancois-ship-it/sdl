import pathlib
import math
import os
import struct
import unittest

from codec_cases import CodecCases, State
from schema import (AnonymousEnvelope, AnonymousEnvelope_1, AnonymousEnvelope_2,
   AnonymousEnvelope_3, FixedBoard, FixedItem, FixedRow, FixedVector,
   RootPayload, VarItem)
from sdl_runtime import (CodecError, Complex32, Complex64, decode,
   decode_dynamic, encode)


WIRE_ENDIAN = os.environ.get('SDL_WIRE_ENDIAN', 'big').lower()
FIXTURE_NAME = 'root_payload.bin' if WIRE_ENDIAN == 'little' else 'root_payload_be.bin'
FIXTURE = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / FIXTURE_NAME


def fl32(value):
   return struct.unpack('<f', struct.pack('<f', value))[0]


def wire_u32(value):
   return value.to_bytes(4, WIRE_ENDIAN)


def reverse_body_fields(wire):
   descriptor_size = int.from_bytes(wire[:4], WIRE_ENDIAN)
   body_offset = 8 + descriptor_size
   fields = []
   offset = body_offset
   while offset < len(wire):
      field_id = int.from_bytes(wire[offset:offset + 4], WIRE_ENDIAN)
      length = int.from_bytes(wire[offset + 4:offset + 8], WIRE_ENDIAN)
      end = offset + 8 + length
      if end > len(wire):
         raise AssertionError('test input contains a truncated field')
      if fields and fields[-1][0] == field_id:
         fields[-1][1].append(wire[offset:end])
      else:
         fields.append((field_id, [wire[offset:end]]))
      offset = end
   return wire[:body_offset] + b''.join(
      part for unused_field_id, group in reversed(fields) for part in group)


def codec_cases():
   return CodecCases(
      tiny=-128,
      small=-32768,
      signed_value=-2147483648,
      wide=9223372036854775807,
      ratio=-0.0,
      precise=1.0 / 3.0,
      point=Complex32(1.25, -2.5),
      position=Complex64(-3.125, 4.75),
      state=State.READY,
      empty_text='',
      required_zero=0,
      samples=[-32768, -1, 32767],
      measurements=[0.125, -1024.5],
      labels=['', 'alpha', 'omega'],
      points=[Complex32(5.5, -6.25), Complex32(0.0, 9.0)],
      empty_values=[],
      required_enabled=True,
      optional_enabled=False,
      bool_flags=[True, False, True],
   )


class CodecTests(unittest.TestCase):
   def test_s01_field_order_is_independent_of_schema_order(self):
      original = codec_cases()
      wire = reverse_body_fields(encode(original))
      self.assertEqual(decode(wire, CodecCases), original)
      self.assertEqual(decode_dynamic(wire).fields['required_zero'], 0)

   def test_s02_unicode_strings_and_float_special_values_round_trip(self):
      text = ('SDL-é-📦-' * 128) + '\x00tail'
      original = RootPayload(header=text)
      decoded = decode(encode(original), RootPayload)
      self.assertEqual(decoded.header, text)

      values = CodecCases(ratio=float('inf'), precise=float('nan'),
         point=Complex32(float('-inf'), float('nan')),
         position=Complex64(float('inf'), float('-inf')))
      decoded_values = decode(encode(values), CodecCases)
      self.assertEqual(decoded_values.ratio, float('inf'))
      self.assertTrue(math.isnan(decoded_values.precise))
      self.assertEqual(decoded_values.point.real, float('-inf'))
      self.assertTrue(math.isnan(decoded_values.point.imag))
      self.assertEqual(decoded_values.position.real, float('inf'))
      self.assertEqual(decoded_values.position.imag, float('-inf'))

   def test_s03_invalid_frame_and_field_lengths_are_rejected(self):
      valid = encode(codec_cases())
      self.assertEqual(decode(valid, CodecCases), codec_cases())

      bad_descriptor_length = bytearray(valid)
      bad_descriptor_length[:4] = wire_u32(0xFFFFFFFF)
      with self.assertRaises(CodecError):
         decode(bytes(bad_descriptor_length), CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(bytes(bad_descriptor_length))

      descriptor_size = int.from_bytes(valid[:4], WIRE_ENDIAN)
      body_offset = 8 + descriptor_size
      bad_field_length = bytearray(valid)
      bad_field_length[body_offset + 4:body_offset + 8] = wire_u32(0xFFFFFFFF)
      with self.assertRaises(CodecError):
         decode(bytes(bad_field_length), CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(bytes(bad_field_length))

      with self.assertRaises(CodecError):
         decode(valid[:7], CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(valid[:7])

   def test_root_payload_matches_c_fixture_and_round_trips(self):
      original = RootPayload(
         header='Mission_Data_Packet',
         fixed_array=[
            FixedItem(x=fl32(1.1), y=fl32(2.2)),
            FixedItem(x=fl32(3.3), y=fl32(4.4)),
         ],
         var_array=[
            VarItem(name='Variable_Node_A', id=99999),
            VarItem(name='Variable_Node_B', id=77777),
         ],
      )
      wire = encode(original)
      fixture = FIXTURE.read_bytes()
      self.assertEqual(wire, fixture)
      self.assertEqual(decode(wire, RootPayload), original)
      self.assertEqual(decode(fixture, RootPayload), original)
      self.assertIsNot(original.fixed_array, decode(wire, RootPayload).fixed_array)

   def test_wire_descriptor_decodes_without_generated_message_classes(self):
      original = RootPayload(
         header='descriptor-driven',
         fixed_array=[FixedItem(x=1.25, y=-2.5)],
         var_array=[VarItem(name='nested', id=123456789)],
      )
      decoded = decode_dynamic(encode(original))
      self.assertEqual(decoded.type_name, 'RootPayload')
      self.assertEqual(decoded.fields['header'], 'descriptor-driven')
      self.assertEqual(decoded.fields['fixed_array'][0].type_name, 'FixedItem')
      self.assertAlmostEqual(decoded.fields['fixed_array'][0].fields['x'], 1.25)
      self.assertEqual(decoded.fields['var_array'][0].type_name, 'VarItem')
      self.assertEqual(decoded.fields['var_array'][0].fields['name'], 'nested')
      self.assertEqual(decoded.fields['var_array'][0].fields['id'], 123456789)

   def test_dynamic_decode_rejects_modified_descriptor_hash(self):
      wire = bytearray(encode(RootPayload(header='check')))
      descriptor_size = int.from_bytes(wire[:4], WIRE_ENDIAN)
      wire[4 + descriptor_size] ^= 1
      with self.assertRaisesRegex(CodecError, 'descriptor hash'):
         decode_dynamic(bytes(wire))

   def test_nested_fixed_arrays_round_trip(self):
      vector = lambda base: FixedVector(
         coords=[base, base + 1.0],
         grid=[[int(base) + value for value in range(3)],
            [int(base) + value for value in range(3, 6)]],
      )
      row = lambda base: FixedRow(vectors=[vector(base), vector(base + 10.0)])
      original = FixedBoard(
         rows=[row(0.0), row(100.0)],
         packed_rows=[row(200.0), row(300.0)],
      )
      wire = encode(original)
      self.assertEqual(decode(wire, FixedBoard), original)
      dynamic = decode_dynamic(wire)
      self.assertEqual(dynamic.type_name, 'FixedBoard')
      self.assertEqual(dynamic.fields['rows'][1].fields['vectors'][0].fields['coords'],
         [100.0, 101.0])
      self.assertEqual(dynamic.fields['packed_rows'][1].fields['vectors'][0].fields['grid'],
         [[300, 301, 302], [303, 304, 305]])
      malformed = wire + wire_u32(1) + wire_u32(1) + b'\x00'
      with self.assertRaisesRegex(CodecError, 'fixed array length'):
         decode(malformed, FixedBoard)

   def test_anonymous_nested_structs_round_trip(self):
      original = AnonymousEnvelope(
         metadata=AnonymousEnvelope_1(
            code=42,
            detail=AnonymousEnvelope_2(text='anonymous detail'),
         ),
         points=[AnonymousEnvelope_3(x=1.25, y=-2.5),
            AnonymousEnvelope_3(x=3.0, y=4.5)],
      )
      self.assertEqual(decode(encode(original), AnonymousEnvelope), original)
      generic = decode_dynamic(encode(original))
      metadata = generic.fields['metadata']
      self.assertEqual(metadata.type_name, 'AnonymousEnvelope$1')
      self.assertEqual(metadata.fields['detail'].fields['text'], 'anonymous detail')
      self.assertAlmostEqual(generic.fields['points'][1].fields['x'], 3.0)

   def test_scalars_optionals_enums_arrays_and_empty_values(self):
      original = codec_cases()
      decoded = decode(encode(original), CodecCases)
      self.assertEqual(decoded, original)
      self.assertTrue(math.copysign(1.0, decoded.ratio) < 0.0)
      dynamic = decode_dynamic(encode(original))
      self.assertEqual(dynamic.fields['state'].type_name, 'State')
      self.assertEqual(dynamic.fields['state'].name, 'READY')
      self.assertEqual(dynamic.fields['state'].value, 1)

   def test_absent_optionals_and_empty_arrays(self):
      original = CodecCases()
      decoded = decode(encode(original), CodecCases)
      self.assertEqual(decoded, original)
      self.assertIsNone(decoded.tiny)
      self.assertIsNone(decoded.optional_enabled)
      self.assertEqual(decoded.bool_flags, [])

   def test_unknown_fields_are_skipped_and_truncation_fails(self):
      wire = encode(codec_cases())
      extended = wire + wire_u32(999) + wire_u32(3) + b'\xa1\xb2\xc3'
      self.assertEqual(decode(extended, CodecCases), codec_cases())
      with self.assertRaises(CodecError):
         decode(wire[:-1], CodecCases)

   def test_invalid_boolean_and_wrong_hash_are_rejected(self):
      malformed_bool = encode(codec_cases()) + wire_u32(17) + wire_u32(1) + b'\x02'
      with self.assertRaisesRegex(CodecError, 'boolean'):
         decode(malformed_bool, CodecCases)
      wrong_hash = bytearray(encode(codec_cases()))
      descriptor_size = int.from_bytes(wrong_hash[:4], WIRE_ENDIAN)
      wrong_hash[4 + descriptor_size] ^= 0x80
      with self.assertRaisesRegex(CodecError, 'hash'):
         decode(bytes(wrong_hash), CodecCases)

   def test_packed_field_rejects_non_multiple_element_length(self):
      malformed = encode(codec_cases()) + wire_u32(15) + wire_u32(1) + b'\x00'
      with self.assertRaisesRegex(CodecError, 'packed field length'):
         decode(malformed, CodecCases)


if __name__ == '__main__':
   unittest.main()
