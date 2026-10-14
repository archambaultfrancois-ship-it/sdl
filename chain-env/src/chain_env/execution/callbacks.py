"""Synchronous typed callbacks used by generated Python component containers."""
from collections import OrderedDict
import importlib.util
import inspect
from pathlib import Path
import sys
from chain_env.codec.sdl2 import SDL2
from chain_env.execution.component import ComponentSession


class CallbackContext:
    """Component parameters and typed publication, valid during a scheduled tick."""
    def __init__(self, session, name, input_types, output_types, codec):
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
        self._active = False
        self._decoders = OrderedDict()
        self._encoders = {port: codec.api.prepare(self.config["outputs"][port]["catalogue"], [cls])
                          for port, cls in output_types.items()}

    def publish(self, port, message):
        if not self._active:
            raise RuntimeError("publication is only allowed inside a tick callback")
        if port not in self.output_types:
            raise ValueError(f"unknown output port: {port}")
        if not isinstance(message, self.output_types[port]):
            raise TypeError(f"output {port} expects {self.output_types[port].__name__}")
        self.session.publish(port, self.codec.api.encode(self._encoders[port], message))

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
    spec = importlib.util.spec_from_file_location("_chain_component_handler", path)
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
                  context_type=CallbackContext, reader=None, writer=None):
    """Dispatch one complete scheduler snapshot, finishing only successful ticks."""
    codec = SDL2(sdl_root)
    session = ComponentSession(reader, writer, auto_ready=False)
    context = context_type(session, name, input_types, output_types, codec)
    module = _load_handler(handler)
    start = _callback(module, "on_start", 1)
    stop = _callback(module, "on_stop", 1)
    tick = _callback(module, "on_tick", 2 if input_types else 1, required=not input_types)
    callbacks = {} if tick else {port: _callback(module, f"on_{port}", 2, required=True) for port in input_types}
    try:
        if start:
            _call(start, context)
        session.ready()
        for raw in session.ticks():
            inputs = context._decode(raw)
            context._active = True
            try:
                if tick:
                    _call(tick, context, inputs) if input_types else _call(tick, context)
                else:
                    for port, callback in callbacks.items():
                        if inputs[port] is not None:
                            _call(callback, context, inputs[port])
            finally:
                context._active = False
    finally:
        if stop:
            _call(stop, context)
