"""End-to-end recording transformations, independent readers and failure rollback."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_recording import ROOT, V1, V2, Q, description
from sdl_recording import RecordingError, RecordingReader, RecordingWriter, transform_recordings
from sdl_recording_transform import _Predicates, parse_duration, parse_size, time_bound


class TransformTests(unittest.TestCase):
   def setUp(self):
      self.temporary = tempfile.TemporaryDirectory()
      self.addCleanup(self.temporary.cleanup)
      self.root = Path(self.temporary.name)

   def capture(self, name, *, format='pcapng', times=(10, 20, 30), values=None, topic='sample', cls=V1):
      path = self.root / name
      values = range(len(times)) if values is None else values
      with RecordingWriter(path, format=format, metadata={'source': name}) as writer:
         channel = writer.register_channel(topic=topic, catalogue=description(cls), root_type='Sample', metadata={'unit': 'raw'})
         for index, (timestamp, value) in enumerate(zip(times, values)):
            writer.write(channel, cls(value=value), log_time_ns=timestamp, publish_time_ns=timestamp // 2, sequence=100 + index)
      return path

   def records(self, report):
      records = []
      for output in report['outputs']:
         path = Path(output['path'])
         reader = RecordingReader(path)
         self.assertEqual(reader.validate(), output['message_count'])
         self.assertEqual(reader.format, output['format'])
         self.assertEqual(path.stat().st_size, output['bytes'])
         records.extend(reader.iter_records())
      return records

   def filters_available(self):
      probe = _Predicates(['value >= 0'])
      channel = type('Channel', (), dict(catalogue=description(V1), root_type='Sample'))()
      try:
         probe.prepare(channel)
      except RecordingError as error:
         if 'filters require' in str(error):
            self.skipTest(str(error))
         raise
      finally:
         probe.close()

   def test_concat_preserves_formats_file_order_catalogues_and_raw_values(self):
      for format in ('pcapng', 'mcap'):
         with self.subTest(format=format):
            a = self.capture('a.' + format, format=format, times=(30, 10, 20), values=(.375, 1.375, 2.375), cls=Q)
            b = self.capture('b.' + format, format=format, times=(15, 20), cls=V2)
            originals = list(RecordingReader(a).iter_records()) + list(RecordingReader(b).iter_records())
            report = transform_recordings([a, b], self.root / ('combined.' + format), concat=True)
            result = self.records(report)
            self.assertEqual([(r.payload, r.log_time_ns, r.publish_time_ns, r.sequence) for r in result],
                             [(r.payload, r.log_time_ns, r.publish_time_ns, r.sequence) for r in originals])
            info = RecordingReader(report['outputs'][0]['path']).info()
            self.assertEqual(len(info['schemas']), 2)
            self.assertEqual(len(info['channels']), 2)
            self.assertEqual(info['metadata'][0]['data'], {'source': a.name})
            self.assertEqual(info['metadata'][1]['data'], {'source': b.name})
            provenance = info['metadata'][2]
            self.assertEqual(provenance['name'], 'sdl.transform')
            self.assertEqual(len(json.loads(provenance['data']['sources'])), 2)
            self.assertEqual(len(report['warnings']), 1)

   def test_append_rebases_selected_log_times_only_and_handles_empty_inputs(self):
      a = self.capture('a', times=(100, 130))
      empty = self.capture('empty', times=())
      b = self.capture('b', times=(2000, 2020))
      c = self.capture('c', times=(0, 5))
      report = transform_recordings([a, empty, b, c], self.root / 'joined', concat=True,
                                   timeline='append', gap_ns=10)
      records = self.records(report)
      self.assertEqual([r.log_time_ns for r in records], [100, 130, 141, 161, 172, 177])
      self.assertEqual([r.publish_time_ns for r in records], [50, 65, 1000, 1010, 0, 2])
      self.assertEqual([s['offset_ns'] for s in report['inputs']], [0, 0, -1859, 172])
      self.assertFalse(report['warnings'])

   def test_content_topic_type_and_time_filters_then_concat_and_all_split_limits(self):
      self.filters_available()
      for format in ('pcapng', 'mcap'):
         a = self.capture('a.' + format, format=format, times=(0, 10, 20, 30, 40), values=(0, 1, 2, 3, 4))
         b = self.capture('b.' + format, format=format, times=(100, 110, 120, 130, 140), values=(5, 6, 7, 8, 9))
         report = transform_recordings([a, b], self.root / ('part-{index:04d}.' + format), concat=True,
            timeline='append', topics=['sam*'], types=['Sam*'], filters=['value >= 2', 'value < 8'],
            start=(10, True), duration_ns=40, split_duration_ns=15, split_messages=2, split_size=4096)
         result = self.records(report)
         self.assertEqual([r.decode().fields['value'] for r in result], [2, 3, 4, 6, 7])
         self.assertEqual([r.log_time_ns for r in result], [20, 30, 40, 41, 51])
         self.assertEqual([output['message_count'] for output in report['outputs']], [2, 2, 1])
         self.assertEqual(report['read'], 10)
         self.assertEqual(report['selected'], 5)
         self.assertEqual([source['time'] for source in report['inputs']], [1, 1])

   def test_separate_inputs_can_have_different_formats_and_index_is_global(self):
      a = self.capture('one.pcapng')
      b = self.capture('two.mcap', format='mcap')
      report = transform_recordings([a, b], self.root / '{source}-{index:02d}.{format}', split_messages=2)
      self.assertEqual([Path(o['path']).name for o in report['outputs']],
                       ['one-01.pcapng', 'one-02.pcapng', 'two-03.mcap', 'two-04.mcap'])
      self.assertEqual([r.decode().fields['value'] for r in self.records(report)], [0, 1, 2, 0, 1, 2])

   def test_exact_size_limits_include_metadata_indexes_and_footer(self):
      for format in ('pcapng', 'mcap'):
         source = self.capture('source.' + format, format=format, times=tuple(range(20)))
         pattern = self.root / ('sized-{index:04d}.' + format)
         probe = transform_recordings([source], pattern, split_messages=3, dry_run=True)
         limit = probe['outputs'][0]['bytes']
         report = transform_recordings([source], pattern, split_size=limit)
         self.assertEqual([output['message_count'] for output in report['outputs']], [3] * 6 + [2])
         self.assertTrue(all(output['bytes'] <= limit for output in report['outputs']))
         self.assertEqual(len(self.records(report)), 20)

   def test_large_mcap_chunks_and_multiple_channel_indexes_have_exact_sizes(self):
      source = self.root / 'large.mcap'
      with RecordingWriter(source, format='mcap') as writer:
         large = writer.register_channel(topic='large', catalogue=description(V2), root_type='Sample')
         small = writer.register_channel(topic='small', catalogue=description(V1), root_type='Sample')
         for index in range(2):
            writer.write(small, V1(value=index), log_time_ns=index)
            writer.write(large, V2(value=index, note='x' * 1100000), log_time_ns=index)
         writer.write(small, V1(value=3), log_time_ns=3)
      pattern = self.root / 'large-out-{index}.mcap'
      dry = transform_recordings([source], pattern, dry_run=True)
      result = transform_recordings([source], pattern, split_size=dry['outputs'][0]['bytes'])
      self.assertEqual(dry['outputs'], result['outputs'])
      self.assertEqual(len(self.records(result)), 5)

   def test_dry_run_is_exact_and_creates_no_files(self):
      for format in ('pcapng', 'mcap'):
         source = self.capture('source.' + format, format=format)
         pattern = self.root / ('out-{index}.' + format)
         before = set(self.root.iterdir())
         dry = transform_recordings([source], pattern, split_messages=1, dry_run=True)
         self.assertEqual(set(self.root.iterdir()), before)
         real = transform_recordings([source], pattern, split_messages=1)
         self.assertEqual(dry['outputs'], real['outputs'])
         self.records(real)

   def test_no_matches_and_empty_recordings_produce_valid_empty_outputs(self):
      for format in ('pcapng', 'mcap'):
         source = self.capture('empty-source.' + format, format=format, times=())
         report = transform_recordings([source], self.root / ('empty.' + format))
         self.assertEqual(self.records(report), [])
         source = self.capture('filtered-source.' + format, format=format)
         report = transform_recordings([source], self.root / ('filtered.' + format), topics=['absent*'])
         self.assertEqual(self.records(report), [])
         self.assertEqual(report['inputs'][0]['selection'], 3)

   def test_identical_channels_are_deduplicated_even_if_source_ids_differ(self):
      a = self.capture('a')
      b = self.root / 'b'
      with RecordingWriter(b) as writer:
         writer.register_channel(topic='unused', catalogue=description(V2), root_type='Sample')
         channel = writer.register_channel(topic='sample', catalogue=description(V1), root_type='Sample', metadata={'unit': 'raw'})
         writer.write(channel, V1(value=9), log_time_ns=40)
      report = transform_recordings([a, b], self.root / 'dedup', concat=True)
      self.assertEqual(len(self.records(report)), 4)
      self.assertEqual(len(RecordingReader(report['outputs'][0]['path']).info()['channels']), 1)

   def test_corruption_or_invalid_filtered_payload_never_creates_outputs(self):
      good = self.capture('good')
      bad = self.capture('bad')
      bad.write_bytes(bad.read_bytes()[:-1])
      with self.assertRaises(RecordingError):
         transform_recordings([good, bad], self.root / 'out-{index}', concat=True)
      invalid = self.root / 'invalid'
      with RecordingWriter(invalid, validate_payload=False) as writer:
         channel = writer.register_channel(topic='excluded', catalogue=description(V1), root_type='Sample')
         writer.write_encoded(channel, b'\1', log_time_ns=0)
      with self.assertRaisesRegex(RecordingError, 'invalid SDL2 payload'):
         transform_recordings([invalid], self.root / 'out', topics=['sample'])
      self.assertFalse(list(self.root.glob('out*')))
      self.assertFalse(list(self.root.glob('.sdl-transform-*')))

   def test_aliases_output_collisions_and_overwrite(self):
      source = self.capture('source')
      original = source.read_bytes()
      with self.assertRaisesRegex(RecordingError, 'aliases an input'):
         transform_recordings([source], source, overwrite=True)
      alias = self.root / 'alias'
      alias.symlink_to(source)
      with self.assertRaisesRegex(RecordingError, 'aliases an input'):
         transform_recordings([source], alias, overwrite=True)
      output = self.root / 'out-2'
      output.write_bytes(b'existing output')
      for dry in (True, False):
         with self.assertRaises(FileExistsError):
            transform_recordings([source], self.root / 'out-{index}', split_messages=1, dry_run=dry)
         self.assertFalse((self.root / 'out-1').exists())
         self.assertEqual(output.read_bytes(), b'existing output')
      report = transform_recordings([source], self.root / 'out-{index}', split_messages=1, overwrite=True)
      self.assertEqual(len(self.records(report)), 3)
      self.assertEqual(source.read_bytes(), original)

   def test_publication_failure_rolls_back_new_files_and_existing_outputs(self):
      source = self.capture('source')
      import sdl_recording_transform as module
      real_replace, real_install = module.os.replace, module._publish_new
      for overwrite in (False, True):
         with self.subTest(overwrite=overwrite):
            out1, out2 = self.root / 'out-1', self.root / 'out-2'
            if overwrite:
               out1.write_bytes(b'old-1')
               out2.write_bytes(b'old-2')
            def install(stage, path):
               if path == out2:
                  raise OSError('injected publish failure')
               return real_install(stage, path)
            def replace(stage, path):
               if str(stage).endswith('part-2'):
                  raise OSError('injected publish failure')
               return real_replace(stage, path)
            with patch.object(module, '_publish_new', side_effect=install), \
                  patch.object(module.os, 'replace', side_effect=replace), self.assertRaisesRegex(OSError, 'injected'):
               transform_recordings([source], self.root / 'out-{index}', split_messages=1, overwrite=overwrite)
            if overwrite:
               self.assertEqual(out1.read_bytes(), b'old-1')
               self.assertEqual(out2.read_bytes(), b'old-2')
            else:
               self.assertFalse(out1.exists())
               self.assertFalse(out2.exists())
            self.assertFalse((self.root / 'out-3').exists())
            self.assertFalse(list(self.root.glob('.sdl-transform-*')))

   def test_no_hardlink_support_and_message_too_large(self):
      source = self.capture('source')
      with patch('sdl_recording_transform.os.link', create=True, side_effect=OSError(errno.EOPNOTSUPP, 'unsupported')):
         report = transform_recordings([source], self.root / 'portable')
      self.assertEqual(len(self.records(report)), 3)
      with self.assertRaisesRegex(RecordingError, 'single message exceeds'):
         transform_recordings([source], self.root / 'tiny-{index}', split_size=1)
      self.assertFalse(list(self.root.glob('tiny-*')))

   def test_write_and_finish_failures_clean_staging_and_preserve_primary_error(self):
      source = self.capture('source')
      with patch('sdl_recording_transform._Part.write', side_effect=OSError('write failed')), \
            patch('sdl_recording_transform.RecordingWriter.close', side_effect=OSError('finish failed')), \
            self.assertRaisesRegex(OSError, 'write failed'):
         transform_recordings([source], self.root / 'out')
      self.assertEqual(set(self.root.iterdir()), {source})

   def test_changed_source_between_preflight_and_writing_aborts(self):
      source = self.capture('source')
      import sdl_recording_transform as module
      initialize = module._Part.__init__
      def change(part, *args, **kwargs):
         initialize(part, *args, **kwargs)
         source.write_bytes(source.read_bytes() + b'changed')
      with patch.object(module._Part, '__init__', change), self.assertRaisesRegex(RecordingError, 'input changed'):
         transform_recordings([source], self.root / 'out')
      self.assertFalse((self.root / 'out').exists())
      self.assertFalse(list(self.root.glob('.sdl-transform-*')))

   def test_concurrent_output_creation_is_preserved_while_earlier_outputs_roll_back(self):
      source = self.capture('source')
      import sdl_recording_transform as module
      install = module._publish_new
      out2 = self.root / 'out-2'
      def collide(stage, path):
         if path == out2:
            out2.write_bytes(b'other writer')
         return install(stage, path)
      with patch.object(module, '_publish_new', side_effect=collide), self.assertRaises(FileExistsError):
         transform_recordings([source], self.root / 'out-{index}', split_messages=1)
      self.assertFalse((self.root / 'out-1').exists())
      self.assertEqual(out2.read_bytes(), b'other writer')

   def test_failed_rollback_retains_original_files_for_recovery(self):
      source = self.capture('source')
      out1, out2 = self.root / 'out-1', self.root / 'out-2'
      out1.write_bytes(b'old first')
      out2.write_bytes(b'old second')
      import sdl_recording_transform as module
      replace = module.os.replace
      def fail(stage, path):
         if Path(stage).name in ('part-2', 'backup-0'):
            raise OSError('publish/restore failure')
         return replace(stage, path)
      with patch.object(module.os, 'replace', side_effect=fail), self.assertRaisesRegex(RecordingError, 'recovery files in'):
         transform_recordings([source], self.root / 'out-{index}', split_messages=1, overwrite=True)
      directories = list(self.root.glob('.sdl-transform-*'))
      self.assertEqual(len(directories), 1)
      self.assertEqual((directories[0] / 'backup-0').read_bytes(), b'old first')
      self.assertEqual(out2.read_bytes(), b'old second')

   def test_bad_combinations_names_and_time_overflow(self):
      source = self.capture('source', times=((1 << 64) - 2, (1 << 64) - 1))
      for options in (dict(timeline='append'), dict(gap_ns=1), dict(split_messages=0),
                      dict(split_duration_ns=0), dict(split_size=0), dict(start=(20, False), end=(10, False)),
                      dict(end=(5, False), duration_ns=10), dict(start=(10, True)), dict(start=(-1, True))):
         with self.subTest(options=options), self.assertRaises(RecordingError):
            transform_recordings([source], self.root / 'out-{index}', **options)
      with self.assertRaisesRegex(RecordingError, 'uint64'):
         transform_recordings([source, source], self.root / 'append', concat=True, timeline='append')
      for pattern in ('{unknown}', '{source!r}', '{index.__class__}', 'sub-{index}/part'):
         with self.subTest(pattern=pattern), self.assertRaises((RecordingError, ValueError)):
            transform_recordings([source], self.root / pattern)
      with self.assertRaisesRegex(RecordingError, 'requires an.*index'):
         transform_recordings([source], self.root / 'out', split_messages=1)
      with self.assertRaisesRegex(RecordingError, 'duplicate paths'):
         transform_recordings([source, source], self.root / 'same')
      mixed = self.capture('mixed', format='mcap')
      with self.assertRaisesRegex(RecordingError, 'same format'):
         transform_recordings([source, mixed], self.root / 'out', concat=True)

   def test_invalid_and_indeterminate_filters(self):
      self.filters_available()
      source = self.capture('optional', cls=V2)
      with self.assertRaises(ValueError):
         transform_recordings([source], self.root / 'bad-filter', filters=['no_such_field == 1'])
      report = transform_recordings([source], self.root / 'unknown', filters=['note == "present"'])
      self.assertEqual(report['written'], 0)
      self.assertEqual(report['inputs'][0]['indeterminate'], 3)
      self.assertFalse((self.root / 'bad-filter').exists())

   def test_exact_unit_parsing(self):
      self.assertEqual(parse_duration('10m'), 600000000000)
      self.assertEqual(parse_duration('2'), 2000000000)
      self.assertEqual(parse_duration('.5ms'), 500000)
      self.assertEqual(time_bound('+1ns'), (1, True))
      self.assertEqual(parse_size('1.5MB'), 1500000)
      for value in ('-1s', 'nan', '1.1ns', '18446744073709551616ns'):
         with self.subTest(value=value), self.assertRaises(RecordingError):
            parse_duration(value)
      for value in ('0', '1.1B', 'nan', '-1MB'):
         with self.subTest(value=value), self.assertRaises(RecordingError):
            parse_size(value)

   def test_cli_combined_transform_and_filter_option(self):
      self.filters_available()
      a = self.capture('a.pcapng')
      b = self.capture('b.pcapng', times=(0, 10, 20))
      command = [sys.executable, str(ROOT / 'tools/recording/sdl-recording'), 'transform', str(a), str(b),
                 '--concat', '--timeline', 'append', '--gap', '2ns', '--topic', 'sam*', '--type', 'Sample',
                 '--filter', 'value > 0', '--split-messages', '2', '--split-duration', '10m',
                 '--split-size', '10KB', '--output', str(self.root / 'cli-{index:04d}.pcapng')]
      dry = subprocess.run(command + ['--dry-run'], text=True, capture_output=True)
      self.assertEqual(dry.returncode, 0, dry.stderr)
      self.assertFalse(list(self.root.glob('cli-*')))
      result = subprocess.run(command, text=True, capture_output=True)
      self.assertEqual(result.returncode, 0, result.stderr)
      report = json.loads(result.stdout)
      self.assertEqual(report['written'], 4)
      self.assertEqual(json.loads(dry.stdout)['outputs'], report['outputs'])
      self.assertEqual([r.log_time_ns for r in self.records(report)], [20, 30, 33, 43])
      bad = subprocess.run(command + ['--where', 'value > 0'], text=True, capture_output=True)
      self.assertEqual(bad.returncode, 2)
