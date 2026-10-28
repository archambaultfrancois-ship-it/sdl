"""Real inotify and OSE RPCs, without component processes or business timers."""
import asyncio
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import tempfile
import unittest
from unittest.mock import patch

from ose.codec.sdl2 import SDL2
from ose.execution.broker import Broker
from ose.execution.distributed import Coordinator
from ose.parameters.client import ParameterClient
from ose.parameters.files import read_value
from ose.parameters.sync import DirectorySync
from ose.parameters.values import Values

SDL_ROOT = Path(__file__).resolve().parents[3] / 'sdl'
TOKEN = 'parameter-sync-test-token'


@unittest.skipUnless(importlib.util.find_spec('inotify_simple'), 'install OSE with the sync extra')
class SyncIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def setup_server(self, distributed=False, count=2):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'parameters'
        self.codec = SDL2(SDL_ROOT)
        catalogue = 'SDL2|Settings{1:fl32:threshold;}'
        self.values = Values(self.codec.api, catalogue, 'Settings')
        paths = [f'machines/m{index}/detection' for index in range(count)]
        self.path = paths[0]
        self.file = self.directory / (self.path + '.json')
        self.plan = dict(sdl_root=str(SDL_ROOT), components={}, subscriptions=[], max_frame=1024 * 1024,
            max_datagram=1200, execution='distributed' if distributed else 'local',
            shared_parameters={path: dict(root='Settings', catalogue=catalogue,
                initial_payload=base64.b64encode(self.values.encode({'threshold': 80.})).decode()) for path in paths},
            parameter_clients={'operator': dict(read=paths, write=paths, token=TOKEN)})
        self.server_type = Coordinator if distributed else Broker
        self.server = self.server_type(self.plan)
        self.port = await self.server.start()
        self.distributed = distributed
        self.logs = []
        self.sync = DirectorySync(self.directory, self.connect, debounce=.025, retry_delay=.05, report=self.logs.append)
        self.task = None
        self.addAsyncCleanup(self.cleanup_server)

    async def connect(self):
        return await ParameterClient.connect('127.0.0.1', self.port, 'operator', token=TOKEN,
                                             runtime=self.codec, distributed=self.distributed)

    async def cleanup_server(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.server.close()

    async def until(self, predicate):
        async def wait():
            while not predicate():
                if self.task is not None and self.task.done():
                    self.task.result()
                await asyncio.sleep(.01)
        await asyncio.wait_for(wait(), 10)

    def remote(self):
        return self.values.decode(self.server.parameters.service.values[self.path])['threshold']

    def local(self):
        try:
            return read_value(self.file)[self.path]['threshold']
        except (OSError, ValueError, KeyError):
            return None

    def edit(self, value):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps({self.path: {'threshold': value}}))

    async def exercise_events(self, distributed):
        await self.setup_server(distributed)
        self.edit(85.)
        self.task = asyncio.create_task(self.sync.session())
        await self.until(lambda: any('watching ' in line for line in self.logs))
        self.assertEqual(self.remote(), 85.)
        service = self.server.parameters.service
        original_set = service.set
        writes = []
        def counted(path, payload):
            writes.append(path)
            return original_set(path, payload)
        service.set = counted
        self.edit(90.)
        await self.until(lambda: self.remote() == 90.)
        await asyncio.sleep(.12)
        self.assertEqual(writes, [self.path])
        async with await self.connect() as client:
            block = await client.block(self.path)
            await block.set({'threshold': 95.})
        await self.until(lambda: self.local() == 95.)
        await asyncio.sleep(.12)
        self.assertEqual(writes, [self.path, self.path])  # No file echo.
        # Editors often save to a temporary file, then replace the original.
        temporary = self.file.with_suffix('.tmp')
        temporary.write_text(json.dumps({self.path: {'threshold': 100.}}))
        os.replace(temporary, self.file)
        await self.until(lambda: self.remote() == 100.)
        self.file.write_text('{broken')
        await self.until(lambda: self.sync.entries[self.path].blocked)
        service.set(self.path, self.values.encode({'threshold': 110.}))
        await asyncio.sleep(.12)
        self.assertEqual(self.file.read_text(), '{broken')
        self.edit(120.)
        await self.until(lambda: self.remote() == 120.)
        self.file.unlink()
        await self.until(lambda: self.local() == 120.)
        # Directory move-in installs watches and finds already existing JSONs.
        incoming = self.root / 'incoming'
        incoming.mkdir()
        (incoming / 'detection.json').write_text(json.dumps({self.path: {'threshold': 124.}}))
        shutil.rmtree(self.file.parent)
        os.replace(incoming, self.file.parent)
        await self.until(lambda: self.remote() == 124.)
        self.edit(125.)
        await self.until(lambda: self.remote() == 125.)
        await asyncio.sleep(.1)
        previous = len(writes)
        stamp = self.file.stat().st_mtime_ns + 1000
        os.utime(self.file, ns=(stamp, stamp))
        await self.until(lambda: len(writes) > previous)

    async def test_local_events(self):
        await self.exercise_events(False)

    async def test_distributed_events(self):
        await self.exercise_events(True)

    async def test_subscription_groups_and_no_cyclic_get(self):
        await self.setup_server(count=257)
        self.sync.initial = 'app'
        gets = []
        service = self.server.parameters.service
        original = service.request
        async def counted(owner, header, payload, rights):
            if header['op'] == 'parameters_get':
                gets.append(header['path'])
            return await original(owner, header, payload, rights)
        service.request = counted
        self.task = asyncio.create_task(self.sync.session())
        await self.until(lambda: len(service.sessions) == 2)
        self.assertEqual(len(gets), 257)
        await asyncio.sleep(.35)
        self.assertEqual(len(gets), 257)
        self.assertFalse(self.task.done())

    async def test_reconnect_reapplies_files_after_service_restart(self):
        await self.setup_server()
        self.edit(85.)
        self.task = asyncio.create_task(self.sync.run())
        await self.until(lambda: self.remote() == 85.)
        old_port = self.port
        await self.server.close()
        self.edit(99.)
        self.server = self.server_type(self.plan)
        self.port = await self.server.start(port=old_port)
        await self.until(lambda: self.remote() == 99.)
        self.assertTrue(any('disconnected' in line for line in self.logs))

    async def test_overflow_scan_recovers_a_missed_file_event(self):
        from inotify_simple import Event, flags
        await self.setup_server()
        self.task = asyncio.create_task(self.sync.session())
        await self.until(lambda: any('watching ' in line for line in self.logs))
        await asyncio.sleep(.1)
        watcher = self.sync.watcher
        watcher.loop.remove_reader(watcher.io.fileno())
        self.edit(135.)  # Simulate an event that could not be delivered.
        with patch.object(watcher.io, 'read', return_value=[Event(-1, flags.Q_OVERFLOW, 0, '')]):
            watcher.read()
        await self.until(lambda: self.remote() == 135.)

    async def test_cli_initial_app_and_sigterm(self):
        await self.setup_server()
        self.edit(5.)
        environment = dict(os.environ, OSE_PARAMETER_TOKEN=TOKEN,
                           PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
        process = await asyncio.create_subprocess_exec(os.sys.executable, '-m', 'ose.cli', 'parameters', 'sync',
            '--dir', str(self.directory), '--client', 'operator', '--initial', 'app',
            '--host', '127.0.0.1', '--port', str(self.port), '--sdl-root', str(SDL_ROOT),
            env=environment, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            await self.until(lambda: self.local() == 80.)
            process.send_signal(signal.SIGTERM)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 5)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(stdout, b'')
        finally:
            if process.returncode is None:
                process.kill()
                await process.communicate()

    @unittest.skipUnless(shutil.which('bash') and (Path(__file__).resolve().parents[3] / 'setenv.sh').is_file(),
                         'requires Bash and the framework checkout')
    async def test_shell_sync_dot_uses_caller_directory_not_application(self):
        await self.setup_server()
        framework = Path(__file__).resolve().parents[3]
        caller = self.root / 'empty directory'
        caller.mkdir()
        (self.root / 'model.json').write_text('{"application": true}')
        build = self.root / 'build'
        build.mkdir()
        (build / 'plan.json').write_text(json.dumps(self.plan))
        (build / 'endpoint.json').write_text(json.dumps({'host': '127.0.0.1', 'port': self.port}))
        env = dict(os.environ, FRAMEWORK_ENV=str(framework / 'setenv.sh'),
                   OSE_APP_ROOT=str(self.root), OSE_PYTHON=os.sys.executable, OSE_PARAMETER_CLIENT='operator')
        env.pop('OSE_PARAMETER_TOKEN', None)
        script = ('set -eu\nshopt -s expand_aliases\nsource "$FRAMEWORK_ENV"\n'
                  'params-sync --dir .\n')
        process = await asyncio.create_subprocess_exec('bash', '--noprofile', '--norc', '-c', script,
            cwd=caller, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            start_new_session=True)
        try:
            await self.until(lambda: len(list(caller.rglob('*.json'))) == 2)
            self.assertEqual({file.relative_to(caller).as_posix() for file in caller.rglob('*.json')},
                             {path + '.json' for path in self.plan['shared_parameters']})
            self.assertFalse((self.root / 'machines').exists())
            self.assertEqual(json.loads((self.root / 'model.json').read_text()), {'application': True})
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
            _, stderr = await asyncio.wait_for(process.communicate(), 5)
        self.assertNotIn('unknown parameter file', stderr.decode())
