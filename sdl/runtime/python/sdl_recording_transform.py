"""Validated recording transformations with staged, format-preserving outputs."""
import copy
from contextlib import suppress
from decimal import Decimal, localcontext
import errno
import fnmatch
import importlib
import json
import os
from pathlib import Path
import re
import shutil
from string import Formatter
import tempfile

from mcap.data_stream import RecordBuilder
from mcap.records import Channel, Header, Metadata, Schema
from sdl_pcapng import PROFILE as PCAPNG_PROFILE
from sdl_recording import (ENCODING, PROFILE, RecordingChannel, RecordingError,
                           RecordingReader, RecordingRecord, RecordingWriter, _uint)
from sdl_runtime import CodecError

LIBRARY = 'sdl-recording/python-v1'
CHUNK_SIZE = 1024 * 1024
MAX_NS = (1 << 64) - 1


class _RollbackError(RecordingError):
   pass


def parse_duration(value):
   """Seconds by default; explicit ns/us/ms/s/m/h, without float rounding."""
   match = re.fullmatch(r'(\d+(?:\.\d+)?|\.\d+)(ns|us|ms|s|m|h)?', value)
   if not match:
      raise RecordingError('use a nonnegative duration in seconds or ns/us/ms/s/m/h')
   scale = {'ns': 1, 'us': 1000, 'ms': 10**6, 's': 10**9, 'm': 60 * 10**9, 'h': 3600 * 10**9}
   with localcontext() as context:
      context.prec = max(32, len(match[1]) + 16)
      result = Decimal(match[1]) * scale[match[2] or 's']
   if result != result.to_integral_value() or not 0 <= result <= MAX_NS:
      raise RecordingError('duration must resolve to whole uint64 nanoseconds')
   return int(result)


def parse_size(value):
   match = re.fullmatch(r'(\d+(?:\.\d+)?)(B|KB|MB|GB)?', value, re.IGNORECASE)
   if not match:
      raise RecordingError('use a positive size in B, KB, MB or GB')
   with localcontext() as context:
      context.prec = max(32, len(match[1]) + 12)
      result = Decimal(match[1]) * {'B': 1, 'KB': 1000, 'MB': 10**6, 'GB': 10**9}[(match[2] or 'B').upper()]
   if result < 1 or result != result.to_integral_value():
      raise RecordingError('size must resolve to a positive whole number of bytes')
   return int(result)


def time_bound(value):
   relative = value.startswith('+')
   return parse_duration(value[1:] if relative else value), relative


def _channel_key(channel):
   return channel.topic, channel.catalogue, channel.root_type, tuple(sorted(channel.metadata.items()))


def _record_size(record):
   builder = RecordBuilder()
   record.write(builder)
   return len(builder.end())


