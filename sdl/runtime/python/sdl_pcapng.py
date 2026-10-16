"""SDL PCAPNG transport. Packet bodies reuse the MCAP primitive record layouts.

The public API remains sdl_recording; this module only handles the container.
"""
import io
import struct
import zlib

from mcap.data_stream import RecordBuilder, ReadDataStream
from mcap.records import Header, Schema, Channel, Message, Metadata, DataEnd, Footer

PROFILE = 'sdl2-recording-pcapng-v1'
MAGIC = b'\x0a\x0d\x0d\x0a'
SHB = struct.pack('<IHHq', 0x1a2b3c4d, 1, 0, -1)
IDB = struct.pack('<HHIHHB3xHH', 147, 0, 0, 9, 1, 9, 0, 0)
MAX_TEXT = 1024 * 1024
MAX_PAYLOAD = 32 * 1024 * 1024
MAX_BLOCK = 64 * 1024 * 1024

class PcapngError(ValueError):
   pass

def _block(kind, body):
   length = len(body) + 12
   return struct.pack('<II', kind, length) + body + struct.pack('<I', length)

def _read(stream, size):
   result = bytearray()
   while len(result) < size:
      chunk = stream.read(size - len(result))
      if not chunk:
         raise PcapngError('truncated PCAPNG block')
      result.extend(chunk)
   return bytes(result)

class _Cursor:
   def __init__(self, data):
      self.data, self.pos = data, 0
   def take(self, size):
      end = self.pos + size
      if end > len(self.data):
         raise PcapngError('PCAPNG field exceeds packet')
      value = self.data[self.pos:end]
      self.pos = end
      return value
   def uint32(self):
      return struct.unpack('<I', self.take(4))[0]
   def text(self):
      size = self.uint32()
      if size > MAX_TEXT:
         raise PcapngError('PCAPNG text exceeds 1 MiB')
      return self.take(size).decode('utf-8')
   def metadata(self):
      size = self.uint32()
      if size > MAX_TEXT:
         raise PcapngError('PCAPNG metadata exceeds 1 MiB')
      items = _Cursor(self.take(size))
      keys = set()
      while items.pos < size:
         key = items.text()
         if key in keys:
            raise PcapngError('duplicate metadata key')
         keys.add(key)
         if len(keys) > 65535:
            raise PcapngError('too many metadata keys')
         items.text()


def _validate_body(kind, body):
   c = _Cursor(body)
   if kind == 1:
      c.text(); c.text()
   elif kind == 3:
      c.take(2); c.text(); c.text()
      size = c.uint32()
      if size > MAX_TEXT:
         raise PcapngError('PCAPNG catalogue exceeds 1 MiB')
      c.take(size)
   elif kind == 4:
      c.take(4); c.text(); c.text(); c.metadata()
   elif kind == 5:
      c.take(22)
      if len(body) - 22 > MAX_PAYLOAD:
         raise PcapngError('PCAPNG payload exceeds 32 MiB')
      c.pos = len(body)
   elif kind == 12:
      c.text(); c.metadata()
   else:
      raise PcapngError('unknown SDL recording packet kind')
   if c.pos != len(body):
      raise PcapngError('trailing data in SDL recording packet')

class Writer:
   def __init__(self, stream):
      self.stream = stream
      self.packets = self.messages = self.crc = 0
      self.schema_id = self.channel_id = 0
      self.failed = self.ended = False
   def _write(self, data):
      remaining = memoryview(data)
      try:
         while remaining:
            written = self.stream.write(remaining)
            if not isinstance(written, int) or not 0 < written <= len(remaining):
               raise OSError('PCAPNG output did not accept bytes')
            remaining = remaining[written:]
      except BaseException:
         self.failed = True
         raise
   def start(self, *, profile, library):
      self._write(_block(0x0a0d0d0a, SHB))
      self._write(_block(1, IDB))
      self._record(Header(PROFILE, library))
   def _packet(self, kind, body, timestamp=0):
      if self.failed or self.ended:
         raise PcapngError('PCAPNG writer is closed or has failed')
      prefix = struct.pack('<4sBBHI', b'S2RC', 1, kind, 0, len(body))
      packet = prefix + struct.pack('<I', zlib.crc32(body, zlib.crc32(prefix))) + body
      epb = struct.pack('<IIIII', 0, timestamp >> 32, timestamp & 0xffffffff, len(packet), len(packet))
      epb += packet + b'\0' * (-len(packet) % 4)
      if len(epb) + 12 > MAX_BLOCK:
         raise PcapngError('PCAPNG block exceeds size limit')
      self._write(_block(6, epb))
      if kind != 15:
         self.packets += 1
         self.crc = zlib.crc32(packet, self.crc)
      if kind == 5:
         self.messages += 1
   def _record(self, record):
      builder = RecordBuilder()
      record.write(builder)
      data = builder.end()
      kind, body = data[0], data[9:]
      _validate_body(kind, body)
      self._packet(kind, body, record.log_time if isinstance(record, Message) else 0)
   def register_schema(self, *, name, encoding, data):
      if self.schema_id == 65535:
         raise PcapngError('schema ID space exhausted')
      self.schema_id += 1
      self._record(Schema(id=self.schema_id, data=data, encoding=encoding, name=name))
      return self.schema_id
   def register_channel(self, *, topic, message_encoding, schema_id, metadata):
      if self.channel_id == 65535:
         raise PcapngError('channel ID space exhausted')
      self.channel_id += 1
      self._record(Channel(id=self.channel_id, topic=topic, message_encoding=message_encoding, schema_id=schema_id, metadata=metadata))
      return self.channel_id
   def add_message(self, *, channel_id, data, log_time, publish_time, sequence):
      self._record(Message(channel_id=channel_id, log_time=log_time, data=data, publish_time=publish_time, sequence=sequence))
   def add_metadata(self, name, data):
      self._record(Metadata(name, data))
   def flush(self):
      if self.failed or self.ended:
         raise PcapngError('PCAPNG writer is closed or has failed')
      try:
         self.stream.flush()
      except BaseException:
         self.failed = True
         raise
   def finish(self):
      self._packet(15, struct.pack('<QQI', self.packets, self.messages, self.crc))
      self.ended = True
      self.stream.flush()


