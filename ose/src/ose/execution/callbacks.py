"""Synchronous typed callbacks used by generated Python component containers."""
from collections import OrderedDict
import importlib.util
import inspect
from pathlib import Path
import sys
from ose.codec.sdl2 import SDL2
from ose.execution.component import ComponentSession, RecordResult


class CallbackContext:
    """Component parameters and typed publication, valid during a scheduled tick."""
    def __init__(self, session, name, input_types, output_types, codec, parameter_types=None):
        self.session, self.name, self.codec = session, name, codec
        self.config = session.config
        self.parameters = self.config.get("parameters", {})
        self.input_types, self.output_types = input_types, output_types
        if set(input_types) != set(self.config["inputs"]) or set(output_types) != set(self.config["outputs"]):
            raise ValueError("generated ports differ from INIT; rebuild the container")
        for direction, types in (("inputs", input_types), ("outputs", output_types)):
            for port, cls in types.items():
                if codec.api.message_name(cls) != self.config[direction][port]["root"]:
                    raise ValueError("generated types differ from INIT; rebuild the container")
        self._parameter_writes = True
        self.parameter_types = parameter_types or {}
        if set(self.parameter_types) != set(self.config.get('parameter_bindings', {})):
            raise ValueError('generated parameter bindings differ from INIT; rebuild the container')
        self._parameter_codecs = {}
        for alias, cls in self.parameter_types.items():
            binding = self.config['parameter_bindings'][alias]
            if codec.api.message_name(cls) != binding['root']:
                raise ValueError('generated parameter root differs from INIT')
            self._parameter_codecs[alias] = codec.api.prepare(binding['catalogue'], [cls])
        self._active = False
        self._recording_types = {}
        self._decoders = OrderedDict()
        self._encoders = {port: codec.api.prepare(self.config["outputs"][port]["catalogue"], [cls])
                          for port, cls in output_types.items()}

    @property
    def time_ns(self):
        return self.session.time_ns

    @property
    def tick_index(self):
        return self.session.tick_index

    def now(self):
        return self.time_ns / 1_000_000_000

    def publish(self, port, message):
        if not self._active:
            raise RuntimeError("publication is only allowed inside a tick callback")
        if port not in self.output_types:
            raise ValueError(f"unknown output port: {port}")
        if not isinstance(message, self.output_types[port]):
            raise TypeError(f"output {port} expects {self.output_types[port].__name__}")
        self.session.publish(port, self.codec.api.encode(self._encoders[port], message))

    def declare_recording_point(self, name, message_type, description=""):
        if self.session.is_ready or not self._parameter_writes:
            raise RuntimeError("recording declarations require on_start")
        root = self.codec.api.message_name(message_type)
        catalogue = self.codec.api.description([message_type])
        encoder = self.codec.api.prepare(catalogue, [message_type])
        self.session.declare_recording_point(name, root, catalogue, description)
        self._recording_types[name] = (message_type, encoder)

    def recording_enabled(self, name):
        return self.session.recording_enabled(name)

    def record(self, name, message):
        if not self._active:
            raise RuntimeError("recording requires an active tick callback")
        message_type, encoder = self._recording_types[name]
        if not isinstance(message, message_type):
            raise TypeError(f"recording {name} expects {message_type.__name__}")
        if not self.recording_enabled(name):
            return RecordResult.DISABLED
        return self.session.record(name, self.codec.api.encode(encoder, message))

    def parameter(self, alias):
        binding = self.config.get('parameter_bindings', {}).get(alias)
        if binding is None or 'read' not in binding['access']:
            raise ValueError('parameter read access denied')
        catalogue, payload = self.session.parameter_values[alias]
        value = self.codec.api.decode(self._parameter_codecs[alias], payload)
        if not isinstance(value, self.parameter_types[alias]):
            raise ValueError('wrong parameter message root')
        return value

    def set_parameter(self, alias, value):
        if not self._parameter_writes:
            raise RuntimeError("parameter writes require an active initialization or tick callback")
        if alias not in self.parameter_types or not isinstance(value, self.parameter_types[alias]):
            raise TypeError('wrong parameter message type')
        payload = self.codec.api.encode(self._parameter_codecs[alias], value)
        self.session.set_parameters(alias, payload)

    def _decode(self, raw):
        if set(raw) - set(self.input_types):
            raise ValueError("unknown input port")
        result = {}
        for port, cls in self.input_types.items():
            if port not in raw:
                if self.config["inputs"][port].get("required", True):
                    raise ValueError(f"missing required input: {port}")
                result[port] = None
                continue
            if raw[port] is None:
                desc = self.config["inputs"][port]
                if not desc.get("required", True) or "timeout" not in desc:
                    raise ValueError(f"null input requires a timeout: {port}")
                result[port] = None
                continue
            catalogue, payload = raw[port]
            key = (port, catalogue)
            if key not in self._decoders:
                desc = self.config["inputs"][port]
                self.codec.compare(catalogue, desc["catalogue"], desc["root"])
                decoder = self.codec.api.prepare(catalogue, [cls])
                if len(self._decoders) >= 64:
                    self._decoders.popitem(last=False)
                self._decoders[key] = decoder
            self._decoders.move_to_end(key)
            value = self.codec.api.decode(self._decoders[key], payload)
            if not isinstance(value, cls):
                raise TypeError(f"input {port} expects {cls.__name__}")
            result[port] = value
        return result


def _load_handler(path):
    path = Path(path).resolve()
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("_ose_component_handler", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load Python handler: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _callback(module, name, nargs, *, required=False):
    callback = getattr(module, name, None)
    if callback is None and not required:
        return None
    if not callable(callback) or inspect.iscoroutinefunction(callback):
        raise ValueError(f"handler must define synchronous {name}")
    try:
        inspect.signature(callback).bind(*([None] * nargs))
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid callback signature: {name} expects {nargs} arguments") from error
    return callback


def _call(callback, *args):
    result = callback(*args)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise TypeError("component callbacks must be synchronous")


def run_callbacks(*, name, handler, sdl_root, input_types, output_types,
                  context_type=CallbackContext, reader=None, writer=None, parameter_types=None):
    """Dispatch one complete scheduler snapshot, finishing only successful ticks."""
    codec = SDL2(sdl_root)
    session = ComponentSession(reader, writer, auto_ready=False, timed_ticks=True)
    if parameter_types:
        context = context_type(session, name, input_types, output_types, codec, parameter_types)
    else:
        context = context_type(session, name, input_types, output_types, codec)
    module = _load_handler(handler)
    start = _callback(module, "on_start", 1)
    stop = _callback(module, "on_stop", 1)
    tick = _callback(module, "on_tick", 2 if input_types else 1, required=not input_types)
    callbacks = {} if tick else {port: _callback(module, f"on_{port}", 2, required=True) for port in input_types}
    try:
        if start:
            _call(start, context)
        context._parameter_writes = False
        session.ready()
        for raw in session.ticks():
            inputs = context._decode(raw)
            context._active = True
            context._parameter_writes = True
            try:
                if tick:
                    _call(tick, context, inputs) if input_types else _call(tick, context)
                else:
                    for port, callback in callbacks.items():
                        if inputs[port] is not None or port in raw:
                            _call(callback, context, inputs[port])
            finally:
                context._active = False
                context._parameter_writes = False
    finally:
        context._parameter_writes = False
        if stop:
            _call(stop, context)
