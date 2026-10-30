"""PCAPNG profile, conversion and independent Python/native compatibility."""
import ctypes as C
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from test_recording import (ROOT, Q, V1, V2, RecordingReader, RecordingWriter, RecordingError,
                           description, encode, prepare, replay, Header, Schema, Channel, Message)
from test_native import LIBRARY, Bytes, load
from sdl_recording import convert_recording

# Independent encoder of the documented framing; intentionally does not use
# sdl_pcapng internals, so CRC/length/end bugs are not tested against themselves.
PROFILE = 'sdl2-recording-pcapng-v1'
def block(kind, body):
    n = len(body) + 12
    return struct.pack('<II', kind, n) + body + struct.pack('<I', n)
def epb(kind, body, time=0, flags=0):
    prefix = struct.pack('<4sBBHI', b'S2RC', 1, kind, flags, len(body))
    packet = prefix + struct.pack('<I', zlib.crc32(prefix + body)) + body
    frame = struct.pack('<IIIII', 0, time >> 32, time & 0xffffffff, len(packet), len(packet))
    return block(6, frame + packet + b'\0' * (-len(packet) % 4)), packet

def body(record):
    from mcap.data_stream import RecordBuilder
    builder = RecordBuilder(); record.write(builder)
    data = builder.end(); return data[0], data[9:]

def capture(*records, end=True):
    result = block(0x0a0d0d0a, struct.pack('<IHHq', 0x1a2b3c4d, 1, 0, -1))
    result += block(1, struct.pack('<HHIHHB3xHH', 147, 0, 0, 9, 1, 9, 0, 0))
    crc = count = messages = 0
    for record in (Header(PROFILE, 'independent'),) + records:
        kind, data = body(record)
        encoded, packet = epb(kind, data, record.log_time if isinstance(record, Message) else 0)
        result += encoded; crc = zlib.crc32(packet, crc); count += 1
        messages += isinstance(record, Message)
    if end: result += epb(15, struct.pack('<QQI', count, messages, crc))[0]
    return result

def blocks(data):
    pos = 0
    while pos < len(data):
        kind, size = struct.unpack_from('<II', data, pos)
        assert size >= 12 and size % 4 == 0
        assert struct.unpack_from('<I', data, pos + size - 4)[0] == size
        yield pos, kind, data[pos+8:pos+size-4]
        pos += size
    assert pos == len(data)

class PcapngTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / 'capture.pcapng'
    def tearDown(self): self.tmp.cleanup()
    def test_default_writer_uses_pcapng_regardless_of_extension(self):
        path = Path(self.tmp.name) / 'default.mcap'
        payload = encode(prepare(description(Q), [Q]), Q(value=.375))
        with RecordingWriter(path) as writer:
            channel = writer.register_channel(topic='radio/iq', catalogue=description(Q), root_type='Sample')
            writer.write_encoded(channel, payload, log_time_ns=10, publish_time_ns=9, sequence=3)
        self.assertEqual(path.read_bytes()[:4], b'\x0a\x0d\x0d\x0a')
        reader = RecordingReader(path)
        self.assertEqual(reader.info()['format'], 'pcapng')
        self.assertEqual(reader.validate(), 1)
        record, = reader.iter_records()
        self.assertEqual(record.payload, payload)
        self.assertEqual((record.log_time_ns, record.publish_time_ns, record.sequence), (10, 9, 3))
    def record(self):
        payloads = []
        with RecordingWriter(self.path, format='pcapng', metadata={'producer':'é📻\0'}) as w:
            for i, cls in enumerate((V1, V2, Q)):
                ch = w.register_channel(topic='radio/é📻\0iq', catalogue=description(cls), root_type='Sample',
                    metadata={'origin':'é📻\0'})
                data = encode(prepare(description(cls), [cls]), cls(value=i+.375 if cls is Q else i))
                payloads.append(data)
                w.write_encoded(ch, data, log_time_ns=2**64-1, publish_time_ns=2**64-2, sequence=2**32-1-i)
                w.flush(sync=True)
        return payloads
    def test_profile_independent_framing_crc_timestamp_and_exact_payloads(self):
        payloads = self.record()
        chunks = list(blocks(self.path.read_bytes()))
        self.assertEqual([kind for _,kind,_ in chunks[:2]], [0x0a0d0d0a, 1])
        self.assertEqual(struct.unpack_from('<H',chunks[1][2])[0],147)
        self.assertEqual(chunks[1][2][12],9)
        packets = messages = crc = 0
        for _, kind, raw in chunks[2:]:
            self.assertEqual(kind,6)
            interface,high,low,length,original = struct.unpack_from('<IIIII',raw)
            self.assertEqual((interface,length),(0,original))
            packet = raw[20:20+length]
            magic,version,opcode,flags,size,checksum = struct.unpack_from('<4sBBHII',packet)
            self.assertEqual((magic,version,flags,size),(b'S2RC',1,0,len(packet)-16))
            self.assertEqual(checksum,zlib.crc32(packet[:12]+packet[16:]))
            if opcode == 15:
                self.assertEqual(struct.unpack('<QQI',packet[16:]),(packets,messages,crc))
            else:
                packets += 1; crc = zlib.crc32(packet,crc)
            if opcode == 5:
                messages += 1
                self.assertEqual(high << 32 | low,2**64-1)
                self.assertEqual(packet[38:],payloads[messages-1])
        reader = RecordingReader(self.path)
        records = list(reader.iter_records())
        self.assertEqual([r.payload for r in records],payloads)
        self.assertEqual([r.decode().fields['value'] for r in records],[0,1,2.5])
        self.assertEqual(reader.validate(),3)
        info = reader.info()
        self.assertEqual(info['profile'],PROFILE); self.assertEqual(info['format'],'pcapng')
        self.assertEqual(info['metadata'][0]['data'],{'producer':'é📻\0'})
        self.assertEqual(len(info['schemas']),3)
    def test_empty_recording_stream_ownership_exclusive_creation_and_defaults(self):
        stream = io.BytesIO()
        with RecordingWriter(stream): pass
        self.assertFalse(stream.closed)
        self.path.write_bytes(stream.getvalue())
        self.assertEqual(stream.getvalue()[:4], b'\x0a\x0d\x0d\x0a')
        self.assertEqual(RecordingReader(self.path).info()['format'], 'pcapng')
        self.assertEqual(RecordingReader(self.path).validate(),0)
        with self.assertRaises(FileExistsError): RecordingWriter(self.path,format='pcapng')
        with self.assertRaises(RecordingError): RecordingWriter(self.path,format='wrong',overwrite=True)
        self.assertEqual(self.path.read_bytes(),stream.getvalue())

    def test_partial_writes_and_io_failure_do_not_silently_finish(self):
        class Partial(io.BytesIO):
            def write(self, data): return super().write(data[:3])
        stream=Partial()
        with RecordingWriter(stream,format='pcapng') as writer:
            channel=writer.register_channel(topic='t',catalogue='SDL2|Packet{}',root_type='Packet')
            writer.write_encoded(channel,b'\x01',log_time_ns=1)
        self.path.write_bytes(stream.getvalue())
        self.assertEqual(RecordingReader(self.path).validate(),1)
        class Broken(io.BytesIO):
            fail=False
            def write(self,data): return 0 if self.fail else super().write(data)
        stream=Broken(); writer=RecordingWriter(stream,format='pcapng')
        channel=writer.register_channel(topic='t',catalogue='SDL2|Packet{}',root_type='Packet')
        stream.fail=True
        with self.assertRaises(OSError): writer.write_encoded(channel,b'\x01',log_time_ns=1)
        with self.assertRaises(ValueError): writer.close()
        self.path.write_bytes(stream.getvalue())
        with self.assertRaises(RecordingError): RecordingReader(self.path).validate()
    def test_independent_fixture_and_rejection_of_invalid_inner_lengths(self):
        sid = Schema(id=1,name='catalogue',encoding='sdl2',data=description(V1).encode())
        ch = Channel(id=1,schema_id=1,topic='t',message_encoding='sdl2',metadata={'root_type':'Sample'})
        msg = Message(channel_id=1,log_time=123,publish_time=456,sequence=789,data=b'\x01\0\0\0\0')
        self.path.write_bytes(capture(sid,ch,msg))
        self.assertEqual(RecordingReader(self.path).validate(),1)
        # Recompute the per-packet CRC around an invalid counted field, so it
        # reaches the bounded parser instead of being rejected only by CRC.
        prefix = capture(end=False)
        self.path.write_bytes(prefix+epb(3,b'\x01\0'+struct.pack('<I',0xffffffff))[0])
        with self.assertRaises(RecordingError): RecordingReader(self.path).validate()
    def test_malformed_end_wrapper_packet_crc_and_timestamp(self):
        self.record(); original = self.path.read_bytes(); offsets = list(blocks(original))
        defects = [original[:-1], original[:offsets[-1][0]], original+b'x']
        for offset in (4, offsets[1][0]+20, offsets[2][0]+20, offsets[2][0]+40, offsets[-1][0]+48):
            data=bytearray(original); data[offset]^=1; defects.append(data)
        message_offset = next(offset for offset,kind,raw in offsets if kind==6 and raw[25]==5)
        data=bytearray(original); data[message_offset+12]^=1; defects.append(data) # EPB timestamp only.
        for data in defects:
            self.path.write_bytes(data)
            with self.assertRaises(RecordingError): RecordingReader(self.path).validate()

    def test_end_rejects_deletion_reordering_and_forged_counts_with_valid_packet_crcs(self):
        self.record(); data=self.path.read_bytes(); records=list(blocks(data))
        def raw(i):
            offset=records[i][0]; return data[offset:offset+len(records[i][2])+12]
        # All retained packets have a correct individual CRC; only END catches
        # deletion/reordering and a correctly framed but false stream checksum.
        defects=[b''.join(raw(i) for i in range(len(records)) if i != 3),
                 raw(0)+raw(1)+raw(2)+raw(4)+raw(3)+b''.join(raw(i) for i in range(5,len(records))),
                 data[:records[-1][0]]+epb(15,struct.pack('<QQI',0,0,0))[0]]
        for defect in defects:
            self.path.write_bytes(defect)
            with self.assertRaises(RecordingError): RecordingReader(self.path).validate()
    def test_conflicting_catalogues_wrong_root_and_invalid_filtered_message(self):
        sid = Schema(id=1,name='catalogue',encoding='sdl2',data=description(V1).encode())
        ch = Channel(id=1,schema_id=1,topic='t',message_encoding='sdl2',metadata={'root_type':'Sample'})
        self.path.write_bytes(capture(sid,Schema(id=1,name='catalogue',encoding='sdl2',data=description(V2).encode())))
        with self.assertRaises(RecordingError): RecordingReader(self.path).validate()
        self.path.write_bytes(capture(sid,ch,Message(channel_id=1,log_time=0,publish_time=0,sequence=0,data=b'\x02')))
        with self.assertRaises(RecordingError): list(RecordingReader(self.path).iter_records(topics='other'))
        self.path.write_bytes(capture(sid,Channel(id=0,schema_id=1,topic='t',message_encoding='sdl2',metadata={'root_type':'Sample'})))
        with self.assertRaises(RecordingError): RecordingReader(self.path).validate()
    def test_local_types_filters_and_unpaced_replay_preserve_file_order(self):
        with RecordingWriter(self.path,format='pcapng') as writer:
            c=writer.register_channel(topic='t',catalogue=description(V1),root_type='Sample')
            for i,t in enumerate((20,10,10)): writer.write(c,V1(value=i),log_time_ns=t,sequence=i)
        reader=RecordingReader(self.path,local_types=[V2]); records=list(reader.iter_records(start_time_ns=10,end_time_ns=20))
        self.assertEqual([r.sequence for r in records],[1,2]); self.assertEqual(records[0].decode(dynamic=False).value,1)
        seen=[]; self.assertEqual(replay(reader.iter_records(),lambda r:seen.append(r.payload),speed=0),3)
        self.assertEqual(seen,[r.payload for r in reader.iter_records()])
    def test_conversion_both_directions_metadata_unused_channel_and_preflight(self):
        self.record(); before=RecordingReader(self.path).info(); original=list(RecordingReader(self.path).iter_records())
        mcap=Path(self.tmp.name)/'converted.mcap'; pcap=Path(self.tmp.name)/'again.pcapng'
        self.assertEqual(convert_recording(self.path,mcap,format='mcap'),3)
        self.assertEqual(convert_recording(mcap,pcap),3)
        after=list(RecordingReader(pcap).iter_records()); info=RecordingReader(pcap).info()
        self.assertEqual([(r.channel.catalogue,r.payload,r.log_time_ns,r.publish_time_ns,r.sequence) for r in original],
                         [(r.channel.catalogue,r.payload,r.log_time_ns,r.publish_time_ns,r.sequence) for r in after])
        self.assertEqual(info['metadata'],before['metadata'])
        self.assertEqual(info['channels'],before['channels'])
        with self.assertRaises(RecordingError): convert_recording(self.path,self.path,overwrite=True)
        with self.assertRaises(FileExistsError): convert_recording(self.path,pcap)
        with RecordingWriter(Path(self.tmp.name)/'unused.mcap',format='mcap') as writer:
            writer.register_channel(topic='unused',catalogue=description(V1),root_type='Sample')
        unused=Path(self.tmp.name)/'unused.pcapng'
        convert_recording(Path(self.tmp.name)/'unused.mcap',unused)
        self.assertEqual(len(RecordingReader(unused).info()['channels']),1)
        self.path.write_bytes(self.path.read_bytes()[:-1])
        target=Path(self.tmp.name)/'not-created.pcapng'
        with self.assertRaises(RecordingError): convert_recording(self.path,target)
        self.assertFalse(target.exists())
    def test_cli_convert_inspect_validate_and_detection_without_extension(self):
        self.record(); other=Path(self.tmp.name)/'arbitrary.bin'
        command=[sys.executable,str(ROOT/'tools/recording/sdl-recording')]
        result=subprocess.run(command+['convert',str(self.path),str(other),'--format','mcap'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(json.loads(result.stdout)['message_count'],3)
        result=subprocess.run(command+['inspect',str(self.path),'--format','text','--limit','1'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertIn('message: Sample {',result.stdout)
        result=subprocess.run(command+['validate',str(other)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr); self.assertEqual(json.loads(result.stdout)['message_count'],3)
        default=Path(self.tmp.name)/'default.mcap'
        result=subprocess.run(command+['convert',str(other),str(default)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['format'],'pcapng')
        self.assertEqual(RecordingReader(default).info()['format'],'pcapng')
        self.assertEqual([r.payload for r in RecordingReader(default).iter_records()],
                         [r.payload for r in RecordingReader(other).iter_records()])

@unittest.skipUnless(LIBRARY.exists(),'optional native recording library not built')
class PcapngNativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'native.pcapng'; self.lib=load()
        self.lib.sdl_recording_writer_open_format.restype=C.c_void_p
        self.lib.sdl_recording_writer_open_format.argtypes=[Bytes,C.c_uint8,C.c_uint64,C.c_uint8]
    def tearDown(self): self.tmp.cleanup()
    def ok(self,status): self.assertGreaterEqual(status,0,self.lib.error().decode())
    def native_records(self):
        from test_native import NativeTests
        return NativeTests.native_records(self)
    def test_python_to_native_and_native_to_python_quantum_revision_and_uint64(self):
        with RecordingWriter(self.path,format='pcapng') as w:
            ch=w.register_channel(topic='radio/é📻\0iq',catalogue=description(Q),root_type='Sample')
            payload=encode(prepare(description(Q),[Q]),Q(value=.375))
            w.write_encoded(ch,payload,log_time_ns=2**64-1,publish_time_ns=2**64-2,sequence=2**32-1)
        records=self.native_records(); self.assertEqual(records[0][1],payload); self.assertEqual(records[0][2],2**64-1)
        writer=self.lib.sdl_recording_writer_open_format(Bytes.of(str(self.path)),1,1024,1)
        self.assertTrue(writer,self.lib.error())
        expected=[]
        try:
            for cls in (V1,V2,Q):
                ch=C.c_uint16()
                self.ok(self.lib.writer_channel(writer,Bytes.of('radio/é📻\0iq'),Bytes.of(description(cls)),Bytes.of('Sample'),None,0,C.byref(ch)))
                data=encode(prepare(description(cls),[cls]),cls(value=1)); expected.append(data)
                self.ok(self.lib.writer_write(writer,ch,Bytes.of(data),2**64-1,2**64-2,2**32-1))
                self.ok(self.lib.writer_flush(writer,1))
        finally:self.ok(self.lib.writer_close(writer))
        self.assertEqual([r.payload for r in RecordingReader(self.path).iter_records()],expected)
        self.assertEqual(RecordingReader(self.path).validate(),3)
    def test_native_rejects_corruption_truncation_and_invalid_format(self):
        with RecordingWriter(self.path,format='pcapng'):pass
        original=self.path.read_bytes()
        corrupt=bytearray(original); corrupt[68]^=1
        for data in (original[:-1],original[:-68],original+b'x',corrupt):
            self.path.write_bytes(data); count=C.c_uint64()
            self.assertEqual(self.lib.validate(Bytes.of(str(self.path)),C.byref(count)),-1)
        target=Path(self.tmp.name)/'not-created.pcapng'
        self.assertFalse(self.lib.sdl_recording_writer_open_format(Bytes.of(str(target)),0,1024,2))
        self.assertFalse(target.exists())

    def test_native_and_python_reject_oversized_counted_fields_before_parsing(self):
        prefix=capture(end=False)
        cases = [(1,struct.pack('<I',1048577)+b'x'*1048577),
                 (3,b'\x01\0'+struct.pack('<I',0xffffffff)),
                 (12,struct.pack('<II',0,1048577)+b'x'*1048577)]
        for kind, data in cases:
            self.path.write_bytes(prefix+epb(kind,data)[0])
            with self.assertRaises(RecordingError): RecordingReader(self.path).validate()
            count=C.c_uint64()
            self.assertEqual(self.lib.validate(Bytes.of(str(self.path)),C.byref(count)),-1)

if __name__ == '__main__':unittest.main()
