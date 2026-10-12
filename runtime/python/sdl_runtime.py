"""Small dependency-free runtime for SDL generated Python messages."""

import os
import struct


_WIRE_ENDIAN = os.environ.get('SDL_WIRE_ENDIAN', 'big').lower()
if _WIRE_ENDIAN not in ('big', 'little'):
   raise RuntimeError('SDL_WIRE_ENDIAN must be "big" or "little"')
_WIRE_PREFIX = '<' if _WIRE_ENDIAN == 'little' else '>'


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
   return struct.pack(_WIRE_PREFIX + 'I', message._SDL_HASH) + message.encode_payload()


def decode(wire, message_type):
   if len(wire) < 4:
      raise CodecError('truncated message hash')
   type_hash = struct.unpack_from(_WIRE_PREFIX + 'I', wire)[0]
   if type_hash != message_type._SDL_HASH:
      raise CodecError('message type hash mismatch')
   return message_type.decode_payload(wire[4:])
