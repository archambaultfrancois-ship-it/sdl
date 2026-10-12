"""Optional SDL2 MCAP/PCAPNG recording profiles (require the Python mcap package)."""

import io
import math
import operator
import os
import struct
import time
import zlib
from dataclasses import dataclass, field
from types import MappingProxyType

from mcap.data_stream import ReadDataStream
from mcap.records import Channel, Chunk, DataEnd, Footer, Header, Message, Metadata, Schema
from mcap.stream_reader import CRCValidationError, StreamReader
from mcap.writer import CompressionType, Writer

from sdl_pcapng import Writer as PcapngWriter, records as pcapng_records, PROFILE as PCAPNG_PROFILE

from sdl_runtime import (CodecError, catalogue_id, compare_catalogues, decode,
                         decode_dynamic, description, encode, prepare)

PROFILE = 'sdl2-recording-v1'
ENCODING = 'sdl2'
MAX_RECORD_SIZE = 64 * 1024 * 1024
MAX_CATALOGUE_SIZE = 1024 * 1024
MAGIC = b'\x89MCAP0\r\n'


class RecordingError(ValueError):
   """Invalid recording, channel, payload or replay request."""


def _uint(value, bits, name):
   try:
      result = operator.index(value)
   except TypeError as error:
      raise RecordingError(name + ' must be an integer') from error
   if isinstance(value, bool) or not 0 <= result < 1 << bits:
      raise RecordingError(name + ' is outside uint' + str(bits))
   return result


def _metadata(value):
   result = dict(value or {})
   if any(not isinstance(k, str) or not isinstance(v, str) for k, v in result.items()):
      raise RecordingError('metadata keys and values must be strings')
   return result


def _catalogue(text):
   if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_CATALOGUE_SIZE:
      raise RecordingError('catalogue must be UTF-8 text of at most 1 MiB')
   try:
      return prepare(text)
   except CodecError as error:
      raise RecordingError('invalid SDL2 catalogue: ' + str(error)) from error


def _root(context, payload):
   # Root IDs use the ordinary SDL2 ULEB count even with custom wire clauses.
   value = 0
   for i, byte in enumerate(payload[:5]):
      value |= (byte & 127) << (7 * i)
      if byte < 128:
         if i and byte == 0:
            break
         if not 1 <= value <= len(context.names):
            break
         return context.names[value - 1]
   raise RecordingError('invalid SDL2 root message ID')


@dataclass(frozen=True)
class RecordingChannel:
   id: int
   schema_id: int
   topic: str
   root_type: str
   catalogue: str
   metadata: object
   context: object = field(repr=False, compare=False)

   @property
   def catalogue_id(self):
      return catalogue_id(self.catalogue)


@dataclass(frozen=True)
class RecordingRecord:
   channel: RecordingChannel
   payload: bytes
   log_time_ns: int
   publish_time_ns: int
   sequence: int

   def decode(self, *, dynamic=True):
      """Decode with the recorded catalogue and the reader's optional local types."""
      function = decode_dynamic if dynamic else decode
      return function(self.channel.context, self.payload)


