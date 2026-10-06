"""Small dependency-free runtime for SDL generated Python messages."""

import os
import struct
import json
from enum import IntEnum


_WIRE_ENDIAN = os.environ.get('SDL_WIRE_ENDIAN', 'big').lower()
if _WIRE_ENDIAN not in ('big', 'little'):
   raise RuntimeError('SDL_WIRE_ENDIAN must be "big" or "little"')
_WIRE_PREFIX = '<' if _WIRE_ENDIAN == 'little' else '>'
_MAX_DESCRIPTOR_SIZE = 1024 * 1024
_PRIMITIVE_WIRE_SIZES = {
   'bool': 1, 'int8': 1, 'int16': 2, 'int32': 4, 'int64': 8,
   'fl32': 4, 'fl64': 8, 'c32': 8, 'c64': 16,
}
_BUILTIN_TYPES = frozenset(tuple(_PRIMITIVE_WIRE_SIZES) + ('string',))


class CodecError(ValueError):
   """Raised when an SDL wire payload is malformed or has the wrong type."""


class Complex32:
   def __init__(self, real=0.0, imag=0.0):
      self.real = real
      self.imag = imag

   def __eq__(self, other):
      return isinstance(other, Complex32) and self.real == other.real and self.imag == other.imag

   def __repr__(self):
      return 'Complex32(real={!r}, imag={!r})'.format(self.real, self.imag)


class Complex64:
   def __init__(self, real=0.0, imag=0.0):
      self.real = real
      self.imag = imag

   def __eq__(self, other):
      return isinstance(other, Complex64) and self.real == other.real and self.imag == other.imag

   def __repr__(self):
      return 'Complex64(real={!r}, imag={!r})'.format(self.real, self.imag)


class SdlDynamicEnum:
   """Language-neutral enum value decoded from a wire descriptor."""

   def __init__(self, type_name, value, name=None):
      self.type_name = type_name
      self.value = value
      self.name = name

   def __eq__(self, other):
      return (isinstance(other, SdlDynamicEnum) and
         (self.type_name, self.value, self.name) ==
         (other.type_name, other.value, other.name))

   def __repr__(self):
      return 'SdlDynamicEnum({!r}, {!r}, {!r})'.format(
         self.type_name, self.value, self.name)


class SdlDynamicMessage:
   """Neutral message value whose shape comes from its wire descriptor."""

   def __init__(self, type_name, fields):
      self.type_name = type_name
      self.fields = fields

   def __eq__(self, other):
      return (isinstance(other, SdlDynamicMessage) and
         self.type_name == other.type_name and self.fields == other.fields)

   def __repr__(self):
      return 'SdlDynamicMessage({!r}, {!r})'.format(self.type_name, self.fields)


def default_value(type_name, namespace):
   defaults = {
      'bool': False,
      'int8': 0,
      'int16': 0,
      'int32': 0,
      'int64': 0,
      'fl32': 0.0,
      'fl64': 0.0,
      'c32': Complex32(),
      'c64': Complex64(),
      'string': '',
   }
   if type_name in defaults:
      value = defaults[type_name]
      if type_name in ('c32', 'c64'):
         return type(value)()
      return value
   value_type = namespace.get(type_name)
   if value_type is not None and hasattr(value_type, '_SDL_FIELDS'):
      return None
   if value_type is not None and hasattr(value_type, '__members__'):
      return next(iter(value_type))
   return None


