"""JSON conversion using the same SDL catalogue and codecs as typed components."""
from enum import IntEnum
import math


class Values:
    def __init__(self, api, catalogue, root):
        self.api, self.catalogue, self.root = api, catalogue, root
        shape = api.prepare(catalogue)
        if root not in api.message_names(shape):
            raise ValueError(f"unknown parameter root: {root}")
        self.messages, self.enums = shape.messages, shape.enums
        namespace = {name: IntEnum('ParameterEnum', {label: value for value, label in pairs.items()})
                     for name, pairs in self.enums.items()}
        for name, fields in self.messages.items():
            namespace[name] = type('ParameterMessage', (api.SdlMessage,), {
                '_SDL_NAME': name, '_SDL_DESCRIPTOR': catalogue, '_SDL_TYPES': namespace,
                '_SDL_FIELDS': tuple((f['id'], f['name'], f['modifier'], f['type'], tuple(f['dimensions'])) for f in fields)})
        self.types = namespace
        self.context = api.prepare(catalogue, [namespace[name] for name in self.messages])

    def _value(self, name, value, dimensions=()):
        if dimensions:
            if not isinstance(value, list) or len(value) != dimensions[0]:
                raise ValueError('invalid parameter array dimensions')
            return [self._value(name, item, dimensions[1:]) for item in value]
        if name in self.messages:
            if not isinstance(value, dict):
                raise ValueError(f'{name} requires an object')
            fields = self.messages[name]
            if set(value) - {f['name'] for f in fields}:
                raise ValueError(f'unknown parameter fields in {name}')
            result = {}
            for field in fields:
                key, modifier = field['name'], field['modifier']
                if key not in value:
                    if modifier == 'optional':
                        result[key] = None
                        continue
                    raise ValueError(f'missing parameter field: {name}.{key}')
                item = value[key]
                if item is None and modifier == 'optional':
                    result[key] = None
                elif modifier in ('repeated', 'packed'):
                    if not isinstance(item, list):
                        raise ValueError(f'{name}.{key} requires an array')
                    result[key] = [self._value(field['type'], v, field['dimensions']) for v in item]
                else:
                    result[key] = self._value(field['type'], item, field['dimensions'])
            return self.types[name](**result)
        if name in self.enums:
            if isinstance(value, str):
                return self.types[name][value]
            if type(value) is not int:
                raise ValueError(f'{name} requires an enum label or integer')
            return self.types[name](value)
        if name == 'bool':
            valid = type(value) is bool
        elif name.startswith(('int', 'uint')):
            valid = type(value) is int
        elif name in ('fl32', 'fl64'):
            valid = type(value) in (int, float) and math.isfinite(value)
        elif name == 'string':
            valid = isinstance(value, str)
        elif name in ('c32', 'c64'):
            if not isinstance(value, dict) or set(value) != {'real', 'imag'}:
                raise ValueError('complex parameters require real and imag')
            return getattr(self.api, 'Complex32' if name == 'c32' else 'Complex64')(
                self._value('fl64', value['real']), self._value('fl64', value['imag']))
        else:
            valid = False
        if not valid:
            raise ValueError(f'invalid parameter value for {name}')
        return value

    def encode(self, value):
        try:
            return self.api.encode(self.context, self._value(self.root, value))
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError(f'invalid parameter value: {error}') from error

    def decode(self, payload):
        message = self.api.decode_dynamic(self.context, payload)
        if message.type_name != self.root:
            raise ValueError('wrong parameter message root')
        def plain(value):
            if isinstance(value, self.api.SdlDynamicMessage):
                return {key: plain(item) for key, item in value.fields.items()}
            if isinstance(value, self.api.SdlDynamicEnum):
                return value.name
            if isinstance(value, (self.api.Complex32, self.api.Complex64)):
                return dict(real=value.real, imag=value.imag)
            if isinstance(value, (list, tuple)):
                return [plain(item) for item in value]
            return value
        return plain(message)
