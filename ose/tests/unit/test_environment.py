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
    def test_sync_directory_is_relative_to_caller_while_endpoint_stays_in_app(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            application, caller = root / 'application', root / 'empty sync directory'
            application.mkdir()
            caller.mkdir()
            python = root / 'python probe'
            python.write_text('#!' + sys.executable + '\nimport json, os, sys\n'
                              'print(json.dumps({"args": sys.argv[1:], "cwd": os.getcwd()}))\n')
            python.chmod(0o755)
            env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                       OSE_APP_ROOT=str(application), OSE_PYTHON=sys.executable, PROBE=str(python),
                       OSE_PARAMETER_TOKEN='test-only-explicit-token')
            options = [(['--dir', '.'], ['--dir', str(caller) + '/.']),
                       (['--dir=relative folder'], ['--dir=' + str(caller / 'relative folder')]),
                       (['--dir', str(caller)], ['--dir', str(caller)]),
                       (['--dir'], ['--dir']),
                       (['--dir', '--help'], ['--dir', '--help']),
                       (['--', '--dir', '.'], ['--', '--dir', '.'])]
            for command in ('params-sync', 'params sync'):
                script = ('set -eu\nshopt -s expand_aliases\nsource "$FRAMEWORK_ENV"\n'
                          'OSE_PYTHON="$PROBE"\n' + command + ' "$@"\n')
                for supplied, expected in options:
                    with self.subTest(command=command, options=supplied):
                        result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script, 'test', *supplied],
                            cwd=caller, env=env, capture_output=True, text=True, check=True)
                        invocation = json.loads(result.stdout)
                        self.assertEqual(invocation['cwd'], str(application))
                        self.assertEqual(invocation['args'],
                            ['-m', 'ose.cli', 'parameters', 'sync', '--client',
                             os.environ.get('OSE_PARAMETER_CLIENT', 'operator'), *expected])

    def test_switching_frameworks_selects_current_modules_and_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frameworks = [root / 'first checkout', root / 'second checkout']
            for checkout in frameworks:
                package = checkout / 'ose/src/ose'
                package.mkdir(parents=True)
                (package / '__init__.py').write_text('')
                (package / 'cli.py').write_text('')
                runtime = checkout / 'sdl/runtime/python'
                runtime.mkdir(parents=True)
                (runtime / 'sdl_runtime.py').write_text('')
                shutil.copyfile(FRAMEWORK / 'setenv.sh', checkout / 'setenv.sh')
            custom = [str(root / 'custom path'), str(root / 'literal [*] path')]
            env = dict(os.environ, OSE_APP_ROOT=str(root), OSE_PYTHON=sys.executable,
                       FIRST_ENV=str(frameworks[0] / 'setenv.sh'),
                       SECOND_ENV=str(frameworks[1] / 'setenv.sh'),
                       PYTHONPATH=':'.join([*custom, str(frameworks[0] / 'ose/src'),
                                            str(frameworks[0] / 'ose/src')]),
                       PROBE='import json, os, ose, sdl_runtime; print(json.dumps({'
                             '"ose": ose.__file__, "sdl": sdl_runtime.__file__, '
                             '"pythonpath": os.environ["PYTHONPATH"].split(":"), '
                             '"path": os.environ["PATH"].split(":")}))')
            env.pop('OSE_PARAMETER_TOKEN', None)
            script = ('set -eu\n'
                      'source "$FIRST_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n'
                      'source "$SECOND_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n'
                      'source "$FIRST_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n'
                      'source "$FIRST_ENV"\n"$OSE_PYTHON" -c "$PROBE"\n')
            result = subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                                    env=env, capture_output=True, text=True, check=True)
            snapshots = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(snapshots), 4)
            selected = [frameworks[0], frameworks[1], frameworks[0], frameworks[0]]
            for snapshot, checkout in zip(snapshots, selected):
                self.assertEqual(snapshot['ose'], str(checkout / 'ose/src/ose/__init__.py'))
                self.assertEqual(snapshot['sdl'], str(checkout / 'sdl/runtime/python/sdl_runtime.py'))
                expected = [str(checkout / path) for path in ('ose/src', 'sdl/runtime/python',
                            'sdl/runtime/filter/python', 'sdl/build/filter/release', 'sdl/build/recording-deps')]
                self.assertEqual(snapshot['pythonpath'][:5], expected)
                for path in expected:
                    self.assertEqual(snapshot['pythonpath'].count(path), 1)
                recording = str(checkout / 'sdl/tools/recording')
                self.assertEqual(snapshot['path'][0], recording)
                self.assertEqual(snapshot['path'].count(recording), 1)
                self.assertLess(snapshot['pythonpath'].index(custom[0]), snapshot['pythonpath'].index(custom[1]))
            self.assertEqual(snapshots[-1], snapshots[-2])

    def token_environment(self, application, commands='', **variables):
        env = dict(os.environ, FRAMEWORK_ENV=str(FRAMEWORK / 'setenv.sh'),
                   OSE_APP_ROOT=str(application), OSE_PYTHON=sys.executable)
        env.pop('OSE_PARAMETER_TOKEN', None)
        env.pop('OSE_PARAMETER_CLIENT', None)
        env.update(variables)
        script = ('set -eu\nsource "$FRAMEWORK_ENV"\n' + commands +
                  '\n"$OSE_PYTHON" -c \'import json, os; '
                  'print(json.dumps(os.environ.get("OSE_PARAMETER_TOKEN")))\'\n')
        return subprocess.run(['bash', '--noprofile', '--norc', '-c', script],
                              env=env, capture_output=True, text=True, check=True)

    def test_parameter_token_loads_selected_client_and_plan_precedes_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            root.joinpath('build').mkdir()
            root.joinpath('deployment.local.json').write_text(json.dumps({'parameter_clients': {
                'operator': {'token': 'test-only-deployment-token'},
                'maintenance': {'token': 'test-only-other-token'}}}))
            result = self.token_environment(root)
            self.assertEqual(json.loads(result.stdout), 'test-only-deployment-token')
            self.assertEqual(result.stderr, '')
            result = self.token_environment(root, OSE_PARAMETER_CLIENT='maintenance')
            self.assertEqual(json.loads(result.stdout), 'test-only-other-token')
            # Preserve literal shell characters and trailing newlines in JSON values.
            token = 'test-only-$(false)-token\n'
            root.joinpath('build/plan.json').write_text(json.dumps({'parameter_clients': {
                'operator': {'token': token}}}))
            self.assertEqual(json.loads(self.token_environment(root).stdout), token)
            for value in ('test-only-manual-token', ''):
                result = self.token_environment(root, OSE_PARAMETER_TOKEN=value)
                self.assertEqual(json.loads(result.stdout), value)
            # A compiled TLS client without a token must not inherit a local token.
            root.joinpath('build/plan.json').write_text(json.dumps({'parameter_clients': {'operator': {}}}))
            self.assertIsNone(json.loads(self.token_environment(root).stdout))

    def test_parameter_token_refreshes_on_resourcing_but_keeps_manual_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, clients in (('first', {'operator': {'token': 'test-only-first-token'}}),
                                  ('second', {'operator': {'token': 'test-only-second-token'},
                                              'maintenance': {'token': 'test-only-maintenance-token'}})):
                application = root / name
                application.mkdir()
                application.joinpath('deployment.json').write_text(json.dumps({'parameter_clients': clients}))
            commands = 'export OSE_APP_ROOT="$NEXT_APP"\nsource "$FRAMEWORK_ENV"\n'
            result = self.token_environment(root / 'first', commands, NEXT_APP=str(root / 'second'))
            self.assertEqual(json.loads(result.stdout), 'test-only-second-token')
            result = self.token_environment(root / 'first', commands +
                'export OSE_PARAMETER_CLIENT=maintenance\nsource "$FRAMEWORK_ENV"\n', NEXT_APP=str(root / 'second'))
            self.assertEqual(json.loads(result.stdout), 'test-only-maintenance-token')
            result = self.token_environment(root / 'first',
                'OSE_PARAMETER_TOKEN=test-only-manual-override\n' + commands, NEXT_APP=str(root / 'second'))
            self.assertEqual(json.loads(result.stdout), 'test-only-manual-override')
            # Switching to an application with no credentials clears the auto value.
            result = self.token_environment(root / 'first', commands, NEXT_APP=str(root))
            self.assertIsNone(json.loads(result.stdout))

    def test_missing_or_invalid_parameter_configuration_keeps_environment_usable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertIsNone(json.loads(self.token_environment(root).stdout))
            root.joinpath('deployment.json').write_text('{invalid JSON')
            result = self.token_environment(root)
            self.assertIsNone(json.loads(result.stdout))
            self.assertIn('Cannot load parameter token', result.stderr)

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
