"""Recording integration tests with independent MCAP readers and a fake clock."""

import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'runtime/python'))
sys.path.insert(0, str(ROOT / 'sdlc'))

from mcap.data_stream import RecordBuilder
from mcap.reader import make_reader
from mcap.records import Channel, Chunk, DataEnd, Footer, Header, Message, Schema
from mcap.stream_reader import StreamReader
from mcap.writer import CompressionType, Writer
from generator import MsgParser
from python_backend import PythonBackend
from sdl_runtime import description, encode, prepare
from sdl_recording import (ENCODING, MAGIC, PROFILE, RecordingError, RecordingReader,
                           RecordingWriter, replay)


def bindings(source):
   parser = MsgParser()
   parser.parse_text(source)
   namespace = {'__name__': __name__}
   exec(PythonBackend(parser).generate(), namespace)
   return namespace['Sample']


V1 = bindings('message Sample {\n int32 value;\n}\n')
V2 = bindings('message Sample {\n int32 value;\n optional string note;\n}\n')
BAD = bindings('message Sample {\n string value;\n}\n')
Q = bindings('message Sample {\n fl64 value wire(bits=12,q=0.25);\n}\n')


def raw_recording(*records):
   builder = RecordBuilder()
   for record in (Header(PROFILE, 'test'),) + records + (DataEnd(0), Footer(0, 0, 0)):
      record.write(builder)
   return MAGIC + builder.end() + MAGIC


