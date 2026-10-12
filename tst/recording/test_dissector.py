"""Compile and exercise the C USER0 adapter without Wireshark or Lua."""
import ctypes as C
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

from test_recording import ROOT, Q, V1, V2, description, encode, prepare, Header, Schema, Channel, Message
from test_pcapng import PROFILE, body, epb
from mcap.records import Metadata

class Bytes(C.Structure):
    _fields_ = [('data', C.c_void_p), ('size', C.c_size_t)]
    def bytes(self): return C.string_at(self.data, self.size)

class Pair(C.Structure):
    _fields_ = [('key', Bytes), ('value', Bytes)]

class Packet(C.Structure):
    _fields_ = [('kind', C.c_uint8), ('id', C.c_uint16), ('schema_id', C.c_uint16),
                ('crc', C.c_uint32), ('sequence', C.c_uint32), ('stream_crc', C.c_uint32),
                ('log_time', C.c_uint64), ('publish_time', C.c_uint64),
                ('packet_count', C.c_uint64), ('message_count', C.c_uint64)] + [
                (name, Bytes) for name in ('profile','library','name','encoding','catalogue','topic','root','payload')
                ] + [('metadata', C.POINTER(Pair)), ('metadata_count', C.c_size_t)]

class Result(C.Structure):
    _fields_ = [('packet', Packet), ('parsed', C.c_bool), ('channel', C.POINTER(Packet)),
                ('schema', C.POINTER(Packet)), ('message', C.c_void_p)]

class Enumeration(C.Structure):
    _fields_ = [('type_name', C.c_void_p), ('name', C.c_void_p), ('value', C.c_int32)]

class ValueUnion(C.Union):
    _fields_ = [('integer', C.c_int64), ('unsigned_integer', C.c_uint64), ('floating', C.c_double),
                ('enumeration', Enumeration)]

class Value(C.Structure):
    _fields_ = [('kind', C.c_int), ('value', ValueUnion)]

def packet(record): return epb(*body(record))[1]

def schema(cls=Q, id=1):
    return packet(Schema(id=id,name='catalogue',encoding='sdl2',data=description(cls).encode()))

def channel(id=1, sid=1, topic='radio/é📻\0iq', root='Sample', metadata=None):
    return packet(Channel(id=id,schema_id=sid,topic=topic,message_encoding='sdl2',
                          metadata={'root_type':root, **(metadata or {})}))

def message(cls=Q, id=1, value=.375, raw=None):
    payload=encode(prepare(description(cls),[cls]),cls(value=value)) if raw is None else raw
    return packet(Message(channel_id=id,sequence=2**32-1,log_time=2**64-1,publish_time=2**64-2,data=payload))

def end(packets):
    return epb(15,struct.pack('<QQI',len(packets),sum(p[5]==5 for p in packets),zlib.crc32(b''.join(packets))))[1]

