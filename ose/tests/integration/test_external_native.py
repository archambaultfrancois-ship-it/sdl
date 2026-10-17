import asyncio
import os
import signal
from pathlib import Path
import subprocess
import threading
import unittest
import test_external_client as external
from ose.client import Latest

ROOT = Path(__file__).resolve().parents[2]

class NativeExternalTests(external.ExternalTests):
    # Use the same broker fixture; exercise native examples as separate executables.
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('SDL_ROOT'):
            raise unittest.SkipTest('set SDL_ROOT')
        subprocess.run(['make','external-native'],cwd=ROOT,check=True,stdout=subprocess.DEVNULL)

    async def exchange(self,language,transport,evolved=False,resume=False):
        c=await self.client('driver')
        message_type=self.Message
        if evolved:
            class Extended(self.codec.api.SdlMessage):
                _SDL_NAME='Measurement'
                _SDL_DESCRIPTOR='SDL2|Alpha{1:int32:x;}|Measurement{1:uint64:sequence;2:fl64:value;3:uint32:extra;}'
                _SDL_FIXED_SIZE=20
                _SDL_FIELDS=((1,'sequence','required','uint64',()),(2,'value','required','fl64',()),(3,'extra','required','uint32',()))
            message_type=Extended
        publisher=await asyncio.to_thread(c.publisher,'external.input',message_type)
        executable=ROOT/'build'/('external-c' if language=='c' else 'client-example/debug/chain-external-example')
        proc=await asyncio.create_subprocess_exec(str(executable),str(self.broker.port),transport,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        try:
            line=await asyncio.wait_for(proc.stdout.readline(),5)
            if line!=b'READY\n':
                _,error=await proc.communicate()
                self.fail(f'{language} startup {line!r}: {error.decode()}')
            if resume:
                # Linux may interrupt recv_from with a socket timeout after SIGCONT.
                proc.send_signal(signal.SIGSTOP)
                try:
                    await asyncio.sleep(.3)
                finally:
                    proc.send_signal(signal.SIGCONT)
                await asyncio.sleep(.15)
                self.assertIn('external-' + language, {s.client_id for s in self.broker.sessions.values()})
            event=threading.Event();received=[]
            def callback(value,info): received.append(value);event.set()
            await asyncio.to_thread(c.subscribe,f'{language}.result',self.Message,callback,qos=Latest())
            result=await asyncio.to_thread(publisher.publish,message_type(sequence=9,value=3.5,**({"extra":7} if evolved else {})))
            self.assertTrue(result.accepted)
            self.assertTrue(await asyncio.to_thread(event.wait,5),f'{language}/{transport} result')
            self.assertEqual((received[0].sequence,received[0].value),(9,7.))
            stdout,stderr=await asyncio.wait_for(proc.communicate(),5)
            self.assertEqual(proc.returncode,0,stderr.decode()+stdout.decode())
        finally:
            if proc.returncode is None:
                proc.kill();await proc.wait()

    async def test_native_c_tcp(self): await self.exchange('c','tcp')
    async def test_native_c_udp(self): await self.exchange('c','udp')
    async def test_native_rust_tcp(self): await self.exchange('rust','tcp')
    async def test_native_rust_udp(self): await self.exchange('rust','udp')
    async def test_c_evolved_catalogue(self): await self.exchange('c','tcp',True)
    async def test_rust_evolved_catalogue(self): await self.exchange('rust','udp',True)

    @unittest.skipUnless(hasattr(signal, 'SIGSTOP'), 'requires POSIX process suspension')
    async def test_c_session_survives_process_resume(self): await self.exchange('c','tcp',resume=True)

    @unittest.skipUnless(hasattr(signal, 'SIGSTOP'), 'requires POSIX process suspension')
    async def test_rust_session_survives_process_resume(self): await self.exchange('rust','udp',resume=True)

    # Shared fixture only; these behaviors are already tested by ExternalTests.
    test_external_model_binding_and_internal_topic_ownership=None
    test_errors_ownership_and_callback_failure=None
    test_slow_callback_does_not_block_control_or_other_subscription=None
    test_typed_fifo_udp_latest_and_cleanup=None

    test_each_schema_is_checked_and_publisher_reconnects=None

    test_fifo_has_no_replay_and_udp_retries_deduplicate=None

    test_concurrent_publication_ids_keep_their_sequence=None

    test_external_publisher_wait_includes_long_internal_admission=None
