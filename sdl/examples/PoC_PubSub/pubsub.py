"""Experimental topic-centric transport profile; SDL2 payloads are unchanged."""
import base64
from collections import deque
import json
import socket
import socketserver
import struct
import threading

from sdl_runtime import (decode, decode_dynamic, description, encode, prepare,
                         catalogue_id as sdl_catalogue_id, message_names, CodecError,
                         pack_envelope as sdl_pack_envelope, unpack_envelope as sdl_unpack_envelope)

TOPIC = 'usine/temperature/v1'
CATALOGUE_PREFIX = '_sdl2/catalogues/'
CONTRACT_PREFIX = '_sdl2/topics/'
MAX_FRAME = 2 * 1024 * 1024
MAX_CATALOGUE = 1024 * 1024
MAX_CATALOGUES = 64
HISTORY_LIMIT = 256
MAX_ENVELOPE = 4096
MAX_PRODUCER_NAME = 128
# Experimental v2 envelope: magic/version, truncated SHA-256 ID, SDL2 data.
ENVELOPE_MAGIC = b'S2PS\x02'
CATALOGUE_ID_SIZE = 4
HEADER_SIZE = len(ENVELOPE_MAGIC) + CATALOGUE_ID_SIZE


# Preserve the transport's public error name while sharing integration errors.
from resolution import CatalogueDecoder, IntegrationError as PocError
from contracts import TopicContract


def catalogue_id(text):
    """Use the first 32 SHA-256 bits of exact canonical UTF-8 catalogue bytes."""
    return '%08x' % sdl_catalogue_id(text)


def pack_envelope(identity, payload):
    try:
        digest = bytes.fromhex(identity)
    except (ValueError, TypeError) as error:
        raise PocError('INVALID_ENVELOPE', 'invalid catalogue digest') from error
    if len(digest) != CATALOGUE_ID_SIZE or digest.hex() != identity or not payload:
        raise PocError('INVALID_ENVELOPE', 'digest or payload has invalid size')
    return sdl_pack_envelope(int(identity, 16), payload)


def unpack_envelope(data):
    try:
        identity, payload = sdl_unpack_envelope(data)
        return '%08x' % identity, payload
    except CodecError as error:
        raise PocError('INVALID_ENVELOPE', str(error)) from error


def read_exact(stream, count):
    result = bytearray()
    while len(result) < count:
        part = stream.recv(count - len(result))
        if not part:
            raise EOFError('TCP connection closed during frame')
        result.extend(part)
    return bytes(result)


def send_json(stream, value):
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if not 0 < len(payload) <= MAX_FRAME:
        raise PocError('FRAME_TOO_LARGE', 'JSON transport frame exceeds limit')
    stream.sendall(struct.pack('>I', len(payload)) + payload)


def receive_json(stream):
    size = struct.unpack('>I', read_exact(stream, 4))[0]
    if not 0 < size <= MAX_FRAME:
        raise PocError('FRAME_TOO_LARGE', 'invalid TCP frame size')
    value = json.loads(read_exact(stream, size))
    if not isinstance(value, dict):
        raise PocError('INVALID_REQUEST', 'expected JSON object')
    return value


class BrokerState:
    """In-memory immutable schema store and bounded replay log per topic."""
    def __init__(self, baseline, topic=TOPIC, *, require_required=False):
        # Existing callers may still supply canonical text or a generated class.
        if isinstance(baseline, TopicContract):
            self.topic_contract = baseline
        else:
            text = baseline if isinstance(baseline, str) else description([baseline])
            names = message_names(prepare(text))
            if len(names) != 1:
                raise PocError('TOPIC_CONTRACT', 'baseline must contain one message declaration')
            self.topic_contract = TopicContract(topic, names[0], text, require_required)
        self.topic = self.topic_contract.topic
        self.baseline = self.topic_contract.baseline
        self.root_name = self.topic_contract.root_type
        self.require_required = self.topic_contract.require_required
        self.catalogues = {}
        self.contexts = {}
        self.history = deque(maxlen=HISTORY_LIMIT)
        self.sequence = 0
        self.lock = threading.Lock()
        self.contract = self.topic_contract.metadata()
        self.contract.update(catalogue_topic_prefix=CATALOGUE_PREFIX,
                             history_limit=HISTORY_LIMIT)

    def handle(self, request):
        with self.lock:
            return self._handle(request)

    def _handle(self, request):
        op = request['op']
        if op == 'put_catalogue':
            text, identity = request['text'], request['catalogue_id']
            if len(text.encode('utf-8')) > MAX_CATALOGUE:
                raise PocError('CATALOGUE_TOO_LARGE', 'catalogue exceeds limit')
            if catalogue_id(text) != identity:
                raise PocError('DIGEST_MISMATCH', 'catalogue digest does not match text')
            # The state lock makes collision checks and registration atomic.
            if identity in self.catalogues and self.catalogues[identity] != text:
                raise PocError('CATALOGUE_COLLISION',
                               '32-bit ID already belongs to a different catalogue: ' + identity)
            if identity not in self.catalogues and len(self.catalogues) >= MAX_CATALOGUES:
                raise PocError('CATALOGUE_LIMIT', 'catalogue store is full')
            ctx = self.topic_contract.prepare_candidate(text)
            self.catalogues[identity] = text
            self.contexts[identity] = ctx
            return {'topic': CATALOGUE_PREFIX + identity, 'catalogue_id': identity}
        if op == 'get_catalogue':
            identity = request['catalogue_id']
            if identity not in self.catalogues:
                raise PocError('CATALOGUE_UNKNOWN', identity)
            return {'text': self.catalogues[identity], 'catalogue_id': identity}
        if op == 'contract':
            self._check_topic(request['topic'])
            return {'topic': CONTRACT_PREFIX + self.topic + '/contract', 'contract': self.contract}
        if op == 'publish':
            self._check_topic(request['topic'])
            frame = base64.b64decode(request['envelope'], validate=True)
            if len(frame) > MAX_ENVELOPE:
                raise PocError('DATA_TOO_LARGE', 'envelope exceeds PoC limit')
            producer = request['producer']
            if not isinstance(producer, str) or len(producer.encode('utf-8')) > MAX_PRODUCER_NAME:
                raise PocError('INVALID_REQUEST', 'invalid producer name')
            identity, payload = unpack_envelope(frame)
            if identity not in self.contexts:
                raise PocError('CATALOGUE_UNKNOWN', identity)
            # Validate the entire body, including fields unknown to the baseline.
            try:
                decode_dynamic(self.contexts[identity], payload)
            except ValueError as error:
                raise PocError('INVALID_DATA', str(error)) from error
            self.sequence += 1
            event = {'sequence': self.sequence, 'topic': self.topic,
                     'producer': request['producer'], 'envelope': request['envelope']}
            self.history.append(event)
            return {'sequence': self.sequence}
        if op == 'poll':
            self._check_topic(request['topic'])
            after = request['after']
            if not isinstance(after, int) or isinstance(after, bool) or not 0 <= after <= self.sequence:
                raise PocError('INVALID_CURSOR', 'cursor must be between zero and latest sequence')
            if self.history and after < self.history[0]['sequence'] - 1:
                raise PocError('HISTORY_GAP', 'cursor predates available replay history')
            return {'events': [event for event in self.history if event['sequence'] > after],
                    'latest': self.sequence}
        raise PocError('INVALID_REQUEST', 'unknown operation')

    def _check_topic(self, topic):
        if topic != self.topic:
            raise PocError('TOPIC_UNKNOWN', topic)


class RequestHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(10)
        try:
            request = receive_json(self.request)
            result = self.server.state.handle(request)
            response = {'ok': True, 'result': result}
        except PocError as error:
            response = {'ok': False, 'code': error.code, 'message': str(error)}
            if error.diagnostic is not None:
                response['diagnostic'] = error.diagnostic
        except (ValueError, TypeError, KeyError) as error:
            response = {'ok': False, 'code': 'INVALID_REQUEST', 'message': str(error)}
        except (EOFError, OSError):
            return
        try:
            send_json(self.request, response)
        except (OSError, PocError):
            return


class Broker(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, state):
        self.state = state
        super().__init__(address, RequestHandler)


class Client:
    def __init__(self, address):
        self.address = address

    def call(self, op, **fields):
        with socket.create_connection(self.address, timeout=5) as stream:
            send_json(stream, dict(op=op, **fields))
            response = receive_json(stream)
        if not response['ok']:
            raise PocError(response['code'], response['message'], response.get('diagnostic'))
        return response['result']


class Publisher:
    def __init__(self, client, message_type, producer, topic=TOPIC):
        self.client, self.producer, self.topic = client, producer, topic
        self.text = description([message_type])
        self.identity = catalogue_id(self.text)
        self.context = prepare(self.text, [message_type])
        # Confirm topic existence before making the immutable schema available.
        client.call('contract', topic=topic)
        client.call('put_catalogue', catalogue_id=self.identity, text=self.text)

    def publish(self, message):
        frame = pack_envelope(self.identity, encode(self.context, message))
        return self.client.call('publish', topic=self.topic, producer=self.producer,
                                envelope=base64.b64encode(frame).decode('ascii'))['sequence']


class TcpCatalogueResolver:
    """Adapt the PoC RPC catalogue store to the transport-independent callback."""
    def __init__(self, client):
        self.client = client

    def __call__(self, identity):
        key = '%08x' % identity
        try:
            result = self.client.call('get_catalogue', catalogue_id=key)
        except PocError as error:
            if error.code == 'CATALOGUE_UNKNOWN':
                return None
            raise
        if result['catalogue_id'] != key:
            raise PocError('DIGEST_MISMATCH', 'catalogue response has incorrect ID')
        return result['text']


class Subscriber:
    def __init__(self, client, local_types=(), topic=TOPIC, cache_limit=16, *, resolver=None):
        self.client, self.topic = client, topic
        self.decoder = CatalogueDecoder(
            TcpCatalogueResolver(client) if resolver is None else resolver,
            local_types, cache_limit=cache_limit,
            max_catalogue_size=MAX_CATALOGUE, max_envelope_size=MAX_ENVELOPE)
        self.cursor = 0
        self.contract = client.call('contract', topic=topic)['contract']

    @property
    def local_types(self):
        return self.decoder.local_types

    @property
    def cache(self):
        return self.decoder.cache

    @property
    def catalogue_fetches(self):
        return self.decoder.catalogue_fetches

    def decode_event(self, event):
        return self.decoder.decode(base64.b64decode(event['envelope'], validate=True))

    def poll(self):
        result = self.client.call('poll', topic=self.topic, after=self.cursor)
        decoded = []
        for event in result['events']:
            value = self.decode_event(event)
            decoded.append((event, value))
            self.cursor = event['sequence']
        return decoded
