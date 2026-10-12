"""Wire validity, ownership and alternate representations for Packed buffers."""
import array
import gc
import os
import struct
import unittest
from unittest.mock import patch

import sdl_runtime as sdl
from benchmark import BenchPayload
from bench_cases import BenchRecordBatch, BenchRecord, BenchPose, BenchVector


MIXED_DESCRIPTOR = '''SDL2
message MixedBatch {
  1: packed MixedRecord records;
}
message MixedNested {
  1: required MixedBatch batch;
}
message MixedRecord {
  1: required bool flag;
  2: required int16 small;
  3: required int64 large;
  4: required fl32[2] vector;
  5: required c64 sample;
}
'''


class MixedRecord(sdl.SdlMessage):
   _SDL_NAME = 'MixedRecord'
   _SDL_DESCRIPTOR = MIXED_DESCRIPTOR
   _SDL_FIELDS = ((1, 'flag', 'required', 'bool', ()),
      (2, 'small', 'required', 'int16', ()), (3, 'large', 'required', 'int64', ()),
      (4, 'vector', 'required', 'fl32', (2,)), (5, 'sample', 'required', 'c64', ()))


class MixedBatch(sdl.SdlMessage):
   _SDL_NAME = 'MixedBatch'
   _SDL_DESCRIPTOR = MIXED_DESCRIPTOR
   _SDL_FIELDS = ((1, 'records', 'packed', 'MixedRecord', ()),)


class MixedNested(sdl.SdlMessage):
   _SDL_NAME = 'MixedNested'
   _SDL_DESCRIPTOR = MIXED_DESCRIPTOR
   _SDL_FIELDS = ((1, 'batch', 'required', 'MixedBatch', ()),)