CC=shlex.split(os.environ.get('CC','cc'))
@unittest.skipUnless(CC and shutil.which(CC[0]), 'C compiler unavailable')
class CDissectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory=tempfile.TemporaryDirectory()
        library=Path(cls.directory.name)/'adapter.so'
        subprocess.run(CC+['-std=c99','-Wall','-Wextra','-pedantic','-Werror','-O2','-fPIC','-shared',
            '-I'+str(ROOT/'runtime/c'), '-I'+str(ROOT/'tools/wireshark'),
            str(ROOT/'tools/wireshark/sdl-recording.c'), *map(str,sorted((ROOT/'runtime/c').glob('*.c'))),
            '-lm','-o',str(library)],check=True)
        cls.lib=C.CDLL(str(library))
        cls.lib.sdlrec_capture_new.restype=C.c_void_p
        cls.lib.sdlrec_capture_free.argtypes=[C.c_void_p]
        cls.lib.sdlrec_process.argtypes=[C.c_void_p,C.c_uint32,C.c_void_p,C.c_size_t,C.POINTER(Result),C.POINTER(C.c_char_p)]
        cls.lib.sdlrec_process.restype=C.c_bool
        cls.lib.sdlrec_result_clear.argtypes=[C.POINTER(Result)]
        cls.lib.sdlrec_parse.argtypes=[C.c_void_p,C.c_size_t,C.POINTER(Packet),C.POINTER(C.c_char_p)]
        cls.lib.sdlrec_parse.restype=C.c_bool
        cls.lib.sdlrec_packet_clear.argtypes=[C.POINTER(Packet)]
        cls.lib.type_dynamic_get.argtypes=[C.c_void_p,C.c_char_p]
        cls.lib.type_dynamic_get.restype=C.POINTER(Value)
    @classmethod
    def tearDownClass(cls): cls.directory.cleanup()
    def setUp(self):
        self.capture=self.lib.sdlrec_capture_new(); self.assertTrue(self.capture)
        self.result=Result(); self.frames=[]
    def tearDown(self):
        self.lib.sdlrec_result_clear(C.byref(self.result)); self.lib.sdlrec_capture_free(self.capture)
    def process(self, data, frame=None, valid=True):
        self.lib.sdlrec_result_clear(C.byref(self.result))
        self.buffer=C.create_string_buffer(data)
        if frame is None:
            self.frames.append(data); frame=len(self.frames)
        error=C.c_char_p()
        success=self.lib.sdlrec_process(self.capture,frame,self.buffer,len(data),C.byref(self.result),C.byref(error))
        self.assertEqual(success,valid,error.value)
        return error.value.decode() if error.value else None
    def ready(self, cls=Q):
        for data in (packet(Header(PROFILE,'independent')),schema(cls),channel()): self.process(data)
    def test_exact_metadata_quantum_payload_uint64_and_revisits(self):
        self.ready(); data=message(); self.process(data)
        self.assertEqual(self.result.packet.payload.bytes(),data[38:])
        self.assertEqual(self.result.packet.log_time,2**64-1)
        self.assertEqual(self.result.packet.publish_time,2**64-2)
        self.assertEqual(self.result.packet.sequence,2**32-1)
        self.assertEqual(self.result.channel.contents.topic.bytes(),'radio/é📻\0iq'.encode())
        value=self.lib.type_dynamic_get(self.result.message,b'value').contents
        self.assertEqual(value.kind,3); self.assertEqual(value.value.floating,.5)
        ending=end(self.frames)
        self.process(data,frame=4)  # Redissection must not count the message twice.
        self.process(ending); self.process(ending,frame=5)
        self.process(data,frame=4)
        self.assertTrue(self.result.message)
    def test_schema_revisions_and_catalogues_are_isolated_by_channel(self):
        self.ready(V1)
        self.process(schema(V2,id=2)); self.process(channel(id=2,sid=2))
        self.process(message(V1,value=7)); self.assertEqual(self.lib.type_dynamic_get(self.result.message,b'value').contents.value.integer,7)
        self.process(message(V2,id=2,value=8)); self.assertEqual(self.lib.type_dynamic_get(self.result.message,b'value').contents.value.integer,8)
        self.process(end(self.frames))
    def test_unsigned_field_extrema_remain_exact(self):
        self.process(packet(Header(PROFILE,'independent')))
        self.process(packet(Schema(id=1,name='unsigned',encoding='sdl2',data=b'SDL2|Unsigned{1:uint64:value;}')))
        self.process(channel(root='Unsigned'))
        self.process(message(raw=b'\x01'+b'\xff'*8))
        value=self.lib.type_dynamic_get(self.result.message,b'value').contents
        self.assertEqual(value.kind,9); self.assertEqual(value.value.unsigned_integer,2**64-1)
        self.process(end(self.frames))
    def test_future_declarations_do_not_fix_earlier_messages(self):
        self.process(packet(Header(PROFILE,'independent')))
        self.assertIn('missing',self.process(message(),valid=False))
        self.process(schema()); self.process(channel())
        self.assertIn('missing',self.process(message(),frame=2,valid=False))
    def test_conflicts_identical_metadata_in_different_order_and_wrong_root(self):
        self.ready()
        self.process(channel(metadata={'a':'1','z':'2'}),valid=False)
        self.process(channel(id=2,metadata={'a':'1','z':'2'}))
        same=packet(Channel(id=2,schema_id=1,topic='radio/é📻\0iq',message_encoding='sdl2',
                            metadata={'z':'2','a':'1','root_type':'Sample'}))
        self.process(same)
        self.assertIn('Conflicting',self.process(schema(V1),valid=False))
        self.assertIn('not a catalogue',self.process(channel(id=3,root='Missing'),valid=False))
        self.assertIn('Malformed',self.process(message(raw=b'\x01'),valid=False))
        # Both roots exist: a valid payload for the other root must still fail.
        text='SDL2|A{}|B{}'
        self.process(packet(Schema(id=3,name='roots',encoding='sdl2',data=text.encode())))
        self.process(channel(id=3,sid=3,root='A'))
        self.assertIn('Payload root',self.process(message(id=3,raw=b'\x02'),valid=False))
    def test_crc_lengths_utf8_duplicate_keys_and_resource_bounds(self):
        valid=channel()
        bad=bytearray(valid); bad[-1]^=1
        malformed=[bytes(bad), valid[:-1],valid+b'x',epb(3,b'\x01\0'+struct.pack('<I',0xffffffff))[1],
            epb(1,struct.pack('<I',1048577)+b'x'*1048577)[1],epb(12,struct.pack('<I',1)+b'\xff'+struct.pack('<I',0))[1]]
        # Duplicate keys are encoded manually; ordinary map constructors deduplicate.
        entry=struct.pack('<I',1)+b'x'+struct.pack('<I',1)+b'y'
        malformed.append(epb(12,struct.pack('<II',0,len(entry)*2)+entry*2)[1])
        for raw in malformed:
            record=Packet(); error=C.c_char_p(); buf=C.create_string_buffer(raw)
            try: self.assertFalse(self.lib.sdlrec_parse(buf,len(raw),C.byref(record),C.byref(error)))
            finally: self.lib.sdlrec_packet_clear(C.byref(record))
        self.process(packet(Header(PROFILE,'independent')))
        metadata=packet(Metadata('run',{'unicode':'é📻\0'})); self.process(metadata)
        self.assertEqual(self.result.packet.metadata[0].value.bytes(),'é📻\0'.encode())
        self.process(end(self.frames))
    def test_header_end_counts_stream_crc_and_capture_reset(self):
        self.process(schema(),valid=False)
        self.lib.sdlrec_capture_free(self.capture); self.capture=self.lib.sdlrec_capture_new()
        self.frames=[]; self.ready()
        self.process(epb(15,struct.pack('<QQI',0,0,0))[1],valid=False)
        self.process(message(),valid=False)
        self.process(self.frames[3],frame=4,valid=False)  # END failure persists on redissection.
        self.lib.sdlrec_capture_free(self.capture); self.capture=self.lib.sdlrec_capture_new(); self.frames=[]
        self.ready(); self.process(end(self.frames))
        self.process(packet(Header(PROFILE,'independent')),valid=False)

if __name__=='__main__': unittest.main()
