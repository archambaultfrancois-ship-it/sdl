"""Adapter-side subscription caches; only the tick driver writes component IPC."""
import asyncio
from ose.execution import ipc


class RemoteBindings:
    def __init__(self, node, request, activity):
        self.bindings = node.get('parameter_bindings', {})
        self.request, self.activity = request, activity
        self.initial, self.pending = {}, {}
        self.by_path = {}
        self.task = None
        self.closed = False
        for alias, binding in self.bindings.items():
            if 'read' in binding['access']:
                self.by_path.setdefault(binding['path'], []).append(alias)

    async def open(self):
        if not self.by_path:
            return self
        await self.request('parameters_subscribe', paths=list(self.by_path))
        received = set()
        while received != set(self.by_path):
            header, payload = await self.request('parameters_next')
            if 'path' in header:
                path = header['path']
                if path not in self.by_path:
                    raise ValueError('unexpected parameter block')
                received.add(path)
                for alias in self.by_path[path]:
                    self.initial[alias] = payload
        self.task = asyncio.create_task(self._poll())
        return self

    async def _poll(self):
        while not self.closed:
            header, payload = await self.request('parameters_next')
            if self.closed:
                return
            if 'path' in header:
                path = header['path']
                if path not in self.by_path:
                    raise ValueError('unexpected parameter block')
                for alias in self.by_path[path]:
                    self.pending[alias] = payload
                self.activity.set()

    def drain(self):
        pending, self.pending = self.pending, {}
        return pending

    async def write(self, alias, payload):
        binding = self.bindings.get(alias)
        if binding is None or 'write' not in binding['access']:
            raise ValueError('parameter write access denied')
        await self.request('parameters_set', payload, path=binding['path'])

    async def close(self):
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


async def updates(process, node, values):
    for alias, payload in values.items():
        binding = node['parameter_bindings'][alias]
        await ipc.send(process.stdin, 'U', ipc.port_data(alias, payload, binding['catalogue']))


async def write_reply(process, data, bindings):
    try:
        alias, payload = ipc.unpack_port(data)
        if bindings is None:
            raise ValueError('component has no shared parameter bindings')
        await bindings.write(alias, payload)
        result = b'\x01'
    except (ValueError, KeyError, TypeError) as error:
        result = b'\x00' + str(error).encode()[:4096]
    await ipc.send(process.stdin, 'A', result)


class LocalBindings:
    """In-process counterpart for simulation and the shared component driver."""
    def __init__(self, node, service):
        self.bindings, self.service = node.get('parameter_bindings', {}), service
        self.by_path = {}
        self.staged = None
        for alias, binding in self.bindings.items():
            if 'read' in binding['access']:
                self.by_path.setdefault(binding['path'], []).append(alias)
        self.reader = service.subscribe(self, list(self.by_path)) if self.by_path else None

    @property
    def initial(self):
        if self.reader:
            self.reader.pending.clear()
        return {alias: self.service.values[path] for path, aliases in self.by_path.items() for alias in aliases}

    def drain(self):
        if self.reader is None:
            return {}
        pending, self.reader.pending = self.reader.pending, {}
        return {alias: self.service.values[path] for path in pending for alias in self.by_path[path]}

    async def write(self, alias, payload):
        binding = self.bindings.get(alias)
        if binding is None or 'write' not in binding['access']:
            raise ValueError('parameter write access denied')
        path = binding['path']
        self.service.validate(path, payload)
        if self.staged is None:
            self.service.set(path, payload)
        else:
            self.staged[path] = payload

    def close(self):
        self.service.disconnect(self)