def _pack_value(type_name, value):
   formats = {
      'int8': _WIRE_PREFIX + 'b', 'int16': _WIRE_PREFIX + 'h',
      'int32': _WIRE_PREFIX + 'i', 'int64': _WIRE_PREFIX + 'q',
      'fl32': _WIRE_PREFIX + 'f', 'fl64': _WIRE_PREFIX + 'd',
   }
   if type_name == 'bool':
      return b'\x01' if value else b'\x00'
   if type_name in formats:
      try:
         return struct.pack(formats[type_name], value)
      except (struct.error, TypeError) as error:
         raise CodecError('invalid value for ' + type_name) from error
   if type_name in ('c32', 'c64'):
      fmt = _WIRE_PREFIX + ('ff' if type_name == 'c32' else 'dd')
      try:
         return struct.pack(fmt, value.real, value.imag)
      except (struct.error, AttributeError, TypeError) as error:
         raise CodecError('invalid complex value') from error
   if type_name == 'string':
      if not isinstance(value, str):
         raise CodecError('string field requires str')
      return value.encode('utf-8')
   if hasattr(value, '_SDL_FIELDS'):
      return value.encode_payload()
   if hasattr(value, '__int__'):
      return struct.pack(_WIRE_PREFIX + 'i', int(value))
   raise CodecError('unsupported SDL type: ' + type_name)


def _unpack_value(type_name, payload, namespace):
   formats = {
      'int8': _WIRE_PREFIX + 'b', 'int16': _WIRE_PREFIX + 'h',
      'int32': _WIRE_PREFIX + 'i', 'int64': _WIRE_PREFIX + 'q',
      'fl32': _WIRE_PREFIX + 'f', 'fl64': _WIRE_PREFIX + 'd',
   }
   if type_name == 'bool':
      if len(payload) != 1:
         raise CodecError('invalid boolean length')
      if payload[0] not in (0, 1):
         raise CodecError('invalid boolean value')
      return payload[0] == 1
   if type_name in formats:
      if len(payload) != struct.calcsize(formats[type_name]):
         raise CodecError('invalid scalar length for ' + type_name)
      return struct.unpack(formats[type_name], payload)[0]
   if type_name in ('c32', 'c64'):
      fmt = _WIRE_PREFIX + ('ff' if type_name == 'c32' else 'dd')
      if len(payload) != struct.calcsize(fmt):
         raise CodecError('invalid complex length')
      value = struct.unpack(fmt, payload)
      return (Complex32 if type_name == 'c32' else Complex64)(*value)
   if type_name == 'string':
      try:
         return payload.decode('utf-8')
      except UnicodeDecodeError as error:
         raise CodecError('invalid UTF-8 string') from error
   value_type = namespace.get(type_name)
   if value_type is not None and hasattr(value_type, '_SDL_FIELDS'):
      return value_type.decode_payload(payload)
   if value_type is not None and hasattr(value_type, '__members__'):
      if len(payload) != 4:
         raise CodecError('invalid enum length')
      try:
         return value_type(struct.unpack(_WIRE_PREFIX + 'i', payload)[0])
      except ValueError as error:
         raise CodecError('invalid enum value') from error
   raise CodecError('unknown SDL type: ' + type_name)


def _fixed_wire_size(type_name, namespace, dimensions=()):
   sizes = {
      'bool': 1, 'int8': 1, 'int16': 2, 'int32': 4, 'int64': 8,
      'fl32': 4, 'fl64': 8, 'c32': 8, 'c64': 16,
   }
   if type_name in sizes:
      size = sizes[type_name]
   else:
      value_type = namespace.get(type_name)
      if value_type is not None and hasattr(value_type, '__members__'):
         size = 4
      elif value_type is not None:
         size = getattr(value_type, '_SDL_FIXED_SIZE', None)
      else:
         size = None
   if size is None:
      return None
   for dimension in dimensions:
      size *= dimension
      if size > 0xFFFFFFFF:
         return None
   return size


def _pack_fixed(type_name, value, namespace, dimensions=()):
   if dimensions:
      if not isinstance(value, (list, tuple)) or len(value) != dimensions[0]:
         raise CodecError('fixed array has the wrong length')
      return b''.join(_pack_fixed(type_name, item, namespace, dimensions[1:])
         for item in value)
   value_type = namespace.get(type_name)
   if value_type is not None and hasattr(value_type, '_SDL_FIXED_SIZE'):
      output = bytearray()
      for unused_id, name, modifier, field_type, field_dimensions in value_type._SDL_FIELDS:
         if modifier != 'required':
            raise CodecError('fixed-size struct contains a non-required field')
         output.extend(_pack_fixed(field_type, getattr(value, name), namespace,
            field_dimensions))
      if len(output) != value_type._SDL_FIXED_SIZE:
         raise CodecError('fixed-size struct payload has an invalid size')
      return bytes(output)
   return _pack_value(type_name, value)