def records(stream, max_record_size):
   section = interface = header = ended = False
   packets = messages = crc = 0
   while True:
      first = stream.read(1)
      if not first:
         if not ended:
            raise PcapngError('incomplete PCAPNG recording: missing END')
         return
      if ended:
         raise PcapngError('data after PCAPNG END')
      prefix = first + _read(stream, 7)
      kind, size = struct.unpack('<II', prefix)
      if size < 12 or size % 4 or size > min(max_record_size, MAX_BLOCK):
         raise PcapngError('invalid PCAPNG block size')
      data = _read(stream, size - 8)
      if data[-4:] != prefix[4:]:
         raise PcapngError('PCAPNG block length mismatch')
      body = data[:-4]
      if not section and kind != 0x0a0d0d0a:
         raise PcapngError('PCAPNG section must come first')
      if kind == 0x0a0d0d0a:
         if section or body != SHB:
            raise PcapngError('unsupported PCAPNG section (expected single little-endian v1.0 section)')
         section = True
      elif kind == 1:
         if interface or body != IDB:
            raise PcapngError('unsupported PCAPNG interface (expected USER0, nanoseconds, no snaplen)')
         interface = True
      elif kind == 6:
         if not interface or len(body) < 36:
            raise PcapngError('invalid PCAPNG packet block')
         iface, high, low, length, original = struct.unpack_from('<IIIII', body)
         if iface or length != original or length < 16 or length > len(body) - 20:
            raise PcapngError('invalid PCAPNG interface or captured packet length')
         padded = (length + 3) & ~3
         if len(body) != 20 + padded or any(body[20 + length:]):
            raise PcapngError('invalid PCAPNG packet padding or options')
         packet = body[20:20 + length]
         magic, version, opcode, flags, body_size, packet_crc = struct.unpack_from('<4sBBHII', packet)
         if magic != b'S2RC' or version != 1 or flags:
            raise PcapngError('unsupported SDL recording packet')
         payload = packet[16:]
         if body_size != len(payload):
            raise PcapngError('SDL recording packet length mismatch')
         if packet_crc != zlib.crc32(payload, zlib.crc32(packet[:12])):
            raise PcapngError('SDL recording packet CRC mismatch')
         timestamp = high << 32 | low
         if opcode != 5 and timestamp:
            raise PcapngError('control packet timestamp must be zero')
         if not header and opcode != 1:
            raise PcapngError('SDL recording HEADER must come first')
         if opcode == 15:
            if len(payload) != 20 or struct.unpack('<QQI', payload) != (packets, messages, crc):
               raise PcapngError('SDL recording END count or stream CRC mismatch')
            ended = True
            yield DataEnd(0)
            yield Footer(0, 0, 0)
            continue
         _validate_body(opcode, payload)
         parser = {1: Header, 3: Schema, 4: Channel, 12: Metadata}.get(opcode)
         content = ReadDataStream(io.BytesIO(payload))
         record = Message.read(content, len(payload)) if opcode == 5 else parser.read(content)
         if opcode == 4 and record.id == 0:
            raise PcapngError('PCAPNG channel ID must be nonzero')
         if opcode == 1:
            if header or record.profile != PROFILE:
               raise PcapngError('unsupported or duplicate PCAPNG profile')
            header = True
            # Normalize transport events for the common catalogue/channel checks.
            record = Header('sdl2-recording-v1', record.library)
         if opcode == 5:
            if record.log_time != timestamp:
               raise PcapngError('PCAPNG timestamp differs from SDL log time')
            messages += 1
         packets += 1
         crc = zlib.crc32(packet, crc)
         yield record
      else:
         raise PcapngError('unsupported block in SDL PCAPNG profile')