class RecordingWriter:
   """Write indexed MCAP or sequential PCAPNG. Create paths exclusively by default.

   Caller-owned binary streams remain open. write_encoded preserves exact SDL2
   bytes; validate_payload=False skips body decoding, but still checks the root.
   """
   def __init__(self, output, *, metadata=None, chunk_size=1024 * 1024,
                max_payload_size=32 * 1024 * 1024, validate_payload=True,
                overwrite=False, format="mcap"):
      if format not in ('mcap', 'pcapng'):
         raise RecordingError('format must be mcap or pcapng')
      self.format = format
      self._closed = False
      self._channels = {}
      self._schemas = {}
      self._emission_contexts = {}
      self._max_payload = _uint(max_payload_size, 32, 'max_payload_size')
      chunk_size = _uint(chunk_size, 32, 'chunk_size')
      if not self._max_payload or not 0 < chunk_size <= MAX_RECORD_SIZE - 1024:
         raise RecordingError('invalid payload or chunk size')
      self._validate_payload = validate_payload
      self._owns_stream = isinstance(output, (str, os.PathLike))
      metadata = _metadata(metadata)
      self._stream = open(output, 'wb' if overwrite else 'xb') if self._owns_stream else output
      try:
         self._writer = PcapngWriter(self._stream) if format == 'pcapng' else Writer(self._stream, chunk_size=chunk_size,
            compression=CompressionType.NONE, enable_crcs=True, enable_data_crcs=True)
         self._writer.start(profile=PROFILE, library='sdl-recording/python-v1')
         if metadata:
            self._writer.add_metadata('sdl.recording', metadata)
      except BaseException:
         if self._owns_stream:
            self._stream.close()
         raise

   def _open(self):
      if self._closed:
         raise RecordingError('recording writer is closed')

   def register_channel(self, *, topic, catalogue, root_type, metadata=None):
      self._open()
      if not isinstance(topic, str) or not topic:
         raise RecordingError('topic must be a nonempty string')
      context = _catalogue(catalogue)
      if root_type not in context.messages:
         raise RecordingError('root_type is not a message in the catalogue')
      meta = _metadata(metadata)
      if 'root_type' in meta and meta['root_type'] != root_type:
         raise RecordingError('conflicting root_type metadata')
      meta['root_type'] = root_type
      # Local IDs, rather than truncated SDL hashes, identify catalogues.
      existing = self._schemas.get(catalogue)
      if len(self._channels) >= 65535 or existing is None and len(self._schemas) >= 65535:
         raise RecordingError('recording schema or channel ID space exhausted')
      if existing is None:
         existing = self._writer.register_schema(name='SDL2 catalogue',
            encoding=ENCODING, data=catalogue.encode('utf-8'))
         self._schemas[catalogue] = existing
      channel_id = self._writer.register_channel(topic=topic,
         message_encoding=ENCODING, schema_id=existing, metadata=meta)
      channel = RecordingChannel(channel_id, existing, topic, root_type, catalogue,
         MappingProxyType(meta), context)
      self._channels[channel_id] = channel
      return channel

   def write_metadata(self, name, metadata):
      self._open()
      if not isinstance(name, str):
         raise RecordingError('metadata name must be a string')
      self._writer.add_metadata(name, _metadata(metadata))

   def _channel(self, channel):
      self._open()
      if not isinstance(channel, RecordingChannel) or self._channels.get(channel.id) is not channel:
         raise RecordingError('channel does not belong to this writer')

   def write_encoded(self, channel, payload, *, log_time_ns=None,
                     publish_time_ns=None, sequence=0):
      self._channel(channel)
      if not isinstance(payload, (bytes, bytearray, memoryview)):
         raise RecordingError('payload must be bytes')
      if memoryview(payload).nbytes > self._max_payload:
         raise RecordingError('payload size limit exceeded')
      payload = bytes(payload)
      if _root(channel.context, payload) != channel.root_type:
         raise RecordingError('payload root does not match channel root_type')
      if self._validate_payload:
         try:
            decode_dynamic(channel.context, payload)
         except CodecError as error:
            raise RecordingError('invalid SDL2 payload: ' + str(error)) from error
      log_time_ns = _uint(time.time_ns() if log_time_ns is None else log_time_ns, 64, 'log_time_ns')
      publish_time_ns = _uint(log_time_ns if publish_time_ns is None else publish_time_ns, 64, 'publish_time_ns')
      sequence = _uint(sequence, 32, 'sequence')
      self._writer.add_message(channel_id=channel.id, data=payload,
         log_time=log_time_ns, publish_time=publish_time_ns, sequence=sequence)

   def write(self, channel, message, **timestamps):
      self._channel(channel)
      key = (channel.id, type(message))
      context = self._emission_contexts.get(key)
      if context is None:
         context = prepare(channel.catalogue, [type(message)])
         self._emission_contexts[key] = context
      self.write_encoded(channel, encode(context, message), **timestamps)

   def flush(self, *, sync=False):
      """Flush buffered output (finishing MCAP chunks); sync=True calls os.fsync."""
      self._open()
      self._writer.flush()
      if sync:
         os.fsync(self._stream.fileno())

   def close(self):
      if not self._closed:
         self._closed = True
         try:
            self._writer.finish()
            self._stream.flush()
         finally:
            if self._owns_stream:
               self._stream.close()

   def __enter__(self):
      return self

   def __exit__(self, *_):
      self.close()