def _unpack_fixed(type_name, payload, namespace, dimensions=()):
   if dimensions:
      element_size = _fixed_wire_size(type_name, namespace, dimensions[1:])
      if element_size is None or len(payload) != element_size * dimensions[0]:
         raise CodecError('invalid fixed array length')
      return [_unpack_fixed(type_name,
         payload[index * element_size:(index + 1) * element_size], namespace,
         dimensions[1:]) for index in range(dimensions[0])]
   value_type = namespace.get(type_name)
   if value_type is not None and hasattr(value_type, '_SDL_FIXED_SIZE'):
      if len(payload) != value_type._SDL_FIXED_SIZE:
         raise CodecError('invalid fixed-size struct length')
      value = value_type()
      offset = 0
      for unused_id, name, modifier, field_type, field_dimensions in value_type._SDL_FIELDS:
         if modifier != 'required':
            raise CodecError('fixed-size struct contains a non-required field')
         field_size = _fixed_wire_size(field_type, namespace, field_dimensions)
         if field_size is None or field_size > len(payload) - offset:
            raise CodecError('invalid fixed-size struct field')
         setattr(value, name, _unpack_fixed(field_type,
            payload[offset:offset + field_size], namespace, field_dimensions))
         offset += field_size
      return value
   return _unpack_value(type_name, payload, namespace)


def _pack_array_fixed(type_name, values, namespace):
   item_size = _fixed_wire_size(type_name, namespace)
   if item_size is None:
      raise CodecError('packed field type has no fixed wire size')
   if len(values) > 0xFFFFFFFF // item_size:
      raise CodecError('packed field exceeds uint32 length')
   return b''.join(_pack_fixed(type_name, value, namespace) for value in values)


def _default_fixed_array(type_name, dimensions, namespace):
   if not dimensions:
      value = default_value(type_name, namespace)
      value_type = namespace.get(type_name)
      if value is None and value_type is not None and hasattr(value_type, '_SDL_FIELDS'):
         return value_type()
      return value
   return [_default_fixed_array(type_name, dimensions[1:], namespace)
      for unused_index in range(dimensions[0])]


def _unpack_array_fixed(type_name, payload, namespace):
   item_size = _fixed_wire_size(type_name, namespace)
   if item_size is None or len(payload) % item_size != 0:
      raise CodecError('invalid packed field length')
   return [_unpack_fixed(type_name, payload[offset:offset + item_size], namespace)
      for offset in range(0, len(payload), item_size)]


