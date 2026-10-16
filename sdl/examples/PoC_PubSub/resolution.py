"""Optional synchronous integration helpers; no networking or runtime changes."""
from collections import OrderedDict
from types import MappingProxyType

from sdl_runtime import (CodecError, catalogue_id, prepare, decode,
                         decode_dynamic, unpack_envelope)


class IntegrationError(ValueError):
    def __init__(self, code, message, diagnostic=None):
        self.code = code
        self.diagnostic = diagnostic
        super().__init__(message)


class CatalogueDecoder:
    """Resolve unsigned IDs and cache contexts for one fixed set of local types.

    resolver(id) returns exact catalogue text (str or UTF-8 bytes), or None
    when absent. Resolver exceptions propagate. No retries, buffering, or threads
    are created. Instances are owned by one consumer and are not thread-safe.
    """
    def __init__(self, resolver, local_types=(), *, cache_limit=16,
                 max_catalogue_size=1024 * 1024, max_envelope_size=4096):
        if not callable(resolver):
            raise TypeError('resolver must be callable')
        for name, limit in (('cache_limit', cache_limit),
                            ('max_catalogue_size', max_catalogue_size),
                            ('max_envelope_size', max_envelope_size)):
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError(name + ' must be a positive integer')
        self._resolver = resolver
        self._local_types = tuple(local_types)
        self._contexts = OrderedDict()
        self._cache_limit = cache_limit
        self._max_catalogue_size = max_catalogue_size
        self._max_envelope_size = max_envelope_size
        self.catalogue_fetches = 0

    @property
    def local_types(self):
        return self._local_types

    @property
    def cache(self):
        """Read-only view, in least-to-most-recently-used order."""
        return MappingProxyType(self._contexts)

    def decode(self, envelope):
        if not isinstance(envelope, (bytes, bytearray, memoryview)):
            raise IntegrationError('INVALID_ENVELOPE', 'expected binary envelope')
        if memoryview(envelope).nbytes > self._max_envelope_size:
            raise IntegrationError('DATA_TOO_LARGE', 'envelope exceeds integration limit')
        try:
            identity, payload = unpack_envelope(envelope)
        except CodecError as error:
            raise IntegrationError('INVALID_ENVELOPE', str(error)) from error
        context = self._contexts.get(identity)
        if context is None:
            text = self._resolver(identity)
            if text is None:
                raise IntegrationError('CATALOGUE_UNKNOWN', '%08x' % identity)
            if not isinstance(text, (str, bytes)):
                raise IntegrationError('INVALID_CATALOGUE', 'resolver must return text, bytes, or None')
            try:
                size = len(text.encode('utf-8')) if isinstance(text, str) else len(text)
                if size > self._max_catalogue_size:
                    raise IntegrationError('CATALOGUE_TOO_LARGE', 'catalogue exceeds integration limit')
                if catalogue_id(text) != identity:
                    raise IntegrationError('DIGEST_MISMATCH', 'retrieved catalogue has incorrect digest')
                context = prepare(text, self._local_types)
            except (CodecError, UnicodeError) as error:
                raise IntegrationError('INVALID_CATALOGUE', str(error)) from error
            self._contexts[identity] = context
            self.catalogue_fetches += 1
            if len(self._contexts) > self._cache_limit:
                self._contexts.popitem(last=False)
        self._contexts.move_to_end(identity)
        try:
            return (decode if self._local_types else decode_dynamic)(context, payload)
        except CodecError as error:
            raise IntegrationError('INVALID_DATA', str(error)) from error
