import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from ose.codec.sdl2 import SDL2
from ose.execution.broker import Broker
from ose.modeling.ada_client_build import build_ada

ROOT = Path(__file__).resolve().parents[2]
SDL = Path(os.environ.get('SDL_ROOT', ROOT.parent / 'sdl'))


class AdaClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_client_tcp_udp_and_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); schema = root / 'measurement.sdl'
            schema.write_text('message Measurement {1: required uint64 sequence; 2: required fl64 value;}')
            await asyncio.to_thread(SDL2(SDL).generate, schema, root / 'types', ['ada'])
            binary = await asyncio.to_thread(build_ada, SDL, ROOT / 'build/client-ada-test', ROOT / 'tests/managed/ada/external_probe.adb', [root / 'types/ada'])
            await asyncio.to_thread(build_ada, SDL, ROOT / 'build/client-ada-test', ROOT / 'examples/external/ada/client_example.adb', [root / 'types/ada'])
            broker = Broker(dict(sdl_root=str(SDL), components={}, subscriptions=[], max_frame=1024*1024, max_datagram=1200))
            broker.sequence = 2**63
            await broker.start(); broker.enabled = True
            try:
                process = await asyncio.create_subprocess_exec(str(binary), str(broker.port), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try: out, err = await asyncio.wait_for(process.communicate(), 30)
                except BaseException:
                    process.kill(); await process.wait(); raise
                self.assertEqual(process.returncode, 0, err.decode() + out.decode()); self.assertIn(b'ada external OK', out)
            finally:
                await broker.close()
