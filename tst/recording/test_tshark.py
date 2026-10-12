"""Opt-in integration tests against an installed matching native SDL plugin."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_pcapng import capture, blocks, epb, Q, Schema, Channel, Message, description, encode, prepare, ROOT
from mcap.records import Metadata


@unittest.skipUnless(os.environ.get('SDL_TEST_TSHARK') == '1', 'requires installed SDL plugin; use make test-wireshark-tshark')
class TsharkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tshark = os.environ.get('TSHARK', 'tshark')
        if not shutil.which(cls.tshark):
            raise RuntimeError('tshark unavailable')
        plugins = subprocess.run([cls.tshark, '-G', 'plugins'], capture_output=True, text=True, check=True).stdout
        if 'sdl.so' not in plugins and 'sdl.dll' not in plugins:
            raise RuntimeError('Install the matching native SDL plugin before running these tests')

    def dissect(self, path, fields, two=False, extra=()):
        command = [self.tshark] + (['-2'] if two else []) + ['-r', str(path), *extra, '-T', 'fields']
        for field in fields:
            command += ['-e', field]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return [line.split('\t') for line in result.stdout.splitlines()]

    def recording(self, data, fields, extra=()):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'recording.pcapng'
            path.write_bytes(data)
            first = self.dissect(path, fields, extra=extra)
            self.assertEqual(first, self.dissect(path, fields, two=True, extra=extra))
            return first

    def records(self):
        payload = encode(prepare(description(Q), [Q]), Q(value=.375))
        return [Schema(id=1, name='quantum', encoding='sdl2', data=description(Q).encode()),
                Channel(id=1, schema_id=1, topic='radio/iq', message_encoding='sdl2', metadata={'root_type': 'Sample'}),
                Metadata('run', {'origin': 'é📻\0'}),
                Message(channel_id=1, sequence=2**32-1, log_time=2**64-1, publish_time=2**64-2, data=payload)]

    def test_udp_heuristic_decode_as_and_malformed(self):
        fields = ['frame.number', '_ws.col.Protocol', 'sdl.type', 'sdl.malformed']
        for name, count, malformed in [('sdl-demo.pcap', 6, 0), ('sdl-robustness.pcapng', 9, 3)]:
            path = ROOT / 'tools/wireshark' / name
            rows = self.dissect(path, fields)
            self.assertEqual(rows, self.dissect(path, fields, two=True))
            self.assertEqual(rows, self.dissect(path, fields, extra=['-d', 'udp.port==47000,sdl']))
            self.assertEqual(len(rows), count)
            self.assertTrue(all(row[1] == 'SDL2' for row in rows))
            self.assertEqual(sum(bool(row[3]) for row in rows), malformed)
            self.assertEqual([row[2] for row in rows[1:6]], ['Packet', 'Packet', 'EmptyMessage', 'CodecCases', 'RootPayload'])

    def test_user0_exact_values_end_and_filtered_second_pass(self):
        records = self.records()
        fields = ['sdlrec.kind', 'sdlrec.topic', 'sdlrec.sequence', 'sdlrec.log_time_ns',
                  'sdlrec.publish_time_ns', 'sdlrec.payload', 'sdl.value', 'sdlrec.packet_count',
                  'sdlrec.message_count', '_ws.expert.message']
        rows = self.recording(capture(*records), fields)
        self.assertEqual([r[0] for r in rows], ['1', '3', '4', '12', '5', '15'])
        self.assertTrue(all(not r[-1] for r in rows))
        self.assertEqual(rows[4][1:7], ['radio/iq', str(2**32-1), str(2**64-1), str(2**64-2), records[-1].data.hex(), '0.5'])
        self.assertEqual(rows[-1][7:9], ['5', '1'])
        self.assertEqual(self.recording(capture(*records), fields, ['-Y', 'sdlrec.kind == 5 && sdlrec.topic == "radio/iq"']), [rows[4]])

    def test_future_declarations_and_end_failure_persist(self):
        records = self.records()
        rows = self.recording(capture(records[-1], *records), ['sdlrec.kind', '_ws.expert.message'])
        self.assertIn('missing', rows[1][1])
        self.assertFalse(rows[-2][1])
        data = capture(*records)
        end_offset = list(blocks(data))[-1][0]
        rows = self.recording(data[:end_offset] + epb(15, bytes(20))[0], ['sdlrec.kind', '_ws.expert.message'])
        self.assertTrue(rows[-1][1])


if __name__ == '__main__':
    unittest.main()