class SdlMessage:
   """Base class used by generated SDL message dataclasses."""

   _SDL_FIELDS = ()
   _SDL_HASH = 0

   def __init__(self, *args, **kwargs):
      if len(args) > len(self._SDL_FIELDS):
         raise TypeError('too many positional message arguments')
      for index, field in enumerate(self._SDL_FIELDS):
         unused_id, name, modifier, type_name, dimensions = field
         if name in kwargs and index < len(args):
            raise TypeError('field supplied more than once: ' + name)
         if index < len(args):
            value = args[index]
         elif name in kwargs:
            value = kwargs.pop(name)
         elif dimensions:
            value = _default_fixed_array(type_name, dimensions,
               __import__(self.__class__.__module__, fromlist=['*']).__dict__)
         elif modifier == 'optional':
            value = None
         elif modifier in ('repeated', 'packed'):
            value = []
         else:
            value = default_value(type_name, self.__class__.__module__ and
               __import__(self.__class__.__module__, fromlist=['*']).__dict__)
         setattr(self, name, value)
      if kwargs:
         raise TypeError('unknown message field: ' + next(iter(kwargs)))

   def __eq__(self, other):
      return (type(self) is type(other) and
         all(getattr(self, field[1]) == getattr(other, field[1])
            for field in self._SDL_FIELDS))

   def __repr__(self):
      values = ', '.join(field[1] + '=' + repr(getattr(self, field[1]))
         for field in self._SDL_FIELDS)
      return self.__class__.__name__ + '(' + values + ')'

   def display(self, indent_width=3):
      """Return this SDL object as a readable structure with fixed-width indentation."""
      return display(self, indent_width)

   def encode_payload(self):
      output = bytearray()
      namespace = __import__(self.__class__.__module__, fromlist=['*']).__dict__
      for field_id, name, modifier, type_name, dimensions in self._SDL_FIELDS:
         value = getattr(self, name)
         if modifier == 'packed':
            if not value:
               continue
            payload = _pack_array_fixed(type_name, value, namespace)
            output.extend(struct.pack(_WIRE_PREFIX + 'II', field_id, len(payload)))
            output.extend(payload)
            continue
         if dimensions:
            payload = _pack_fixed(type_name, value, namespace, dimensions)
            output.extend(struct.pack(_WIRE_PREFIX + 'II', field_id, len(payload)))
            output.extend(payload)
            continue
         if modifier == 'optional':
            if value is None:
               continue
            values = (value,)
         elif modifier == 'repeated':
            values = value
         else:
            values = (value,)
         for item in values:
            payload = _pack_value(type_name, item)
            if len(payload) > 0xFFFFFFFF:
               raise CodecError('field payload exceeds uint32 length')
            output.extend(struct.pack(_WIRE_PREFIX + 'II', field_id, len(payload)))
            output.extend(payload)
      return bytes(output)

   @classmethod
   def decode_payload(cls, payload):
      value = cls()
      fields = {field[0]: field for field in cls._SDL_FIELDS}
      offset = 0
      while offset < len(payload):
         if len(payload) - offset < 8:
            raise CodecError('truncated field header')
         field_id, length = struct.unpack_from(_WIRE_PREFIX + 'II', payload, offset)
         offset += 8
         if length > len(payload) - offset:
            raise CodecError('truncated field payload')
         field = fields.get(field_id)
         if field is not None:
            unused_id, name, modifier, type_name, dimensions = field
            namespace = __import__(cls.__module__, fromlist=['*']).__dict__
            if modifier == 'packed':
               setattr(value, name, _unpack_array_fixed(type_name,
                  payload[offset:offset + length], namespace))
            elif dimensions:
               setattr(value, name, _unpack_fixed(type_name,
                  payload[offset:offset + length], namespace, dimensions))
            elif modifier == 'repeated':
               item = _unpack_value(type_name, payload[offset:offset + length], namespace)
               getattr(value, name).append(item)
            else:
               item = _unpack_value(type_name, payload[offset:offset + length], namespace)
               setattr(value, name, item)
         offset += length
      return value


def encode(message):
   if not isinstance(message, SdlMessage):
      raise CodecError('encode expects an SDL message')
   descriptor = message._SDL_DESCRIPTOR
   if len(descriptor) > 0xFFFFFFFF:
      raise CodecError('schema descriptor exceeds uint32 length')
   return (struct.pack(_WIRE_PREFIX + 'I', len(descriptor)) + descriptor +
      struct.pack(_WIRE_PREFIX + 'I', message._SDL_HASH) + message.encode_payload())


def decode(wire, message_type):
   if len(wire) < 8:
      raise CodecError('truncated message descriptor')
   descriptor_size = struct.unpack_from(_WIRE_PREFIX + 'I', wire)[0]
   if descriptor_size > len(wire) - 8:
      raise CodecError('truncated message descriptor')
   descriptor = wire[4:4 + descriptor_size]
   if descriptor != message_type._SDL_DESCRIPTOR:
      raise CodecError('message schema descriptor mismatch')
   hash_offset = 4 + descriptor_size
   type_hash = struct.unpack_from(_WIRE_PREFIX + 'I', wire, hash_offset)[0]
   if type_hash != message_type._SDL_HASH:
      raise CodecError('message type hash mismatch')
   return message_type.decode_payload(wire[hash_offset + 4:])


def _fnv1a_32_bytes(data):
   value = 2166136261
   for byte in data:
      value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
   return value


