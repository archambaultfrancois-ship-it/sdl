import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from chain_env.codec.sdl2 import SDL2
from chain_env.execution.broker import Broker
from chain_env.modeling.client_build import build_java
from chain_env.modeling.managed_containers import tool

ROOT = Path(__file__).resolve().parents[2]
SDL = Path(os.environ.get('SDL_ROOT', ROOT.parent))


class JavaClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_client_tcp_udp_unicode_and_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = root / 'measurement.sdl'
            schema.write_text('message Measurement {1: required uint64 sequence; 2: required fl64 value;}')
            await asyncio.to_thread(SDL2(SDL).generate, schema, root / 'types', ['java'])
            sources = [ROOT / 'tests/managed/java/ExternalProbe.java', ROOT / 'examples/external/java/ClientExample.java', *[p for p in (root / 'types/java').rglob('*.java') if p.name != 'SdlCodec.java']]
            classes, library = await asyncio.to_thread(build_java, SDL, ROOT / 'build/client-java-test', sources)
            broker = Broker(dict(sdl_root=str(SDL), components={}, subscriptions=[], max_frame=1024*1024, max_datagram=1200))
            broker.sequence = 2**63
            await broker.start(); broker.enabled = True
            try:
                process = await asyncio.create_subprocess_exec(*tool('JAVA', 'java'), '-Xcheck:jni', '-Djava.library.path=' + str(library.parent), '-cp', str(classes), 'ExternalProbe', str(broker.port), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    out, err = await asyncio.wait_for(process.communicate(), 30)
                except BaseException:
                    process.kill(); await process.wait(); raise
                self.assertEqual(process.returncode, 0, err.decode() + out.decode())
                self.assertIn(b'java external OK', out)
                self.assertNotIn(b'WARNING', out + err)
            finally:
                await broker.close()
