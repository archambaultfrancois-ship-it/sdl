import pathlib
import math
import struct
import unittest

from codec_cases import CodecCases, State
from schema import FixedItem, RootPayload, VarItem
from sdl_runtime import CodecError, Complex32, Complex64, decode, encode


FIXTURE = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / 'root_payload.bin'


def fl32(value):
   return struct.unpack('<f', struct.pack('<f', value))[0]


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
      self.assertEqual(wire, FIXTURE.read_bytes())
      self.assertEqual(decode(wire, RootPayload), original)
      self.assertEqual(decode(FIXTURE.read_bytes(), RootPayload), original)
      self.assertIsNot(original.fixed_array, decode(wire, RootPayload).fixed_array)

   def test_scalars_optionals_enums_arrays_and_empty_values(self):
      original = codec_cases()
      decoded = decode(encode(original), CodecCases)
      self.assertEqual(decoded, original)
      self.assertTrue(math.copysign(1.0, decoded.ratio) < 0.0)

   def test_absent_optionals_and_empty_arrays(self):
      original = CodecCases()
      decoded = decode(encode(original), CodecCases)
      self.assertEqual(decoded, original)
      self.assertIsNone(decoded.tiny)
      self.assertIsNone(decoded.optional_enabled)
      self.assertEqual(decoded.bool_flags, [])

   def test_unknown_fields_are_skipped_and_truncation_fails(self):
      wire = encode(codec_cases())
      extended = wire + (999).to_bytes(4, 'little') + (3).to_bytes(4, 'little') + b'\xa1\xb2\xc3'
      self.assertEqual(decode(extended, CodecCases), codec_cases())
      with self.assertRaises(CodecError):
         decode(wire[:-1], CodecCases)

   def test_invalid_boolean_and_wrong_hash_are_rejected(self):
      malformed_bool = encode(codec_cases()) + (17).to_bytes(4, 'little') + (1).to_bytes(4, 'little') + b'\x02'
      with self.assertRaisesRegex(CodecError, 'boolean'):
         decode(malformed_bool, CodecCases)
      wrong_hash = bytearray(encode(codec_cases()))
      wrong_hash[0] ^= 0x80
      with self.assertRaisesRegex(CodecError, 'hash'):
         decode(bytes(wrong_hash), CodecCases)


if __name__ == '__main__':
   unittest.main()