def _packet_size(record_size):
   return 32 + ((record_size + 7 + 3) // 4) * 4


class _Layout:
   """Exact closed size for the existing uncompressed writers, including indexes.

   Track counts/byte lengths, never payloads or a per-message index. Verify this
   accounting against the actual serializer before publishing every output.
   """
   def __init__(self, format, metadata):
      self.format = format
      self.schemas = self.channels = self.used = frozenset()
      self.declarations = self.chunk_bytes = self.chunk_messages = self.chunk_indexes = 0
      self.chunk_channels = frozenset()
      self.metadata_indexes = 0
      self.written = (60 + _packet_size(_record_size(Header(PCAPNG_PROFILE, LIBRARY)))
                      if format == 'pcapng' else 8 + _record_size(Header(PROFILE, LIBRARY)))
      for name, values in metadata:
         size = _record_size(Metadata(name, values))
         self.written += _packet_size(size) if format == 'pcapng' else size
         self.metadata_indexes += 29 + len(name.encode('utf-8'))

   def _flush_chunk(self):
      if self.chunk_messages:
         count = len(self.chunk_channels)
         self.written += 49 + self.chunk_bytes + 15 * count + 16 * self.chunk_messages
         self.chunk_indexes += 73 + 10 * count
         self.chunk_bytes = self.chunk_messages = 0
         self.chunk_channels = frozenset()

   def _add(self, size):
      if self.format == 'pcapng':
         self.written += _packet_size(size)
      else:
         self.chunk_bytes += size
         if self.chunk_bytes > CHUNK_SIZE:
            self._flush_chunk()

   def declare(self, channel):
      key = _channel_key(channel)
      if key not in self.channels:
         if len(self.channels) == 65535 or channel.catalogue not in self.schemas and len(self.schemas) == 65535:
            raise RecordingError('recording schema or channel ID space exhausted')
         if channel.catalogue not in self.schemas:
            size = _record_size(Schema(1, channel.catalogue.encode('utf-8'), ENCODING, 'SDL2 catalogue'))
            self.schemas = self.schemas | {channel.catalogue}
            self.declarations += size
            self._add(size)
         size = _record_size(Channel(id=1, schema_id=1, topic=channel.topic,
                                     message_encoding=ENCODING, metadata=dict(channel.metadata)))
         self.channels = self.channels | {key}
         self.declarations += size
         self._add(size)

   def with_record(self, record):
      result = copy.copy(self)
      result.declare(record.channel)
      key = _channel_key(record.channel)
      if key not in result.used:
         result.used = result.used | {key}
      if key not in result.chunk_channels:
         result.chunk_channels = result.chunk_channels | {key}
      result.chunk_messages += 1
      result._add(31 + len(record.payload))
      return result

   def closed_size(self):
      if self.format == 'pcapng':
         return self.written + 68
      result = copy.copy(self)
      result._flush_chunk()
      # DataEnd + repeated declarations + Statistics + index groups + six
      # SummaryOffsets + Footer + trailing magic. Integer field widths are fixed.
      return (result.written + 13 + result.declarations + 55 + 10 * len(result.used)
              + result.chunk_indexes + result.metadata_indexes + 6 * 26 + 29 + 8)


class _Counter:
   def __init__(self):
      self.position = 0
   def write(self, data):
      self.position += len(data)
      return len(data)
   def tell(self):
      return self.position
   def flush(self):
      pass
   def close(self):
      pass


def _signature(path):
   info = path.stat()
   return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class _Predicates:
   def __init__(self, expressions):
      self.expressions, self.plans = expressions, {}

   def prepare(self, channel):
      key = channel.catalogue, channel.root_type
      if not self.expressions or key in self.plans:
         return
      try:
         module = importlib.import_module('sdl_filter')
      except ImportError:
         import sys
         root = Path(__file__).resolve().parents[2]
         for relative in ('runtime/filter/python', 'build/filter/release'):
            sys.path.insert(0, str(root / relative))
         try:
            module = importlib.import_module('sdl_filter')
         except ImportError as error:
            raise RecordingError('content filters require `make -C <SDL_ROOT> filter-python` with this Python interpreter') from error
      plans = []
      try:
         for expression in self.expressions:
            plans.append(module.Filter(channel.catalogue, channel.root_type, expression))
      except BaseException:
         for plan in plans:
            plan.close()
         raise
      self.plans[key] = plans

   def evaluate(self, record):
      indeterminate = False
      for plan in self.plans.get((record.channel.catalogue, record.channel.root_type), ()):
         result = plan.evaluate_wire(record.payload)
         if result.status == -1:
            raise RecordingError('filter evaluation failed on %s: %s' % (record.channel.topic, result.diagnostic))
         if result.status == 0:
            return 'filter'
         indeterminate |= result.status == 2
      return 'indeterminate' if indeterminate else None

   def close(self):
      for plans in self.plans.values():
         for plan in plans:
            plan.close()


def _matches(channel, topics, types):
   return ((not topics or any(fnmatch.fnmatchcase(channel.topic, pattern) for pattern in topics))
           and (not types or any(fnmatch.fnmatchcase(channel.root_type, pattern) for pattern in types)))


def _bounds(info, start, end, duration):
   origin = info['start_time_ns'] or 0
   def resolve(bound):
      if bound is None:
         return None
      value, relative = bound
      _uint(value, 64, 'time bound')
      if type(relative) is not bool:
         raise RecordingError('relative time flag must be a bool')
      return _uint(value + (origin if relative else 0), 64, 'resolved time bound')
   first, last = resolve(start), resolve(end)
   if duration is not None:
      first = origin if first is None else first
      last = _uint(first + duration, 64, 'resolved end time')
   if first is not None and last is not None and last < first:
      raise RecordingError('--to must be at or after --from')
   return first, last


def _records(source, topics, types, predicates, counts=None):
   if _signature(source['path']) != source['signature']:
      raise RecordingError('input changed during transformation: ' + str(source['path']))
   for event in RecordingReader(source['path'])._scan():
      if isinstance(event, RecordingChannel):
         if _matches(event, topics, types):
            predicates.prepare(event)
         continue
      if not isinstance(event, RecordingRecord):
         continue
      # Content predicates are targeted readers. Validate every complete payload,
      # including messages excluded by topics, time or a false predicate.
      try:
         event.decode()
      except CodecError as error:
         raise RecordingError('invalid SDL2 payload on %s: %s' % (event.channel.topic, error)) from error
      reason = None
      if not _matches(event.channel, topics, types):
         reason = 'selection'
      elif ((source['start'] is not None and event.log_time_ns < source['start']) or
            (source['end'] is not None and event.log_time_ns >= source['end'])):
         reason = 'time'
      else:
         reason = predicates.evaluate(event)
      if counts is not None:
         counts['read'] += 1
         if reason:
            counts[reason] += 1
         else:
            counts['selected'] += 1
      if reason is None:
         yield event
   if _signature(source['path']) != source['signature']:
      raise RecordingError('input changed during transformation: ' + str(source['path']))


class _Names:
   def __init__(self, pattern):
      self.pattern = os.fspath(pattern)
      self.fields = set()
      for _, name, spec, conversion in Formatter().parse(self.pattern):
         if name is None:
            continue
         if name not in ('index', 'source', 'format') or conversion or (spec and
               (name != 'index' or re.fullmatch(r'0?\d*d', spec) is None)):
            raise RecordingError('output fields: {index}, {index:04d}, {source}, {format}')
         self.fields.add(name)
      self.parent = Path(self.pattern).absolute().parent
      if '{' in str(self.parent) or '}' in str(self.parent):
         raise RecordingError('output fields must appear in the filename, not its directory')
      if not self.parent.is_dir():
         raise RecordingError('output directory must already exist: ' + str(self.parent))
      self.parent = self.parent.resolve()

   def name(self, index, source, format):
      return self.parent / Path(self.pattern.format(index=index, source=source.stem, format=format)).name


def _check_output(path, sources, overwrite):
   for source in sources:
      if path.resolve() == source['path'] or (path.exists() and os.path.samefile(path, source['path'])):
         raise RecordingError('output aliases an input: ' + str(path))
   if path.is_symlink() or (path.exists() and not path.is_file()):
      raise RecordingError('output must be a regular file: ' + str(path))
   if path.exists() and not overwrite:
      raise FileExistsError('output already exists: ' + str(path))
   return _signature(path) if path.exists() else None


class _Part:
   def __init__(self, path, stage, format, metadata, dry_run):
      self.path, self.stage, self.format = path, stage, format
      self.layout = _Layout(format, metadata)
      self.stream = _Counter() if dry_run else open(stage, 'xb')
      self.writer = None
      try:
         self.writer = RecordingWriter(self.stream, format=format, validate_payload=False, chunk_size=CHUNK_SIZE)
         for name, values in metadata:
            self.writer.write_metadata(name, values)
      except BaseException:
         self.stream.close()
         raise
      self.channels = {}
      self.count = 0
      self.first = self.last = None
      self.topics = {}

   def declare(self, channel):
      key = _channel_key(channel)
      if key not in self.channels:
         self.channels[key] = self.writer.register_channel(topic=channel.topic, catalogue=channel.catalogue,
            root_type=channel.root_type, metadata=channel.metadata)
      return self.channels[key]

   def write(self, record, timestamp, layout):
      channel = self.declare(record.channel)
      self.writer.write_encoded(channel, record.payload, log_time_ns=timestamp,
         publish_time_ns=record.publish_time_ns, sequence=record.sequence)
      self.layout = layout
      self.count += 1
      self.first = timestamp if self.first is None else min(self.first, timestamp)
      self.last = timestamp if self.last is None else max(self.last, timestamp)
      self.topics[channel.topic] = self.topics.get(channel.topic, 0) + 1

   def close(self):
      try:
         self.writer.close()
         size = self.stream.tell()
         if size != self.layout.closed_size():
            raise RecordingError('output size accounting differs from serializer; no files published')
         return {'path': str(self.path), 'format': self.format, 'message_count': self.count,
                 'bytes': size, 'start_time_ns': self.first, 'end_time_ns': self.last,
                 'message_count_by_topic': self.topics}
      finally:
         self.stream.close()


def _publish(parts, sources, overwrite, directory):
   """Publish completed files; roll back our changes if a later publication fails."""
   previous, published = {}, []
   try:
      for index, (path, stage, signature) in enumerate(parts):
         if _check_output(path, sources, overwrite) != signature:
            raise RecordingError('output changed during transformation: ' + str(path))
         if signature is not None:
            backup = directory / ('backup-%d' % index)
            shutil.copy2(path, backup)
            previous[path] = backup
      for path, stage, signature in parts:
         if _check_output(path, sources, overwrite) != signature:
            raise RecordingError('output changed during transformation: ' + str(path))
         if overwrite:
            os.replace(stage, path)
         else:
            _publish_new(stage, path)
         published.append((path, _signature(path)))
   except BaseException as error:
      failures = []
      for path, signature in reversed(published):
         try:
            if not path.exists() or _signature(path) != signature:
               raise RecordingError('published output changed outside this command: ' + str(path))
            if path in previous:
               os.replace(previous[path], path)
            else:
               path.unlink()
         except OSError as failure:
            failures.append(str(failure))
         except RecordingError as failure:
            failures.append(str(failure))
      if failures:
         raise _RollbackError('rollback incomplete; recovery files in %s: %s' % (directory, '; '.join(failures))) from error
      raise


def _publish_new(stage, path):
   if hasattr(os, 'link'):
      try:
         os.link(stage, path)
         return
      except OSError as error:
         if error.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS):
            raise
   # Termux and some external filesystems have no hard-link support. Reserve
   # the name exclusively, then replace that reservation with the completed file.
   with path.open('xb'):
      signature = _signature(path)
   try:
      if _signature(path) != signature:
         raise RecordingError('output reservation changed outside this command: ' + str(path))
      os.replace(stage, path)
   except BaseException:
      if path.exists() and _signature(path) == signature:
         path.unlink()
      raise