class _ExactBytesIO(io.BytesIO):
   def read(self, size=-1):
      remaining = self.getbuffer().nbytes - self.tell()
      if size > remaining:
         raise RecordingError('MCAP field extends beyond its record')
      return super().read(size)


class _BoundedStreamReader(StreamReader):
   """Isolate SDK record parsers so inner lengths cannot cross record bounds."""
   def _read_record(self, opcode, length):
      if opcode == 0 or opcode == 2 and length != 20 or opcode == 15 and length != 4:
         raise RecordingError('invalid MCAP opcode or control record size')
      outer = self._stream
      content = outer.read(length)
      if len(content) != length:
         raise RecordingError('truncated MCAP record')
      self._stream = ReadDataStream(_ExactBytesIO(content))
      try:
         record = super()._read_record(opcode, length)
      finally:
         self._stream = outer
      if isinstance(record, DataEnd):
         self._summary_crc = 0
      elif hasattr(self, '_summary_crc'):
         prefix = struct.pack('<BQ', opcode, length)
         if isinstance(record, Footer):
            actual = zlib.crc32(prefix + content[:16], self._summary_crc)
            if record.summary_crc and actual != record.summary_crc:
               raise CRCValidationError(record.summary_crc, actual, record)
         else:
            self._summary_crc = zlib.crc32(prefix + content, self._summary_crc)
      return record


def _chunk_records(chunk, limit):
   if chunk.compression:
      raise RecordingError('this recording version supports uncompressed chunks only')
   if chunk.uncompressed_size != len(chunk.data) or len(chunk.data) > limit:
      raise RecordingError('invalid MCAP uncompressed chunk size')
   if chunk.uncompressed_crc:
      actual = zlib.crc32(chunk.data)
      if actual != chunk.uncompressed_crc:
         raise CRCValidationError(chunk.uncompressed_crc, actual, chunk)
   data = _ExactBytesIO(chunk.data)
   while data.tell() < len(chunk.data):
      opcode, length = struct.unpack('<BQ', data.read(9))
      if opcode == 0:
         raise RecordingError('invalid MCAP opcode inside chunk')
      if length > limit:
         raise RecordingError('MCAP chunk record size limit exceeded')
      content = ReadDataStream(_ExactBytesIO(data.read(length)))
      if opcode == 3:
         yield Schema.read(content)
      elif opcode == 4:
         yield Channel.read(content)
      elif opcode == 5:
         yield Message.read(content, length)
      else:
         # MCAP permits unknown extensions; known section-control records do
         # not belong inside chunks in this SDL profile.
         if opcode in (1, 2, 6, 15):
            raise RecordingError('invalid control record inside MCAP chunk')


