import pathlib
import math
import os
import struct
import subprocess
import sys
import unittest

from codec_cases import CodecCases, EnumRecord, EnumRecordBatch, State
from empty_message import EmptyMessage
from schema import (AnonymousEnvelope, AnonymousEnvelope_1, AnonymousEnvelope_2,
   AnonymousEnvelope_3, FixedBoard, FixedItem, FixedRow, FixedVector,
   RootPayload, VarItem)
from sdl_runtime import (CodecError, Complex32, Complex64, decode,
   decode_dynamic, display, encode)


WIRE_ENDIAN = os.environ.get('SDL_WIRE_ENDIAN', 'big').lower()
FIXTURE_NAME = 'root_payload.bin' if WIRE_ENDIAN == 'little' else 'root_payload_be.bin'
FIXTURE = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / FIXTURE_NAME


def fl32(value):
   return struct.unpack('<f', struct.pack('<f', value))[0]


def wire_u32(value):
   return value.to_bytes(4, WIRE_ENDIAN)


def empty_enum_descriptor_frame():
   descriptor = bytearray(b'SDD1')
   def append_text(value):
      encoded = value.encode('utf-8')
      descriptor.extend(struct.pack('>H', len(encoded)))
      descriptor.extend(encoded)

   append_text('Packet')
   descriptor.extend(struct.pack('>H', 1))
   append_text('Packet')
   descriptor.extend(struct.pack('>H', 0))
   descriptor.extend(struct.pack('>H', 1))
   append_text('State')
   descriptor.extend(struct.pack('>H', 0))
   fingerprint = 2166136261
   for byte in descriptor:
      fingerprint = ((fingerprint ^ byte) * 16777619) & 0xFFFFFFFF
   return wire_u32(len(descriptor)) + descriptor + wire_u32(fingerprint)


def builtin_name_collision_frame(type_name, as_enum):
   def append_text(output, value):
      encoded = value.encode('utf-8')
      output.extend(struct.pack('>H', len(encoded)))
      output.extend(encoded)

   descriptor = bytearray(b'SDD1')
   append_text(descriptor, 'Packet')
   message_names = ['Packet'] if as_enum else sorted(('Packet', type_name))
   descriptor.extend(struct.pack('>H', len(message_names)))
   for name in message_names:
      append_text(descriptor, name)
      descriptor.extend(struct.pack('>H', 0))
   enum_names = [type_name] if as_enum else []
   descriptor.extend(struct.pack('>H', len(enum_names)))
   for name in enum_names:
      append_text(descriptor, name)
      descriptor.extend(struct.pack('>H', 1))
      append_text(descriptor, 'VALUE')
      descriptor.extend(struct.pack('>i', 1))
   fingerprint = 2166136261
   for byte in descriptor:
      fingerprint = ((fingerprint ^ byte) * 16777619) & 0xFFFFFFFF
   return (wire_u32(len(descriptor)) + descriptor + wire_u32(fingerprint))


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
      high_id_value=2147483647,
      samples=[-32768, -1, 32767],
      measurements=[0.125, -1024.5],
      labels=['', 'alpha', 'ome\x00ga\x00'],
      points=[Complex32(5.5, -6.25), Complex32(0.0, 9.0)],
      empty_values=[],
      required_enabled=True,
      optional_enabled=False,
      bool_flags=[True, False, True],
      fixed_states=[State.READY, State.NEGATIVE, State.UNKNOWN],
      packed_states=[State.READY, State.NEGATIVE],
      packed_flags=[False, True, False],
   )