def transform_recordings(inputs, output, *, concat=False, timeline='preserve', gap_ns=0,
                         topics=(), types=(), filters=(), start=None, end=None, duration_ns=None,
                         split_duration_ns=None, split_size=None, split_messages=None,
                         dry_run=False, overwrite=False):
   """Filter, optionally concatenate/rebase, then split into autonomous files.

   Bounds are (uint64 nanoseconds, relative-to-each-input bool). No message sort,
   payload rewrite or implicit format conversion. Paths and counts are returned
   as a JSON-serializable report. The output parent directory must exist.
   """
   inputs = [inputs] if isinstance(inputs, (str, os.PathLike)) else list(inputs)
   if not inputs:
      raise RecordingError('at least one input is required')
   if timeline not in ('preserve', 'append'):
      raise RecordingError('timeline must be preserve or append')
   _uint(gap_ns, 64, 'gap')
   if timeline == 'append' and not concat or gap_ns and (not concat or timeline != 'append'):
      raise RecordingError('append/gap require --concat --timeline append')
   if end is not None and duration_ns is not None:
      raise RecordingError('--to and --duration are mutually exclusive')
   for name, value in (('duration', duration_ns), ('split-duration', split_duration_ns)):
      if value is not None:
         _uint(value, 64, name)
   for name, value in (('split-duration', split_duration_ns), ('split-size', split_size), ('split-messages', split_messages)):
      if value is not None and (type(value) is not int or value < 1):
         raise RecordingError(name + ' must be positive')
   names = _Names(output)
   if (split_duration_ns is not None or split_size is not None or split_messages is not None) and 'index' not in names.fields:
      raise RecordingError('splitting requires an {index} field in --output')
   topics = (topics,) if isinstance(topics, str) else tuple(topics)
   types = (types,) if isinstance(types, str) else tuple(types)
   filters = (filters,) if isinstance(filters, str) else tuple(filters)
   predicates = _Predicates(filters)
   sources, warnings, outputs, parts = [], [], [], []
   directory, part, keep_recovery = None, None, False
   try:
      for index, path in enumerate(inputs, 1):
         path = Path(path).resolve(strict=True)
         signature = _signature(path)
         info = RecordingReader(path).info()
         first, last = _bounds(info, start, end, duration_ns)
         source = {'path': path, 'signature': signature, 'info': info, 'start': first, 'end': last,
                   'offset_ns': 0, 'first': None, 'last': None, 'index': index,
                   'counts': dict(read=0, selected=0, selection=0, time=0, filter=0, indeterminate=0)}
         for record in _records(source, topics, types, predicates, source['counts']):
            source['first'] = record.log_time_ns if source['first'] is None else min(source['first'], record.log_time_ns)
            source['last'] = record.log_time_ns if source['last'] is None else max(source['last'], record.log_time_ns)
         sources.append(source)
      if concat and len({source['info']['format'] for source in sources}) != 1:
         raise RecordingError('concatenation requires inputs of the same format; use convert first')
      previous_end = None
      for source in sources:
         if source['first'] is None:
            continue
         if concat and timeline == 'append' and previous_end is not None:
            source['offset_ns'] = previous_end + gap_ns + 1 - source['first']
         first, last = source['first'] + source['offset_ns'], source['last'] + source['offset_ns']
         if not 0 <= first <= last <= MAX_NS:
            raise RecordingError('appended timeline exceeds uint64 nanoseconds')
         if concat and timeline == 'preserve' and previous_end is not None and first <= previous_end:
            warnings.append('input %d overlaps or precedes earlier selected log times' % source['index'])
         previous_end = last if previous_end is None else max(previous_end, last)
      groups = [sources] if concat else [[source] for source in sources]
      seen = set()
      def open_part(group):
         nonlocal directory
         index = len(outputs) + 1
         format = group[0]['info']['format']
         path = names.name(index, group[0]['path'], format)
         if path in seen:
            raise RecordingError('output pattern produces duplicate paths; use {index}: ' + str(path))
         seen.add(path)
         signature = _check_output(path, sources, overwrite)
         if signature is not None and any(old is not None and old[:2] == signature[:2] for _, _, old in parts):
            raise RecordingError('output paths refer to the same existing file: ' + str(path))
         metadata = [(item['name'], item['data']) for source in group for item in source['info']['metadata']]
         provenance = {'version': '1', 'timeline': timeline, 'concat': str(bool(concat)).lower(),
            'gap_ns': str(gap_ns), 'output_index': str(index),
            'selection': json.dumps(dict(topics=topics, types=types, filters=filters, start=start, end=end,
                                         duration_ns=duration_ns), separators=(',', ':')),
            'sources': json.dumps([dict(input_index=source['index'], path=str(source['path']),
                offset_ns=source['offset_ns']) for source in group], separators=(',', ':'))}
         metadata.append(('sdl.transform', provenance))
         stage = None
         if not dry_run:
            if directory is None:
               directory = Path(tempfile.mkdtemp(prefix='.sdl-transform-', dir=names.parent))
            stage = directory / ('part-%d' % index)
         candidate = _Part(path, stage, format, metadata, dry_run)
         parts.append((path, stage, signature))
         return candidate
      def close_part():
         nonlocal part
         current, part = part, None
         summary = current.close()
         if split_size is not None and summary['bytes'] > split_size:
            raise RecordingError('output header/metadata or a single message exceeds --split-size')
         if not dry_run:
            RecordingReader(current.stage).validate()
         outputs.append(summary)
      for group in groups:
         part = open_part(group)
         for source in group:
            for record in _records(source, topics, types, predicates):
               timestamp = record.log_time_ns + source['offset_ns']
               duration_full = (split_duration_ns is not None and part.count and
                  max(part.last, timestamp) - min(part.first, timestamp) >= split_duration_ns)
               count_full = split_messages is not None and part.count >= split_messages
               if part.count and (duration_full or count_full):
                  close_part()
                  part = open_part(group)
               layout = part.layout.with_record(record)
               if part.count and split_size is not None and layout.closed_size() > split_size:
                  close_part()
                  part = open_part(group)
                  layout = part.layout.with_record(record)
               if split_size is not None and layout.closed_size() > split_size:
                  raise RecordingError('output header/metadata or a single message exceeds --split-size')
               part.write(record, timestamp, layout)
         close_part()
      for source in sources:
         if _signature(source['path']) != source['signature']:
            raise RecordingError('input changed during transformation: ' + str(source['path']))
      if not dry_run:
         try:
            _publish(parts, sources, overwrite, directory)
         except _RollbackError:
            keep_recovery = True
            raise
      return {'dry_run': dry_run, 'concat': concat, 'timeline': timeline, 'gap_ns': gap_ns,
              'inputs': [dict(path=str(source['path']), format=source['info']['format'],
                 offset_ns=source['offset_ns'], start_time_ns=source['first'], end_time_ns=source['last'],
                 **source['counts']) for source in sources],
              'outputs': outputs, 'read': sum(source['counts']['read'] for source in sources),
              'selected': sum(source['counts']['selected'] for source in sources),
              'written': sum(output['message_count'] for output in outputs), 'warnings': warnings}
   finally:
      with suppress(Exception):
         predicates.close()
      if part is not None:
         # An unfinished part only remains after an earlier error. Preserve that
         # error even if finish/flush also fails (e.g. disk full), then remove staging.
         with suppress(Exception):
            part.writer.close()
         with suppress(Exception):
            part.stream.close()
      if directory is not None and not keep_recovery:
         shutil.rmtree(directory)
