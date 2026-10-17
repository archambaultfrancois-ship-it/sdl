"""Framework shell shortcuts preserve inspection options without adding a cap."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


FRAMEWORK = Path(__file__).resolve().parents[3]


@unittest.skipUnless(shutil.which('bash') and (FRAMEWORK / 'setenv.sh').is_file(),
                     'requires Bash and the framework source checkout')
class EnvironmentTests(unittest.TestCase):
    def test_record_check_forwards_selection_and_format_to_inspect(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python = root / 'python'
            python.write_text('#!' + sys.executable + '\nimport json, sys\n'
                              'print(json.dumps(sys.argv[1:]))\n')
            python.chmod(0o755)
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=str(root), OSE_PYTHON=str(python))
            command = 'set -eu\nshopt -s expand_aliases\nsource "$FRAMEWORK_ENV"\nrecord-check "$@"\n'
            options = ['capture.pcapng', '--summary', '--from', '+1s', '--duration', '1s',
                       '--topic', 'alarm.event', '--format', 'json']
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command,
                                     'test', *options], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout),
                             [str(FRAMEWORK / 'sdl/tools/recording/sdl-recording'),
                              'inspect', '--check', *options])

    def test_record_inspect_default_and_explicit_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application = root / 'application with spaces'
            application.mkdir()
            python = root / 'selected python'
            python.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                              'print(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd()}))\n')
            python.chmod(0o755)
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=str(application), OSE_PYTHON=str(python))
            command = 'set -eu\nshopt -s expand_aliases\nsource "$FRAMEWORK_ENV"\nrecord-inspect "$@"\n'
            for options in ([], ['--limit', '3']):
                with self.subTest(options=options):
                    result = subprocess.run(['bash', '--noprofile', '--norc', '-c', command,
                                             'test', 'capture with spaces.pcapng', *options],
                                            cwd=root, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    call = json.loads(result.stdout)
                    self.assertEqual(call['cwd'], str(application))
                    self.assertEqual(call['args'], [str(FRAMEWORK / 'sdl/tools/recording/sdl-recording'),
                        'inspect', '--format', 'text', 'capture with spaces.pcapng', *options])
