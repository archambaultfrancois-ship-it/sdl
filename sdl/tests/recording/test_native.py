"""Bidirectional compatibility and malformed-input tests of the native C ABI."""
import ctypes as C
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from test_recording import (ROOT, V1, V2, Q, RecordingReader, RecordingWriter, description,
                            encode, prepare, raw_recording, Schema, Channel, Message, Chunk)

LIBRARY = Path(os.environ.get('SDL_RECORDING_LIBRARY', ROOT / 'build/recording/release/libsdl_recording.so'))
class Bytes(C.Structure):
    _fields_ = [('data', C.c_void_p), ('size', C.c_size_t)]
    @classmethod
    def of(cls, value):
        if isinstance(value, str): value = value.encode('utf-8')
        # ctypes retains the pointed-to object for the lifetime of this structure.
        return cls(C.cast(C.c_char_p(value), C.c_void_p), len(value))
    def copy(self): return C.string_at(self.data, self.size)
class Metadata(C.Structure):
    _fields_ = [('key', Bytes), ('value', Bytes)]
class Record(C.Structure):
    _fields_ = [('channel_id', C.c_uint16), ('schema_id', C.c_uint16),
                ('topic', Bytes), ('root_type', Bytes), ('catalogue', Bytes), ('payload', Bytes),
                ('metadata', C.POINTER(Metadata)), ('metadata_count', C.c_size_t),
                ('log_time_ns', C.c_uint64), ('publish_time_ns', C.c_uint64), ('sequence', C.c_uint32)]
Consumer = C.CFUNCTYPE(C.c_int, C.POINTER(Record), C.c_void_p)

def load():
    lib = C.CDLL(str(LIBRARY))
    signatures = {
        'abi_version': (C.c_uint32, []), 'error': (C.c_char_p, []),
        'writer_open': (C.c_void_p, [Bytes, C.c_uint8, C.c_uint64]),
        'writer_channel': (C.c_int, [C.c_void_p, Bytes, Bytes, Bytes, C.POINTER(Metadata), C.c_size_t, C.POINTER(C.c_uint16)]),
        'writer_write': (C.c_int, [C.c_void_p, C.c_uint16, Bytes, C.c_uint64, C.c_uint64, C.c_uint32]),
        'writer_flush': (C.c_int, [C.c_void_p, C.c_uint8]),
        'writer_close': (C.c_int, [C.c_void_p]),
        'reader_open': (C.c_void_p, [Bytes]),
        'reader_next': (C.c_int, [C.c_void_p, C.POINTER(C.POINTER(Record))]),
        'reader_filter': (C.c_int, [C.c_void_p, Bytes, C.c_uint8, C.c_uint64, C.c_uint64]),
        'reader_close': (C.c_int, [C.c_void_p]),
        'validate': (C.c_int, [Bytes, C.POINTER(C.c_uint64)]),
        'replay': (C.c_int, [C.c_void_p, C.c_double, Consumer, C.c_void_p, C.POINTER(C.c_uint64)]),
    }
    for name, (result, args) in signatures.items():
        f = getattr(lib, 'sdl_recording_' + name); f.restype = result; f.argtypes = args
        setattr(lib, name, f)
    return lib

