"""Typed parameter blocks across real generated component processes."""
import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from ose.codec.sdl2 import SDL2
from ose.modeling.compiler import build
from ose.execution.process import start_component, tick_events, stop_component
from ose.parameters.service import ParameterService
from ose.parameters.bindings import LocalBindings
from ose.parameters.client import ParameterClient
from ose.execution.broker import Broker
from ose.execution.adapter import Adapter
from ose.execution.distributed import Coordinator, Worker
from ose.execution.simulation.coordinator import Simulation

SDL_ROOT = os.environ.get('SDL_ROOT', str(Path(__file__).resolve().parents[3] / 'sdl'))

PYTHON = '''from settings import Settings
def on_start(ctx):
    p = ctx.parameters_detection()
    assert p.threshold == 80.0
    p.threshold = 81.0
    ctx.set_parameters_detection(p)
def on_tick(ctx):
    p = ctx.parameters_detection()
    ctx.publish_result(p)
    p.threshold += 1.0
    ctx.set_parameters_detection(p)
'''
C = '''#include "component.h"
static int on_start(ChainContext *ctx) {
    Settings p = *parameters_detection(ctx);
    if (p.threshold != 80.0) return 0;
    p.threshold = 81.0;
    return set_parameters_detection(ctx, &p);
}
static int on_tick(ChainContext *ctx) {
    Settings p = *parameters_detection(ctx);
    if (!publish_result(ctx, &p)) return 0;
    p.threshold += 1.0;
    return set_parameters_detection(ctx, &p);
}
const ComponentCallbacks component_callbacks = { .on_start=on_start, .on_tick=on_tick };
'''
RUST = '''use crate::container::{Callbacks, Context, Result};
#[derive(Default)] pub struct Handler;
impl Callbacks for Handler {
    fn on_start(&mut self, ctx: &mut Context) -> Result {
        let mut p = ctx.parameters_detection().clone();
        assert_eq!(p.threshold, 80.0);
        p.threshold = 81.0;
        ctx.set_parameters_detection(&p)
    }
    fn on_tick(&mut self, ctx: &mut Context) -> Result {
        let mut p = ctx.parameters_detection().clone();
        ctx.publish_result(&p)?;
        p.threshold += 1.0;
        ctx.set_parameters_detection(&p)
    }
}
'''
JAVA = '''import org.sdlchain.generated.probe.API;
import sdl.globaltypes.settings.Settings;
public class ParameterHandler extends API.Handler {
    public void onStart(API.Context ctx) throws Exception {
        Settings p = ctx.parameters_detection();
        if (p.threshold != 80.0) throw new Exception("wrong initial parameter");
        p.threshold = 81.0;
        ctx.set_parameters_detection(p);
    }
    public void onTick(API.Context ctx, API.Inputs inputs) throws Exception {
        Settings p = ctx.parameters_detection();
        ctx.publish_result(p);
        p.threshold += 1.0;
        ctx.set_parameters_detection(p);
    }
}
'''
ADA_SPEC = '''with Chain_probe;
package Parameter_Handler is
    type Object is new Chain_probe.Handler with null record;
    overriding procedure On_Start (Self : in out Object; Ctx : in out Chain_probe.Context);
    overriding procedure On_Tick (Self : in out Object; Ctx : in out Chain_probe.Context; Selected : Chain_probe.Inputs);
end Parameter_Handler;
'''
ADA = '''with SDL_settings; with SDL_Runtime;
package body Parameter_Handler is
    use type SDL_Runtime.Float64;
    overriding procedure On_Start (Self : in out Object; Ctx : in out Chain_probe.Context) is
        P : SDL_settings.T_Settings := Chain_probe.Parameters_detection (Ctx);
    begin
        if P.F_threshold /= 80.0 then raise Program_Error; end if;
        P.F_threshold := 81.0;
        Chain_probe.Set_Parameters_detection (Ctx, P);
    end;
    overriding procedure On_Tick (Self : in out Object; Ctx : in out Chain_probe.Context; Selected : Chain_probe.Inputs) is
        P : SDL_settings.T_Settings := Chain_probe.Parameters_detection (Ctx);
    begin
        Chain_probe.Publish_result (Ctx, P);
        P.F_threshold := P.F_threshold + 1.0;
        Chain_probe.Set_Parameters_detection (Ctx, P);
    end;
end Parameter_Handler;
'''