class RecordingReader:
   """Reopen a path for each sequential scan; preserve file order and timestamp ties.

   Iteration validates framing, catalogues, channel roots and available CRCs.
   validate() also decodes every payload and scans through final magic/END and EOF.
   """
   def __init__(self, path, *, local_types=(), max_record_size=MAX_RECORD_SIZE):
      self.path = os.fspath(path)
      self.format = None
      self.local_types = tuple(local_types) if isinstance(local_types, (list, tuple)) else (local_types,)
      self.max_record_size = _uint(max_record_size, 32, 'max_record_size')
      if not self.max_record_size:
         raise RecordingError('max_record_size must be positive')

   def _scan(self):
      schemas, channels = {}, {}
      header_seen = False
      data_end = False
      consumer = description(self.local_types) if self.local_types else None
      try:
         with open(self.path, 'rb') as stream:
            magic = stream.read(8)
            stream.seek(0)
            self.format = 'pcapng' if magic[:4] == b'\x0a\x0d\x0d\x0a' else 'mcap'
            if self.format == 'pcapng':
               events = pcapng_records(stream, self.max_record_size)
            else:
               reader = _BoundedStreamReader(stream, emit_chunks=True,
                  validate_crcs=True, record_size_limit=self.max_record_size)
               events = reader.records
            for outer in events:
               if not header_seen and not isinstance(outer, Header):
                  raise RecordingError('recording header must come first')
               records = _chunk_records(outer, self.max_record_size) if isinstance(outer, Chunk) else (outer,)
               if data_end and isinstance(outer, Chunk):
                  raise RecordingError('chunk after data section end')
               for record in records:
                  if not header_seen and not isinstance(record, Header):
                     raise RecordingError('recording header must come first')
                  if isinstance(record, Header):
                     if header_seen or record.profile != PROFILE:
                        raise RecordingError('unsupported or duplicate recording header')
                     header_seen = True
                  elif isinstance(record, Schema):
                     old = schemas.get(record.id)
                     if old is not None:
                        if old[0] != record:
                           raise RecordingError('conflicting recording schema ID')
                     else:
                        if record.id == 0 or record.encoding != ENCODING:
                           raise RecordingError('invalid SDL2 recording schema')
                        text = record.data.decode('utf-8')
                        context = _catalogue(text)
                        schemas[record.id] = (record, text, context)
                     yield record
                  elif isinstance(record, Channel):
                     old = channels.get(record.id)
                     if old is not None:
                        if old[0] != record:
                           raise RecordingError('conflicting recording channel ID')
                     else:
                        if record.schema_id not in schemas or record.message_encoding != ENCODING or not record.topic:
                           raise RecordingError('invalid SDL2 recording channel')
                        _, text, context = schemas[record.schema_id]
                        root = record.metadata.get('root_type')
                        if root not in context.messages:
                           raise RecordingError('channel root_type is not a catalogue message')
                        if consumer:
                           diagnostic = compare_catalogues(text, consumer, root=root)
                           if not diagnostic.compatible:
                              raise RecordingError('incompatible local types: ' + diagnostic.code + ' ' + diagnostic.path)
                           context = prepare(text, self.local_types)
                        channels[record.id] = (record, RecordingChannel(record.id,
                           record.schema_id, record.topic, root, text,
                           MappingProxyType(dict(record.metadata)), context))
                     yield channels[record.id][1]
                  elif isinstance(record, Message):
                     if data_end or record.channel_id not in channels:
                        raise RecordingError('message references an unknown channel or follows DataEnd')
                     channel = channels[record.channel_id][1]
                     if _root(channel.context, record.data) != channel.root_type:
                        raise RecordingError('payload root does not match channel root_type')
                     yield RecordingRecord(channel, record.data, record.log_time,
                        record.publish_time, record.sequence)
                  elif isinstance(record, DataEnd):
                     if data_end:
                        raise RecordingError('duplicate MCAP DataEnd')
                     data_end = True
                  elif isinstance(record, Footer):
                     if not data_end:
                        raise RecordingError('missing MCAP DataEnd')
                  elif isinstance(record, Metadata):
                     yield record
            if stream.read(1):
               raise RecordingError('data after MCAP trailing magic')
      except RecordingError:
         raise
      except (ValueError, EOFError, struct.error, UnicodeError) as error:
         raise RecordingError('invalid recording: ' + str(error)) from error
      except Exception as error:
         # The SDK uses its own exceptions for EOF, CRC and size limits.
         if type(error).__module__.startswith('mcap.'):
            raise RecordingError('invalid recording: ' + str(error)) from error
         raise

   def iter_records(self, *, topics=None, start_time_ns=None, end_time_ns=None):
      """Filter log times in [start,end); yielding a prefix does not validate EOF."""
      topics = {topics} if isinstance(topics, str) else set(topics) if topics is not None else None
      if start_time_ns is not None:
         start_time_ns = _uint(start_time_ns, 64, 'start_time_ns')
      if end_time_ns is not None:
         end_time_ns = _uint(end_time_ns, 64, 'end_time_ns')
      for record in self._scan():
         if not isinstance(record, RecordingRecord):
            continue
         if topics is not None and record.channel.topic not in topics:
            continue
         if start_time_ns is not None and record.log_time_ns < start_time_ns:
            continue
         if end_time_ns is not None and record.log_time_ns >= end_time_ns:
            continue
         yield record

   def info(self):
      """Scan metadata and message headers without decoding message bodies."""
      schemas, channels, metadata = {}, {}, []
      count, first, last = 0, None, None
      for record in self._scan():
         if isinstance(record, Schema):
            text = record.data.decode('utf-8')
            schemas[record.id] = {'id': record.id, 'catalogue': text,
               'catalogue_id': catalogue_id(text)}
         elif isinstance(record, RecordingChannel):
            channels.setdefault(record.id, {'id': record.id, 'schema_id': record.schema_id,
               'topic': record.topic, 'root_type': record.root_type,
               'metadata': dict(record.metadata), 'message_count': 0})
         elif isinstance(record, RecordingRecord):
            count += 1
            channels[record.channel.id]['message_count'] += 1
            first = record.log_time_ns if first is None else min(first, record.log_time_ns)
            last = record.log_time_ns if last is None else max(last, record.log_time_ns)
         elif isinstance(record, Metadata):
            metadata.append({'name': record.name, 'data': record.metadata})
      return {'profile': PCAPNG_PROFILE if self.format == 'pcapng' else PROFILE, 'format': self.format, 'message_count': count, 'start_time_ns': first,
         'end_time_ns': last, 'schemas': list(schemas.values()),
         'channels': list(channels.values()), 'metadata': metadata}

   def validate(self):
      count = 0
      for record in self.iter_records():
         try:
            record.decode()
         except CodecError as error:
            raise RecordingError('invalid SDL2 payload on channel %d: %s' % (record.channel.id, error)) from error
         count += 1
      return count