def _read_descriptor_frame(wire):
   if len(wire) < 8:
      raise CodecError('truncated message descriptor')
   descriptor_size = struct.unpack_from(_WIRE_PREFIX + 'I', wire)[0]
   if descriptor_size > _MAX_DESCRIPTOR_SIZE or descriptor_size > len(wire) - 8:
      raise CodecError('invalid or truncated message descriptor length')
   descriptor_bytes = wire[4:4 + descriptor_size]
   try:
      descriptor = _decode_binary_descriptor(descriptor_bytes)
   except (UnicodeDecodeError, ValueError, IndexError) as error:
      raise CodecError('invalid schema descriptor') from error
   hash_offset = 4 + descriptor_size
   type_hash = struct.unpack_from(_WIRE_PREFIX + 'I', wire, hash_offset)[0]
   if type_hash != _fnv1a_32_bytes(descriptor_bytes):
      raise CodecError('schema descriptor hash mismatch')
   messages, enums = _validate_dynamic_descriptor(descriptor)
   return descriptor['root'], messages, enums, wire[hash_offset + 4:]


class _DescriptorReader:
   def __init__(self, data):
      self.data = data
      self.offset = 0

   def read(self, size):
      if size < 0 or size > len(self.data) - self.offset:
         raise ValueError('truncated descriptor')
      value = self.data[self.offset:self.offset + size]
      self.offset += size
      return value

   def read_u8(self):
      return self.read(1)[0]

   def read_u16(self):
      return int.from_bytes(self.read(2), 'big')

   def read_u32(self):
      return int.from_bytes(self.read(4), 'big')

   def read_i32(self):
      return int.from_bytes(self.read(4), 'big', signed=True)

   def read_text(self):
      size = self.read_u16()
      return self.read(size).decode('utf-8')


def _decode_binary_descriptor(data):
   reader = _DescriptorReader(data)
   if reader.read(4) != b'SDD1':
      raise ValueError('unknown descriptor version')
   root = reader.read_text()
   messages = []
   previous_message_name = None
   for unused_message_index in range(reader.read_u16()):
      name = reader.read_text()
      if previous_message_name is not None and name <= previous_message_name:
         raise ValueError('message declarations are not ordered')
      previous_message_name = name
      fields = []
      previous_field_id = 0
      for unused_field_index in range(reader.read_u16()):
         field_id = reader.read_u32()
         if field_id <= previous_field_id:
            raise ValueError('message fields are not ordered')
         previous_field_id = field_id
         field_name = reader.read_text()
         modifier_code = reader.read_u8()
         if modifier_code > 3:
            raise ValueError('invalid field modifier')
         type_name = reader.read_text()
         dimensions = [reader.read_u32() for unused_dimension in
            range(reader.read_u8())]
         fields.append({
            'id': field_id,
            'name': field_name,
            'modifier': ('required', 'optional', 'repeated', 'packed')[modifier_code],
            'type': type_name,
            'dimensions': dimensions,
         })
      messages.append([name, fields])
   enums = []
   previous_enum_name = None
   for unused_enum_index in range(reader.read_u16()):
      name = reader.read_text()
      if previous_enum_name is not None and name <= previous_enum_name:
         raise ValueError('enum declarations are not ordered')
      previous_enum_name = name
      pairs = [[reader.read_text(), reader.read_i32()]
         for unused_value_index in range(reader.read_u16())]
      enums.append([name, pairs])
   if reader.offset != len(data):
      raise ValueError('trailing descriptor bytes')
   return {'format': 'SDL-DESC-1', 'root': root,
      'messages': messages, 'enums': enums}


