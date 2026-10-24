"""Async external parameter client, independent of models and SDL checkouts."""
from ose.codec.runtime import Runtime
from ose.transport.tcp import Client as RPC
from ose.transport.secure import connect as secure_connect
from ose.parameters.values import Values


class ParameterBlock:
    def __init__(self, client, path, root, catalogue, message_type=None):
        self.client, self.path, self.root, self.catalogue = client, path, root, catalogue
        self.api = client.runtime.api
        self.values = Values(self.api, catalogue, root)
        self.message_type = message_type
        self.context = self.api.prepare(catalogue, [message_type]) if message_type is not None else None
        if message_type is not None and self.api.message_name(message_type) != root:
            raise ValueError('parameter root does not match requested message type')
        if message_type is not None and self.api.description([message_type]) != catalogue:
            # Compatible local declarations are useful when a catalogue also contains other roots.
            report = self.api.compare_catalogues(catalogue, self.api.description([message_type]), root=root, require_required=True)
            if not report.compatible:
                raise ValueError('incompatible parameter message type')

    def decode(self, payload):
        if self.context is None:
            return self.values.decode(payload)
        result = self.api.decode(self.context, payload)
        if not isinstance(result, self.message_type):
            raise ValueError('wrong parameter message root')
        return result

    async def get(self):
        _, payload = await self.client.rpc.request('parameters_get', path=self.path)
        return self.decode(payload)

    async def set(self, value):
        if self.context is None:
            payload = self.values.encode(value)
        else:
            if not isinstance(value, self.message_type):
                raise TypeError('wrong parameter message type')
            payload = self.api.encode(self.context, value)
        await self.client.rpc.request('parameters_set', payload, path=self.path)


class ParameterClient:
    def __init__(self, rpc, runtime=None):
        self.rpc, self.runtime = rpc, runtime or Runtime()
        self.blocks = {}

    @classmethod
    async def connect(cls, host, port, client, *, token=None, context=None, distributed=False, runtime=None, max_frame=1024*1024):
        rpc = await secure_connect(host, port, max_frame, context, 'coordinator') if distributed else await RPC.connect(host, port, limit=max_frame)
        try:
            await rpc.request('parameters_open', client=client, token=token)
            return cls(rpc, runtime)
        except BaseException:
            await rpc.close()
            raise

    async def list(self):
        result, _ = await self.rpc.request('parameters_list')
        return result['blocks']

    async def block(self, path, message_type=None):
        result, catalogue = await self.rpc.request('parameters_describe', path=path)
        block = ParameterBlock(self, path, result['root'], catalogue.decode(), message_type)
        self.blocks[path] = block
        return block

    async def subscribe(self, paths):
        paths = list(paths)
        for path in paths:
            if path not in self.blocks:
                await self.block(path)
        await self.rpc.request('parameters_subscribe', paths=paths)

    async def next(self):
        result, payload = await self.rpc.request('parameters_next')
        if result.get('empty'):
            return None
        path = result['path']
        return path, self.blocks[path].decode(payload)

    async def unsubscribe(self):
        await self.rpc.request('parameters_unsubscribe')

    async def close(self):
        for future in self.rpc.pending.values():
            if not future.done():
                future.set_exception(ConnectionError("parameter client closed"))
        await self.rpc.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