class RecordingTests(unittest.TestCase):
   def setUp(self):
      self.tmp = tempfile.TemporaryDirectory()
      self.path = Path(self.tmp.name) / 'capture.mcap'

   def tearDown(self):
      self.tmp.cleanup()

   def record(self, cls=V1, times=(1000000000, 1100000000, 1100000000), topic='radio/iq'):
      catalogue = description(cls)
      ctx = prepare(catalogue, [cls])
      payloads = []
      with RecordingWriter(self.path, format='mcap', metadata={'producer': 'test'}, chunk_size=128) as writer:
         channel = writer.register_channel(topic=topic, catalogue=catalogue, root_type='Sample')
         for i, timestamp in enumerate(times):
            message = cls(value=i + 0.375 if cls is Q else i)
            payload = encode(ctx, message)
            payloads.append(payload)
            writer.write_encoded(channel, payload, log_time_ns=timestamp,
               publish_time_ns=max(0, timestamp - 100), sequence=i)
      return payloads

   def test_exact_payloads_catalogue_and_independent_mcap_reader(self):
      payloads = self.record(Q)
      reader = RecordingReader(self.path)
      records = list(reader.iter_records())
      self.assertEqual([r.payload for r in records], payloads)
      self.assertEqual([r.decode().fields['value'] for r in records], [0.5, 1.5, 2.5])
      self.assertEqual([r.publish_time_ns for r in records], [999999900, 1099999900, 1099999900])
      self.assertEqual(reader.validate(), 3)
      info = reader.info()
      self.assertEqual(info['message_count'], 3)
      self.assertEqual(info['schemas'][0]['catalogue'], description(Q))
      self.assertEqual(info['metadata'][0]['data'], {'producer': 'test'})
      with self.path.open('rb') as stream:
         sdk = make_reader(stream, validate_crcs=True)
         self.assertEqual(sdk.get_header().profile, PROFILE)
         self.assertTrue(sdk.get_summary().chunk_indexes)
         self.assertEqual([m.data for _, _, m in sdk.iter_messages(log_time_order=False)], payloads)

   def test_multiple_topics_revisions_and_schema_deduplication(self):
      with RecordingWriter(self.path) as writer:
         a = writer.register_channel(topic='radio/iq', catalogue=description(V1), root_type='Sample')
         b = writer.register_channel(topic='radio/iq', catalogue=description(V2), root_type='Sample')
         c = writer.register_channel(topic='health', catalogue=description(V1), root_type='Sample')
         self.assertEqual(a.schema_id, c.schema_id)
         self.assertNotEqual(a.schema_id, b.schema_id)
         writer.write(a, V1(value=42), log_time_ns=10)
         writer.write(b, V2(value=43, note='new version'), log_time_ns=20)
         writer.write(c, V1(value=44), log_time_ns=20)
      records = list(RecordingReader(self.path).iter_records(topics='radio/iq', start_time_ns=10, end_time_ns=20))
      self.assertEqual(len(records), 1)
      self.assertEqual(records[0].decode().fields, {'value': 42})
      typed = list(RecordingReader(self.path, local_types=[V1]).iter_records())
      self.assertEqual([r.decode(dynamic=False).value for r in typed], [42, 43, 44])
      with self.assertRaisesRegex(RecordingError, 'incompatible local types'):
         RecordingReader(self.path, local_types=[BAD]).validate()

   def test_catalogue_hash_collision_does_not_merge_schemas(self):
      with patch('sdl_recording.catalogue_id', return_value=123):
         with RecordingWriter(self.path) as writer:
            a = writer.register_channel(topic='t', catalogue=description(V1), root_type='Sample')
            b = writer.register_channel(topic='t', catalogue=description(V2), root_type='Sample')
            self.assertNotEqual(a.schema_id, b.schema_id)
            writer.write(a, V1(value=1), log_time_ns=1)
            writer.write(b, V2(value=2), log_time_ns=2)
         records = list(RecordingReader(self.path).iter_records())
         self.assertEqual(records[0].channel.catalogue_id, records[1].channel.catalogue_id)
         self.assertNotEqual(records[0].channel.catalogue, records[1].channel.catalogue)

   def test_empty_file_unused_channels_and_stream_ownership(self):
      stream = io.BytesIO()
      with RecordingWriter(stream) as writer:
         writer.register_channel(topic='empty', catalogue=description(V1), root_type='Sample')
      self.assertFalse(stream.closed)
      self.path.write_bytes(stream.getvalue())
      info = RecordingReader(self.path).info()
      self.assertEqual(info['message_count'], 0)
      self.assertEqual(len(info['channels']), 1)
      self.assertEqual(RecordingReader(self.path).validate(), 0)
      self.assertEqual(RecordingReader(self.path).validate_summary(),
         {'message_count': 0, 'message_count_by_topic': {'empty': 0}})
      with self.assertRaises(FileExistsError):
         RecordingWriter(self.path)
      writer.close()
      with self.assertRaisesRegex(RecordingError, 'closed'):
         writer.flush()

   def test_root_foreign_channel_invalid_body_and_timestamp_validation(self):
      with RecordingWriter(self.path, max_payload_size=10) as writer:
         c = writer.register_channel(topic='t', catalogue=description(V1), root_type='Sample')
         for payload in (b'', b'\x02', b'\x01', b'\x01' + b'\0' * 10):
            with self.subTest(payload=payload), self.assertRaises(RecordingError):
               writer.write_encoded(c, payload)
         payload = encode(prepare(description(V1), [V1]), V1(value=1))
         for keyword, value in (('log_time_ns', -1), ('publish_time_ns', 1 << 64),
               ('sequence', 1 << 32), ('log_time_ns', 1.5), ('sequence', True)):
            with self.subTest(keyword=keyword), self.assertRaises(RecordingError):
               writer.write_encoded(c, payload, **{keyword: value})
         with RecordingWriter(io.BytesIO()) as other:
            other_c = other.register_channel(topic='t', catalogue=description(V1), root_type='Sample')
            with self.assertRaisesRegex(RecordingError, 'belong'):
               writer.write_encoded(other_c, payload)
         with self.assertRaisesRegex(RecordingError, 'root_type'):
            writer.register_channel(topic='t', catalogue=description(V1), root_type='Missing')

   def test_validate_decodes_bodies_even_when_writer_skips_validation(self):
      with RecordingWriter(self.path, validate_payload=False) as writer:
         c = writer.register_channel(topic='t', catalogue=description(V1), root_type='Sample')
         writer.write_encoded(c, b'\x01', log_time_ns=1)
      self.assertEqual(RecordingReader(self.path).info()['message_count'], 1)
      with self.assertRaisesRegex(RecordingError, 'invalid SDL2 payload'):
         RecordingReader(self.path).validate()

   def test_truncated_records_footer_and_crc_corruption(self):
      self.record()
      original = self.path.read_bytes()
      for cut in (0, 8, 20, len(original) // 2, len(original) - 1, len(original) - 8):
         with self.subTest(cut=cut):
            self.path.write_bytes(original[:cut])
            with self.assertRaises(RecordingError):
               RecordingReader(self.path).validate()
      damaged = bytearray(original)
      # Corrupt a message inside an uncompressed chunk without updating its CRC.
      marker = b'\x01\x00\x00\x00\x02'
      pos = original.find(marker)
      self.assertGreater(pos, 0)
      damaged[pos + 4] ^= 1
      self.path.write_bytes(damaged)
      with self.assertRaisesRegex(RecordingError, 'crc'):
         RecordingReader(self.path).validate()
      self.path.write_bytes(original + b'extra')
      with self.assertRaisesRegex(RecordingError, 'trailing magic'):
         RecordingReader(self.path).validate()

   def test_flush_exposes_complete_chunks_before_close(self):
      stream = io.BytesIO()
      writer = RecordingWriter(stream, format='mcap')
      c = writer.register_channel(topic='t', catalogue=description(V1), root_type='Sample')
      writer.write(c, V1(value=4), log_time_ns=10)
      writer.flush()
      self.path.write_bytes(stream.getvalue())
      iterator = RecordingReader(self.path).iter_records()
      self.assertEqual(next(iterator).decode().fields['value'], 4)
      with self.assertRaises(RecordingError):
         next(iterator)
      writer.close()

   def test_paced_replay_ties_deadlines_and_slow_consumer(self):
      payloads = self.record(times=(1000000000, 1200000000, 1200000000, 1400000000))
      now = [0]
      sleeps, deliveries = [], []

      def sleep(seconds):
         sleeps.append(seconds)
         now[0] += int(seconds * 1e9)

      def consume(record):
         deliveries.append((now[0], record.payload))
         now[0] += 30000000  # Consumer cost must not accumulate timing drift.

      count = replay(RecordingReader(self.path).iter_records(), consume,
         speed=2, clock=lambda: now[0], sleep=sleep)
      self.assertEqual(count, 4)
      self.assertEqual([t for t, _ in deliveries], [0, 100000000, 130000000, 200000000])
      self.assertEqual([p for _, p in deliveries], payloads)
      self.assertEqual(len(sleeps), 2)

   def test_replay_clock_reversal_fast_mode_and_consumer_failure(self):
      self.record(times=(20, 10))
      records = list(RecordingReader(self.path).iter_records())
      with self.assertRaisesRegex(RecordingError, 'backwards'):
         replay(records, lambda r: None, sleep=lambda t: None)
      with patch('time.sleep', side_effect=AssertionError('must not sleep')):
         self.assertEqual(replay(records, lambda r: None, speed=0), 2)
      for speed in (-1, float('inf'), float('nan')):
         with self.assertRaises(RecordingError):
            replay(records, lambda r: None, speed=speed)
      def fail(record):
         raise RuntimeError('consumer failed')
      with self.assertRaisesRegex(RuntimeError, 'consumer failed'):
         replay(records, fail, speed=0)

   def test_invalid_profile_schema_channel_and_root(self):
      for defect in ('profile', 'encoding', 'catalogue', 'root', 'payload'):
         with self.subTest(defect=defect), self.path.open('wb') as stream:
            writer = Writer(stream, compression=CompressionType.NONE)
            writer.start(profile='wrong' if defect == 'profile' else PROFILE)
            sid = writer.register_schema(name='test', encoding='wrong' if defect == 'encoding' else ENCODING,
               data=b'invalid' if defect == 'catalogue' else description(V1).encode())
            cid = writer.register_channel(topic='t', message_encoding=ENCODING,
               schema_id=sid, metadata={'root_type': 'Wrong' if defect == 'root' else 'Sample'})
            writer.add_message(cid, 1, b'\x02' if defect == 'payload' else b'\x01\0\0\0\0', 1)
            writer.finish()
         with self.assertRaises(RecordingError):
            RecordingReader(self.path).validate()

   def test_record_size_and_inner_length_limits(self):
      self.record()
      with self.assertRaises(RecordingError):
         RecordingReader(self.path, max_record_size=10).validate()
      # A bounded outer Header must not follow its malicious internal length.
      self.path.write_bytes(MAGIC + struct.pack('<BQ', 1, 4) + struct.pack('<I', 0xffffffff))
      with self.assertRaisesRegex(RecordingError, 'beyond its record'):
         RecordingReader(self.path).validate()

   def test_duplicate_id_conflicts_and_valid_root_on_wrong_channel(self):
      schema = Schema(id=1, name='catalogue', encoding=ENCODING, data=description(V1).encode())
      channel = Channel(id=1, schema_id=1, topic='t', message_encoding=ENCODING,
         metadata={'root_type': 'Sample'})
      cases = (
         (schema, Schema(id=1, name='catalogue', encoding=ENCODING, data=description(V2).encode())),
         (schema, channel, Channel(id=1, schema_id=1, topic='other', message_encoding=ENCODING,
            metadata={'root_type': 'Sample'})),
         (Schema(id=1, name='catalogue', encoding=ENCODING, data=b'SDL2|A{}|B{}'),
            Channel(id=1, schema_id=1, topic='t', message_encoding=ENCODING, metadata={'root_type': 'A'}),
            Message(channel_id=1, log_time=1, publish_time=1, sequence=0, data=b'\x02')),
         (schema, channel, Message(channel_id=1, log_time=1, publish_time=1, sequence=0, data=b'\x81\0\0\0\0\0')),
      )
      for records in cases:
         with self.subTest(records=records):
            self.path.write_bytes(raw_recording(*records))
            with self.assertRaises(RecordingError):
               RecordingReader(self.path).validate()

   def test_summary_crc_and_compressed_chunk_rejection(self):
      self.record()
      data = bytearray(self.path.read_bytes())
      # Change only the footer's summary CRC, leaving the data CRC intact.
      data[-12] ^= 1
      self.path.write_bytes(data)
      with self.assertRaisesRegex(RecordingError, 'crc'):
         RecordingReader(self.path).validate()
      chunk = Chunk(message_start_time=0, message_end_time=0, uncompressed_size=0,
         uncompressed_crc=0, compression='zstd', data=b'')
      self.path.write_bytes(raw_recording(chunk))
      with self.assertRaisesRegex(RecordingError, 'uncompressed'):
         RecordingReader(self.path).validate()

   def test_io_errors_and_explicit_sync(self):
      class FaultyStream(io.BytesIO):
         fail = False
         def write(self, data):
            if self.fail:
               raise OSError('disk full')
            return super().write(data)

      stream = FaultyStream()
      writer = RecordingWriter(stream, format='mcap')
      c = writer.register_channel(topic='t', catalogue=description(V1), root_type='Sample')
      writer.write(c, V1(value=1), log_time_ns=1)
      stream.fail = True
      with self.assertRaisesRegex(OSError, 'disk full'):
         writer.flush()
      stream.fail = False
      writer.close()
      with RecordingWriter(self.path) as writer:
         with patch('sdl_recording.os.fsync') as sync:
            writer.flush(sync=True)
            sync.assert_called_once()

   def cli(self, *args, env=None):
      return subprocess.run([sys.executable, str(ROOT / 'tools/recording/sdl-recording'), *args],
         capture_output=True, text=True, env=env)

   def test_cli_validate_counts_topics_across_versions_and_unused_channels(self):
      expected = {'message_count': 3,
         'message_count_by_topic': {'alarm.event': 2, 'empty': 0, 'sensor.signal': 1}}
      for format in ('mcap', 'pcapng'):
         with self.subTest(format=format):
            with RecordingWriter(self.path, format=format, overwrite=True) as writer:
               first = writer.register_channel(topic='alarm.event', catalogue=description(V1), root_type='Sample')
               evolved = writer.register_channel(topic='alarm.event', catalogue=description(V2), root_type='Sample')
               sensor = writer.register_channel(topic='sensor.signal', catalogue=description(V1), root_type='Sample')
               writer.register_channel(topic='empty', catalogue=description(V1), root_type='Sample')
               writer.write(first, V1(value=1), log_time_ns=1)
               writer.write(sensor, V1(value=2), log_time_ns=2)
               writer.write(evolved, V2(value=3), log_time_ns=3)
            reader = RecordingReader(self.path)
            self.assertEqual(reader.validate_summary(), expected)
            self.assertEqual(reader.validate(), 3)
            result = self.cli('validate', str(self.path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'valid': True, **expected})
            with RecordingWriter(self.path, format=format, overwrite=True):
               pass
            result = self.cli('validate', str(self.path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout),
               {'valid': True, 'message_count': 0, 'message_count_by_topic': {}})

   def test_cli_validate_emits_no_counts_for_bad_payloads_or_truncated_files(self):
      for format in ('mcap', 'pcapng'):
         for malformed in (False, True):
            with self.subTest(format=format, malformed=malformed):
               with RecordingWriter(self.path, format=format, overwrite=True, validate_payload=False) as writer:
                  channel = writer.register_channel(topic='alarm.event', catalogue=description(V1), root_type='Sample')
                  writer.write(channel, V1(value=1), log_time_ns=1)
                  if malformed:
                     writer.write_encoded(channel, b'\x01', log_time_ns=2)
               if not malformed:
                  self.path.write_bytes(self.path.read_bytes()[:-1])
               result = self.cli('validate', str(self.path))
               self.assertEqual(result.returncode, 1, result.stderr)
               self.assertEqual(result.stdout, '')

   def test_cli_info_inspect_offsets_validate_and_replay(self):
      self.record()
      result = self.cli('info', str(self.path))
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertEqual(json.loads(result.stdout)['message_count'], 3)
      result = self.cli('inspect', str(self.path), '--from', '+100ms', '--to', '+101ms', '--limit', '1')
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertEqual(json.loads(result.stdout)['sequence'], 1)
      result = self.cli('validate', str(self.path))
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertTrue(json.loads(result.stdout)['valid'])
      (Path(self.tmp.name) / 'consumer.py').write_text('def on_record(record):\n print(record.sequence)\n')
      env = dict(os.environ, PYTHONPATH=self.tmp.name + os.pathsep + os.environ.get('PYTHONPATH', ''))
      result = self.cli('replay', str(self.path), '--speed', '0', '--consumer', 'consumer:on_record', env=env)
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertEqual(result.stdout.splitlines(), ['0', '1', '2'])
      self.path.write_bytes(self.path.read_bytes()[:-1])
      result = self.cli('replay', str(self.path), '--speed', '0', '--consumer', 'consumer:on_record', env=env)
      self.assertEqual(result.returncode, 1)
      self.assertEqual(result.stdout, '')  # Preflight before invoking the consumer.

   def test_cli_inspect_summary_check_filters_formats_and_channel_versions(self):
      expected = {'message_count': 3,
         'message_count_by_topic': {'alarm.event': 2, 'empty': 0, 'sensor.signal': 1},
         'start_time_ns': 1000000001, 'end_time_ns': 1000000002, 'duration_seconds': 1e-9}
      for container in ('mcap', 'pcapng'):
         with RecordingWriter(self.path, format=container, overwrite=True) as writer:
            alarm = writer.register_channel(topic='alarm.event', catalogue=description(V1), root_type='Sample')
            evolved = writer.register_channel(topic='alarm.event', catalogue=description(V2), root_type='Sample')
            sensor = writer.register_channel(topic='sensor.signal', catalogue=description(V1), root_type='Sample')
            writer.register_channel(topic='empty', catalogue=description(V1), root_type='Sample')
            writer.write(evolved, V2(value=3), log_time_ns=1000000002)
            writer.write(sensor, V1(value=0), log_time_ns=1000000000)
            writer.write(alarm, V1(value=1), log_time_ns=1000000001)
            writer.write(sensor, V1(value=2), log_time_ns=1000000002)
            writer.write(alarm, V1(value=4), log_time_ns=1000000003)
         selection = ['--from', '+1ns', '--to', '+3ns', '--limit', '1']
         self.assertEqual(RecordingReader(self.path).summary(start_time_ns=1000000001,
            end_time_ns=1000000003), expected)
         for mode in (['--summary'], ['--check'], ['--check', '--summary']):
            for format in ('json', 'text', 'table'):
               with self.subTest(container=container, mode=mode, format=format):
                  result = self.cli('inspect', str(self.path), *mode, *selection,
                     '--format', format, '--indent-width', '2')
                  self.assertEqual(result.returncode, 0, result.stderr)
                  if format == 'json':
                     self.assertEqual(json.loads(result.stdout),
                        {'valid': True, **expected} if '--check' in mode else expected)
                  elif format == 'text':
                     self.assertIn('RecordingSummary {\n', result.stdout)
                     self.assertIn('\n  message_count: 3\n', result.stdout)
                     self.assertIn('\n    alarm.event: 2\n', result.stdout)
                     self.assertIn('\n    empty: 0\n', result.stdout)
                     self.assertIn('\n  start_time_ns: 1000000001\n', result.stdout)
                     self.assertIn('\n  duration_seconds: 1e-09\n', result.stdout)
                     self.assertEqual('valid: true' in result.stdout, '--check' in mode)
                  else:
                     self.assertRegex(result.stdout, r'message_count\s*\| uint64\s*\| 3\b')
                     self.assertIn('message_count_by_topic["alarm.event"]', result.stdout)
                     self.assertRegex(result.stdout, r'end_time_ns\s*\| uint64\s*\| 1000000002\b')
                     self.assertRegex(result.stdout, r'duration_seconds\s*\| fl64\s*\| 1e-09\b')
                     self.assertEqual('valid' in result.stdout, '--check' in mode)
                  self.assertNotIn('RecordingRecord {', result.stdout)
         for topics, counts in ((['alarm.event', 'empty'], {'alarm.event': 2, 'empty': 0}),
               (['missing'], {})):
            options = [value for topic in topics for value in ('--topic', topic)]
            result = self.cli('inspect', str(self.path), '--check', *selection, *options)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary['message_count_by_topic'], counts)
            self.assertEqual(summary['message_count'], sum(counts.values()))
            if not counts:
               self.assertIsNone(summary['start_time_ns'])
               self.assertIsNone(summary['end_time_ns'])
               self.assertEqual(summary['duration_seconds'], 0.0)
         result = self.cli('inspect', str(self.path), '--summary', '--from', '3ns', '--to', '1ns')
         self.assertEqual(result.returncode, 1)
         self.assertEqual(result.stdout, '')

   def test_cli_inspect_check_decodes_only_selection_and_never_limits_validation(self):
      for container in ('mcap', 'pcapng'):
         with RecordingWriter(self.path, format=container, overwrite=True, validate_payload=False) as writer:
            good = writer.register_channel(topic='good', catalogue=description(V1), root_type='Sample')
            bad = writer.register_channel(topic='bad', catalogue=description(V1), root_type='Sample')
            writer.write(good, V1(value=1), log_time_ns=10)
            writer.write_encoded(good, b'\x01', log_time_ns=20)
            writer.write_encoded(bad, b'\x01', log_time_ns=30)
         result = self.cli('inspect', str(self.path), '--summary')
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(json.loads(result.stdout)['message_count'], 3)
         for mode in (['--check'], ['--check', '--summary']):
            result = self.cli('inspect', str(self.path), *mode, '--topic', 'good', '--to', '+10ns')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['message_count_by_topic'], {'good': 1})
            self.assertEqual(RecordingReader(self.path).validate_summary(topics='good', end_time_ns=20),
               {'message_count': 1, 'message_count_by_topic': {'good': 1}})
            for format in ('json', 'text', 'table'):
               for selection in (['--limit', '1'], ['--from', '+10ns', '--to', '+20ns', '--topic', 'good']):
                  with self.subTest(container=container, mode=mode, format=format, selection=selection):
                     result = self.cli('inspect', str(self.path), *mode, '--format', format, *selection)
                     self.assertEqual(result.returncode, 1, result.stderr)
                     self.assertIn('invalid SDL2 payload', result.stderr)
                     self.assertEqual(result.stdout, '')
         self.path.write_bytes(self.path.read_bytes()[:-1])
         for mode in ('--summary', '--check'):
            result = self.cli('inspect', str(self.path), mode, '--topic', 'missing')
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stdout, '')

   def test_cli_absolute_and_relative_bounds_duration_and_replay(self):
      origin = 1700000000000000001
      expected = {'message_count': 2, 'message_count_by_topic': {'target': 2},
         'start_time_ns': origin + 1000000000, 'end_time_ns': origin + 2500000000,
         'duration_seconds': 1.5}
      absolute = ['--from', str(origin + 1000000000) + 'ns',
         '--to', str(origin + 3000000000) + 'ns']
      relative = ['--from', '+1s', '--to', '+3s']
      (Path(self.tmp.name) / 'consumer.py').write_text('def on_record(record):\n print(record.sequence)\n')
      env = dict(os.environ, PYTHONPATH=self.tmp.name + os.pathsep + os.environ.get('PYTHONPATH', ''))
      for container in ('mcap', 'pcapng'):
         with RecordingWriter(self.path, format=container, overwrite=True) as writer:
            early = writer.register_channel(topic='early', catalogue=description(V1), root_type='Sample')
            target = writer.register_channel(topic='target', catalogue=description(V1), root_type='Sample')
            # File order and the selected topic's first time differ from the global origin.
            writer.write(target, V1(value=3), log_time_ns=origin + 3000000000, sequence=3)
            writer.write(early, V1(value=0), log_time_ns=origin, sequence=0)
            writer.write(target, V1(value=1), log_time_ns=origin + 1000000000, sequence=1)
            writer.write(target, V1(value=2), log_time_ns=origin + 2500000000, sequence=2)
         for bounds in (absolute, relative,
               ['--from', absolute[1], '--to', '+3s'],
               ['--from', '+1s', '--to', absolute[3]],
               ['--from', '+1s', '--duration', '2s'],
               ['--from', absolute[1], '--duration', '2s']):
            with self.subTest(container=container, bounds=bounds):
               for mode in ('--summary', '--check'):
                  result = self.cli('inspect', str(self.path), mode, '--topic', 'target', *bounds)
                  self.assertEqual(result.returncode, 0, result.stderr)
                  self.assertEqual(json.loads(result.stdout),
                     {'valid': True, **expected} if mode == '--check' else expected)
               result = self.cli('inspect', str(self.path), '--topic', 'target', *bounds)
               self.assertEqual(result.returncode, 0, result.stderr)
               self.assertEqual([json.loads(line)['sequence'] for line in result.stdout.splitlines()], [1, 2])
               result = self.cli('replay', str(self.path), '--topic', 'target', *bounds,
                  '--speed', '0', '--consumer', 'consumer:on_record', env=env)
               self.assertEqual(result.returncode, 0, result.stderr)
               self.assertEqual(result.stdout.splitlines(), ['1', '2'])
         for format in ('text', 'table'):
            result = self.cli('inspect', str(self.path), '--summary', '--topic', 'target',
               *relative, '--format', format)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('duration_seconds: 1.5' if format == 'text' else '1.5', result.stdout)
         result = self.cli('inspect', str(self.path), '--summary', '--from', '1s', '--to', '3s')
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(json.loads(result.stdout)['message_count'], 0)
         self.assertEqual(json.loads(result.stdout)['duration_seconds'], 0.0)
         result = self.cli('inspect', str(self.path), '--summary', '--from', '+3s')
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(json.loads(result.stdout)['message_count'], 1)
         self.assertEqual(json.loads(result.stdout)['duration_seconds'], 0.0)
         result = self.cli('inspect', str(self.path), '--summary', '--duration', '2.5s')
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(json.loads(result.stdout), {'message_count': 2,
            'message_count_by_topic': {'early': 1, 'target': 1}, 'start_time_ns': origin,
            'end_time_ns': origin + 1000000000, 'duration_seconds': 1.0})
         result = self.cli('inspect', str(self.path), '--check', '--from', '+1s', '--duration', '0s')
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(json.loads(result.stdout)['message_count'], 0)
         self.assertIsNone(json.loads(result.stdout)['start_time_ns'])
         for bounds in (['--from', '+3s', '--to', str(origin + 2000000000) + 'ns'],
               ['--from', '+18446744073709551615ns'],
               ['--duration', '18446744073709551615ns']):
            result = self.cli('inspect', str(self.path), '--summary', *bounds)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(result.stdout, '')

   def test_cli_rejects_duration_with_to_and_invalid_time_syntax(self):
      self.record()
      for command in ('inspect', 'replay'):
         required = [] if command == 'inspect' else ['--consumer', 'unused:on_record']
         for options in (['--to', '+1s', '--duration', '1s'],
               ['--relative'], ['--from', '++1s'], ['--to', '+-1s'],
               ['--duration=-1s'], ['--duration', '0.1ns']):
            with self.subTest(command=command, options=options):
               result = self.cli(command, str(self.path), *required, *options)
               self.assertEqual(result.returncode, 2, result.stderr)
               self.assertEqual(result.stdout, '')

   def test_cli_short_options_and_equals_syntax(self):
      self.record()
      report_styles = (
         ['--check', '--summary', '--from', '+100ms', '--duration', '1ms',
            '--topic', 'radio/iq', '--format', 'json'],
         ['--check', '--summary', '--from=+100ms', '--duration=1ms',
            '--topic=radio/iq', '--format=json'],
         ['-cs', '-b', '+100ms', '-d', '1ms', '-t', 'radio/iq', '-f', 'json'],
         ['-cs', '-b+100ms', '-d1ms', '-tradio/iq', '-fjson'])
      for options in report_styles:
         with self.subTest(options=options):
            # The framework shortcut supplies --format text before user options.
            result = self.cli('inspect', str(self.path), '--format', 'text', *options)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report['valid'])
            self.assertEqual(report['message_count'], 2)
            self.assertEqual(report['message_count_by_topic'], {'radio/iq': 2})
      message_styles = (
         ['--from', '+100ms', '--to', '+101ms', '--limit', '1', '--full',
            '--indent-width', '2', '--format', 'text'],
         ['--from=+100ms', '--to=+101ms', '--limit=1', '--full',
            '--indent-width=2', '--format=text'],
         ['-b', '+100ms', '-e', '+101ms', '-n', '1', '-a', '-i', '2', '-f', 'text'])
      outputs = []
      for options in message_styles:
         result = self.cli('inspect', str(self.path), *options)
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual(result.stdout.count('RecordingRecord {'), 1)
         self.assertIn('\n  sequence: 1\n', result.stdout)
         outputs.append(result.stdout)
      self.assertEqual(outputs, [outputs[0]] * len(outputs))
      result = self.cli('inspect', str(self.path), '-s', '-e+1s', '-d1s')
      self.assertEqual(result.returncode, 2)
      self.assertEqual(result.stdout, '')

   def test_cli_inspect_summary_empty_recording_and_empty_time_range(self):
      for container in ('mcap', 'pcapng'):
         with RecordingWriter(self.path, format=container, overwrite=True) as writer:
            writer.register_channel(topic='empty', catalogue=description(V1), root_type='Sample')
         for mode in ('--summary', '--check'):
            for selection in ([], ['--from', '1s', '--to', '1s'], ['--from', '+1s', '--to', '+1s'],
                  ['--duration', '1s']):
               result = self.cli('inspect', str(self.path), mode, *selection)
               self.assertEqual(result.returncode, 0, result.stderr)
               expected = {'message_count': 0, 'message_count_by_topic': {'empty': 0},
                  'start_time_ns': None, 'end_time_ns': None, 'duration_seconds': 0.0}
               if mode == '--check':
                  expected['valid'] = True
               self.assertEqual(json.loads(result.stdout), expected)

   def test_cli_inspect_is_unlimited_by_default_and_honors_explicit_limit(self):
      for format in ('mcap', 'pcapng'):
         with RecordingWriter(self.path, format=format, overwrite=True) as writer:
            channel = writer.register_channel(topic='radio/iq', catalogue=description(V1), root_type='Sample')
            for number in range(8):
               writer.write(channel, V1(value=number), log_time_ns=number, sequence=number)
         for display_format in ('json', 'text', 'table'):
            for limit, count in ((None, 8), (3, 3)):
               with self.subTest(format=format, display_format=display_format, limit=limit):
                  options = [] if limit is None else ['--limit', str(limit)]
                  result = self.cli('inspect', str(self.path), '--format', display_format, *options)
                  self.assertEqual(result.returncode, 0, result.stderr)
                  if display_format == 'json':
                     rows = [json.loads(line) for line in result.stdout.splitlines()]
                     self.assertEqual([row['sequence'] for row in rows], list(range(count)))
                  elif display_format == 'text':
                     self.assertEqual(result.stdout.count('RecordingRecord {'), count)
                  else:
                     self.assertEqual(sum(line.startswith('Record ') for line in result.stdout.splitlines()), count)
         result = self.cli('inspect', str(self.path), '--from', '2ns', '--to', '8ns', '--topic', 'radio/iq')
         self.assertEqual(result.returncode, 0, result.stderr)
         self.assertEqual([json.loads(line)['sequence'] for line in result.stdout.splitlines()], list(range(2, 8)))
         for limit in ('0', '-1'):
            with self.subTest(format=format, limit=limit):
               self.assertEqual(self.cli('inspect', str(self.path), '--limit=' + limit).returncode, 2)

   def test_cli_bounded_preview_full_output_and_invalid_duration(self):
      Array = bindings('message Sample {\n packed fl64 value;\n}\n')
      with RecordingWriter(self.path) as writer:
         c = writer.register_channel(topic='t', catalogue=description(Array), root_type='Sample')
         writer.write(c, Array(value=[float('nan')] + list(range(20))), log_time_ns=0)
      result = self.cli('inspect', str(self.path))
      self.assertEqual(result.returncode, 0, result.stderr)
      preview = json.loads(result.stdout)['message']['fields']['value']
      self.assertEqual(preview['length'], 21)
      self.assertEqual(len(preview['preview']), 8)
      self.assertEqual(preview['preview'][0], 'nan')
      result = self.cli('inspect', str(self.path), '--full')
      self.assertEqual(len(json.loads(result.stdout)['message']['fields']['value']), 21)
      for duration in ('-1s', '0.1ns', '1.000000000000000000000000000001ns', '1e999s'):
         with self.subTest(duration=duration):
            result = self.cli('inspect', str(self.path), '--from=' + duration)
            self.assertEqual(result.returncode, 2)

   def test_cli_table_and_display_nested_fields_arrays_escaping_and_filters(self):
      parser = MsgParser()
      parser.parse_text('message TextMetadata {\n uint64 counter;\n string note;\n}\n'
         'message Sample {\n TextMetadata metadata;\n packed c32 samples;\n optional string missing;\n}\n')
      namespace = {'__name__': __name__}
      exec(PythonBackend(parser).generate(), namespace)
      Sample, Metadata = namespace['Sample'], namespace['TextMetadata']
      from sdl_runtime import Complex32
      message = Sample(metadata=Metadata(counter=2**64 - 1, note='line\ncolumn\t\x1b'),
         samples=[Complex32(i, -i) for i in range(12)])
      with patch.dict(globals(), {'TextMetadata': Metadata}):
         with RecordingWriter(self.path) as writer:
            c = writer.register_channel(topic='radio/iq', catalogue=description(Sample), root_type='Sample')
            writer.write(c, message, log_time_ns=1000000000, sequence=1)
            writer.write(c, message, log_time_ns=2000000000, sequence=2)
      result = self.cli('inspect', str(self.path), '--format', 'table', '--topic', 'radio/iq',
         '--from', '+1s', '--limit', '1')
      self.assertEqual(result.returncode, 0, result.stderr)
      output = result.stdout
      self.assertEqual(output.count('Record '), 1)
      self.assertIn('FIELD', output)
      self.assertIn('TYPE', output)
      self.assertIn('VALUE', output)
      self.assertRegex(output, r'@sequence\s*\| uint32\s*\| 2\b')
      self.assertIn('metadata.counter', output)
      self.assertIn('18446744073709551615', output)
      self.assertIn(r'"line\ncolumn\t\u001b"', output)
      self.assertNotIn('\x1b', output)
      self.assertNotIn('\t', output)
      self.assertIn('length=12', output)
      self.assertIn('samples[7]', output)
      self.assertNotIn('samples[8]', output)
      self.assertIn('4 more elements', output)
      self.assertRegex(output, r'missing\s*\| optional string\s*\| null')
      result = self.cli('inspect', str(self.path), '--format', 'table', '--full', '--limit', '1')
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertIn('samples[11]', result.stdout)
      self.assertIn('(11.0, -11.0)', result.stdout)
      self.assertNotIn('more elements', result.stdout)
      result = self.cli('inspect', str(self.path), '--format', 'text', '--from', '+1s', '--limit', '1')
      self.assertEqual(result.returncode, 0, result.stderr)
      output = result.stdout
      self.assertEqual(output.count('RecordingRecord {'), 1)
      self.assertIn('\n   sequence: 2\n', output)
      self.assertIn('\n   message: Sample {\n      metadata: TextMetadata {\n', output)
      self.assertIn('\n         counter: 18446744073709551615\n', output)
      self.assertIn(r'"line\ncolumn\t\u001b"', output)
      self.assertNotIn('\x1b', output)
      self.assertNotIn('\t', output)
      self.assertIn('\n      samples: [\n', output)
      self.assertIn('... (4 more elements)', output)
      self.assertNotIn('(8.0, -8.0)', output)
      self.assertIn('\n      missing: null\n', output)
      self.assertNotIn('FIELD', output)
      result = self.cli('inspect', str(self.path), '--format', 'text', '--full',
         '--indent-width', '2', '--limit', '1')
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertIn('\n  message: Sample {\n    metadata: TextMetadata {\n', result.stdout)
      self.assertIn('(11.0, -11.0)', result.stdout)
      self.assertNotIn('more elements', result.stdout)
      result = self.cli('inspect', str(self.path), '--format', 'text', '--topic', 'missing')
      self.assertEqual(result.returncode, 0, result.stderr)
      self.assertEqual(result.stdout, '')


if __name__ == '__main__':
   unittest.main()