def _validate_dynamic_descriptor(descriptor):
   if (not isinstance(descriptor, dict) or
         set(descriptor) != {'format', 'root', 'messages', 'enums'} or
         descriptor.get('format') != 'SDL-DESC-1' or
         not isinstance(descriptor.get('root'), str) or
         not descriptor.get('root') or
         not isinstance(descriptor.get('messages'), list) or
         not isinstance(descriptor.get('enums'), list)):
      raise CodecError('unsupported schema descriptor shape')
   messages = {}
   enums = {}
   previous_message_name = None
   for entry in descriptor['messages']:
      if (not isinstance(entry, list) or len(entry) != 2 or
            not isinstance(entry[0], str) or not entry[0] or
            not isinstance(entry[1], list) or
            entry[0] in messages):
         raise CodecError('invalid message declaration in descriptor')
      if previous_message_name is not None and entry[0] <= previous_message_name:
         raise CodecError('message declarations are not canonically ordered')
      previous_message_name = entry[0]
      fields = []
      ids = set()
      names = set()
      previous_field_id = 0
      for field in entry[1]:
         if (not isinstance(field, dict) or set(field) !=
               {'id', 'name', 'modifier', 'type', 'dimensions'}):
            raise CodecError('invalid field declaration in descriptor')
         field_id = field['id']
         name = field.get('name', '')
         modifier = field['modifier']
         type_name = field['type']
         dimensions = field['dimensions']
         if (type(field_id) is not int or field_id <= 0 or field_id > 0xFFFFFFFF or
               not isinstance(name, str) or not name or name in names or field_id in ids or
               modifier not in ('required', 'optional', 'repeated', 'packed') or
               not isinstance(type_name, str) or not isinstance(dimensions, list) or
               any(type(item) is not int or item <= 0 or item > 0xFFFFFFFF
                  for item in dimensions)):
            raise CodecError('invalid field metadata in descriptor')
         if field_id <= previous_field_id:
            raise CodecError('message fields are not canonically ordered')
         previous_field_id = field_id
         if dimensions and modifier != 'required':
            raise CodecError('fixed array has an invalid modifier')
         ids.add(field_id)
         names.add(name)
         fields.append(field)
      messages[entry[0]] = sorted(fields, key=lambda item: item['id'])
   previous_enum_name = None
   for entry in descriptor['enums']:
      if (not isinstance(entry, list) or len(entry) != 2 or
            not isinstance(entry[0], str) or not entry[0] or
            not isinstance(entry[1], list) or
            entry[0] in enums):
         raise CodecError('invalid enum declaration in descriptor')
      if previous_enum_name is not None and entry[0] <= previous_enum_name:
         raise CodecError('enum declarations are not canonically ordered')
      previous_enum_name = entry[0]
      values = {}
      names = set()
      for pair in entry[1]:
         if (not isinstance(pair, list) or len(pair) != 2 or
               not isinstance(pair[0], str) or not pair[0] or
               type(pair[1]) is not int or
               pair[1] < -0x80000000 or pair[1] > 0x7FFFFFFF or
               pair[0] in names or pair[1] in values):
            raise CodecError('invalid enum value in descriptor')
         names.add(pair[0])
         values[pair[1]] = pair[0]
      enums[entry[0]] = values
   if descriptor['root'] not in messages:
      raise CodecError('descriptor root message is missing')
   if set(messages).intersection(enums):
      raise CodecError('descriptor type name is ambiguous')

   known_types = _BUILTIN_TYPES | set(messages) | set(enums)
   for type_name, fields in messages.items():
      for field in fields:
         if field['type'] not in known_types:
            raise CodecError('field refers to an unknown descriptor type')
         if field['dimensions']:
            fixed_size = _dynamic_type_fixed_size(field['type'], messages, enums)
            if fixed_size is None:
               raise CodecError('fixed array element type has variable wire size')
            for dimension in field['dimensions']:
               if fixed_size > 0xFFFFFFFF // dimension:
                  raise CodecError('fixed array wire size overflows')
               fixed_size *= dimension
         if field['modifier'] == 'packed' and _dynamic_type_fixed_size(
               field['type'], messages, enums) is None:
            raise CodecError('packed field type has variable wire size')

   visiting = set()
   visited = set()

   def visit(type_name, depth):
      if depth > 64:
         raise CodecError('descriptor type nesting is too deep')
      if type_name in visited or type_name in _BUILTIN_TYPES or type_name in enums:
         return
      if type_name in visiting:
         raise CodecError('recursive descriptor types are unsupported')
      visiting.add(type_name)
      for field in messages[type_name]:
         if field['type'] in messages:
            visit(field['type'], depth + 1)
      visiting.remove(type_name)
      visited.add(type_name)

   for type_name in messages:
      visit(type_name, 0)
   return messages, enums


