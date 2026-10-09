"""Independent wire and fallback checks for the optional native Packed kernel."""
import gc
import math
import os
import random
import struct
import unittest
from unittest.mock import patch

import sdl_runtime as runtime
from benchmark import BenchPayload
from bench_cases import BenchRecordBatch, BenchRecord, BenchPose, BenchVector

native = runtime._sdl_native


@unittest.skipIf(native is None, 'optional extension is not built')
class NativeTests(unittest.TestCase):
   def contexts(self, cls):
      text = runtime.description(cls)
      with patch.dict(os.environ, {'SDL_PYTHON_NO_NATIVE': '1'}):
         baseline = runtime.prepare(text, cls)
      with patch.dict(os.environ, {'SDL_PYTHON_NO_NATIVE': ''}):
         accelerated = runtime.prepare(text, cls)
      self.assertFalse(baseline.native_packed)
      self.assertTrue(accelerated.native_packed)
      return baseline, accelerated

   def test_01_codec_native_scalar_oracles(self):
      cases = {
         'bool': ('?', [False, True]),
         'int8': ('b', [-128, -1, 0, 127]),
         'int16': ('h', [-32768, -1, 0, 32767]),
         'int32': ('i', [-2**31, -1, 0, 2**31-1]),
         'int64': ('q', [-2**63, -1, 0, 2**63-1]),
         'fl32': ('f', [-0.0, 1.25, float('inf'), -float('inf')]),
         'fl64': ('d', [-0.0, 1.25, float('inf'), -float('inf')]),
      }
      for kind, (fmt, values) in cases.items():
         with self.subTest(kind=kind):
            plan = native.prepare((kind,))
            expected = struct.pack('>' + fmt * len(values), *values)
            self.assertEqual(native.encode(plan, values), expected)
            for data in (expected, bytearray(expected), memoryview(expected)):
               decoded = native.decode(plan, data, len(values))
               self.assertEqual(native.encode(plan, decoded), expected)
            self.assertEqual(native.decode(plan, b'', 0), [])
            self.assertEqual(native.encode(plan, []), b'')
            if kind.startswith('fl'):
               decoded = native.decode(plan, native.encode(plan, [float('nan')]), 1)
               self.assertTrue(math.isnan(decoded[0]))
      rng = random.Random(437)
      for kind, fmt, width in [('fl32', 'f', 4), ('fl64', 'd', 8)]:
         plan = native.prepare((kind,))
         raw = b''.join(rng.getrandbits(width*8).to_bytes(width, 'big') for _ in range(512))
         values = struct.unpack('>' + fmt * 512, raw)
         # Python floats may quiet signaling float32 NaNs during conversion.
         expected = struct.pack('>' + fmt * 512, *values)
         self.assertEqual(native.encode(plan, values), expected)
         self.assertEqual(native.encode(plan, native.decode(plan, raw, 512)), expected)
      for kind, cls, fmt in [('c32', runtime.Complex32, 'ff'), ('c64', runtime.Complex64, 'dd')]:
         plan = native.prepare((kind, cls))
         values = [cls(-0.0, 1.25), cls(float('inf'), -2.5)]
         expected = struct.pack('>' + fmt * 2, -0.0, 1.25, float('inf'), -2.5)
         self.assertEqual(native.encode(plan, values), expected)
         self.assertEqual(native.encode(plan, native.decode(plan, expected, 2)), expected)

   def test_01_codec_native_messages_and_lifetime(self):
      for cls in (BenchPayload, BenchRecordBatch):
         baseline, accelerated = self.contexts(cls)
         for count in (0, 1, 3, 127, 128, 1000):
            if cls is BenchPayload:
               message = cls(header='été\0😀', samples=[runtime.Complex32(i*.25, -i*.5) for i in range(count)])
            else:
               message = cls(records=[BenchRecord(id=i-7,
                  pose=BenchPose(position=BenchVector(values=[i*.25, -0.0, -i*.5]),
                     rotation=[0., 0., 0., 1.]), measures=[i*.125, -i*.5]) for i in range(count)])
            wire = runtime.encode(baseline, message)
            self.assertEqual(runtime.encode(accelerated, message), wire)
            self.assertEqual(runtime.decode(accelerated, wire), message)
            self.assertEqual(runtime.encode(baseline, runtime.decode(accelerated, wire)), wire)
            self.assertEqual(runtime.decode_dynamic(accelerated, wire), runtime.decode_dynamic(baseline, wire))
            if count == 3:
               for end in range(len(wire)):
                  with self.assertRaises(runtime.CodecError):
                     runtime.decode(accelerated, wire[:end])
               with self.assertRaises(runtime.CodecError):
                  runtime.decode(accelerated, wire + b'\0')
      # Capsules own their classes and child metadata independently of Context.
      plan = accelerated.native_packed[('BenchRecord', ())]
      del accelerated
      gc.collect()
      self.assertEqual(native.decode(plan, native.encode(plan, message.records), len(message.records)), message.records)

   def test_01_codec_native_reentrant_sequence(self):
      # Numeric conversions may execute Python code that changes the inputs.
      values = []
      class MutatingFloat:
         def __float__(self):
            values.clear()
            return 1.25
      values.extend([MutatingFloat(), 2.5])
      plan = native.prepare(('fl64',))
      self.assertEqual(native.encode(plan, values), struct.pack('>dd', 1.25, 2.5))
      self.assertEqual(values, [])

   def test_02_invalid_native_arguments(self):
      for spec in (None, (), ('missing',), ('c32', 3), ('array', 0, ('int32',)),
                   ('array', 65537, ('int8',)), ('record', BenchRecord, ()),
                   ('record', BenchRecord, (('id', ('missing',)),))):
         with self.assertRaises((ValueError, TypeError)):
            native.prepare(spec)
      plan = native.prepare(('array', 3, ('int32',)))
      for values in ([[1, 2]], [[1, 2, 3, 4]], [[1, 2, 2**31]]):
         with self.assertRaises((ValueError, OverflowError)):
            native.encode(plan, values)
      for count, data in ((-1, b''), (2**32, b''), (1, b'\0'*11), (1, b'\0'*13)):
         with self.assertRaises(ValueError):
            native.decode(plan, data, count)
      with self.assertRaises(ValueError):
         native.decode(native.prepare(('bool',)), b'\2', 1)
      with self.assertRaises(TypeError):
         native.encode(native.prepare(('bool',)), [1])
      for kind in ('int8', 'int16', 'int32', 'int64'):
         bits = int(kind[3:])
         for value in (-2**(bits-1)-1, 2**(bits-1), 1.5):
            with self.assertRaises((TypeError, OverflowError)):
               native.encode(native.prepare((kind,)), [value])
      with self.assertRaises(OverflowError):
         native.encode(native.prepare(('fl32',)), [1e300])
      with self.assertRaises(ValueError):
         native.decode(object(), b'', 0)
      with self.assertRaises(BufferError):
         native.decode(native.prepare(('int8',)), memoryview(b'abcd')[::2], 2)

   def test_02_invalid_native_schema_evolution_fallback(self):
      # Dropped local fields require the ordinary evolution reader.
      class LegacyRecord(runtime.SdlMessage):
         _SDL_NAME = 'BenchRecord'
         _SDL_DESCRIPTOR = BenchRecord._SDL_DESCRIPTOR
         _SDL_FIELDS = ((1, 'id', 'required', 'int32', ()),)
      class LegacyBatch(runtime.SdlMessage):
         _SDL_NAME = 'BenchRecordBatch'
         _SDL_DESCRIPTOR = BenchRecordBatch._SDL_DESCRIPTOR
         _SDL_FIELDS = ((1, 'records', 'packed', 'BenchRecord', ()),)
      with patch.dict(globals(), {'BenchRecord': LegacyRecord}):
         ctx = runtime.prepare(runtime.description(BenchRecordBatch), (LegacyBatch,))
      self.assertNotIn(('BenchRecord', ()), ctx.native_packed)
      full_ctx = runtime.prepare(runtime.description(BenchRecordBatch), BenchRecordBatch)
      sample = BenchRecordBatch(records=[BenchRecord(id=17,
         pose=BenchPose(position=BenchVector(values=[1, 2, 3]), rotation=[0, 0, 0, 1]), measures=[4, 5])])
      decoded = runtime.decode(ctx, runtime.encode(full_ctx, sample))
      self.assertIsInstance(decoded, LegacyBatch)
      self.assertEqual(decoded.records[0].id, 17)
      self.assertFalse(hasattr(decoded.records[0], 'pose'))
      baseline, accelerated = self.contexts(BenchRecordBatch)
      bad = BenchRecordBatch(records=[BenchRecord(id=2**31,
         pose=BenchPose(position=BenchVector(values=[0, 0, 0]), rotation=[0]*4), measures=[0, 0])])
      for ctx in (baseline, accelerated):
         with self.assertRaises(runtime.CodecError):
            runtime.encode(ctx, bad)