class CodecTests(unittest.TestCase):
   def test_01_codec_14_display_formats_typed_objects_with_configurable_indentation(self):
      original = codec_cases()
      rendered = display(original, indent_width=2)
      self.assertTrue(rendered.startswith('CodecCases {\n'))
      self.assertIn('  tiny: -128\n', rendered)
      self.assertIn('  state: READY\n', rendered)
      self.assertIn('  samples: [\n    -32768,\n', rendered)
      self.assertEqual(original.display(indent_width=2), rendered)

      absent = display(CodecCases(), indent_width=4)
      self.assertIn('    tiny: null\n', absent)
      with self.assertRaises(ValueError):
         display(original, indent_width=-1)

   def test_01_codec_06_absent_required_fields_decode_to_default_values(self):
      encoded = encode(codec_cases())
      descriptor_size = int.from_bytes(encoded[:4], WIRE_ENDIAN)
      empty_body = encoded[:8 + descriptor_size]
      self.assertEqual(decode(empty_body, CodecCases), CodecCases())
      self.assertEqual(decode_dynamic(empty_body).type_name, 'CodecCases')

   def test_01_codec_11_s01_field_order_is_independent_of_schema_order(self):
      original = codec_cases()
      wire = reverse_body_fields(encode(original))
      self.assertEqual(decode(wire, CodecCases), original)
      self.assertEqual(decode_dynamic(wire).fields['required_zero'], 0)

   def test_03_limits_08_s02_unicode_strings_and_float_special_values_round_trip(self):
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

      smallest_f32 = struct.unpack('>f', b'\x00\x00\x00\x01')[0]
      smallest_f64 = math.ldexp(1.0, -1074)
      subnormals = CodecCases(ratio=smallest_f32, precise=smallest_f64,
         point=Complex32(-smallest_f32, -0.0),
         position=Complex64(smallest_f64, -0.0))
      decoded_subnormals = decode(encode(subnormals), CodecCases)
      self.assertEqual(decoded_subnormals.ratio, smallest_f32)
      self.assertEqual(decoded_subnormals.precise, smallest_f64)
      self.assertEqual(decoded_subnormals.point.real, -smallest_f32)
      self.assertTrue(math.copysign(1.0, decoded_subnormals.point.imag) < 0.0)
      self.assertEqual(decoded_subnormals.position.real, smallest_f64)
      self.assertTrue(math.copysign(1.0, decoded_subnormals.position.imag) < 0.0)

   def test_02_invalid_03_primitive_payload_widths_are_checked(self):
      cases = ((1, 0), (1, 2), (2, 1), (2, 3), (3, 3), (3, 5),
         (4, 7), (4, 9), (5, 3), (5, 5), (6, 7), (6, 9),
         (7, 7), (7, 9), (8, 15), (8, 17), (18, 0), (18, 2))
      for field_id, length in cases:
         with self.subTest(field_id=field_id, length=length):
            wire = encode(CodecCases()) + wire_u32(field_id) + wire_u32(length)
            wire += bytes(length)
            expected_error = ('invalid boolean length' if field_id == 18 else
               'invalid complex length' if field_id in (7, 8) else
               'invalid scalar length')
            with self.assertRaisesRegex(CodecError, expected_error):
               decode(wire, CodecCases)
            with self.assertRaisesRegex(CodecError, expected_error):
               decode_dynamic(wire)

   def test_02_invalid_02_s03_invalid_frame_and_field_lengths_are_rejected(self):
      valid = encode(codec_cases())
      self.assertEqual(decode(valid, CodecCases), codec_cases())

      bad_descriptor_length = bytearray(valid)
      bad_descriptor_length[:4] = wire_u32(0xFFFFFFFF)
      with self.assertRaises(CodecError):
         decode(bytes(bad_descriptor_length), CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(bytes(bad_descriptor_length))

      oversized_descriptor = bytearray(valid)
      oversized_descriptor[:4] = wire_u32(1024 * 1024 + 1)
      with self.assertRaisesRegex(CodecError, 'descriptor length'):
         decode_dynamic(bytes(oversized_descriptor))

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

      wrong_scalar_length = encode(CodecCases()) + wire_u32(11) + wire_u32(3)
      wrong_scalar_length += b'\x01\x02\x03'
      with self.assertRaises(CodecError):
         decode(wrong_scalar_length, CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(wrong_scalar_length)

   def test_01_codec_01_root_payload_matches_c_fixture_and_round_trips(self):
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

   def test_01_codec_13_wire_descriptor_decodes_without_generated_message_classes(self):
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

   def test_02_invalid_05_dynamic_decode_rejects_invalid_utf8_descriptor_strings(self):
      descriptor = bytes((
         0x53, 0x44, 0x44, 0x31, 0x00, 0x01, 0xFF, 0x00,
         0x01, 0x00, 0x01, 0xFF, 0x00, 0x00, 0x00, 0x00,
      ))
      fingerprint = 2166136261
      for byte in descriptor:
         fingerprint = ((fingerprint ^ byte) * 16777619) & 0xFFFFFFFF
      wire = wire_u32(len(descriptor)) + descriptor + wire_u32(fingerprint)
      with self.assertRaisesRegex(CodecError, 'schema descriptor'):
         decode_dynamic(wire)

   def test_02_invalid_06_dynamic_decode_rejects_nul_in_descriptor_strings(self):
      descriptor = bytes((
         0x53, 0x44, 0x44, 0x31, 0x00, 0x02, 0x41, 0x00,
         0x00, 0x01, 0x00, 0x02, 0x41, 0x00, 0x00, 0x00,
         0x00, 0x00,
      ))
      fingerprint = 2166136261
      for byte in descriptor:
         fingerprint = ((fingerprint ^ byte) * 16777619) & 0xFFFFFFFF
      wire = wire_u32(len(descriptor)) + descriptor + wire_u32(fingerprint)
      with self.assertRaisesRegex(CodecError, 'schema descriptor'):
         decode_dynamic(wire)

   def test_02_invalid_07_dynamic_decode_rejects_recursive_descriptor_types(self):
      descriptor = bytes((
         0x53, 0x44, 0x44, 0x31, 0x00, 0x01, 0x41, 0x00,
         0x01, 0x00, 0x01, 0x41, 0x00, 0x01, 0x00, 0x00,
         0x00, 0x01, 0x00, 0x04, 0x6E, 0x65, 0x78, 0x74,
         0x01, 0x00, 0x01, 0x41, 0x00, 0x00, 0x00,
      ))
      fingerprint = 2166136261
      for byte in descriptor:
         fingerprint = ((fingerprint ^ byte) * 16777619) & 0xFFFFFFFF
      wire = wire_u32(len(descriptor)) + descriptor + wire_u32(fingerprint)
      with self.assertRaisesRegex(CodecError, 'recursive descriptor'):
         decode_dynamic(wire)

   def test_02_invalid_08_dynamic_decode_rejects_modified_descriptor_hash(self):
      wire = bytearray(encode(RootPayload(header='check')))
      descriptor_size = int.from_bytes(wire[:4], WIRE_ENDIAN)
      wire[4 + descriptor_size] ^= 1
      with self.assertRaisesRegex(CodecError, 'descriptor hash'):
         decode_dynamic(bytes(wire))


   def test_02_invalid_09_dynamic_descriptor_rejects_fixed_arrays_of_variable_size_messages(self):
      descriptor = bytes((
         0x53, 0x44, 0x44, 0x31, 0x00, 0x06, 0x50, 0x61,
         0x63, 0x6B, 0x65, 0x74, 0x00, 0x02, 0x00, 0x05,
         0x43, 0x68, 0x69, 0x6C, 0x64, 0x00, 0x01, 0x00,
         0x00, 0x00, 0x01, 0x00, 0x04, 0x74, 0x65, 0x78,
         0x74, 0x00, 0x00, 0x06, 0x73, 0x74, 0x72, 0x69,
         0x6E, 0x67, 0x00, 0x00, 0x06, 0x50, 0x61, 0x63,
         0x6B, 0x65, 0x74, 0x00, 0x01, 0x00, 0x00, 0x00,
         0x01, 0x00, 0x05, 0x69, 0x74, 0x65, 0x6D, 0x73,
         0x00, 0x00, 0x05, 0x43, 0x68, 0x69, 0x6C, 0x64,
         0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00,
      ))
      fingerprint = 2166136261
      for byte in descriptor:
         fingerprint = ((fingerprint ^ byte) * 16777619) & 0xFFFFFFFF
      wire = wire_u32(len(descriptor)) + descriptor + wire_u32(fingerprint)
      with self.assertRaisesRegex(CodecError,
            'fixed array element type has variable wire size'):
         decode_dynamic(wire)


   def test_01_codec_03_empty_message_round_trip(self):
      wire = encode(EmptyMessage())
      self.assertEqual(len(wire), 8 + len(EmptyMessage._SDL_DESCRIPTOR))
      self.assertEqual(decode(wire, EmptyMessage), EmptyMessage())
      dynamic = decode_dynamic(wire)
      self.assertEqual(dynamic.type_name, 'EmptyMessage')
      self.assertEqual(dynamic.fields, {})

   def test_02_invalid_10_dynamic_descriptors_reject_empty_enums(self):
      with self.assertRaisesRegex(CodecError, 'enum declaration'):
         decode_dynamic(empty_enum_descriptor_frame())

   def test_02_invalid_11_dynamic_descriptors_reject_builtin_type_name_collisions(self):
      for type_name, as_enum in (('int32', False), ('string', True)):
         with self.subTest(type_name=type_name, as_enum=as_enum):
            with self.assertRaisesRegex(CodecError, 'ambiguous'):
               decode_dynamic(builtin_name_collision_frame(type_name, as_enum))

   def test_02_invalid_12_dynamic_descriptors_reject_invalid_fixed_layouts(self):
      def append_text(output, value):
         encoded = value.encode('utf-8')
         output.extend(struct.pack('>H', len(encoded)))
         output.extend(encoded)

      def make_frame(type_name, modifier, dimensions):
         descriptor = bytearray(b'SDD1')
         append_text(descriptor, 'Packet')
         descriptor.extend(struct.pack('>H', 1))
         append_text(descriptor, 'Packet')
         descriptor.extend(struct.pack('>H', 1))
         descriptor.extend(struct.pack('>I', 1))
         append_text(descriptor, 'value')
         descriptor.append(modifier)
         append_text(descriptor, type_name)
         descriptor.append(len(dimensions))
         for dimension in dimensions:
            descriptor.extend(struct.pack('>I', dimension))
         descriptor.extend(struct.pack('>H', 0))
         fingerprint = 2166136261
         for byte in descriptor:
            fingerprint = ((fingerprint ^ byte) * 16777619) & 0xFFFFFFFF
         return (wire_u32(len(descriptor)) + descriptor +
            wire_u32(fingerprint))

      cases = (('string', 3, ()), ('int8', 0, (0,)),
         ('int16', 0, (0xFFFFFFFF,)))
      for type_name, modifier, dimensions in cases:
         with self.subTest(type_name=type_name, dimensions=dimensions):
            with self.assertRaises(CodecError):
               decode_dynamic(make_frame(type_name, modifier, dimensions))

   def test_01_codec_04_nested_fixed_arrays_round_trip(self):
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

   def test_01_codec_05_anonymous_nested_structs_round_trip(self):
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

   def test_01_codec_02_scalars_optionals_enums_arrays_and_empty_values(self):
      original = codec_cases()
      decoded = decode(encode(original), CodecCases)
      self.assertEqual(decoded, original)
      self.assertTrue(math.copysign(1.0, decoded.ratio) < 0.0)
      dynamic = decode_dynamic(encode(original))
      self.assertEqual(dynamic.fields['state'].type_name, 'State')
      self.assertEqual(dynamic.fields['state'].name, 'READY')
      self.assertEqual(dynamic.fields['state'].value, 1)
      self.assertEqual(dynamic.fields['high_id_value'], 2147483647)
      self.assertEqual(dynamic.fields['packed_flags'], [False, True, False])
      self.assertEqual(dynamic.fields['labels'], original.labels)

   def test_01_codec_07_absent_optionals_and_empty_arrays(self):
      original = CodecCases()
      decoded = decode(encode(original), CodecCases)
      self.assertEqual(decoded, original)
      self.assertIsNone(decoded.tiny)
      self.assertIsNone(decoded.optional_enabled)
      self.assertEqual(decoded.bool_flags, [])

   def test_02_invalid_01_unknown_fields_are_skipped_and_truncation_fails(self):
      wire = encode(codec_cases())
      extended = (wire + wire_u32(998) + wire_u32(0) + wire_u32(11) +
         wire_u32(4) + b'\x00\x00\x00\x00' + wire_u32(999) +
         wire_u32(3) + b'\xa1\xb2\xc3' + wire_u32(999) + wire_u32(1) +
         b'\xd4')
      self.assertEqual(decode(extended, CodecCases), codec_cases())
      dynamic = decode_dynamic(extended)
      self.assertEqual(dynamic.type_name, 'CodecCases')
      self.assertEqual(dynamic.fields['required_zero'], 0)
      self.assertEqual(dynamic.fields['samples'], [-32768, -1, 32767])
      with self.assertRaises(CodecError):
         decode_dynamic(extended[:-1])
      with self.assertRaises(CodecError):
         decode(wire[:-1], CodecCases)

   def test_01_codec_08_duplicate_singular_fields_use_last_value_and_repeated_fields_append(self):
      endian_prefix = '<' if WIRE_ENDIAN == 'little' else '>'
      wire = encode(CodecCases(required_zero=7, optional_enabled=True, samples=[-9]))
      wire += wire_u32(11) + wire_u32(4) + struct.pack(endian_prefix + 'i', 42)
      wire += wire_u32(12) + wire_u32(2) + struct.pack(endian_prefix + 'h', 1234)
      wire += wire_u32(18) + wire_u32(1) + b'\x00'

      decoded = decode(wire, CodecCases)
      self.assertEqual(decoded.required_zero, 42)
      self.assertIs(decoded.optional_enabled, False)
      self.assertEqual(decoded.samples, [-9, 1234])
      dynamic = decode_dynamic(wire)
      self.assertEqual(dynamic.fields['required_zero'], 42)
      self.assertIs(dynamic.fields['optional_enabled'], False)
      self.assertEqual(dynamic.fields['samples'], [-9, 1234])

      malformed_then_valid = (encode(CodecCases()) + wire_u32(998) +
         wire_u32(0) + wire_u32(11) + wire_u32(3) + b'\x00\x00\x00' +
         wire_u32(11) +
         wire_u32(4) + struct.pack(endian_prefix + 'i', 42))
      with self.assertRaises(CodecError):
         decode(malformed_then_valid, CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(malformed_then_valid)

   def test_03_limits_03_signed_integer_minimum_and_maximum_values_round_trip(self):
      boundaries = (
         (-128, -32768, -2147483648, -9223372036854775808),
         (127, 32767, 2147483647, 9223372036854775807),
      )
      names = ('tiny', 'small', 'signed_value', 'wide')
      for expected in boundaries:
         original = CodecCases(tiny=expected[0], small=expected[1],
            signed_value=expected[2], wide=expected[3],
            high_id_value=expected[2])
         wire = encode(original)
         self.assertEqual(decode(wire, CodecCases), original)
         dynamic = decode_dynamic(wire)
         for name, value in zip(names, expected):
            self.assertEqual(dynamic.fields[name], value)

   def test_03_limits_04_negative_enum_values_round_trip(self):
      original = CodecCases(state=State.NEGATIVE)
      wire = encode(original)
      self.assertEqual(decode(wire, CodecCases), original)
      dynamic = decode_dynamic(wire)
      self.assertEqual(dynamic.fields['state'].name, 'NEGATIVE')
      self.assertEqual(dynamic.fields['state'].value, -7)

   def test_03_limits_05_enum_int32_boundary_values_round_trip(self):
      for state, expected, name in (
         (State.MINIMUM, -2147483648, 'MINIMUM'),
         (State.MAXIMUM, 2147483647, 'MAXIMUM'),
      ):
         original = CodecCases(state=state)
         wire = encode(original)
         self.assertEqual(decode(wire, CodecCases), original)
         value = decode_dynamic(wire).fields['state']
         self.assertEqual((value.name, value.value), (name, expected))

   def test_02_invalid_13_undeclared_enum_values_are_rejected_by_typed_and_dynamic_decoders(self):
      endian_prefix = '<' if WIRE_ENDIAN == 'little' else '>'
      with self.assertRaisesRegex(CodecError, 'enum value'):
         encode(CodecCases(state=99))
      wire = encode(CodecCases())
      wire += wire_u32(9) + wire_u32(4) + struct.pack(endian_prefix + 'i', 99)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode(wire, CodecCases)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode_dynamic(wire)

   def test_02_invalid_14_packed_fixed_messages_reject_nested_invalid_enum_values(self):
      with self.assertRaisesRegex(CodecError, 'enum value'):
         encode(EnumRecordBatch(records=[EnumRecord(state=99, code=42)]))

      valid = EnumRecordBatch(records=[EnumRecord(state=State.READY, code=42)])
      wire = encode(valid)
      self.assertEqual(decode(wire, EnumRecordBatch), valid)
      self.assertEqual(decode_dynamic(wire).type_name, 'EnumRecordBatch')

      endian_prefix = '<' if WIRE_ENDIAN == 'little' else '>'
      malformed = encode(EnumRecordBatch()) + wire_u32(1) + wire_u32(8)
      malformed += struct.pack(endian_prefix + 'ii', 99, 42)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode(malformed, EnumRecordBatch)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode_dynamic(malformed)

   def test_02_invalid_15_fixed_enum_arrays_reject_undeclared_values(self):
      self.assertEqual(decode(encode(codec_cases()), CodecCases), codec_cases())
      invalid = codec_cases()
      invalid.fixed_states = [State.READY, 99, State.NEGATIVE]
      with self.assertRaisesRegex(CodecError, 'enum value'):
         encode(invalid)
      endian_prefix = '<' if WIRE_ENDIAN == 'little' else '>'
      malformed = encode(CodecCases())
      malformed += wire_u32(20) + wire_u32(12)
      malformed += struct.pack(endian_prefix + 'iii', 1, 99, -7)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode(malformed, CodecCases)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode_dynamic(malformed)

      wrong_length = encode(CodecCases()) + wire_u32(20) + wire_u32(8)
      wrong_length += struct.pack(endian_prefix + 'ii', 1, -7)
      with self.assertRaises(CodecError):
         decode(wrong_length, CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(wrong_length)

      with self.assertRaisesRegex(CodecError, 'enum value'):
         encode(CodecCases(packed_states=[99]))

      invalid_packed_enum = encode(CodecCases()) + wire_u32(21) + wire_u32(4)
      invalid_packed_enum += struct.pack(endian_prefix + 'i', 99)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode(invalid_packed_enum, CodecCases)
      with self.assertRaisesRegex(CodecError, 'enum value'):
         decode_dynamic(invalid_packed_enum)

      bad_packed_length = encode(CodecCases()) + wire_u32(21) + wire_u32(3) + b'\x00\x00\x01'
      with self.assertRaises(CodecError):
         decode(bad_packed_length, CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(bad_packed_length)

   def test_02_invalid_04_invalid_utf8_strings_are_rejected_by_typed_and_dynamic_codecs(self):
      with self.assertRaisesRegex(CodecError, 'Unicode scalar'):
         encode(RootPayload(header='\ud800'))
      wire = bytearray(encode(RootPayload(header='four')))
      descriptor_size = int.from_bytes(wire[:4], WIRE_ENDIAN)
      payload_offset = 8 + descriptor_size + 8
      invalid_sequences = (
         b'\xc0\xafAB',             # Overlong encoding.
         b'\xe2\x82AB',             # Invalid continuation after a prefix.
         b'\xed\xa0\x80A',        # Encoded surrogate.
         b'\xf4\x90\x80\x80',  # Code point above U+10FFFF.
         b'\x80ABC',                   # Isolated continuation byte.
      )
      for invalid in invalid_sequences:
         wire[payload_offset:payload_offset + 4] = invalid
         with self.assertRaisesRegex(CodecError, 'UTF-8'):
            decode(bytes(wire), RootPayload)
         with self.assertRaisesRegex(CodecError, 'UTF-8'):
            decode_dynamic(bytes(wire))

      wire[payload_offset - 4:payload_offset] = struct.pack(
         ('<' if WIRE_ENDIAN == 'little' else '>') + 'I', 2)
      wire[payload_offset:payload_offset + 2] = b'\xe2\x82'
      truncated = bytes(wire[:payload_offset + 2])
      with self.assertRaisesRegex(CodecError, 'UTF-8'):
         decode(truncated, RootPayload)
      with self.assertRaisesRegex(CodecError, 'UTF-8'):
         decode_dynamic(truncated)

   def test_03_limits_01_integer_encoder_rejects_values_outside_wire_ranges(self):
      cases = (
         ('tiny', -129), ('tiny', 128),
         ('small', -32769), ('small', 32768),
         ('signed_value', -2147483649), ('signed_value', 2147483648),
         ('wide', -9223372036854775809), ('wide', 9223372036854775808),
      )
      for field, value in cases:
         with self.subTest(field=field, value=value):
            with self.assertRaisesRegex(CodecError, 'invalid value for'):
               encode(CodecCases(**{field: value}))

   def test_03_limits_02_float_encoder_rejects_values_outside_wire_ranges(self):
      cases = (
         CodecCases(ratio=1e100), CodecCases(ratio=-1e100),
         CodecCases(point=Complex32(1e100, 0.0)),
         CodecCases(point=Complex32(0.0, -1e100)),
      )
      for value in cases:
         with self.subTest(value=value):
            with self.assertRaisesRegex(CodecError, 'invalid (value for fl32|complex value)'):
               encode(value)

   def test_03_limits_07_python_runtime_rejects_unknown_wire_byte_order(self):
      environment = os.environ.copy()
      environment['SDL_WIRE_ENDIAN'] = 'middle'
      result = subprocess.run([sys.executable, '-c', 'import sdl_runtime'],
         env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
         universal_newlines=True)
      self.assertNotEqual(result.returncode, 0)
      self.assertIn('SDL_WIRE_ENDIAN must be', result.stderr)

   def test_02_invalid_17_boolean_encoder_requires_boolean_values(self):
      with self.assertRaisesRegex(CodecError, 'requires bool'):
         encode(CodecCases(required_enabled=1))
      with self.assertRaisesRegex(CodecError, 'requires bool'):
         encode(CodecCases(optional_enabled='yes'))

   def test_02_invalid_16_invalid_boolean_and_wrong_hash_are_rejected(self):
      malformed_bool = encode(codec_cases()) + wire_u32(17) + wire_u32(1) + b'\x02'
      with self.assertRaisesRegex(CodecError, 'boolean'):
         decode(malformed_bool, CodecCases)
      with self.assertRaisesRegex(CodecError, 'boolean'):
         decode_dynamic(malformed_bool)
      malformed_packed_bool = (encode(codec_cases()) + wire_u32(22) +
         wire_u32(1) + b'\x02')
      with self.assertRaisesRegex(CodecError, 'boolean'):
         decode(malformed_packed_bool, CodecCases)
      with self.assertRaisesRegex(CodecError, 'boolean'):
         decode_dynamic(malformed_packed_bool)
      wrong_hash = bytearray(encode(codec_cases()))
      descriptor_size = int.from_bytes(wrong_hash[:4], WIRE_ENDIAN)
      wrong_hash[4 + descriptor_size] ^= 0x80
      with self.assertRaisesRegex(CodecError, 'hash'):
         decode(bytes(wrong_hash), CodecCases)
      with self.assertRaisesRegex(CodecError, 'hash'):
         decode_dynamic(bytes(wrong_hash))

   def test_01_codec_10_zero_length_packed_occurrence_decodes_as_an_empty_array(self):
      wire = encode(CodecCases()) + wire_u32(15) + wire_u32(0)
      decoded = decode(wire, CodecCases)
      self.assertEqual(decoded.points, [])
      self.assertEqual(decode_dynamic(wire).fields['points'], [])

   def test_01_codec_09_packed_field_occurrences_concatenate_in_wire_order(self):
      endian_prefix = '<' if WIRE_ENDIAN == 'little' else '>'
      first = Complex32(1.25, -2.5)
      second = Complex32(3.5, 4.75)
      wire = encode(CodecCases(points=[first]))
      wire += wire_u32(15) + wire_u32(8)
      wire += struct.pack(endian_prefix + 'ff', second.real, second.imag)
      wire += wire_u32(15) + wire_u32(0)

      decoded = decode(wire, CodecCases)
      self.assertEqual(decoded.points, [first, second])
      self.assertEqual(decode_dynamic(wire).fields['points'], [first, second])

   def test_03_limits_06_packed_field_rejects_non_multiple_element_length(self):
      malformed = encode(codec_cases()) + wire_u32(15) + wire_u32(1) + b'\x00'
      with self.assertRaisesRegex(CodecError, 'packed field length'):
         decode(malformed, CodecCases)
      with self.assertRaisesRegex(CodecError, 'packed field length'):
         decode_dynamic(malformed)

      endian_prefix = '<' if WIRE_ENDIAN == 'little' else '>'
      valid_after_bad_repeated = (encode(CodecCases()) + wire_u32(12) +
         wire_u32(1) + b'\x00' + wire_u32(12) + wire_u32(2) +
         struct.pack(endian_prefix + 'h', 123))
      with self.assertRaises(CodecError):
         decode(valid_after_bad_repeated, CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(valid_after_bad_repeated)

      valid_after_bad_packed = (encode(CodecCases()) + wire_u32(15) +
         wire_u32(1) + b'\x00' + wire_u32(15) + wire_u32(8) +
         struct.pack(endian_prefix + 'ff', 1.25, -2.5))
      with self.assertRaises(CodecError):
         decode(valid_after_bad_packed, CodecCases)
      with self.assertRaises(CodecError):
         decode_dynamic(valid_after_bad_packed)


if __name__ == '__main__':
   unittest.main()