def _dynamic_field_size(field, messages, enums, active=None):
   size = _dynamic_type_fixed_size(field['type'], messages, enums, active)
   if size is None:
      return None
   for dimension in field['dimensions']:
      if size > 0xFFFFFFFF // dimension:
         return None
      size *= dimension
   return size


def _dynamic_type_fixed_size(type_name, messages, enums, active=None):
   if type_name in _PRIMITIVE_WIRE_SIZES:
      return _PRIMITIVE_WIRE_SIZES[type_name]
   if type_name == 'string':
      return None
   if type_name in enums:
      return 4
   if type_name not in messages:
      return None
   if active is None:
      active = set()
   if type_name in active or len(active) >= 64 or not messages[type_name]:
      return None
   active = set(active)
   active.add(type_name)
   total = 0
   for field in messages[type_name]:
      if field['modifier'] != 'required':
         return None
      field_size = _dynamic_field_size(field, messages, enums, active)
      if field_size is None or total > 0xFFFFFFFF - field_size:
         return None
      total += field_size
   return total


def _dynamic_decode_message(type_name, payload, messages, enums, depth=0):
   if depth > 64:
      raise CodecError('message nesting is too deep')
   fields = messages.get(type_name)
   if fields is None:
      raise CodecError('unknown message type in descriptor')
   by_id = {field['id']: field for field in fields}
   values = {field['name']: ([] if field['modifier'] in ('repeated', 'packed')
      else None) for field in fields}
   offset = 0
   while offset < len(payload):
      if len(payload) - offset < 8:
         raise CodecError('truncated field header')
      field_id, length = struct.unpack_from(_WIRE_PREFIX + 'II', payload, offset)
      offset += 8
      if length > len(payload) - offset:
         raise CodecError('truncated field payload')
      field = by_id.get(field_id)
      if field is not None:
         part = payload[offset:offset + length]
         modifier = field['modifier']
         field_type = field['type']
         if modifier == 'packed':
            item_size = _dynamic_type_fixed_size(field_type, messages, enums)
            if item_size is None or len(part) % item_size != 0:
               raise CodecError('invalid packed field length')
            item_values = [_dynamic_decode_fixed(field_type, part[index:index + item_size],
               messages, enums) for index in range(0, len(part), item_size)]
            values[field['name']].extend(item_values)
         elif field['dimensions']:
            values[field['name']] = _dynamic_decode_fixed(field_type, part,
               messages, enums, field['dimensions'])
         else:
            item = _dynamic_decode_value(field_type, part, messages, enums, depth + 1)
            if modifier == 'repeated':
               values[field['name']].append(item)
            else:
               values[field['name']] = item
      offset += length
   return SdlDynamicMessage(type_name, values)


def _dynamic_decode_value(type_name, payload, messages, enums, depth):
   if type_name in enums:
      if len(payload) != 4:
         raise CodecError('invalid enum length')
      number = struct.unpack(_WIRE_PREFIX + 'i', payload)[0]
      values = enums[type_name]
      if number not in values:
         raise CodecError('invalid enum value')
      return SdlDynamicEnum(type_name, number, values[number])
   if type_name in messages:
      return _dynamic_decode_message(type_name, payload, messages, enums, depth)
   return _unpack_value(type_name, payload, {})


