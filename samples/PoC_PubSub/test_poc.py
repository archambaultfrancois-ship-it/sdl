import base64
import socket
import struct
import threading
import unittest
from unittest.mock import patch

from sdl_runtime import description, encode
from pubsub import (Broker, BrokerState, Client, PocError, Publisher, Subscriber,
                    TOPIC, HISTORY_LIMIT, MAX_FRAME, MAX_ENVELOPE, HEADER_SIZE, catalogue_id, pack_envelope, unpack_envelope)
from temperature_v1 import Temperature as V1
from temperature_v2 import Temperature as V2
from temperature_bad import Temperature as Bad


class PubSubIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.broker = Broker(('127.0.0.1', 0), BrokerState(V1))
        cls.worker = threading.Thread(target=cls.broker.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.broker.shutdown()
        cls.worker.join()
        cls.broker.server_close()

    def setUp(self):
        self.broker.state = BrokerState(V1)
        self.client = Client(self.broker.server_address)

    def assert_code(self, code, action):
        with self.assertRaises(PocError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def raw_publish(self, frame, topic=TOPIC):
        return self.client.call('publish', topic=topic, producer='test',
                                envelope=base64.b64encode(frame).decode('ascii'))

    def test_late_subscriber_two_revisions_and_cached_resolution(self):
        first = Publisher(self.client, V1, 'old')
        second = Publisher(self.client, V2, 'new')
        first.publish(V1(sensor='a', celsius=10))
        second.publish(V2(sensor='b', value=20, unit='C'))
        subscriber = Subscriber(self.client, [V1])
        rows = subscriber.poll()
        self.assertEqual([value.celsius for _, value in rows], [10, 20])
        self.assertEqual([event['producer'] for event, _ in rows], ['old', 'new'])
        self.assertEqual(subscriber.catalogue_fetches, 2)
        second.publish(V2(sensor='b', value=21, unit='C'))
        self.assertEqual(subscriber.poll()[0][1].celsius, 21)
        self.assertEqual(subscriber.catalogue_fetches, 2)
        self.assertEqual(subscriber.poll(), [])
        dynamic = Subscriber(self.client)
        self.assertEqual(dynamic.poll()[1][1].fields['unit'], 'C')

    def test_injected_resolver_rejects_missing_catalogue_without_advancing_cursor(self):
        publisher = Publisher(self.client, V1, 'old')
        publisher.publish(V1(sensor='a', celsius=10))
        resolver = unittest.mock.Mock(return_value=None)
        subscriber = Subscriber(self.client, [V1], resolver=resolver)
        self.assert_code('CATALOGUE_UNKNOWN', subscriber.poll)
        resolver.assert_called_once_with(int(publisher.identity, 16))
        self.assertEqual(subscriber.cursor, 0)
        self.assertEqual(len(subscriber.cache), 0)
        resolver.return_value = publisher.text
        self.assertEqual(subscriber.poll()[0][1].celsius, 10)

    def test_tcp_resolver_maps_missing_catalogue_to_rejection(self):
        from pubsub import TcpCatalogueResolver
        self.assertIsNone(TcpCatalogueResolver(self.client)(0))
        subscriber = Subscriber(self.client)
        event = {'envelope': base64.b64encode(pack_envelope('00000000', b'\x01')).decode('ascii')}
        self.assert_code('CATALOGUE_UNKNOWN', lambda: subscriber.decode_event(event))
        self.assertEqual(len(subscriber.cache), 0)

    def test_manifest_contract_drives_topic_metadata_and_registration(self):
        from contracts import TopicContract, load_contract
        loaded = load_contract()
        contract = TopicContract('custom/temperature', loaded.root_type, loaded.baseline)
        self.broker.state = BrokerState(contract)
        advertised = self.client.call('contract', topic=contract.topic)['contract']
        self.assertEqual(advertised['topic'], contract.topic)
        self.assertEqual(advertised['baseline_catalogue_id'], catalogue_id(contract.baseline))
        self.assert_code('TOPIC_UNKNOWN', lambda: self.client.call('contract', topic=TOPIC))
        publisher = Publisher(self.client, V2, 'new', topic=contract.topic)
        publisher.publish(V2(sensor='a', value=25, unit='C'))
        subscriber = Subscriber(self.client, [V1], topic=contract.topic)
        self.assertEqual(subscriber.poll()[0][1].celsius, 25)
        with self.assertRaises(PocError) as offline:
            contract.prepare_candidate(description([Bad]))
        with self.assertRaises(PocError) as online:
            Publisher(self.client, Bad, 'bad', topic=contract.topic)
        self.assertEqual(offline.exception.code, online.exception.code)
        self.assertEqual(offline.exception.diagnostic, online.exception.diagnostic)

    def test_incompatible_schema_rejected_before_data(self):
        self.assert_code('INCOMPATIBLE_SCHEMA', lambda: Publisher(self.client, Bad, 'bad'))
        self.assertEqual(self.broker.state.catalogues, {})
        self.assertEqual(self.broker.state.sequence, 0)

    def test_broker_contract_works_without_generated_bindings(self):
        self.broker.state = BrokerState(description([V1]))
        publisher = Publisher(self.client, V2, 'new')
        publisher.publish(V2(sensor='a', value=25, unit='C'))
        self.assertEqual(Subscriber(self.client, [V1]).poll()[0][1].celsius, 25)
        with self.assertRaises(PocError) as caught:
            Publisher(self.client, Bad, 'bad')
        issue = caught.exception.diagnostic
        self.assertEqual(issue['code'], 'FIELD_TYPE_MISMATCH')
        self.assertEqual(issue['path'], 'Temperature.celsius')
        self.assertEqual(issue['field_id'], 2)
        self.assertEqual(issue['publisher_type'], 'string')
        self.assertEqual(issue['consumer_type'], 'fl64')

    def test_required_field_policy_is_separate_from_default_compatibility(self):
        text = description([V1]).replace('2:fl64:celsius;', '')
        self.client.call('put_catalogue', catalogue_id=catalogue_id(text), text=text)
        self.broker.state = BrokerState(description([V1]), require_required=True)
        with self.assertRaises(PocError) as caught:
            self.client.call('put_catalogue', catalogue_id=catalogue_id(text), text=text)
        self.assertEqual(caught.exception.code, 'TOPIC_CONTRACT')
        self.assertEqual(caught.exception.diagnostic['code'], 'REQUIRED_FIELD_MISSING')
        self.assertEqual(caught.exception.diagnostic['path'], 'Temperature.celsius')
        self.assertEqual(self.broker.state.catalogues, {})

    def test_catalogue_integrity_verified(self):
        self.assert_code('DIGEST_MISMATCH', lambda: self.client.call(
            'put_catalogue', catalogue_id='0' * 8, text=description([V1])))

    def test_unknown_catalogue_and_invalid_envelope(self):
        self.assert_code('CATALOGUE_UNKNOWN', lambda: self.raw_publish(pack_envelope('0' * 8, b'\x01')))
        self.assert_code('INVALID_ENVELOPE', lambda: self.raw_publish(b'broken'))

    def test_envelope_uses_four_byte_digest_and_version_two(self):
        text = description([V1])
        self.assertEqual(catalogue_id(text), '88330348')
        self.assertEqual(HEADER_SIZE, 9)
        frame = pack_envelope(catalogue_id(text), b'\x01')
        self.assertEqual(frame, b'S2PS\x02\x88\x33\x03\x48\x01')
        self.assertEqual(unpack_envelope(frame), ('88330348', b'\x01'))
        for invalid_id in ('0' * 64, 'not-hex!', '00', '67 8f 66 1f'):
            self.assert_code('INVALID_ENVELOPE', lambda: pack_envelope(invalid_id, b'\x01'))
        self.assert_code('INVALID_ENVELOPE', lambda: unpack_envelope(
            b'S2PS\x01' + bytes(32) + b'\x01'))

    def test_collision_rejected_without_overwriting_existing_catalogue(self):
        publisher = Publisher(self.client, V1, 'old')
        original_context = self.broker.state.contexts[publisher.identity]
        # Force two distinct catalogues to have the same truncated digest.
        with patch('pubsub.catalogue_id', return_value=publisher.identity):
            self.assert_code('CATALOGUE_COLLISION', lambda: self.client.call(
                'put_catalogue', catalogue_id=publisher.identity, text=description([V2])))
        self.assertEqual(self.broker.state.catalogues[publisher.identity], publisher.text)
        self.assertIs(self.broker.state.contexts[publisher.identity], original_context)
        publisher.publish(V1(sensor='a', celsius=10))
        self.assertEqual(Subscriber(self.client, [V1]).poll()[0][1].celsius, 10)

    def test_identical_catalogue_registration_is_idempotent(self):
        first = Publisher(self.client, V1, 'first')
        second = Publisher(self.client, V1, 'second')
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(len(self.broker.state.catalogues), 1)

    def test_wrong_topic_and_truncated_data_rejected(self):
        publisher = Publisher(self.client, V1, 'old')
        data = encode(publisher.context, V1(sensor='a', celsius=10))
        frame = pack_envelope(publisher.identity, data)
        self.assert_code('TOPIC_UNKNOWN', lambda: self.raw_publish(frame, 'other/topic'))
        self.assert_code('INVALID_DATA', lambda: self.raw_publish(pack_envelope(publisher.identity, data[:-1])))
        self.assertEqual(self.broker.state.sequence, 0)

    def test_data_limit_bounds_replay_memory(self):
        publisher = Publisher(self.client, V1, 'old')
        self.assert_code('DATA_TOO_LARGE', lambda: publisher.publish(
            V1(sensor='x' * MAX_ENVELOPE, celsius=10)))
        self.assertEqual(self.broker.state.sequence, 0)

    def test_wrong_root_type_rejected(self):
        text = "SDL2|Other{1:bool:active;}"
        self.assert_code('TOPIC_CONTRACT', lambda: self.client.call(
            'put_catalogue', catalogue_id=catalogue_id(text), text=text))

    def test_history_gap_is_explicit(self):
        publisher = Publisher(self.client, V1, 'old')
        for i in range(HISTORY_LIMIT + 1):
            publisher.publish(V1(sensor='a', celsius=i))
        subscriber = Subscriber(self.client, [V1])
        self.assert_code('HISTORY_GAP', subscriber.poll)
        subscriber.cursor = 1
        self.assertEqual(len(subscriber.poll()), HISTORY_LIMIT)

    def test_replay_resolves_old_catalogue_after_new_registration(self):
        first = Publisher(self.client, V1, 'old')
        first.publish(V1(sensor='a', celsius=10))
        Publisher(self.client, V2, 'new')
        self.assertEqual(Subscriber(self.client, [V1]).poll()[0][1].celsius, 10)

    def test_cache_eviction_refetches_catalogue(self):
        first = Publisher(self.client, V1, 'old')
        second = Publisher(self.client, V2, 'new')
        first.publish(V1(sensor='a', celsius=10))
        second.publish(V2(sensor='b', value=20))
        first.publish(V1(sensor='a', celsius=11))
        subscriber = Subscriber(self.client, [V1], cache_limit=1)
        self.assertEqual(len(subscriber.poll()), 3)
        self.assertEqual(subscriber.catalogue_fetches, 3)
        self.assertEqual(len(subscriber.cache), 1)

    def test_oversized_tcp_frame_rejected_before_reading_body(self):
        from pubsub import receive_json
        with socket.create_connection(self.broker.server_address, timeout=5) as stream:
            stream.sendall(struct.pack('>I', MAX_FRAME + 1))
            response = receive_json(stream)
        self.assertFalse(response['ok'])
        self.assertEqual(response['code'], 'FRAME_TOO_LARGE')


if __name__ == '__main__':
    unittest.main()
