import asyncio
import base64
import copy
import unittest
from pathlib import Path
from ose.codec.sdl2 import SDL2
from ose.parameters.service import ParameterService, binding_rights
from ose.parameters.values import Values
from ose.parameters.bindings import RemoteBindings
from ose.modeling.validation import validate_graph

SDL_ROOT = Path(__file__).resolve().parents[3] / 'sdl'
CATALOGUE = 'SDL2|Nested{1:uint32:count;}|Settings{1:bool:enabled;2:fl64:threshold;3:Nested:nested;4*:int32:samples;5?:string:label;}'


class ParameterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.codec = SDL2(SDL_ROOT)
        self.values = Values(self.codec.api, CATALOGUE, 'Settings')
        self.value = dict(enabled=True, threshold=80., nested={'count': 3}, samples=[1, 2], label=None)
        self.payload = self.values.encode(self.value)
        self.plan = dict(shared_parameters={'machine/detection': dict(root='Settings', catalogue=CATALOGUE,
                          initial_payload=base64.b64encode(self.payload).decode())})
        self.service = ParameterService(self.plan, self.codec)

    def test_json_round_trip_and_full_block_validation(self):
        self.assertEqual(self.values.decode(self.payload), self.value)
        for update in ({'enabled': 1}, {'threshold': '80'}, {'samples': [True]}, {'nested': {'count': -1}}, {'extra': 1}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.values.encode({**self.value, **update})
        with self.assertRaises(ValueError):
            self.values.encode({'enabled': True})

    def test_enums_fixed_arrays_complex_and_unsigned(self):
        catalogue = 'SDL2|Settings{1:Mode:mode;2:uint64[2]:limits;3:c64:gain;}|#Mode{OFF=0;ON=1;}'
        values = Values(self.codec.api, catalogue, 'Settings')
        value = dict(mode='ON', limits=[0, 2**64-1], gain={'real': 1., 'imag': -2.})
        self.assertEqual(values.decode(values.encode(value)), value)
        for update in ({'mode': 'UNKNOWN'}, {'limits': [1]}, {'limits': [-1, 0]}, {'gain': {'real': 1}}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                values.encode({**value, **update})

    async def test_subscription_registration_races_and_pending_poll(self):
        owner = object()
        self.service.subscribe(owner, ['machine/detection'])
        await self.service.next(owner)
        task = asyncio.create_task(self.service.next(owner))
        await asyncio.sleep(0)
        newer = self.values.encode({**self.value, 'threshold': 90.})
        self.service.set('machine/detection', newer)
        self.assertEqual((await task)[1], newer)
        self.service.disconnect(owner)
        self.service.subscribe(owner, ['machine/detection'])
        latest = self.values.encode({**self.value, 'threshold': 95.})
        self.service.set('machine/detection', latest)
        self.assertEqual((await self.service.next(owner))[1], latest)

    async def test_adapter_poll_stops_after_absorbed_cancellation(self):
        node = {'parameter_bindings': {'detection': {'path': 'machine/detection', 'access': 'read'}}}
        started = asyncio.Event()
        polls = []
        async def request(op, *args, **kwargs):
            if op == 'parameters_subscribe':
                return {}, b''
            polls.append(op)
            if len(polls) == 1:
                return {'path': 'machine/detection'}, self.payload
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return {'empty': True}, b''
        bindings = await RemoteBindings(node, request, asyncio.Event()).open()
        await started.wait()
        await asyncio.wait_for(bindings.close(), 1)
        self.assertTrue(bindings.task.done())
        self.assertEqual(len(polls), 2)

    def test_storage_budget_rejection_is_atomic(self):
        self.service.max_bytes = len(self.payload)
        larger = self.values.encode({**self.value, 'samples': list(range(100))})
        with self.assertRaises(ValueError):
            self.service.set('machine/detection', larger)
        self.assertEqual(self.service.values['machine/detection'], self.payload)
        self.assertEqual(self.service.bytes, len(self.payload))

    def test_model_bindings_and_external_rights(self):
        model = dict(version=1, schemas='settings.sdl', components={'reader': dict(inputs={}, outputs={},
            trigger=dict(mode='periodic', period_ms=10), parameter_bindings={
                'detection': {'path': 'machine/detection', 'access': 'read'}})}, subscriptions=[],
            shared_parameters={'machine/detection': {'root': 'Settings', 'initial': self.value}})
        deployment = dict(version=1, components={'reader': {'command': ['unused']}},
                          parameter_clients={'operator': {'read': ['machine/detection'], 'token': 'test-only-parameter-token'}})
        validate_graph(model, deployment)
        for binding in ({'path': 'missing', 'access': 'read'}, {'path': 'machine/detection', 'access': 'owner'}):
            bad = copy.deepcopy(model)
            bad['components']['reader']['parameter_bindings']['detection'] = binding
            with self.assertRaises(ValueError):
                validate_graph(bad, deployment)
        bad = copy.deepcopy(deployment)
        bad['parameter_clients']['operator'].pop('token')
        with self.assertRaises(ValueError):
            validate_graph(model, bad)
        bad['execution'] = 'distributed'
        validate_graph(model, bad)

    async def test_initial_latest_multiple_readers_and_reconnect(self):
        first, second = object(), object()
        a = self.service.subscribe(first, ['machine/detection'])
        b = self.service.subscribe(second, ['machine/detection'])
        self.assertEqual((await self.service.next(first))[1], self.payload)
        for i in range(200):
            payload = self.values.encode({**self.value, 'threshold': float(i)})
            self.service.set('machine/detection', payload)
        self.assertEqual(len(a.pending), 1)
        self.assertEqual(len(b.pending), 1)
        self.assertEqual((await self.service.next(first))[1], payload)
        self.assertEqual((await self.service.next(second))[1], payload)
        self.service.disconnect(first)
        self.service.subscribe(first, ['machine/detection'])
        self.assertEqual((await self.service.next(first))[1], payload)
        self.service.close()
        self.assertFalse(self.service.sessions)

    async def test_access_and_invalid_write_preserve_previous_value(self):
        owner = object()
        rights = {'machine/detection': {'read'}}
        with self.assertRaises(ValueError):
            await self.service.request(owner, dict(op='parameters_set', path='machine/detection'), self.payload, rights)
        with self.assertRaises(ValueError):
            self.service.set('machine/detection', b'broken')
        self.assertEqual(self.service.values['machine/detection'], self.payload)
        self.assertEqual(binding_rights({'parameter_bindings': {'a': {'path': 'p', 'access': 'read'},
                           'b': {'path': 'p', 'access': 'write'}}}), {'p': {'read', 'write'}})