def replay(records, consumer, *, speed=1.0, clock=time.monotonic_ns, sleep=time.sleep):
   """Deliver raw RecordingRecords; speed=0 runs without waiting.

   Paced replay uses log time, absolute monotonic deadlines and file order.
   Decreasing log times raise an error. Consumer exceptions propagate.
   """
   if not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed < 0:
      raise RecordingError('speed must be finite and nonnegative')
   start, origin, previous, count = None, None, None, 0
   for record in records:
      if speed:
         if previous is not None and record.log_time_ns < previous:
            raise RecordingError('log time moved backwards during paced replay')
         if start is None:
            start, origin = clock(), record.log_time_ns
         delta = (record.log_time_ns - origin) / speed
         if not math.isfinite(delta):
            raise RecordingError('replay delay is too large')
         deadline = start + int(delta)
         while True:
            remaining = deadline - clock()
            if remaining <= 0:
               break
            sleep(min(remaining / 1e9, 1.0))
         previous = record.log_time_ns
      consumer(record)
      count += 1
   return count


def convert_recording(source, output, *, format='pcapng', overwrite=False):
   """Validate before exclusive output creation, then preserve raw records and metadata.

   Source/destination must differ. Schema/channel IDs are local and may be remapped.
   The source must remain unchanged between validation and conversion.
   """
   if os.path.realpath(os.fspath(source)) == os.path.realpath(os.fspath(output)):
      raise RecordingError('source and output must differ')
   if os.path.exists(output) and os.path.samefile(source, output):
      raise RecordingError('source and output refer to the same file')
   reader = RecordingReader(source)
   reader.validate()
   schemas, channels = {}, {}
   count = 0
   with RecordingWriter(output, format=format, overwrite=overwrite) as writer:
      for record in reader._scan():
         if isinstance(record, Schema):
            if record.id not in schemas:
               text = record.data.decode('utf-8')
               sid = writer._schemas.get(text)
               if sid is None:
                  sid = writer._writer.register_schema(name='SDL2 catalogue', encoding=ENCODING, data=record.data)
                  writer._schemas[text] = sid
               schemas[record.id] = sid
         elif isinstance(record, RecordingChannel):
            if record.id not in channels:
               channels[record.id] = writer.register_channel(topic=record.topic,
                  catalogue=record.catalogue, root_type=record.root_type, metadata=record.metadata)
         elif isinstance(record, Metadata):
            writer.write_metadata(record.name, record.metadata)
         elif isinstance(record, RecordingRecord):
            writer.write_encoded(channels[record.channel.id], record.payload,
               log_time_ns=record.log_time_ns, publish_time_ns=record.publish_time_ns, sequence=record.sequence)
            count += 1
   return count
