"""One retained value per block; bounded latest-value mailboxes per reader.

All mutations run synchronously on the owning event loop. No reader/network wait
can hold up a writer or create a gap between subscription and its initial state.
"""
import asyncio
import base64
import hmac
from dataclasses import dataclass, field
from ose.execution.ipc import MAX_SIZE
from ose.transport.protocol import encode

OPERATIONS = frozenset('parameters_open parameters_list parameters_describe parameters_get parameters_set parameters_subscribe parameters_next parameters_unsubscribe'.split())


@dataclass(eq=False)
class Subscription:
    pending: dict = field(default_factory=dict)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    paths: set = field(default_factory=set)
    polling: bool = False
    closed: bool = False


class ParameterService:
    def __init__(self, plan, codec):
        self.codec, self.limit = codec, plan.get('max_frame', MAX_SIZE)
        self.blocks = plan.get('shared_parameters', {})
        self.values, self.contexts = {}, {}
        self.bytes = 0
        self.max_bytes = 64 * 1024 * 1024
        self.sessions = {}
        self.readers = {path: set() for path in self.blocks}
        prepared = {}
        for path, desc in self.blocks.items():
            if desc['catalogue'] not in prepared:
                prepared[desc['catalogue']] = codec.api.prepare(desc['catalogue'])
            self.contexts[path] = prepared[desc['catalogue']]
            encode(dict(op='result', request=2**63-1, path=path, root=desc['root']), desc['catalogue'].encode(), self.limit)
            payload = base64.b64decode(desc['initial_payload'], validate=True)
            self.validate(path, payload)
            self.values[path] = payload
            self.bytes += len(payload)
            if self.bytes > self.max_bytes:
                raise ValueError("shared parameter storage exceeds 64 MiB")

    def validate(self, path, payload):
        if path not in self.blocks:
            raise ValueError('unknown parameter block')
        if not isinstance(payload, bytes) or not payload:
            raise ValueError('parameter payload must be nonempty bytes')
        desc = self.blocks[path]
        # Reserve enough metadata for paths and RPC IDs as well as the IPC alias.
        encode(dict(op='result', request=2**63-1, path=path), payload, self.limit)
        if len(payload) + len(desc['catalogue'].encode()) + 7 + 200 > MAX_SIZE:
            raise ValueError('parameter block exceeds component frame limit')
        value = self.codec.api.decode_dynamic(self.contexts[path], payload)
        if value.type_name != desc['root']:
            raise ValueError('wrong parameter message root')

    def set(self, path, payload):
        self.validate(path, payload)
        size = self.bytes - len(self.values[path]) + len(payload)
        if size > self.max_bytes:
            raise ValueError("shared parameter storage exceeds 64 MiB")
        self.bytes = size
        self.values[path] = payload
        for reader in self.readers[path]:
            reader.pending[path] = None
            reader.changed.set()

    def subscribe(self, owner, paths):
        if owner in self.sessions:
            raise ValueError('parameter subscription already open')
        if not isinstance(paths, list) or not 1 <= len(paths) <= 256 or len(paths) != len(set(paths)) or any(path not in self.blocks for path in paths):
            raise ValueError('invalid parameter subscription paths')
        if len(self.sessions) >= 1024:
            raise ValueError('too many parameter subscriptions')
        reader = Subscription(paths=set(paths))
        for path in paths:
            self.readers[path].add(reader)
            reader.pending[path] = None
        reader.changed.set()
        self.sessions[owner] = reader
        return reader

    def disconnect(self, owner):
        reader = self.sessions.pop(owner, None)
        if reader:
            for path in reader.paths:
                self.readers[path].discard(reader)
            reader.closed = True
            reader.pending.clear()
            reader.changed.set()

    async def next(self, owner):
        reader = self.sessions.get(owner)
        if reader is None or reader.polling:
            raise ValueError('one active poll per parameter subscription required')
        reader.polling = True
        try:
            reader.changed.clear()
            if not reader.pending and not reader.closed:
                try:
                    await asyncio.wait_for(reader.changed.wait(), .25)
                except asyncio.TimeoutError:
                    pass
            if reader.closed:
                raise ValueError('parameter subscription closed')
            if not reader.pending:
                return {'empty': True}, b''
            path = next(iter(reader.pending))
            reader.pending.pop(path)
            return {'path': path}, self.values[path]
        finally:
            reader.polling = False

    async def request(self, owner, header, payload, rights):
        op = header['op']
        if op == 'parameters_list':
            return {'blocks': sorted(rights)}, None
        if op == 'parameters_unsubscribe':
            self.disconnect(owner)
            return {}, None
        if op == 'parameters_next':
            return await self.next(owner)
        if op == 'parameters_subscribe':
            paths = header.get('paths')
            if not isinstance(paths, list) or not all(isinstance(p, str) and 'read' in rights.get(p, ()) for p in paths):
                raise ValueError('parameter read access denied')
            self.subscribe(owner, paths)
            return {}, None
        path = header.get('path')
        access = 'write' if op == 'parameters_set' else 'read'
        allowed = rights.get(path, ()) if isinstance(path, str) else ()
        if not allowed or (op != 'parameters_describe' and access not in allowed):
            raise ValueError(f'parameter {access} access denied')
        if op == 'parameters_set':
            self.set(path, payload)
            return {}, None
        if op == 'parameters_get':
            return {'path': path}, self.values[path]
        if op == 'parameters_describe':
            return {'path': path, 'root': self.blocks[path]['root']}, self.blocks[path]['catalogue'].encode()
        raise ValueError('unknown parameter operation')

    def close(self):
        for owner in list(self.sessions):
            self.disconnect(owner)

    def snapshot(self):
        return {'bytes': self.bytes, 'blocks': {path: {'root': desc['root'], 'readers': len(self.readers[path])}
                for path, desc in self.blocks.items()}}


def binding_rights(node):
    result = {}
    for binding in node.get('parameter_bindings', {}).values():
        result.setdefault(binding['path'], set()).update(binding['access'].split('_'))
    return result


class ParameterEndpoint:
    """Shared RPC authorization; component sessions keep their existing identity."""
    def __init__(self, plan, codec):
        self.service = ParameterService(plan, codec)
        self.clients = plan.get('parameter_clients', {})
        self.external = {}

    async def request(self, owner, header, payload, *, node=None, certificate=None):
        if header['op'] == 'parameters_open':
            if node is not None or owner in self.external or len(self.external) >= 1024:
                raise ValueError('invalid parameter client session')
            name = header.get('client')
            permissions = self.clients.get(name) if isinstance(name, str) else None
            if permissions is None:
                raise ValueError('unknown parameter client')
            if certificate is None:
                token = header.get('token')
                if not isinstance(token, str) or not permissions.get('token') or not hmac.compare_digest(token.encode(), permissions['token'].encode()):
                    raise ValueError('invalid parameter client token')
            elif certificate != name:
                raise ValueError('certificate does not identify this parameter client')
            rights = {}
            for access in ('read', 'write'):
                for path in permissions.get(access, []):
                    rights.setdefault(path, set()).add(access)
            self.external[owner] = rights
            return {}, None
        rights = binding_rights(node) if node is not None else self.external.get(owner)
        if rights is None:
            raise ValueError('parameter client session required')
        return await self.service.request(owner, header, payload, rights)

    def disconnect(self, owner):
        self.service.disconnect(owner)
        self.external.pop(owner, None)

    def close(self):
        self.service.close()
        self.external.clear()
