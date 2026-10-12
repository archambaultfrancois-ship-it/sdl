"""Transport-free checks for the optional integration layer."""
import unittest
from unittest.mock import Mock

from sdl_runtime import catalogue_id, description, encode, pack_envelope, prepare
from resolution import CatalogueDecoder, IntegrationError
from temperature_v1 import Temperature as V1
from temperature_v2 import Temperature as V2
from temperature_bad import Temperature as Bad


def sample(message_type, message):
    text = description([message_type])
    identity = catalogue_id(text)
    wire = pack_envelope(identity, encode(prepare(text, [message_type]), message))
    return identity, text, wire


class ResolutionTests(unittest.TestCase):
    def setUp(self):
        self.first = sample(V1, V1(sensor='a', celsius=10))
        self.second = sample(V2, V2(sensor='b', value=20, unit='C'))

    def assert_code(self, code, action):
        with self.assertRaises(IntegrationError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_absent_catalogue_rejected_once_without_cache_or_buffer(self):
        resolver = Mock(return_value=None)
        decoder = CatalogueDecoder(resolver, [V1])
        self.assert_code('CATALOGUE_UNKNOWN', lambda: decoder.decode(self.first[2]))
        resolver.assert_called_once_with(self.first[0])
        self.assertEqual(len(decoder.cache), 0)
        self.assertEqual(decoder.catalogue_fetches, 0)
        # Availability changes only affect an explicitly submitted new attempt.
        resolver.return_value = self.first[1]
        self.assertEqual(decoder.decode(self.first[2]).celsius, 10)
        self.assertEqual(resolver.call_count, 2)

    def test_cached_hit_and_lru_eviction(self):
        third = sample(V2, V2(sensor='c', value=30, unit='K'))
        extra_text = self.first[1].replace('sensor;', 'probe;')
        extra_id = catalogue_id(extra_text)
        extra_wire = pack_envelope(extra_id, encode(prepare(self.first[1], [V1]), V1(sensor='d', celsius=40)))
        store = {self.first[0]: self.first[1], self.second[0]: self.second[1], extra_id: extra_text}
        resolver = Mock(side_effect=store.get)
        decoder = CatalogueDecoder(resolver, [V1], cache_limit=2)
        decoder.decode(self.first[2]); decoder.decode(self.second[2]); decoder.decode(self.first[2])
        self.assertEqual(resolver.call_count, 2)
        decoder.decode(extra_wire)
        self.assertEqual(list(decoder.cache), [self.first[0], extra_id])
        self.assertEqual(decoder.decode(third[2]).celsius, 30)
        self.assertEqual(resolver.call_count, 4)
        self.assertEqual(len(decoder.cache), 2)

    def test_fixed_bindings_are_isolated_between_decoders(self):
        resolver = Mock(return_value=self.second[1])
        old = CatalogueDecoder(resolver, [V1])
        new = CatalogueDecoder(resolver, [V2])
        dynamic = CatalogueDecoder(resolver)
        self.assertEqual(old.decode(self.second[2]).celsius, 20)
        self.assertEqual(new.decode(self.second[2]).value, 20)
        self.assertEqual(dynamic.decode(self.second[2]).fields['unit'], 'C')
        self.assertIsNot(old.cache[self.second[0]], new.cache[self.second[0]])
        with self.assertRaises(AttributeError):
            old.local_types = (V2,)
        with self.assertRaises(TypeError):
            old.cache[0] = object()

    def test_invalid_catalogues_do_not_enter_cache(self):
        decoder = CatalogueDecoder(lambda identity: self.second[1])
        self.assert_code('DIGEST_MISMATCH', lambda: decoder.decode(self.first[2]))
        self.assertEqual(len(decoder.cache), 0)
        invalid = 'not a catalogue'
        decoder = CatalogueDecoder(lambda identity: invalid)
        wire = pack_envelope(catalogue_id(invalid), b'\x01')
        self.assert_code('INVALID_CATALOGUE', lambda: decoder.decode(wire))
        self.assertEqual(len(decoder.cache), 0)
        bad = sample(Bad, Bad(sensor='x', celsius='hot'))
        decoder = CatalogueDecoder(lambda identity: bad[1], [V1])
        self.assert_code('INVALID_CATALOGUE', lambda: decoder.decode(bad[2]))
        self.assertEqual(len(decoder.cache), 0)

    def test_bytes_catalogue_supported(self):
        decoder = CatalogueDecoder(lambda identity: self.first[1].encode('utf-8'), [V1])
        self.assertEqual(decoder.decode(self.first[2]).celsius, 10)

    def test_limits_and_bad_header_precede_resolution(self):
        resolver = Mock(return_value=self.first[1])
        decoder = CatalogueDecoder(resolver, max_envelope_size=10)
        self.assert_code('DATA_TOO_LARGE', lambda: decoder.decode(self.first[2]))
        self.assert_code('INVALID_ENVELOPE', lambda: decoder.decode(b'broken'))
        resolver.assert_not_called()
        decoder = CatalogueDecoder(resolver, max_catalogue_size=1)
        self.assert_code('CATALOGUE_TOO_LARGE', lambda: decoder.decode(self.first[2]))
        self.assertEqual(len(decoder.cache), 0)

    def test_resolver_failure_propagates_without_retry(self):
        resolver = Mock(side_effect=OSError('store unavailable'))
        decoder = CatalogueDecoder(resolver)
        with self.assertRaises(OSError): decoder.decode(self.first[2])
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(len(decoder.cache), 0)

    def test_malformed_payload_is_rejected(self):
        decoder = CatalogueDecoder(lambda identity: self.first[1])
        self.assert_code('INVALID_DATA', lambda: decoder.decode(self.first[2][:-1]))

    def test_constructor_limits(self):
        for limit in (0, -1, True, 1.5):
            with self.assertRaises(ValueError): CatalogueDecoder(lambda identity: None, cache_limit=limit)
        with self.assertRaises(TypeError): CatalogueDecoder(None)


if __name__ == '__main__':
    unittest.main()