@unittest.skipIf(sdl._sdl_native is None, 'optional extension is not built')
class BufferTests(unittest.TestCase):
   def context(self, cls):
      with patch.dict(os.environ, {'SDL_PYTHON_NO_NATIVE': ''}):
         return sdl.prepare(sdl.description(cls), cls)

   def test_01_codec_buffers_complex_and_nested_records(self):
      for cls, leaf, kind in ((BenchPayload, 'samples', 'c32'), (BenchRecordBatch, 'records', 'BenchRecord')):
         ctx = self.context(cls)
         self.assertIn(cls._SDL_NAME, ctx.native_messages)
         for count in (0, 1, 3, 127, 128, 1000):
            if cls is BenchPayload:
               values = [sdl.Complex32(i*.25, -(i%97)*.5) for i in range(count)]
               message = cls(header='été\0😀', samples=values)
               payload = b''.join(struct.pack('>ff', v.real, v.imag) for v in values)
            else:
               values = [BenchRecord(id=i, pose=BenchPose(position=BenchVector(values=[i*.25, -i*.5, i%97]),
                  rotation=[0, 0, 0, 1]), measures=[i*.125, -(i%31)*.5]) for i in range(count)]
               message = cls(records=values)
               payload = b''.join(struct.pack('>i9f', v.id, *v.pose.position.values, *v.pose.rotation, *v.measures) for v in values)
            wire = sdl.encode(ctx, message)
            packed = sdl.PackedArray.from_values(ctx, kind, values)
            self.assertEqual(packed.buffer.tobytes(), payload)
            self.assertEqual(packed.tolist(), values)
            self.assertEqual(packed, values)
            self.assertTrue(packed.buffer.readonly)
            setattr(message, leaf, packed)
            self.assertEqual(sdl.encode(ctx, message), wire)
            # Fresh contexts have independently prepared but compatible layouts.
            self.assertEqual(sdl.encode(self.context(cls), message), wire)
            decoded = sdl.decode(ctx, wire, packed='view')
            view = getattr(decoded, leaf)
            self.assertIsInstance(view, sdl.PackedArray)
            self.assertEqual(view.buffer.tobytes(), payload)
            self.assertEqual(sdl.encode(ctx, decoded), wire)
            self.assertEqual(sdl.decode(ctx, wire), message)
            output = bytearray(b'prefix' + b'?'*len(wire) + b'suffix')
            self.assertEqual(sdl.encode_into(ctx, decoded, output, 6), len(wire))
            self.assertEqual(output, b'prefix' + wire + b'suffix')
            self.assertEqual(view[:].tolist(), values)
            self.assertEqual(view[::-1], values[::-1])
            self.assertEqual(view[2:1].tolist(), [])
            if count:
               self.assertEqual(view[-1], values[-1])
               self.assertEqual(view[0], values[0])
            with self.assertRaises(IndexError):
               view[count]
            released = view.buffer
            released.release()
            self.assertEqual(view.tolist(), values)
            with self.assertRaises(AttributeError):
               view._count = 123
            if count == 3:
               for end in range(len(wire)):
                  with self.assertRaises(sdl.CodecError):
                     sdl.decode(ctx, wire[:end], packed='view')
               with self.assertRaises(sdl.CodecError):
                  sdl.decode(ctx, wire + b'\0', packed='view')

   def test_01_codec_buffers_byteorders_mutability_and_lifetime(self):
      ctx = self.context(BenchPayload)
      values = [sdl.Complex32(-0.0, 1.25), sdl.Complex32(2.5, -7.0)]
      expected = struct.pack('>4f', -0.0, 1.25, 2.5, -7.0)
      for order, fmt in (('little', '<4f'), ('big', '>4f'), ('native', '=4f')):
         raw = bytearray(struct.pack(fmt, -0.0, 1.25, 2.5, -7.0))
         packed = sdl.PackedArray.from_buffer(ctx, 'c32', memoryview(raw).toreadonly(), byteorder=order)
         raw[:] = b'\xff'*len(raw)
         self.assertEqual(packed.buffer.tobytes(), expected)
         raw_native = struct.pack(fmt, -0.0, 1.25, 2.5, -7.0)
         deferred = sdl.PackedArray.from_buffer(ctx, 'c32', raw_native, byteorder=order, defer=True)
         self.assertEqual(deferred.buffer.tobytes(), raw_native)
         self.assertEqual(deferred.wire_buffer.tobytes(), expected)
         self.assertEqual(deferred.tolist(), values)
         self.assertEqual(deferred[-1], values[-1])
         message = BenchPayload(header='', samples=deferred)
         self.assertEqual(sdl.encode(ctx, message), b'\1\0\2'+expected)
         output = bytearray(19)
         self.assertEqual(sdl.encode_into(ctx, message, output), 19)
         self.assertEqual(output, b'\1\0\2'+expected)
      typed = array.array('f', [-0.0, 1.25, 2.5, -7.0])
      packed = sdl.PackedArray.from_buffer(ctx, 'c32', typed)
      self.assertEqual(packed.buffer.tobytes(), expected)
      raw_bits = bytes.fromhex('80000000 7fc01234 7fa05678 ff800000')
      packed = sdl.PackedArray.from_buffer(ctx, 'c32', raw_bits, byteorder='big')
      self.assertEqual(packed.buffer.tobytes(), raw_bits)
      wire_bits = sdl.encode(ctx, BenchPayload(header='', samples=packed))
      self.assertEqual(wire_bits, b'\1\0\2'+raw_bits)
      self.assertEqual(sdl.decode(ctx, wire_bits, packed='view').samples.buffer.tobytes(), raw_bits)
      little_bits = b''.join(raw_bits[i:i+4][::-1] for i in range(0, len(raw_bits), 4))
      self.assertEqual(sdl.PackedArray.from_buffer(ctx, 'c32', little_bits, byteorder='little').buffer.tobytes(), raw_bits)
      deferred_bits = sdl.PackedArray.from_buffer(ctx, 'c32', little_bits, byteorder='little', defer=True)
      self.assertEqual(sdl.encode(ctx, BenchPayload(header='', samples=deferred_bits)), wire_bits)
      m = BenchPayload(header='retained', samples=values)
      wire = sdl.encode(ctx, m)
      decoded = sdl.decode(ctx, wire, packed='view')
      self.assertIs(decoded.samples.buffer.obj, wire)
      mutable = bytearray(wire)
      retained = sdl.decode(ctx, memoryview(mutable).toreadonly(), packed='view')
      mutable[:] = b'\xff'*len(mutable)
      del ctx, wire, mutable
      gc.collect()
      self.assertEqual(retained.samples.tolist(), values)
      self.assertEqual(decoded.samples.tolist(), values)

   def test_01_codec_buffers_mixed_widths_booleans_and_generic_root(self):
      ctx = self.context(MixedNested)
      self.assertIn('MixedBatch', ctx.native_messages)
      self.assertNotIn('MixedNested', ctx.native_messages)
      values = [MixedRecord(flag=True, small=-7, large=-2**62, vector=[1.25, -0.0], sample=sdl.Complex64(3.5, -4.25))]
      expected = struct.pack('>bhqffdd', 1, -7, -2**62, 1.25, -0.0, 3.5, -4.25)
      for order, prefix in (('big', '>'), ('little', '<'), ('native', '=')):
         packed = sdl.PackedArray.from_buffer(ctx, 'MixedRecord', struct.pack(prefix+'bhqffdd', 1, -7, -2**62, 1.25, -0.0, 3.5, -4.25), byteorder=order)
         self.assertEqual(packed.buffer.tobytes(), expected)
         self.assertEqual(packed.tolist(), values)
         deferred = sdl.PackedArray.from_buffer(ctx, 'MixedRecord', struct.pack(prefix+'bhqffdd', 1, -7, -2**62, 1.25, -0.0, 3.5, -4.25), byteorder=order, defer=True)
         self.assertEqual(deferred.wire_buffer.tobytes(), expected)
         self.assertEqual(sdl.encode(ctx, MixedBatch(records=deferred)), b'\1\1'+expected)
         nested = MixedNested(batch=MixedBatch(records=deferred))
         self.assertEqual(sdl.decode(ctx, sdl.encode(ctx, nested)), nested)
      msg = MixedNested(batch=MixedBatch(records=packed))
      wire = sdl.encode(ctx, msg)
      result = sdl.decode(ctx, wire, packed='view')
      self.assertIsInstance(result.batch.records, sdl.PackedArray)
      self.assertEqual(result, msg)
      self.assertEqual(sdl.encode_into(ctx, msg, bytearray(len(wire))), len(wire))
      with self.assertRaises(sdl.CodecError):
         sdl.PackedArray.from_buffer(ctx, 'MixedRecord', b'\2'+expected[1:], byteorder='big')
      wire = sdl.encode(ctx, MixedBatch(records=packed))
      for data in (wire[:2]+b'\2'+wire[3:], wire[:1]+b'\x80\0'+wire[2:], wire+b'\0'):
         with self.assertRaises(sdl.CodecError):
            sdl.decode(ctx, data, packed='view')
      # Generic nested root also validates Packed boolean bytes.
      wire = sdl.encode(ctx, msg)
      with self.assertRaises(sdl.CodecError):
         sdl.decode(ctx, wire[:2]+b'\2'+wire[3:], packed='view')

   def test_02_invalid_buffers_layouts_and_message_validity(self):
      ctx = self.context(BenchPayload)
      for raw, kwargs in ((b'\0', {}), (b'\0'*8, {'byteorder':'unknown'}),
                          (memoryview(b'\0'*16)[::2], {}), (b'\0'*8, {'dimensions':(-1,)})):
         with self.assertRaises(sdl.CodecError):
            sdl.PackedArray.from_buffer(ctx, 'c32', raw, **kwargs)
      wrong = sdl.PackedArray.from_values(ctx, 'int64', [1])
      with self.assertRaises(sdl.CodecError):
         sdl.encode(ctx, BenchPayload(header='', samples=wrong))
      with self.assertRaises(TypeError):
         sdl.PackedArray()
      values = sdl.PackedArray.from_values(ctx, 'c32', [sdl.Complex32(1, 2)])
      msg = BenchPayload(header='x', samples=values)
      wire = sdl.encode(ctx, msg)
      for target, offset in ((bytes(len(wire)), 0), (bytearray(len(wire)-1), 0),
                             (bytearray(len(wire)), -1), (bytearray(len(wire)), True),
                             (memoryview(bytearray(len(wire)*2))[::2], 0)):
         with self.assertRaises(sdl.CodecError):
            sdl.encode_into(ctx, msg, target, offset)
      for data in (b'\x81\0'+wire[1:], b'\1\x80\0'+wire[3:], b'\1\1\xff'+wire[3:],
                   b'\1\1x\xff\xff\xff\xff\x0f', b'\1\1x\xff\xff\xff\xff\x10'):
         with self.assertRaises(sdl.CodecError):
            sdl.decode(ctx, data, packed='view')
      with self.assertRaises(sdl.CodecError):
         sdl.encode(ctx, BenchPayload(header='\ud800', samples=values))
      with self.assertRaises(sdl.CodecError):
         sdl.decode(ctx, wire, packed='unknown')
      # The view API requires the extension; ordinary encoding still accepts valid buffers.
      with patch.dict(os.environ, {'SDL_PYTHON_NO_NATIVE': '1'}):
         fallback = sdl.prepare(sdl.description(BenchPayload), BenchPayload)
      self.assertEqual(sdl.encode(fallback, msg), wire)
      with self.assertRaises(sdl.CodecError):
         sdl.decode(fallback, wire, packed='view')
      with self.assertRaises(sdl.CodecError):
         sdl.PackedArray.from_values(fallback, 'c32', [])

   def test_02_invalid_buffers_native_message_plan(self):
      native = sdl._sdl_native
      for fields in ((), (('x',),), ((7, None, None),), (('x', object(), None),)):
         with self.assertRaises(ValueError):
            native.prepare_message(1, BenchPayload, sdl.PackedArray, fields)
      ctx = self.context(BenchPayload)
      fields = (('header', None, None), ('samples', ctx.native_packed[('c32', ())], ctx.native_layouts[('c32', ())]))
      for root_id in (0, -1, 2**32, 2**80):
         with self.assertRaises((ValueError, OverflowError)):
            native.prepare_message(root_id, BenchPayload, sdl.PackedArray, fields)
      wide_id = native.prepare_message(128, BenchPayload, sdl.PackedArray, fields)
      msg = BenchPayload(header='', samples=sdl.PackedArray.from_values(ctx, 'c32', []))
      wide_wire = native.encode_message(wide_id, msg)
      self.assertEqual(wide_wire, b'\x80\1\0\0')
      self.assertEqual(native.decode_message(wide_id, wide_wire), msg)
      with self.assertRaises(ValueError):
         native.decode_message(wide_id, b'\x80\0\0\0')
      plan = ctx.native_messages['BenchPayload']
      with self.assertRaises(ValueError):
         native.decode_message(plan, b'\2\0\0')
      with self.assertRaises(TypeError):
         native.decode_message(plan, bytearray(b'\1\0\0'))
