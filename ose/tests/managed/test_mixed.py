"""Five-language graph uses the same coordinator and deterministic wave ordering."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from chain_env.modeling.compiler import build
from chain_env.execution.simulation import Simulation
from chain_env.execution.supervisor import run
from chain_env.transport.tcp import Client

ROOT = Path(__file__).resolve().parents[2]
SDL = Path(os.environ.get('SDL_ROOT', ROOT.parent / 'sdl'))


class MixedTests(unittest.IsolatedAsyncioTestCase):
    async def test_five_languages_reproducible_across_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = await asyncio.to_thread(build, ROOT / 'examples/multilingual/model.json', ROOT / 'examples/multilingual/deployment.json', SDL, root / 'build')
            original = json.loads(path.read_text()); traces = []
            for workers, slow in ((1, 'java_worker'), (2, 'ada_worker'), (8, 'java_worker')):
                plan = copy.deepcopy(original); plan['components'][slow]['env']['CHAIN_TEST_DELAY_MS'] = '15'
                trace = root / ('trace-' + str(workers) + '.jsonl')
                engine = Simulation(plan, .31, workers=workers, trace=trace)
                summary = await engine.execute(); traces.append(trace.read_bytes())
                self.assertTrue(all(p.returncode == 0 for p in engine.processes.values()))
            self.assertEqual(traces[0], traces[1]); self.assertEqual(traces[1], traces[2])
            # Exercise the same executables through TCP/UDP adapters and the readiness barrier.
            realtime = copy.deepcopy(original)
            for index, sub in enumerate(realtime['subscriptions']):
                if index % 2:
                    sub['policy'].update(transport='udp', ack='received', max_retries=3)
            path.write_text(json.dumps(realtime))
            supervisor = asyncio.create_task(run(path, duration=15))
            control = None
            try:
                deadline = asyncio.get_running_loop().time() + 20
                while True:
                    if supervisor.done():
                        await supervisor
                        self.fail('chain stopped before producing a joined value')
                    endpoint = root / 'build/endpoint.json'
                    if endpoint.exists():
                        address = json.loads(endpoint.read_text())
                        if control is None:
                            control = await Client.connect(address['host'], address['port'])
                        status, _ = await control.request('status')
                        if status['topics']['join.value']['publications'] > 0:
                            break
                    self.assertLess(asyncio.get_running_loop().time(), deadline, 'no real-time joined publication')
                    await asyncio.sleep(.05)
            finally:
                if control is not None:
                    await control.close()
                supervisor.cancel()
                try:
                    await supervisor
                except asyncio.CancelledError:
                    pass
            status = json.loads((root / 'build/last-status.json').read_text())
            self.assertGreater(status['topics']['join.value']['publications'], 0)
            self.assertEqual(status['errors'], [])
