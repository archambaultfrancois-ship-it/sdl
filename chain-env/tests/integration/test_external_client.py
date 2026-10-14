import asyncio
import os
from pathlib import Path
import struct
import json
import tempfile
import sys
import threading
import unittest
from chain_env.codec.sdl2 import SDL2
from chain_env.execution.broker import Broker
from chain_env.client import Client, Fifo, Latest, Retry, UnknownTopic, QoSError, PublicationRefused, CallbackFailed, SchemaError
from chain_env.delivery.policies import SubscriptionPolicy
from chain_env.modeling.compiler import build

SDL_ROOT = os.environ.get('SDL_ROOT')
CAT = 'SDL2|Measurement{1:uint64:sequence;2:fl64:value;}'

@unittest.skipUnless(SDL_ROOT, 'set SDL_ROOT')
class ExternalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.codec = SDL2(SDL_ROOT)
        # A public generated-style message; the client needs no checkout path.
        api = self.codec.api
        class Measurement(api.SdlMessage):
            _SDL_NAME = 'Measurement'
            _SDL_DESCRIPTOR = CAT
            _SDL_FIXED_SIZE = 16
            _SDL_FIELDS = ((1,'sequence','required','uint64',()),(2,'value','required','fl64',()))
        self.Message = Measurement
        self.broker = Broker(dict(sdl_root=SDL_ROOT, components={}, subscriptions=[], max_frame=1024*1024, max_datagram=1200))
        await self.broker.start()
        self.broker.enabled = True
        self.clients = []

    async def asyncTearDown(self):
        for client in self.clients:
            await asyncio.to_thread(client.close)
        await self.broker.close()

    async def client(self, name):
        c = await asyncio.to_thread(Client.connect, port=self.broker.port, client_id=name)
        self.clients.append(c)
        return c

    async def test_typed_fifo_udp_latest_and_cleanup(self):
        p = await self.client('publisher')
        publisher = await asyncio.to_thread(p.publisher, 'capteur.mesure', self.Message)
        for transport, qos in [('tcp',Fifo(ack='received',retry=Retry(ack_timeout_ms=20))),('udp',Latest(ack='received',retry=Retry(ack_timeout_ms=20)))]:
            c = await self.client(transport)
            received = []; event=threading.Event()
            def callback(value,info):
                received.append((value,info));event.set()
            sub = await asyncio.to_thread(c.subscribe,'capteur.mesure',self.Message,callback,qos=qos,transport=transport)
            result = await asyncio.to_thread(publisher.publish,self.Message(sequence=7,value=3.5))
            self.assertTrue(result.accepted)
            self.assertTrue(await asyncio.to_thread(event.wait,2))
            self.assertEqual(received[-1][0].value,3.5)
            self.assertEqual(received[-1][1].publication_id,result.publication_id)
            await asyncio.to_thread(sub.close)
        c = await self.client('late')
        event=threading.Event();values=[]
        def callback(value,info): values.append(value.sequence);event.set()
        sub=await asyncio.to_thread(c.subscribe,'capteur.mesure',self.Message,callback,qos=Latest(),transport='udp')
        self.assertTrue(await asyncio.to_thread(event.wait,2))
        self.assertEqual(values,[7])
        topics=await asyncio.to_thread(c.list_topics)
        self.assertEqual(topics[0]['topic'],'capteur.mesure')
        self.assertEqual(self.broker.ready,set())
        await asyncio.to_thread(c.close)
        await asyncio.sleep(.1)
        self.assertEqual(len(self.broker.external_tasks),0)
        self.assertEqual(len(self.broker.engines),0)

    async def test_errors_ownership_and_callback_failure(self):
        c=await self.client('one')
        with self.assertRaises(UnknownTopic):
            await asyncio.to_thread(c.subscribe,'missing',self.Message,lambda v,i:None)
        pub=await asyncio.to_thread(c.publisher,'topic',self.Message)
        other=await self.client('two')
        with self.assertRaises(PublicationRefused):
            await asyncio.to_thread(other.publisher,'topic',self.Message)
        with self.assertRaises(QoSError):
            await asyncio.to_thread(other.subscribe,'topic',self.Message,lambda v,i:None,qos=Fifo(capacity=999999))
        def fail(v,i): raise RuntimeError('user failed')
        await asyncio.to_thread(other.subscribe,'topic',self.Message,fail)
        await asyncio.to_thread(pub.publish,self.Message(sequence=1,value=1.))
        with self.assertRaisesRegex(CallbackFailed,'user failed'):
            await asyncio.wait_for(asyncio.to_thread(other.run),3)

    async def test_slow_callback_does_not_block_control_or_other_subscription(self):
        c=await self.client('producer');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        consumer=await self.client('consumer');started=threading.Event();release=threading.Event();fast=threading.Event()
        def slow(v,i): started.set();release.wait(3)
        try:
            await asyncio.to_thread(consumer.subscribe,'topic',self.Message,slow,qos=Fifo(capacity=1,overflow='wait',ack='received',retry=Retry()))
            await asyncio.to_thread(consumer.subscribe,'topic',self.Message,lambda v,i:fast.set())
            await asyncio.to_thread(p.publish,self.Message(sequence=1,value=1.))
            self.assertTrue(await asyncio.to_thread(started.wait,2))
            fast.clear()
            for n in range(2,6):
                await asyncio.to_thread(p.publish,self.Message(sequence=n,value=float(n)))
            self.assertTrue(await asyncio.to_thread(fast.wait,2))
            self.assertEqual(len(await asyncio.to_thread(consumer.list_topics)),1)
        finally: release.set()

    async def test_external_model_binding_and_internal_topic_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'schema.sdl').write_text('message Measurement {1: required uint64 sequence;2: required fl64 value;}')
            (root/'handler.py').write_text('from schema import Measurement\ndef on_input(ctx, msg):\n    ctx.publish_result(Measurement(sequence=msg.sequence,value=msg.value*3))\n')
            model=dict(version=1,schemas='schema.sdl',external_topics={'external.input':{'root':'Measurement'}},
                components={'transform':dict(inputs={'input':{'root':'Measurement'}},outputs={'result':{'root':'Measurement'}},trigger={'mode':'on_change'})},
                subscriptions=[{'id':'in','from_topic':'external.input','to':'transform.input'}])
            deployment=dict(version=1,components={'transform':{'container':{'language':'python','handler':'handler.py'}}})
            (root/'model.json').write_text(json.dumps(model));(root/'deployment.json').write_text(json.dumps(deployment))
            plan=await asyncio.to_thread(build,root/'model.json',root/'deployment.json',SDL_ROOT,root/'build')
            process=await asyncio.create_subprocess_exec(sys.executable,'-m','chain_env.cli','run','--plan',str(plan),'--duration','4',
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            try:
                line=await asyncio.wait_for(process.stdout.readline(),5)
                self.assertTrue(line,await process.stderr.read() if process.returncode is not None else 'no readiness')
                endpoint=json.loads(line)
                c=await asyncio.to_thread(Client.connect,port=endpoint['port'],client_id='injector')
                self.clients.append(c)
                pub=await asyncio.to_thread(c.publisher,'external.input',self.Message)
                with self.assertRaises(PublicationRefused):
                    await asyncio.to_thread(c.publisher,'transform.result',self.Message)
                event=threading.Event();received=[]
                def callback(value,info):received.append(value);event.set()
                await asyncio.to_thread(c.subscribe,'transform.result',self.Message,callback)
                await asyncio.to_thread(pub.publish,self.Message(sequence=4,value=2.))
                self.assertTrue(await asyncio.to_thread(event.wait,2))
                self.assertEqual(received[0].value,6.)
            finally:
                if process.returncode is None: process.terminate()
                communication=asyncio.create_task(process.communicate())
                try:
                    stdout,stderr=await asyncio.wait_for(asyncio.shield(communication),5)
                except asyncio.TimeoutError:
                    process.kill()
                    stdout,stderr=await communication
                    raise
                self.assertEqual(process.returncode,0,stderr.decode()+stdout.decode())

    async def test_each_schema_is_checked_and_publisher_reconnects(self):
        c=await self.client('contracts');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        await asyncio.to_thread(c.subscribe,'topic',self.Message,lambda v,i:None)
        api=self.codec.api
        class Extended(api.SdlMessage):
            _SDL_NAME='Measurement'
            _SDL_DESCRIPTOR='SDL2|Measurement{1:uint64:sequence;2:fl64:value;3:uint32:extra;}'
            _SDL_FIXED_SIZE=20
            _SDL_FIELDS=((1,'sequence','required','uint64',()),(2,'value','required','fl64',()),(3,'extra','required','uint32',()))
        with self.assertRaises(SchemaError):
            await asyncio.to_thread(c.subscribe,'topic',Extended,lambda v,i:None)
        await asyncio.to_thread(p.close)
        other=await self.client('replacement')
        replacement=await asyncio.to_thread(other.publisher,'topic',Extended)
        result=await asyncio.to_thread(replacement.publish,Extended(sequence=1,value=2.,extra=7))
        self.assertTrue(result.accepted)

    async def test_fifo_has_no_replay_and_udp_retries_deduplicate(self):
        p=await self.client('producer');publisher=await asyncio.to_thread(p.publisher,'topic',self.Message)
        await asyncio.to_thread(publisher.publish,self.Message(sequence=0,value=0.))
        c=await self.client('receiver');values=[];event=threading.Event()
        def callback(value,info):values.append(value.sequence);event.set()
        await asyncio.to_thread(c.subscribe,'topic',self.Message,callback,transport='udp',qos=Fifo(ack='received',retry=Retry(ack_timeout_ms=20)))
        self.assertFalse(await asyncio.to_thread(event.wait,.05))
        original=self.broker.udp.send;attempts=[0]
        async def lose_first(header,payload=b'',address=None):
            attempts[0]+=1
            if attempts[0]>1:await original(header,payload,address)
        self.broker.udp.send=lose_first
        await asyncio.to_thread(publisher.publish,self.Message(sequence=1,value=1.))
        self.assertTrue(await asyncio.to_thread(event.wait,2))
        await asyncio.sleep(.1)
        self.assertEqual(values,[1]);self.assertEqual(attempts[0],2)

    async def test_concurrent_publication_ids_keep_their_sequence(self):
        c=await self.client('concurrent');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        # Keep a bounded wait pending without attaching a data channel.
        policy=SubscriptionPolicy(capacity=1,overflow='wait',wait_timeout_ms=100)
        from chain_env.delivery.engine import DeliveryEngine
        from chain_env.delivery.stores import MemoryStore
        self.broker.subscriptions['unattached']={'id':'unattached','from':'topic','to':'absent.input','policy':policy.to_dict()}
        self.broker.engines['unattached']=DeliveryEngine(policy,MemoryStore(1))
        first=await asyncio.to_thread(p.publish,self.Message(sequence=1,value=1.))
        results=await asyncio.gather(*(asyncio.to_thread(p.publish,self.Message(sequence=n,value=float(n))) for n in range(2,6)))
        self.assertEqual([first.sequence]+[r.sequence for r in results],list(range(1,6)))
        for r in results:self.assertEqual(r.publication_id,f'{r.epoch}:{r.sequence}')

    async def test_external_publisher_wait_includes_long_internal_admission(self):
        port={'root':'Measurement','catalogue':CAT,'catalogue_id':self.codec.identity(CAT)}
        self.broker.plan['components']['offline']={'inputs':{'input':port},'outputs':{}}
        policy=SubscriptionPolicy(capacity=1,overflow='wait',wait_timeout_ms=20000)
        from chain_env.delivery.engine import DeliveryEngine
        from chain_env.delivery.stores import MemoryStore
        self.broker.subscriptions['long']={'id':'long','from':'topic','to':'offline.input','policy':policy.to_dict()}
        self.broker.engines['long']=DeliveryEngine(policy,MemoryStore(1))
        c=await self.client('long-wait');p=await asyncio.to_thread(c.publisher,'topic',self.Message)
        await asyncio.to_thread(p.publish,self.Message(sequence=1,value=1.))
        pending=asyncio.create_task(asyncio.to_thread(p.publish,self.Message(sequence=2,value=2.)))
        await asyncio.sleep(.05)
        self.assertEqual(len(await asyncio.to_thread(c.list_topics)),1)
        result=await asyncio.wait_for(pending,23)
        self.assertEqual(result.delivery,{'long':'full'})
        self.assertIsNone(c.failure)
