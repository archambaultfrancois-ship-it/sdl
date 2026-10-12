#!/usr/bin/env python3
"""Exercise JNI/C MEX gateways and read their output independently with Python."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'runtime/python'))
from test_recording import Q, RecordingReader, RecordingWriter, description

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jni', action='store_true')
    parser.add_argument('--mex', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        source = directory / 'python.mcap'
        with RecordingWriter(source) as writer:
            ch = writer.register_channel(topic='radio/é📻\0iq', catalogue=description(Q), root_type='Sample')
            writer.write(ch, Q(value=.375), log_time_ns=1)
        if args.jni:
            classes = ROOT / 'build/recording/java'
            subprocess.run([os.environ.get('JAVAC', 'javac'), '-encoding', 'UTF-8', '-source', '8', '-target', '8', '-Xlint:-options',
                '-cp', str(classes), '-d', str(classes), str(ROOT / 'tst/recording/native/SdlRecordingTests.java')], check=True)
            target = directory / 'java.mcap'
            subprocess.run([os.environ.get('JAVA', 'java'), '-Djava.library.path=' + str(ROOT / 'build/recording/release'),
                '-cp', str(classes), 'SdlRecordingTests', str(target), str(source)], check=True)
            check_output(target)
        if args.mex:
            target = directory / 'mex.mcap'
            def quote(path): return "'" + str(path).replace("'", "''") + "'"
            expression = '; '.join(['addpath(' + quote(ROOT / p) + ')' for p in
                ('runtime/recording/matlab', 'build/recording/mex', 'tst/recording/native')])
            expression += '; run_recording_mex_tests(' + quote(target) + ', ' + quote(source) + ')'
            subprocess.run([os.environ.get('OCTAVE', 'octave'), '--quiet', '--no-gui', '--eval', expression], check=True)
            check_output(target)

def check_output(path):
    records = list(RecordingReader(path).iter_records())
    assert len(records) == 2
    assert all(r.payload == b'\x01' and r.log_time_ns == 2**64-1 for r in records)
    assert records[0].sequence == 2**32-1
    assert all(r.channel.topic == 'radio/é📻\0iq' for r in records)
    assert RecordingReader(path).validate() == 2
    print(path.name + ': Python compatibility passed')

if __name__ == '__main__': main()