class ComponentParameterTests(unittest.IsolatedAsyncioTestCase):
    async def check_language(self, language, source, filename, identity=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'settings.sdl').write_text('message Settings { fl64 threshold; }')
            (root / filename).write_text(source)
            if language == 'ada':
                (root / 'parameter_handler.ads').write_text(ADA_SPEC)
            node = dict(inputs={}, outputs={'result': {'root': 'Settings'}}, trigger=dict(mode='periodic', period_ms=100),
                        parameter_bindings={'detection': {'path': 'machine/detection', 'access': 'read_write'}})
            model = dict(version=1, schemas='settings.sdl', components={'probe': node}, subscriptions=[],
                         shared_parameters={'machine/detection': dict(root='Settings', initial={'threshold': 80.})})
            container = dict(language=language, handler=filename, **(identity or {}))
            deployment = dict(version=1, components={'probe': {'container': container}})
            (root / 'model.json').write_text(json.dumps(model))
            (root / 'deployment.json').write_text(json.dumps(deployment))
            path = await asyncio.to_thread(build, root / 'model.json', root / 'deployment.json', SDL_ROOT, root / 'build')
            plan = json.loads(path.read_text())
            codec = SDL2(SDL_ROOT)
            service = ParameterService(plan, codec)
            node = plan['components']['probe']
            bindings = LocalBindings(node, service)
            process, capable = await start_component(node, bindings)
            try:
                self.assertTrue(capable)
                observed = []
                for index in (1, 2):
                    async for port, payload in tick_events(process, node, {}, 0, index, bindings):
                        self.assertEqual(port, 'result')
                        value = codec.api.decode_dynamic(codec.api.prepare(node['outputs']['result']['catalogue']), payload)
                        observed.append(value.fields['threshold'])
                self.assertEqual(observed, [81., 82.])
            finally:
                await stop_component(process)
                bindings.close()

    async def test_python(self):
        await self.check_language('python', PYTHON, 'handler.py')

    async def test_c(self):
        await self.check_language('c', C, 'handler.c')

    async def test_rust(self):
        await self.check_language('rust', RUST, 'handler.rs')

    async def test_java(self):
        if 'java' not in os.environ.get('TEST_LANGUAGES', 'python,c,rust,java').split(','):
            self.skipTest('Java not selected in TEST_LANGUAGES')
        await self.check_language('java', JAVA, 'ParameterHandler.java', {'class': 'ParameterHandler'})

    async def test_ada(self):
        if 'ada' not in os.environ.get('TEST_LANGUAGES', 'python,c,rust,java').split(','):
            self.skipTest('Ada not selected in TEST_LANGUAGES')
        await self.check_language('ada', ADA, 'parameter_handler.adb', {'unit': 'Parameter_Handler'})


