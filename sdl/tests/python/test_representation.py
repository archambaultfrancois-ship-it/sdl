"""Independent wire oracles for representation clauses and unsigned primitives."""
import importlib
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'sdlc'))
from generator import MsgParser, canonical_type_descriptor
from python_backend import generate_python
from sdl_runtime import CodecError, description, prepare, encode, encode_into, decode, decode_dynamic, compare_catalogues


class RepresentationTests(unittest.TestCase):
   @classmethod
   def setUpClass(cls):
      cls.temporary = tempfile.TemporaryDirectory()
      generate_python(str(ROOT / 'tests/fixtures/representation'), cls.temporary.name)
      sys.path.insert(0, cls.temporary.name)
      cls.packet = importlib.import_module('packet')
      cls.unsigned = importlib.import_module('unsigned')
      cls.arrays = importlib.import_module('arrays')
      cls.quantized = importlib.import_module('quantized')

   @classmethod
   def tearDownClass(cls):
      sys.path.remove(cls.temporary.name)
      for name in ('packet', 'unsigned', 'arrays', 'quantized'):
         sys.modules.pop(name, None)
      cls.temporary.cleanup()

   def test_01_codec_quantized_float_oracle(self):
      Q, Child = self.quantized.Quantized, self.quantized.QuantChild
      ctx = prepare(description([Q, Child]), [Q, Child])
      x = Q(flag=True, value=0.625, precise=-1.25, maybe=-1.25,
         samples=[0.125, 0.375, -0.125, -0.375], matrix=[[-8, 7.5], [0.75, -0.75]],
         child=Child(value=2.625), tail=165)
      # Signed codes: 2, -12, -2, [0,2,0,-2], [-16,15,2,-2], 10.
      expected = bytes.fromhex('0382ff4001f0040081e83c5e0aa5')
      self.assertEqual(encode(ctx, x), expected)
      y = decode(ctx, expected)
      self.assertEqual((y.value, y.maybe, y.samples, y.matrix, y.child.value),
         (0.5, -1.0, [0.0, 0.5, 0.0, -0.5], [[-8.0, 7.5], [1.0, -1.0]], 2.5))
      self.assertAlmostEqual(y.precise, -1.2)
      self.assertEqual(encode(ctx, y), expected)
      self.assertEqual(decode_dynamic(ctx, expected).fields['value'], 0.5)
      # Representation differences preserve logical compatibility; use the sender's q.
      remote = Q._SDL_DESCRIPTOR.replace('q0.25', 'q0.5')
      self.assertTrue(compare_catalogues(remote, Q._SDL_DESCRIPTOR).compatible)
      self.assertEqual(decode(prepare(remote, [Q, Child]), expected).value, 1.0)
      for invalid in (float('nan'), float('inf'), -float('inf'), 16.0, -16.5):
         x.value = invalid
         with self.subTest(value=invalid), self.assertRaises(CodecError):
            encode(ctx, x)
      for size in range(len(expected)):
         with self.assertRaises(CodecError):
            decode(ctx, expected[:size])
      bad = bytearray(expected); bad[3] |= 1
      with self.assertRaises(CodecError):
         decode(ctx, bad)

   def test_02_invalid_quantum_clauses_and_catalogues(self):
      for t, w in (('fl32','q=0.1'), ('int32','bits=12,q=0.1'),
            ('c32','bits=12,q=0.1'), ('fl64','bits=12,q=0'), ('fl64','bits=12,q=-0.1'),
            ('fl64','bits=12,q=NaN'), ('fl64','bits=12,q=1e999'),
            ('fl64','bits=12,q=1e-999'), ('fl32','bits=33,q=0.1'),
            ('fl64','bits=65,q=0.1'), ('fl64','bits=12,q=0.1,encoding=varint'),
            ('fl64','bits=12,q=0.1,q=0.1'), ('fl32','bits=12,q=1e38'),
            ('fl32','bits=12,q=1e-46'), ('fl64','bits=12,q=1e308')):
         with self.subTest(type=t, wire=w), self.assertRaises(ValueError):
            MsgParser().parse_text('message X {\n '+t+' value wire('+w+');\n}\n')
      for suffix in ('q0.1', 'b12,q0', 'b12,qNaN', 'b65,q0.1',
            'b12,q0.1,v', 'b12,q0.1,q0.1', 'b12,q1e-1'):
         with self.subTest(suffix=suffix), self.assertRaises(CodecError):
            prepare('SDL2|X{1:fl64:value@'+suffix+';}')
      descriptors = []
      for q in ('0.1', '.1000', '1e-1', '+000.100'):
         parser = MsgParser()
         parser.parse_text('message X {\n fl64 value wire(q='+q+',bits=12);\n}\n')
         descriptors.append(canonical_type_descriptor(parser))
      self.assertEqual(descriptors, [b'SDL2|X{1:fl64:value@b12,q0.1;}'] * 4)

   def test_02_quantum_rejected_on_all_integer_types(self):
      integers = ('int8', 'int16', 'int32', 'int64',
         'uint8', 'uint16', 'uint32', 'uint64')
      shapes = (('', '', ''), ('optional ', '?', ''),
         ('repeated ', '*', ''), ('packed ', '+', ''), ('', '', '[2]'))
      clauses = (('q=0.1', 'q0.1'), ('bits=1,q=0.1', 'b1,q0.1'),
         ('q=0.1,bits=1', 'q0.1,b1'), ('encoding=varint,q=0.1', 'v,q0.1'))
      for t in integers:
         for modifier, marker, dimensions in shapes:
            source = 'message X {\n '+modifier+t+dimensions+' value wire(%s);\n}\n'
            descriptor = 'SDL2|X{1'+marker+':'+t+dimensions+':value@%s;}'
            # The same integer declaration remains valid without a quantum.
            MsgParser().parse_text(source % 'bits=1')
            prepare(descriptor % 'b1')
            for clause, suffix in clauses:
               with self.subTest(type=t, modifier=modifier, dimensions=dimensions, clause=clause):
                  with self.assertRaisesRegex(ValueError, 'wire q requires'):
                     MsgParser().parse_text(source % clause)
                  with self.assertRaisesRegex(CodecError, 'wire q requires'):
                     prepare(descriptor % suffix)

   def test_01_codec_quantum_bit_width_boundaries(self):
      import math
      from sdl_runtime import SdlMessage
      for type_name, bits in (('fl64', 1), ('fl32', 32), ('fl64', 64)):
         descriptor = 'SDL2|QuantumBoundary{1:%s:value@b%d,q0.25;}' % (type_name, bits)
         cls = type('QuantumBoundary', (SdlMessage,), {
            '__module__': __name__, '_SDL_NAME': 'QuantumBoundary',
            '_SDL_DESCRIPTOR': descriptor,
            '_SDL_FIELDS': ((1, 'value', 'required', type_name, ()),)})
         context = prepare(descriptor, cls)
         x = cls(value=-math.ldexp(0.25, bits - 1))
         expected = b'\x01\x80' + b'\x00' * ((bits + 7) // 8 - 1)
         with self.subTest(type=type_name, bits=bits):
            self.assertEqual(encode(context, x), expected)
            self.assertEqual(decode(context, expected).value, x.value)
            x.value = -x.value
            with self.assertRaises(CodecError):
               encode(context, x)
      Wide = self.quantized.QuantWide
      context = prepare(description(Wide), Wide)
      x = Wide(small=-2.0**29, large=-2.0**61)
      expected = bytes.fromhex('02800000008000000000000000')
      self.assertEqual(encode(context, x), expected)
      self.assertEqual(decode(context, expected), x)

   def test_01_codec_scalar_oracle(self):
      P = self.packet.Packet
      ctx = prepare(description([P]), [P])
      value = P(active=True, alarm=False, level=-1, counter=2**64-1, delta=-2, value=42)
      # ID=1, two booleans + signed 6-bit -1, ULEB64 max, zigzag(-2)=3,
      # four alignment zeros, IEEE-754 42, two trailing padding zeros.
      expected = bytes.fromhex('01bf' + 'ff'*9 + '01' + '03' + '00'*4 + '4045000000000000' + '0000')
      self.assertEqual(encode(ctx, value), expected)
      self.assertEqual(decode(ctx, expected), value)
      self.assertEqual(decode_dynamic(ctx, expected).fields['counter'], 2**64-1)
      buffer = bytearray(len(expected)+4)
      self.assertEqual(encode_into(ctx,value,buffer,2),len(expected))
      self.assertEqual(buffer[2:-2],expected)
      self.assertEqual(description([P]), P._SDL_DESCRIPTOR)

   def test_01_codec_unsigned_full_width_and_arrays(self):
      U = self.unsigned.Unsigned
      ctx = prepare(description([U]), [U])
      value = U(tiny=255,small=65535,medium=2**32-1,large=2**64-1,values=[0,2**64-1])
      expected = bytes.fromhex('01' + 'ff'*15 + '02' + '00'*8 + 'ff'*8)
      self.assertEqual(encode(ctx,value), expected)
      self.assertEqual(decode(ctx,expected),value)
      for field in ('tiny','small','medium','large'):
         original=getattr(value,field)
         for invalid in (-1,original+1):
            setattr(value,field,invalid)
            with self.assertRaises(CodecError):encode(ctx,value)
         setattr(value,field,original)

   def test_01_codec_nested_and_bit_arrays_oracle(self):
      A,C = self.arrays.Arrays,self.arrays.Child
      ctx=prepare(description([A]),[A])
      x=A(matrix=[[-2048,-1],[0,2047]],flags=[0,7,1],code=-1,child=C(delta=-1,level=31),label='hi')
      # 12-bit matrix; count=3 followed by 000111001+7 zeros;
      # optional flag=1 followed by 1111+4 zeros; child starts at offset 12,
      # its two fields fill FF; one pad byte; string starts at offset 16.
      expected=bytes.fromhex('01800fff0007ff031c8001f000ff00000002686900')
      self.assertEqual(encode(ctx,x),expected)
      self.assertEqual(decode(ctx,expected),x)
      self.assertEqual(decode_dynamic(ctx,expected).fields['matrix'],x.matrix)
      # Unknown fields still consume their representation before the next value.
      source='message Arrays {\n 1: required int16[2][2] matrix;\n}\n'
      parser=MsgParser();parser.parse_text(source)
      from python_backend import PythonBackend
      namespace={};exec(PythonBackend(parser).generate(),namespace)
      local=namespace['Arrays'];local.__module__=self.arrays.__name__
      remote=prepare(description([A]),[local])
      self.assertEqual(decode(remote,expected).matrix,x.matrix)

   def test_01_codec_packed_struct_bit_fields(self):
      parser=MsgParser()
      parser.parse_text('message BitRecord {\n int16 value wire(bits=12);\n bool flag wire(bits=1);\n}\nmessage Records {\n packed BitRecord items;\n uint8 tail;\n}\n')
      from python_backend import PythonBackend
      namespace={};exec(PythonBackend(parser).generate(),namespace)
      R,B=namespace['Records'],namespace['BitRecord']
      ctx=prepare(description([R]),[R,B])
      value=R(items=[B(value=-2048,flag=True),B(value=2047,flag=False)],tail=165)
      # Each child ends on a byte boundary: 12 bits + 1 bit + 3 zero bits.
      expected=bytes.fromhex('020280087ff0a5')
      self.assertEqual(encode(ctx,value),expected)
      self.assertEqual(decode(ctx,expected),value)
      self.assertEqual(decode_dynamic(ctx,expected).fields['tail'],165)
      for index in (3,5):
         bad=bytearray(expected);bad[index]|=1
         with self.assertRaises(CodecError):decode(ctx,bad)
      value.items[0].value=2048
      with self.assertRaises(CodecError):encode(ctx,value)

   def test_02_invalid_padding_truncation_and_overflow(self):
      P=self.packet.Packet;ctx=prepare(description([P]),[P])
      value=P(active=True,alarm=False,level=-1,counter=2**64-1,delta=-2,value=42)
      data=encode(ctx,value)
      for i in range(len(data)):
         with self.subTest(truncated=i),self.assertRaises(CodecError):decode(ctx,data[:i])
      for index in (-1,-2,13,14,15,16):
         bad=bytearray(data);bad[index]=1
         with self.subTest(padding=index),self.assertRaises(CodecError):decode(ctx,bad)
      with self.assertRaises(CodecError):decode(ctx,data+b'\0')
      for invalid in (-33,32):
         value.level=invalid
         with self.assertRaises(CodecError):encode(ctx,value)
      value.level=-1
      # ULEB64 overflow and redundant zero continuation.
      for bad in (b'\x01\xbf'+b'\xff'*9+b'\x02'+data[12:], b'\x01\xbf\x80\x00'+data[12:]):
         with self.assertRaises(CodecError):decode(ctx,bad)

   def test_02_invalid_clauses(self):
      for t,w in (('int32','bits=0'),('int32','bits=33'),('fl64','bits=12'),
            ('int32','bits=12,encoding=varint'),('uint32','encoding=zigzag'),
            ('bool','encoding=varint'),('int32','align=3'),('int32','align=512'),
            ('int32','pad_after=65536'),('int32','unknown=1'),('int32','bits=2,bits=3'),
            ('int32',''),('int32','encoding=other')):
         with self.subTest(type=t,wire=w),self.assertRaises(ValueError):
            MsgParser().parse_text('message X {\n '+t+' value wire('+w+');\n}\n')

   def test_01_codec_zigzag_boundaries_and_representation_evolution(self):
      P=self.packet.Packet;ctx=prepare(description([P]),[P])
      for n in (-2**63,-1,0,1,2**63-1):
         x=P(delta=n);self.assertEqual(decode(ctx,encode(ctx,x)).delta,n)
      plain=P._SDL_DESCRIPTOR
      import re
      plain=re.sub(r'@[^;]*','',plain)
      self.assertTrue(compare_catalogues(P._SDL_DESCRIPTOR,plain).compatible)
      # Representation comes from the sender's catalogue, independently of local types.
      self.assertEqual(decode(prepare(plain,[P]),encode(prepare(plain,[P]),P(delta=-9))).delta,-9)