def _dynamic_decode_fixed(type_name, payload, messages, enums, dimensions=(), depth=0):
   if depth > 64:
      raise CodecError('fixed value nesting is too deep')
   item_size = _dynamic_type_fixed_size(type_name, messages, enums)
   if item_size is None:
      raise CodecError('fixed array element type has variable wire size')
   expected = item_size
   for dimension in dimensions:
      if expected > 0xFFFFFFFF // dimension:
         raise CodecError('fixed array size overflows')
      expected *= dimension
   if len(payload) != expected:
      raise CodecError('invalid fixed value length')

   def decode_dimension(offset, remaining_dimensions):
      if not remaining_dimensions:
         value = _dynamic_decode_fixed_plain(type_name,
            payload[offset:offset + item_size], messages, enums, depth + 1)
         return value, offset + item_size
      values = []
      for unused_index in range(remaining_dimensions[0]):
         value, offset = decode_dimension(offset, remaining_dimensions[1:])
         values.append(value)
      return values, offset

   if dimensions:
      value, end = decode_dimension(0, dimensions)
      if end != len(payload):
         raise CodecError('invalid fixed array size')
      return value
   return _dynamic_decode_fixed_plain(type_name, payload, messages, enums, depth + 1)


def _dynamic_decode_fixed_plain(type_name, payload, messages, enums, depth):
   if type_name in enums:
      number = struct.unpack(_WIRE_PREFIX + 'i', payload)[0]
      values = enums[type_name]
      if number not in values:
         raise CodecError('invalid enum value')
      return SdlDynamicEnum(type_name, number, values[number])
   if type_name in messages:
      fields = messages[type_name]
      values = {}
      offset = 0
      for field in fields:
         field_size = _dynamic_field_size(field, messages, enums)
         if field_size is None or field_size > len(payload) - offset:
            raise CodecError('invalid fixed struct field')
         part = payload[offset:offset + field_size]
         values[field['name']] = _dynamic_decode_fixed(type_name=field['type'],
            payload=part, messages=messages, enums=enums,
            dimensions=field['dimensions'], depth=depth + 1)
         offset += field_size
      if offset != len(payload):
         raise CodecError('invalid fixed struct size')
      return SdlDynamicMessage(type_name, values)
   return _unpack_value(type_name, payload, {})


def decode_dynamic(wire):
   """Decode a message into neutral values using only its wire descriptor."""
   root, messages, enums, payload = _read_descriptor_frame(wire)
   return _dynamic_decode_message(root, payload, messages, enums)


def _display_indent(depth, indent_width):
   return ' ' * (depth * indent_width)


def _display_sequence(values, indent_width, depth):
   if not values:
      return '[]'
   lines = ['[']
   for index, value in enumerate(values):
      comma = ',' if index + 1 < len(values) else ''
      lines.append(_display_indent(depth + 1, indent_width) +
         _display_value(value, indent_width, depth + 1) + comma)
   lines.append(_display_indent(depth, indent_width) + ']')
   return '\n'.join(lines)


def _display_message(message, indent_width, depth):
   lines = [message.__class__.__name__ + ' {']
   for unused_id, name, unused_modifier, unused_type, unused_dimensions in message._SDL_FIELDS:
      lines.append(_display_indent(depth + 1, indent_width) + name + ': ' +
         _display_value(getattr(message, name), indent_width, depth + 1))
   lines.append(_display_indent(depth, indent_width) + '}')
   return '\n'.join(lines)


def _display_value(value, indent_width, depth):
   if isinstance(value, SdlMessage):
      return _display_message(value, indent_width, depth)
   if isinstance(value, IntEnum):
      return value.name
   if isinstance(value, (Complex32, Complex64)):
      return '({}, {})'.format(value.real, value.imag)
   if isinstance(value, (list, tuple)):
      return _display_sequence(value, indent_width, depth)
   if value is None:
      return 'null'
   if isinstance(value, str):
      return json.dumps(value, ensure_ascii=False)
   if isinstance(value, bool):
      return 'true' if value else 'false'
   return repr(value)


def display(message, indent_width=3):
   """Format a generated SDL message in memory using `indent_width` spaces per level."""
   if not isinstance(message, SdlMessage):
      raise CodecError('display expects an SDL message')
   if isinstance(indent_width, bool) or not isinstance(indent_width, int) or indent_width < 0:
      raise ValueError('indent_width must be a nonnegative integer')
   return _display_message(message, indent_width, 0) + '\n'