class NetworkParameterTests(unittest.IsolatedAsyncioTestCase):
    async def setup_plan(self, distributed=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'settings.sdl').write_text('message Settings { fl64 threshold; }')
        (self.root / 'reader.py').write_text('''import json
def on_start(ctx):
    assert ctx.parameters_detection().threshold in (80., 10., 85.)
def on_tick(ctx):
    with open(ctx.parameters['path'], 'a') as stream:
        stream.write(str(ctx.parameters_detection().threshold) + '\\n')
''')
        nodes = {name: dict(inputs={}, outputs={}, trigger=dict(mode='periodic', period_ms=20),
                    parameters={'path': str(self.root / (name + '.values'))},
                    parameter_bindings={'detection': {'path': path, 'access': 'read'}})
                 for name, path in (('first', 'machine/first'), ('second', 'machine/second'))}
        model = dict(version=1, schemas='settings.sdl', components=nodes, subscriptions=[],
                     shared_parameters={path: dict(root='Settings', initial={'threshold': value})
                         for path, value in (('machine/first', 80.), ('machine/second', 10.))})
        deployment = dict(version=1, execution='distributed' if distributed else 'local',
                          parameter_clients={'operator': {'read': list(model['shared_parameters']),
                                             'write': ['machine/first'], 'token': 'test-only-parameter-token'}},
                          components={name: {'container': {'language': 'python', 'handler': 'reader.py'}} for name in nodes})
        (self.root / 'model.json').write_text(json.dumps(model))
        (self.root / 'deployment.json').write_text(json.dumps(deployment))
        path = await asyncio.to_thread(build, self.root / 'model.json', self.root / 'deployment.json', SDL_ROOT, self.root / 'build')
        self.plan = json.loads(path.read_text())
        self.codec = SDL2(SDL_ROOT)

    async def until(self, predicate):
        async def wait():
            while not predicate():
                for task in getattr(self, 'tasks', []):
                    if task.done():
                        task.result()
                await asyncio.sleep(.02)
        await asyncio.wait_for(wait(), 30)

    def values(self, name):
        path = self.root / (name + '.values')
        return [float(line) for line in path.read_text().splitlines()] if path.exists() else []

    async def exercise(self, distributed):
        await self.setup_plan(distributed)
        server = Coordinator(self.plan, lease_seconds=30) if distributed else Broker(self.plan)
        port = await server.start()
        self.tasks = []
        clients = []
        try:
            for name in self.plan['components']:
                component = Worker(self.plan, name, '127.0.0.1', port) if distributed else Adapter(self.plan, name)
                self.tasks.append(asyncio.create_task(component.run() if distributed else component.run('127.0.0.1', port)))
            if not distributed:
                await self.until(lambda: server.ready == set(self.plan['components']))
                server.enabled = True
            await self.until(lambda: self.values('first') and self.values('second'))
            client = await ParameterClient.connect('127.0.0.1', port, 'operator', token='test-only-parameter-token',
                                                   distributed=distributed, runtime=self.codec)
            clients.append(client)
            block = await client.block('machine/first')
            await client.subscribe(['machine/first'])
            self.assertEqual((await client.next())[1], {'threshold': 80.})
            await block.set({'threshold': 85.})
            self.assertEqual((await client.next())[1], {'threshold': 85.})
            await self.until(lambda: 85. in self.values('first'))
            self.assertEqual(set(self.values('second')), {10.})
            with self.assertRaises(ValueError):
                await (await client.block('machine/second')).set({'threshold': 100.})
            await client.close()
            clients.remove(client)
            client = await ParameterClient.connect('127.0.0.1', port, 'operator', token='test-only-parameter-token',
                                                   distributed=distributed, runtime=self.codec)
            clients.append(client)
            self.assertEqual(await (await client.block('machine/first')).get(), {'threshold': 85.})
        finally:
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for client in clients:
                await client.close()
            await server.close()

    async def test_local_targeted_updates_and_external_client(self):
        await self.exercise(False)

    async def test_distributed_targeted_updates_and_external_client(self):
        await self.exercise(True)

    async def test_python_external_subscription_accepts_iterators(self):
        await self.setup_plan()
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            async with await ParameterClient.connect('127.0.0.1', port, 'operator',
                    token='test-only-parameter-token', runtime=self.codec) as client:
                await client.subscribe(iter(['machine/first', 'machine/second']))
                updates = dict([await client.next(), await client.next()])
                self.assertEqual(updates, {'machine/first': {'threshold': 80.},
                                           'machine/second': {'threshold': 10.}})
        finally:
            await broker.close()

    async def parameter_cli(self, port, action, *args, data=None, success=True):
        env = dict(os.environ, OSE_PARAMETER_TOKEN='test-only-parameter-token',
                   PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
        process = await asyncio.create_subprocess_exec(os.sys.executable, '-m', 'ose.cli', 'parameters', action, *args,
            '--client', 'operator', '--host', '127.0.0.1', '--port', str(port), '--sdl-root', str(self.codec.root),
            env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(data), 15)
        if not success:
            self.assertNotEqual(process.returncode, 0)
            return stderr.decode()
        self.assertEqual(process.returncode, 0, stderr.decode())
        return json.loads(stdout)

    async def test_cli_list_get_set_watch_and_invalid_credentials(self):
        await self.setup_plan()
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            with self.assertRaises(ValueError):
                await ParameterClient.connect('127.0.0.1', port, 'operator', token='wrong-token', runtime=self.codec)
            async def call(action, *args, data=None, success=True):
                return await self.parameter_cli(port, action, *args, data=data, success=success)
            self.assertEqual(await call('list'), ['machine/first', 'machine/second'])
            self.assertEqual(await call('get', 'machine/first'), {'threshold': 80.})
            source = self.root / 'new-parameters.json'
            source.write_text('{"threshold": 85.0}')
            self.assertEqual(await call('set', 'machine/first', '--file', str(source)), {'updated': 'machine/first'})
            self.assertEqual(await call('watch', 'machine/first', '--count', '1'),
                             {'path': 'machine/first', 'value': {'threshold': 85.}})
            self.assertEqual(await call('set', 'machine/first', '--file', '-', data=b'{"threshold": 90.0}'),
                             {'updated': 'machine/first'})
            self.assertEqual(await call('get', 'machine/first'), {'threshold': 90.})
            for data, error in ((b'', 'Expecting value'),
                                (b'{"threshold": 1, "threshold": 2}', 'duplicate key'),
                                (b' ' * (1024 * 1024 + 1), 'exceeds 1 MiB'),
                                (b'{"extra": 1}', 'unknown parameter fields')):
                with self.subTest(error=error):
                    self.assertIn(error, await call('set', 'machine/first', '--file', '-', data=data, success=False))
                    self.assertEqual(await call('get', 'machine/first'), {'threshold': 90.})
        finally:
            await broker.close()

    async def test_cli_exports_exact_and_regex_paths_and_reimports_them(self):
        await self.setup_plan()
        self.plan['shared_parameters']['machine/first_backup'] = dict(self.plan['shared_parameters']['machine/first'])
        self.plan['parameter_clients']['operator']['read'].append('machine/first_backup')
        self.plan['parameter_clients']['operator']['write'].extend(['machine/second', 'machine/first_backup'])
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            async def call(action, *args, data=None, success=True):
                return await self.parameter_cli(port, action, *args, data=data, success=success)
            expected = {'machine/first': {'threshold': 80.}, 'machine/first_backup': {'threshold': 80.},
                        'machine/second': {'threshold': 10.}}
            self.assertEqual(await call('get', 'machine/first', '--export', '--exact'),
                             {'machine/first': expected['machine/first']})
            self.assertEqual(await call('get', 'machine/first', '--export'),
                             {path: value for path, value in expected.items() if 'first' in path})
            self.assertEqual(await call('get', '--export'), expected)
            self.assertEqual(await call('get'), expected)
            exported = await call('get', '--export', '^machine/')
            self.assertEqual(exported, expected)
            self.assertEqual(await call('get', '--export', '/first$'), {'machine/first': expected['machine/first']})
            self.assertEqual(await call('get', '--export', 'no-match'), {})
            self.assertIn('get --exact requires PATH', await call('get', '--exact', success=False))
            self.assertIn('invalid parameter path regex', await call('get', '--export', '[', success=False))
            await call('set', 'machine/first', '--file', '-', data=b'{"threshold": 99.0}')
            self.assertEqual(await call('set', '--file', '-', data=json.dumps(exported).encode()),
                             {'updated': ['machine/first', 'machine/first_backup', 'machine/second']})
            self.assertEqual(await call('get', '--export', '.*'), expected)
            source = self.root / 'export.json'
            source.write_text(json.dumps({'machine/first': {'threshold': 88.}}))
            self.assertEqual(await call('set', '--file', source), {'updated': ['machine/first']})
            self.assertEqual(await call('get', 'machine/first'), {'threshold': 88.})
            self.assertEqual(await call('set', '--file', '-', data=b'{}'), {'updated': []})
            for data in (b'[]', b'{"machine/first": 8}', b'{"": {}}'):
                self.assertIn('parameter import requires', await call('set', '--file', '-', data=data, success=False))
        finally:
            await broker.close()

    async def test_cli_import_prevalidates_and_reports_partial_service_failure(self):
        await self.setup_plan()
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            async def call(action, *args, data=None, success=True):
                return await self.parameter_cli(port, action, *args, data=data, success=success)
            invalid = {'machine/first': {'threshold': 95.}, 'machine/second': {'extra': 1}}
            self.assertIn('unknown parameter fields', await call('set', '--file', '-',
                data=json.dumps(invalid).encode(), success=False))
            self.assertEqual(await call('get', 'machine/first'), {'threshold': 80.})
            valid = {'machine/first': {'threshold': 95.}, 'machine/second': {'threshold': 20.}}
            error = await call('set', '--file', '-', data=json.dumps(valid).encode(), success=False)
            self.assertIn('parameter write access denied', error)
            self.assertIn('updated blocks: ["machine/first"]', error)
            self.assertEqual(await call('get', 'machine/first'), {'threshold': 95.})
            self.assertEqual(await call('get', 'machine/second'), {'threshold': 10.})
        finally:
            await broker.close()

    async def test_cli_directory_imports_nested_json_in_file_order(self):
        await self.setup_plan()
        self.plan['parameter_clients']['operator']['write'].append('machine/second')
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            directory = self.root / 'parameter exports'
            nested = directory / 'a' / 'deep'
            nested.mkdir(parents=True)
            (directory / 'z.json').write_text('{"machine/first": {"threshold": 91.0}}')
            (nested / 'values.json').write_text('{"machine/second": {"threshold": 22.0}}')
            (directory / 'ignored.txt').write_text('not JSON')
            (directory / 'ignored.JSON').write_text('not JSON')
            self.assertEqual(await self.parameter_cli(port, 'set', '--dir', directory),
                             {'updated': ['machine/second', 'machine/first']})
            expected = {'machine/first': {'threshold': 91.}, 'machine/second': {'threshold': 22.}}
            self.assertEqual(await self.parameter_cli(port, 'get'), expected)
            empty = self.root / 'empty'
            empty.mkdir()
            for args, error in ((('--dir', directory, '--file', directory / 'z.json'), 'not allowed with argument'),
                                (('machine/first', '--dir', directory), '--dir cannot be combined'),
                                (('--dir', directory, '--exact'), 'unrecognized arguments: --exact'),
                                (('--dir', directory / 'z.json'), 'not a directory'),
                                (('--dir', self.root / 'missing'), 'not a directory'),
                                (('--dir', empty), 'no *.json files found')):
                with self.subTest(args=args):
                    self.assertIn(error, await self.parameter_cli(port, 'set', *args, success=False))
            self.assertEqual(await self.parameter_cli(port, 'get'), expected)
        finally:
            await broker.close()

    async def test_cli_directory_validates_all_files_before_writing(self):
        await self.setup_plan()
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            directory = self.root / 'exports'
            nested = directory / 'nested'
            nested.mkdir(parents=True)
            first, last = directory / 'a.json', nested / 'z.json'
            first.write_text('{"machine/first": {"threshold": 95.0}}')
            for document, error in (('{"machine/first": {"threshold": 95.0}}', 'duplicate parameter path machine/first'),
                                    ('{broken', 'Expecting property name'),
                                    ('{"machine/second": {"extra": 1}}', 'unknown parameter fields'),
                                    ('{"threshold": 20.0}', 'parameter import requires'),
                                    ('{"machine/second": {}, "machine/second": {}}', 'duplicate key'),
                                    ('{"unknown/path": {"threshold": 20.0}}', 'parameter read access denied')):
                last.write_text(document)
                with self.subTest(error=error):
                    reported = await self.parameter_cli(port, 'set', '--dir', directory, success=False)
                    self.assertIn(error, reported)
                    if error.startswith('duplicate parameter path'):
                        self.assertIn(str(first), reported)
                        self.assertIn(str(last), reported)
                    self.assertEqual(await self.parameter_cli(port, 'get', 'machine/first'), {'threshold': 80.})
            last.write_text('{}')
            # Hidden *.json files are included too.
            hidden = directory / '.hidden.json'
            hidden.write_text('not JSON')
            self.assertIn(str(hidden), await self.parameter_cli(port, 'set', '--dir', directory, success=False))
            hidden.write_text('{}')
            first.write_text(' ' * (600 * 1024) + '{"machine/first": {"threshold": 95.0}}')
            last.write_text(' ' * (600 * 1024) + '{}')
            self.assertIn('directory JSON exceeds 1 MiB in total',
                          await self.parameter_cli(port, 'set', '--dir', directory, success=False))
            self.assertEqual(await self.parameter_cli(port, 'get', 'machine/first'), {'threshold': 80.})
        finally:
            await broker.close()

    @unittest.skipUnless(shutil.which('bash') and (Path(__file__).resolve().parents[3] / 'setenv.sh').is_file(),
                         'requires Bash and the framework checkout')
    async def test_shell_set_reads_stdin_after_automatic_token_loading(self):
        await self.setup_plan()
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            env = dict(os.environ, OSE_APP_ROOT=str(self.root), OSE_PYTHON=os.sys.executable,
                       FRAMEWORK_ENV=str(Path(__file__).resolve().parents[3] / 'setenv.sh'))
            env.pop('OSE_PARAMETER_TOKEN', None)
            env.pop('OSE_PARAMETER_CLIENT', None)
            script = ('set -eu\nshopt -s expand_aliases\nsource "$FRAMEWORK_ENV"\n'
                      'params-set machine/first --file - --host 127.0.0.1 --port "$1"\n')
            process = await asyncio.create_subprocess_exec('bash', '--noprofile', '--norc', '-c', script, 'test', str(port),
                env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(b'{"threshold": 92.0}'), 15)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertEqual(json.loads(stdout), {'updated': 'machine/first'})
            script = ('set -eu\nset -o pipefail\nshopt -s expand_aliases\nsource "$FRAMEWORK_ENV"\n'
                      'params-get "/first$" --host 127.0.0.1 --port "$1" | '
                      'params-set --file - --host 127.0.0.1 --port "$1"\n'
                      'params-get --exact machine/first --host 127.0.0.1 --port "$1"\n'
                      'params-get --host 127.0.0.1 --port "$1"\n'
                      'params-get-all --host 127.0.0.1 --port "$1"\n')
            process = await asyncio.create_subprocess_exec('bash', '--noprofile', '--norc', '-c', script, 'test', str(port),
                env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
            self.assertEqual(process.returncode, 0, stderr.decode())
            imported, exact, exported, all_alias = [json.loads(line) for line in stdout.splitlines()]
            self.assertEqual(imported, {'updated': ['machine/first']})
            self.assertEqual(exact, {'machine/first': {'threshold': 92.}})
            self.assertEqual(exported, {'machine/first': {'threshold': 92.}, 'machine/second': {'threshold': 10.}})
            self.assertEqual(all_alias, exported)
            async with await ParameterClient.connect('127.0.0.1', port, 'operator',
                    token='test-only-parameter-token', runtime=self.codec) as client:
                self.assertEqual(await (await client.block('machine/first')).get(), {'threshold': 92.})
        finally:
            await broker.close()

    async def native_probe(self):
        repository = Path(__file__).resolve().parents[2]
        target = repository / 'build/client-rust'
        await asyncio.to_thread(subprocess.run, ['cargo', 'build', '--offline', '--manifest-path', str(repository / 'sdk/client/rust/Cargo.toml')],
                                env=dict(os.environ, CARGO_TARGET_DIR=str(target)), check=True, capture_output=True)
        source = self.root / 'native_parameters.c'
        source.write_text('''#include "chain_client.h"
#include "chain_parameters.h"
#include "settings.h"
#include "type_engine.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    CenvParameterClient *client;
    CenvParameterBlock *block;
    CenvParameterPacket *packet;
    Settings *value;
    if (!register_settings_types()) return 2;
    client = cenv_parameters_connect(argv[1], (uint16_t)atoi(argv[2]), "operator", argv[3],
        argc == 7 ? argv[4] : NULL, argc == 7 ? argv[5] : NULL, argc == 7 ? argv[6] : NULL);
    if (!client) goto error;
    block = cenv_parameter_block(client, "machine/first", "Settings");
    if (!block) goto error;
    value = cenv_parameter_get(block);
    if (!value || value->threshold != 80.0) goto error;
    value->threshold = 85.0;
    if (!cenv_parameter_set(block, value)) goto error;
    type_free(value);
    packet = cenv_parameters_request(client, "parameters_subscribe", "{\\"paths\\":[\\"machine/first\\"]}", NULL, 0);
    if (!packet) goto error;
    cenv_parameter_packet_free(packet);
    packet = cenv_parameters_request(client, "parameters_next", "{}", NULL, 0);
    if (!packet) goto error;
    value = cenv_parameter_decode(block, packet);
    if (!value || value->threshold != 85.0) goto error;
    type_free(value); cenv_parameter_packet_free(packet);
    cenv_parameter_block_free(block); cenv_parameters_free(client);
    puts("parameters passed"); return 0;
error:
    fprintf(stderr, "%s\\n", cenv_last_error()); return 1;
}
''')
        runtime = self.codec.root / 'runtime/c'
        generated = self.root / 'build/generated/shared/c'
        binary = self.root / 'native_parameters'
        command = [os.environ.get('CC', 'cc'), '-std=c99', '-Wall', '-Wextra', '-Werror', '-O1',
                   '-I' + str(runtime), '-I' + str(generated), '-I' + str(repository / 'sdk/client/c'), str(source),
                   *map(str, sorted(runtime.glob('*.c'))), *map(str, sorted(generated.glob('*.c'))),
                   str(repository / 'sdk/client/c/chain_client.c'), str(target / 'debug/libsdl_chain_client.a'),
                   '-ldl', '-lm', '-lpthread', '-o', str(binary)]
        await asyncio.to_thread(subprocess.run, command, check=True, capture_output=True)
        return binary

    async def run_probe(self, arguments, success=True):
        process = await asyncio.create_subprocess_exec(*map(str, arguments), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
        if success:
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertIn(b'parameters passed', stdout)
        else:
            self.assertNotEqual(process.returncode, 0)

    async def test_c_external_parameters(self):
        await self.setup_plan()
        binary = await self.native_probe()
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            await self.run_probe([binary, '127.0.0.1', port, 'test-only-parameter-token'])
        finally:
            await broker.close()

    async def test_rust_external_parameters(self):
        await self.setup_plan()
        repository = Path(__file__).resolve().parents[2]
        source = self.root / 'rust-client'
        source.mkdir()
        (source / 'Cargo.toml').write_text('[package]\nname="parameter-probe"\nversion="0.1.0"\nedition="2021"\n[workspace]\n' +
            '[[bin]]\nname="parameter-probe"\npath="main.rs"\n[dependencies]\nsdl-chain-client={path=' +
            json.dumps(str(repository / 'sdk/client/rust')) + '}\nsdl-runtime={path=' + json.dumps(str(self.codec.root / 'runtime/rust')) + '}\n')
        (source / 'main.rs').write_text('#[path=' + json.dumps(str(self.root / 'build/generated/shared/rust/settings.rs')) + '] mod settings;\n' + '''
use sdl_chain_client::parameters::ParameterClient;
use sdl_runtime::SdlMessage;
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    let client = ParameterClient::connect(&args[1], args[2].parse()?, "operator", &args[3])?;
    let (root, catalogue) = client.describe("machine/first")?;
    assert_eq!(root, "Settings");
    let codec = sdl_runtime::prepare(&catalogue, &[settings::Settings::type_info()]).map_err(|e| format!("{e:?}"))?;
    let mut value: settings::Settings = sdl_runtime::decode(&codec, &client.get("machine/first")?).map_err(|e| format!("{e:?}"))?;
    assert_eq!(value.threshold, 80.0);
    value.threshold = 85.0;
    client.set("machine/first", &sdl_runtime::encode(&codec, &value).map_err(|e| format!("{e:?}"))?)?;
    client.subscribe(&["machine/first"])?;
    let (path, payload) = client.next()?.expect("initial parameter");
    assert_eq!(path, "machine/first");
    let value: settings::Settings = sdl_runtime::decode(&codec, &payload).map_err(|e| format!("{e:?}"))?;
    assert_eq!(value.threshold, 85.0);
    client.unsubscribe()?;
    println!("parameters passed"); Ok(())
}
''')
        target = repository / 'build/parameter-probe-rust'
        await asyncio.to_thread(subprocess.run, ['cargo', 'build', '--offline', '--manifest-path', str(source / 'Cargo.toml')],
                                env=dict(os.environ, CARGO_TARGET_DIR=str(target)), check=True, capture_output=True)
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            await self.run_probe([target / 'debug/parameter-probe', '127.0.0.1', port, 'test-only-parameter-token'])
        finally:
            await broker.close()

    async def test_java_external_parameters(self):
        if 'java' not in os.environ.get('TEST_LANGUAGES', 'python,c,rust,java').split(','):
            self.skipTest('Java not selected in TEST_LANGUAGES')
        from ose.modeling.client_build import build_java
        from ose.modeling.managed_containers import tool
        await self.setup_plan()
        source = self.root / 'ParameterProbe.java'
        source.write_text('''import org.sdlchain.client.ParameterClient;
import sdl.globaltypes.settings.Settings;
public class ParameterProbe {
    public static void main(String[] args) throws Exception {
        ParameterClient client = ParameterClient.connect(args[0], Integer.parseInt(args[1]), "operator", args[2]);
        try {
            ParameterClient.Block<Settings> block = client.block("machine/first", Settings.class);
            Settings p = block.get();
            if (p.threshold != 80.0) throw new Exception("wrong initial value");
            p.threshold = 85.0; block.set(p);
            client.subscribe("machine/first");
            if (block.decode(client.next()).threshold != 85.0) throw new Exception("wrong retained value");
            client.unsubscribe();
        } finally { client.close(); }
        System.out.println("parameters passed");
    }
}
''')
        await asyncio.to_thread(subprocess.run, [os.sys.executable, str(self.codec.root / 'sdlc/generator.py'),
            '-java', str(self.root / 'settings.sdl'), str(self.root / 'external-types')], check=True)
        classes, library = await asyncio.to_thread(build_java, self.codec.root, self.root / 'java-client',
            extra_sources=[self.root / 'external-types/java/settings.java', source])
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            await self.run_probe([*tool('JAVA', 'java'), '-Djava.library.path=' + str(library.parent), '-cp', classes,
                                  'ParameterProbe', '127.0.0.1', port, 'test-only-parameter-token'])
        finally:
            await broker.close()

    async def test_ada_external_parameters(self):
        if 'ada' not in os.environ.get('TEST_LANGUAGES', 'python,c,rust,java').split(','):
            self.skipTest('Ada not selected in TEST_LANGUAGES')
        from ose.modeling.ada_client_build import build_ada
        await self.setup_plan()
        await asyncio.to_thread(subprocess.run, [os.sys.executable, str(self.codec.root / 'sdlc/generator.py'),
            '-ada', str(self.root / 'settings.sdl'), str(self.root / 'external-types')], check=True)
        source = self.root / 'parameter_probe.adb'
        source.write_text('''with Ada.Command_Line; with Ada.Text_IO;
with Interfaces; with SDL_Runtime; with SDL_settings;
with Chain_Parameters; with Chain_Parameters.Typed;
procedure Parameter_Probe is
    use type SDL_Runtime.Float64;
    package Blocks is new Chain_Parameters.Typed
        (SDL_settings.T_Settings, "Settings", SDL_settings.Description, SDL_settings.Encode, SDL_settings.Decode);
    C : Chain_Parameters.Client;
    B : Blocks.Block;
    P : SDL_settings.T_Settings;
begin
    Chain_Parameters.Connect (C, Ada.Command_Line.Argument (1),
        Interfaces.Unsigned_16'Value (Ada.Command_Line.Argument (2)), "operator", Ada.Command_Line.Argument (3));
    Blocks.Open (B, C, "machine/first");
    P := Blocks.Get (B, C);
    if P.F_threshold /= 80.0 then raise Program_Error; end if;
    P.F_threshold := 85.0;
    Blocks.Set (B, C, P);
    Chain_Parameters.Subscribe (C, "[""machine/first""]");
    P := Blocks.Decode_Update (B, Chain_Parameters.Next (C));
    if P.F_threshold /= 85.0 then raise Program_Error; end if;
    Chain_Parameters.Unsubscribe (C);
    Chain_Parameters.Close (C);
    Ada.Text_IO.Put_Line ("parameters passed");
end Parameter_Probe;
''')
        binary = await asyncio.to_thread(build_ada, self.codec.root, self.root / 'ada-client', source,
                                        include_dirs=[self.root / 'external-types/ada'])
        broker = Broker(self.plan)
        port = await broker.start()
        try:
            await self.run_probe([binary, '127.0.0.1', port, 'test-only-parameter-token'])
        finally:
            await broker.close()

    async def test_native_tls_parameters_and_certificate_identity(self):
        from test_distributed import certificates, tls
        await self.setup_plan(True)
        binary = await self.native_probe()
        await asyncio.to_thread(certificates, self.root, ['coordinator', 'operator', 'impostor'])
        for identity, success in [('coordinator', True), ('impostor', False)]:
            server_context, _ = tls(self.root, identity)
            coordinator = Coordinator(self.plan, context=server_context)
            port = await coordinator.start()
            try:
                await self.run_probe([binary, '127.0.0.1', port, '', self.root / 'ca.crt', self.root / 'operator.crt', self.root / 'operator.key'], success)
            finally:
                await coordinator.close()

    async def test_simulated_parameter_writes_and_scenario_are_deterministic(self):
        await self.setup_plan()
        from copy import deepcopy
        writer = self.root / 'writer.py'
        writer.write_text('''def on_tick(ctx):
    p = ctx.parameters_detection()
    p.threshold += 1
    ctx.set_parameters_detection(p)
''')
        model = json.loads((self.root / 'model.json').read_text())
        deployment = json.loads((self.root / 'deployment.json').read_text())
        model['components']['writer'] = deepcopy(model['components']['first'])
        model['components']['writer']['parameter_bindings']['detection']['access'] = 'read_write'
        deployment['components']['writer'] = {'container': {'language': 'python', 'handler': 'writer.py'}}
        (self.root / 'model.json').write_text(json.dumps(model))
        (self.root / 'deployment.json').write_text(json.dumps(deployment))
        path = await asyncio.to_thread(build, self.root / 'model.json', self.root / 'deployment.json', SDL_ROOT, self.root / 'build')
        plan = json.loads(path.read_text())
        scenario = self.root / 'scenario.json'
        scenario.write_text(json.dumps({'version': 1, 'events': [
            {'at': .03, 'parameter': 'machine/first', 'value': {'threshold': 200.}}]}))
        hashes, observations = [], []
        for workers in (1, 3):
            for name in ('first', 'second'):
                (self.root / (name + '.values')).write_text('')
            result = await Simulation(plan, .06, workers=workers, scenario=scenario).execute()
            hashes.append(result['trace_sha256'])
            observations.append(self.values('first'))
            self.assertEqual(self.values('second'), [10., 10., 10.])
            self.assertEqual(result['parameter_changes'], 4)
        self.assertEqual(hashes[0], hashes[1])
        self.assertEqual(observations, [[80., 81., 200.], [80., 81., 200.]])