@unittest.skipUnless(LIBRARY.exists(), 'optional native recording library not built')
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.lib = load()
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'capture.mcap'
    def tearDown(self): self.tmp.cleanup()
    def ok(self, result):
        self.assertGreaterEqual(result, 0, self.lib.error().decode())
    def python_record(self, cls=Q, times=(10, 20, 20)):
        payloads = []
        with RecordingWriter(self.path, format='mcap', chunk_size=128) as writer:
            ch = writer.register_channel(topic='radio/é📻\0iq', catalogue=description(cls), root_type='Sample',
                                         metadata={'origin': 'é📻\0'})
            ctx = prepare(description(cls), [cls])
            for i, time in enumerate(times):
                payload = encode(ctx, cls(value=i + .375 if cls is Q else i))
                payloads.append(payload)
                writer.write_encoded(ch, payload, log_time_ns=time, publish_time_ns=time, sequence=i)
        return payloads
    def native_records(self):
        handle = self.lib.reader_open(Bytes.of(str(self.path)))
        self.assertTrue(handle, self.lib.error())
        result = []
        try:
            while True:
                record = C.POINTER(Record)()
                status = self.lib.reader_next(handle, C.byref(record)); self.ok(status)
                if not status:
                    self.assertFalse(record); break
                r = record.contents
                result.append((r.catalogue.copy(), r.payload.copy(), r.log_time_ns, r.publish_time_ns,
                               r.sequence, r.topic.copy(), r.schema_id))
        finally: self.ok(self.lib.reader_close(handle))
        return result
    def test_python_to_native_exact_quantized_bytes_and_uint64(self):
        payloads = self.python_record(times=(2**64-2, 2**64-1, 2**64-1))
        records = self.native_records()
        self.assertEqual([r[1] for r in records], payloads)
        self.assertEqual([r[2] for r in records], [2**64-2, 2**64-1, 2**64-1])
        self.assertTrue(all(r[0] == description(Q).encode() for r in records))
        self.assertTrue(all(r[5] == 'radio/é📻\0iq'.encode() for r in records))
    def test_native_to_python_schema_revisions_exact_bytes(self):
        writer = self.lib.writer_open(Bytes.of(str(self.path)), 0, 128)
        self.assertTrue(writer, self.lib.error())
        payloads = []
        try:
            for i, cls in enumerate((V1, V2, Q)):
                ch = C.c_uint16()
                self.ok(self.lib.writer_channel(writer, Bytes.of('radio/iq'), Bytes.of(description(cls)),
                    Bytes.of('Sample'), None, 0, C.byref(ch)))
                payload = encode(prepare(description(cls), [cls]), cls(value=i))
                payloads.append(payload)
                self.ok(self.lib.writer_write(writer, ch, Bytes.of(payload), 2**64-1, 2**64-1, 2**32-1))
                self.ok(self.lib.writer_flush(writer, 1))
        finally: self.ok(self.lib.writer_close(writer))
        records = list(RecordingReader(self.path).iter_records())
        self.assertEqual(RecordingReader(self.path).info()['format'], 'pcapng')
        self.assertEqual([r.payload for r in records], payloads)
        self.assertEqual([r.sequence for r in records], [2**32-1]*3)
        self.assertEqual(len({r.channel.schema_id for r in records}), 3)
        self.assertEqual(RecordingReader(self.path).validate(), 3)
    def test_filter_replay_and_reentrant_reader_rejection(self):
        self.python_record()
        reader = self.lib.reader_open(Bytes.of(str(self.path)))
        self.ok(self.lib.reader_filter(reader, Bytes.of('radio/é📻\0iq'), 3, 20, 21))
        seen = []
        @Consumer
        def consume(ptr, unused):
            seen.append(ptr.contents.sequence)
            out = C.POINTER(Record)()
            self.assertEqual(self.lib.reader_next(reader, C.byref(out)), -1)
            self.assertFalse(out)
            self.assertEqual(self.lib.reader_close(reader), -1)
            return 0
        count = C.c_uint64()
        try:
            self.ok(self.lib.replay(reader, 0, consume, None, C.byref(count)))
            self.assertEqual(count.value, 2); self.assertEqual(seen, [1, 2])
        finally: self.ok(self.lib.reader_close(reader))
    def test_replay_callback_failure_restores_reader(self):
        self.python_record(times=(2, 1, 1))
        for speed, callback in ((0, Consumer(lambda r,u: 1)), (1, Consumer(lambda r,u: 0))):
            reader = self.lib.reader_open(Bytes.of(str(self.path))); count = C.c_uint64()
            try:
                self.assertEqual(self.lib.replay(reader, speed, callback, None, C.byref(count)), -1)
            finally: self.ok(self.lib.reader_close(reader))
    def test_native_rejects_invalid_profile_and_declarations(self):
        sid = Schema(id=1, name='catalogue', encoding='sdl2', data=description(V1).encode())
        ch = Channel(id=1, schema_id=1, topic='t', message_encoding='sdl2', metadata={'root_type':'Sample'})
        defects = [
            raw_recording(sid, Schema(id=1, name='catalogue', encoding='sdl2', data=description(V2).encode())),
            raw_recording(sid, ch, Channel(id=1, schema_id=1, topic='other', message_encoding='sdl2', metadata={'root_type':'Sample'})),
            raw_recording(sid, ch, Message(channel_id=1, sequence=0, log_time=0, publish_time=0, data=b'\x01')),
            raw_recording(Chunk(message_start_time=0, message_end_time=0, uncompressed_size=0,
                                uncompressed_crc=0, compression='zstd', data=b'')),
            b'\x89MCAP0\r\n' + struct.pack('<BQ', 1, 4) + struct.pack('<I', 0xffffffff),
        ]
        for data in defects:
            self.path.write_bytes(data); count = C.c_uint64()
            self.assertEqual(self.lib.validate(Bytes.of(str(self.path)), C.byref(count)), -1)
    def test_native_checks_chunk_data_summary_crc_and_final_magic(self):
        self.python_record()
        original = self.path.read_bytes()
        damaged = [original[:-1], original + b'garbage', original[:-12] + bytes([original[-12]^1]) + original[-11:]]
        # Locate an uncompressed chunk's data and flip a payload byte.
        offset = 8
        while offset < len(original) - 8:
            opcode, size = struct.unpack_from('<BQ', original, offset)
            if opcode == 15:
                data = bytearray(original); data[offset + 9] ^= 1; damaged.append(data)
            if opcode == 6:
                length = struct.unpack_from('<I', original, offset + 9 + 28)[0]
                chunk_data = offset + 9 + 32 + length + 8
                data = bytearray(original); data[chunk_data + 5] ^= 1
                damaged.append(data)
            offset += 9 + size
        for data in damaged:
            self.path.write_bytes(data); count = C.c_uint64()
            self.assertEqual(self.lib.validate(Bytes.of(str(self.path)), C.byref(count)), -1, self.lib.error())
    def test_exclusive_creation_invalid_inputs_and_wrong_root(self):
        writer = self.lib.writer_open(Bytes.of(str(self.path)), 0, 128)
        self.assertTrue(writer)
        try:
            self.assertFalse(self.lib.writer_open(Bytes.of(str(self.path)), 0, 128))
            ch = C.c_uint16()
            self.ok(self.lib.writer_channel(writer, Bytes.of('t'), Bytes.of('SDL2|A{}|B{}'), Bytes.of('A'), None, 0, C.byref(ch)))
            self.assertEqual(self.lib.writer_write(writer, ch, Bytes.of(b'\x02'), 1, 1, 0), -1)
            self.assertEqual(self.lib.writer_flush(writer, 2), -1)
        finally: self.ok(self.lib.writer_close(writer))
        self.assertEqual(self.lib.writer_flush(None, 0), -1)
    def test_distinct_quantum_text_and_filter_do_not_hide_invalid_payloads(self):
        writer = self.lib.writer_open(Bytes.of(str(self.path)), 0, 128)
        self.assertTrue(writer)
        try:
            for quantum in ('0.1', '0.10000000000000001'):
                catalogue = description(Q).replace('q0.25', 'q' + quantum)
                channel = C.c_uint16()
                self.ok(self.lib.writer_channel(writer, Bytes.of('t'), Bytes.of(catalogue),
                    Bytes.of('Sample'), None, 0, C.byref(channel)))
                # An all-zero limited float is valid under either quantum.
                self.ok(self.lib.writer_write(writer, channel, Bytes.of(b'\x01\x00\x00'), 1, 1, 0))
        finally: self.ok(self.lib.writer_close(writer))
        records = self.native_records()
        self.assertNotEqual(records[0][0], records[1][0])
        self.assertNotEqual(records[0][6], records[1][6])
        self.assertEqual(RecordingReader(self.path).validate(), 2)
        self.path.write_bytes(raw_recording(
            Schema(id=1, name='catalogue', encoding='sdl2', data=description(V1).encode()),
            Channel(id=1, schema_id=1, topic='t', message_encoding='sdl2', metadata={'root_type':'Sample'}),
            Message(channel_id=1, sequence=0, log_time=0, publish_time=0, data=b'\x01')))
        reader = self.lib.reader_open(Bytes.of(str(self.path)))
        try:
            self.ok(self.lib.reader_filter(reader, Bytes.of('other'), 0, 0, 0))
            out = C.POINTER(Record)()
            self.assertEqual(self.lib.reader_next(reader, C.byref(out)), -1)
        finally: self.ok(self.lib.reader_close(reader))
    def test_compiled_c_client(self):
        binary = Path(self.tmp.name) / 'c-client'
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c99', '-Wall', '-Wextra', '-Werror',
            '-I'+str(ROOT / 'runtime/recording/include'), str(ROOT / 'tests/recording/native/client.c'),
            '-L'+str(LIBRARY.parent), '-Wl,-rpath,'+str(LIBRARY.parent), '-lsdl_recording', '-o', str(binary)], check=True)
        subprocess.run([str(binary), str(self.path)], check=True)
        self.assertEqual(RecordingReader(self.path).info()['format'], 'pcapng')
        records = list(RecordingReader(self.path).iter_records())
        self.assertEqual(len(records), 1); self.assertEqual(records[0].payload, b'\x01')

if __name__ == '__main__': unittest.main()
