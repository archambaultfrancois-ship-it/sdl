"""Small dependency-free runtime for SDL generated Python messages."""

import re
import struct
import json
import os
import math
import operator
import sys

try:
   import _sdl_native
except ImportError:
   _sdl_native = None
if _sdl_native is not None and getattr(_sdl_native, 'API_VERSION', 0) != 2:
   _sdl_native = None  # A stale binary must not break the ordinary Python API.
from enum import IntEnum


_WIRE_PREFIX = '>'
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


def _pack_value(type_name, value, namespace=None):
   formats = {
      'int8': _WIRE_PREFIX + 'b', 'int16': _WIRE_PREFIX + 'h',
      'int32': _WIRE_PREFIX + 'i', 'int64': _WIRE_PREFIX + 'q',
      'fl32': _WIRE_PREFIX + 'f', 'fl64': _WIRE_PREFIX + 'd',
   }
   if type_name == 'bool':
      if not isinstance(value, bool):
         raise CodecError('boolean field requires bool')
      return b'\x01' if value else b'\x00'
   if type_name in formats:
      try:
         return struct.pack(formats[type_name], value)
      except (struct.error, TypeError, OverflowError) as error:
         raise CodecError('invalid value for ' + type_name) from error
   if type_name in ('c32', 'c64'):
      fmt = _WIRE_PREFIX + ('ff' if type_name == 'c32' else 'dd')
      try:
         return struct.pack(fmt, value.real, value.imag)
      except (struct.error, AttributeError, TypeError, OverflowError) as error:
         raise CodecError('invalid complex value') from error
   if type_name == 'string':
      if not isinstance(value, str):
         raise CodecError('string field requires str')
      try:
         return value.encode('utf-8')
      except UnicodeEncodeError as error:
         raise CodecError('string contains an invalid Unicode scalar value') from error
   value_type = namespace.get(type_name) if namespace is not None else None
   if value_type is not None and hasattr(value_type, '__members__'):
      if not isinstance(value, value_type):
         raise CodecError('invalid enum value for ' + type_name)
      return struct.pack(_WIRE_PREFIX + 'i', int(value))
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
   if value_type is not None and hasattr(value_type, '__members__'):
      if len(payload) != 4:
         raise CodecError('invalid enum length')
      try:
         return value_type(struct.unpack(_WIRE_PREFIX + 'i', payload)[0])
      except ValueError as error:
         raise CodecError('invalid enum value') from error
   raise CodecError('unknown SDL type: ' + type_name)


class SdlMessage:
   """Base class used by generated SDL message classes."""

   _SDL_FIELDS = ()
   _SDL_NAME = None

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

def _validate_dynamic_descriptor(descriptor):
   if (not isinstance(descriptor, dict) or
         set(descriptor) != {'messages', 'enums'} or
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
            not isinstance(entry[1], list) or not entry[1] or
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
   if not messages:
      raise CodecError('catalogue must contain a message')
   if (set(messages).intersection(enums) or
         set(messages).intersection(_BUILTIN_TYPES) or
         set(enums).intersection(_BUILTIN_TYPES)):
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
   visited = {}

   def visit(type_name, depth):
      if depth > 64:
         raise CodecError('descriptor type nesting is too deep')
      if type_name in visited:
         if depth + visited[type_name] > 64:
            raise CodecError('descriptor type nesting is too deep')
         return visited[type_name]
      if type_name in _BUILTIN_TYPES or type_name in enums:
         return 0
      if type_name in visiting:
         raise CodecError('recursive descriptor types are unsupported')
      visiting.add(type_name)
      height = 0
      for field in messages[type_name]:
         if field['type'] in messages:
            height = max(height, 1 + visit(field['type'], depth + 1))
      if depth + height > 64:
         raise CodecError('descriptor type nesting is too deep')
      visiting.remove(type_name)
      visited[type_name] = height
      return height

   for type_name in messages:
      visit(type_name, 0)
   return messages, enums


def _dynamic_field_size(field, messages, enums, active=None, cache=None):
   size = _dynamic_type_fixed_size(field['type'], messages, enums, active, cache)
   if size is None:
      return None
   for dimension in field['dimensions']:
      if size > 0xFFFFFFFF // dimension:
         return None
      size *= dimension
   return size


def _dynamic_type_fixed_size(type_name, messages, enums, active=None, cache=None):
   if cache is None:
      cache = {}
   if type_name in cache:
      return cache[type_name]
   result = _compute_fixed_size(type_name, messages, enums, active, cache)
   cache[type_name] = result
   return result


def _compute_fixed_size(type_name, messages, enums, active, cache):
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
      field_size = _dynamic_field_size(field, messages, enums, active, cache)
      if field_size is None or total > 0xFFFFFFFF - field_size:
         return None
      total += field_size
   return total


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


_NAME = r'[\w$]+'
_FIELD = re.compile(r'  ([0-9]+): (required|optional|repeated|packed) (' + _NAME +
   r')((?:\[[0-9]+\])*) (' + _NAME + r');')


def _parse_description(text):
   if isinstance(text, bytes):
      try:
         text = text.decode('utf-8')
      except UnicodeError as error:
         raise CodecError('invalid description UTF-8') from error
   try:
      if not isinstance(text, str) or len(text.encode('utf-8')) > _MAX_DESCRIPTOR_SIZE:
         raise CodecError('invalid description size')
   except UnicodeError as error:
      raise CodecError('invalid description UTF-8') from error
   if '\x00' in text or not text.startswith('SDL2\n') or not text.endswith('\n'):
      raise CodecError('expected SDL2 catalogue')
   declarations = {'messages': [], 'enums': []}
   current = None
   for line in text.split('\n')[1:-1]:
      if current is None:
         match = re.fullmatch(r'(message|enum) (' + _NAME + r') \{', line)
         if not match:
            raise CodecError('invalid declaration')
         kind, name = match.groups()
         if len(name.encode('utf-8')) > 65535:
            raise CodecError('identifier exceeds limit')
         key = 'messages' if kind == 'message' else 'enums'
         current = [name, []]
         if len(declarations[key]) >= 65535:
            raise CodecError('too many declarations')
         declarations[key].append(current)
      elif line == '}':
         current = None
      elif key == 'messages':
         match = _FIELD.fullmatch(line)
         if not match:
            raise CodecError('invalid field declaration')
         field_id, modifier, type_name, dimensions, name = match.groups()
         if max(len(name.encode('utf-8')), len(type_name.encode('utf-8'))) > 65535:
            raise CodecError('identifier exceeds limit')
         try:
            dims = [int(n) for n in re.findall(r'\[([0-9]+)\]', dimensions)]
            field_id = int(field_id)
         except ValueError as error:
            raise CodecError('invalid decimal integer') from error
         if len(dims) > 255 or len(current[1]) >= 65535:
            raise CodecError('too many fields or dimensions')
         current[1].append(dict(id=field_id, modifier=modifier,
            type=type_name, dimensions=dims, name=name))
      else:
         match = re.fullmatch(r'  (' + _NAME + r') = (-?[0-9]+);', line)
         if not match:
            raise CodecError('invalid enum declaration')
         if len(current[1]) >= 65535 or len(match[1].encode('utf-8')) > 65535:
            raise CodecError('enum exceeds limit')
         try:
            number = int(match[2])
         except ValueError as error:
            raise CodecError('invalid enum decimal') from error
         current[1].append([match[1], number])
   if current is not None:
      raise CodecError('unclosed declaration')
   return _validate_dynamic_descriptor(declarations)


def _render_description(messages, enums):
   lines = ['SDL2']
   for name in sorted(messages, key=lambda value: value.encode('utf-8')):
      lines.append('message ' + name + ' {')
      for f in messages[name]:
         dims = ''.join('[%d]' % n for n in f['dimensions'])
         lines.append('  %d: %s %s%s %s;' %
            (f['id'], f['modifier'], f['type'], dims, f['name']))
      lines.append('}')
   for name in sorted(enums, key=lambda value: value.encode('utf-8')):
      lines.append('enum ' + name + ' {')
      for value, item in enums[name].items():
         lines.append('  %s = %d;' % (item, value))
      lines.append('}')
   return '\n'.join(lines) + '\n'


def _classes(types):
   if isinstance(types, type):
      types = [types]
   result = {}
   pending = list(types)
   while pending:
      cls = pending.pop()
      if not isinstance(cls, type) or not issubclass(cls, SdlMessage):
         raise CodecError('local_types must contain generated message classes')
      name = cls._SDL_NAME
      if name in result:
         if result[name] is not cls:
            raise CodecError('duplicate local type: ' + name)
         continue
      result[name] = cls
      namespace = __import__(cls.__module__, fromlist=['*']).__dict__
      for unused_id, unused_name, unused_modifier, type_name, unused_dims in cls._SDL_FIELDS:
         child = namespace.get(re.sub(r'\W', '_', type_name))
         if isinstance(child, type) and issubclass(child, SdlMessage):
            pending.append(child)
   return result


def description(types):
   """Return the UTF-8 text catalogue to exchange before any data buffers."""
   messages, enums = {}, {}
   for cls in _classes(types).values():
      m, e = _parse_description(cls._SDL_DESCRIPTOR)
      for target, source in ((messages, m), (enums, e)):
         for name, value in source.items():
            if name in target and target[name] != value:
               raise CodecError('conflicting declaration: ' + name)
            target[name] = value
   text = _render_description(messages, enums)
   _parse_description(text)
   return text


class Context:
   """Prepared catalogue; independent of transport and message buffers."""
   def __init__(self, text, local_types=()):
      self.messages, self.enums = _parse_description(text)
      self.names = tuple(sorted(self.messages, key=lambda name: name.encode('utf-8')))
      self.ids = {name: i + 1 for i, name in enumerate(self.names)}
      self.classes = _classes(local_types)
      self.fixed_sizes = dict(_PRIMITIVE_WIRE_SIZES)
      self.fixed_sizes.update({name: 4 for name in self.enums})
      self.fixed_sizes.update({name: _dynamic_type_fixed_size(name, self.messages, self.enums)
         for name in self.messages})
      self.local_fields = {}
      self.namespaces = {}
      for name, cls in self.classes.items():
         self.local_fields[name] = {f[0]: f for f in cls._SDL_FIELDS}
         self.namespaces[name] = __import__(cls.__module__, fromlist=['*']).__dict__
         for f in self.messages.get(name, ()):
            local = self.local_fields[name].get(f['id'])
            if local and (local[3] != f['type'] or tuple(f['dimensions']) != local[4] or
                  _cardinality(local[2]) != _cardinality(f['modifier'])):
               raise CodecError('incompatible field: %s.%s' % (name, f['name']))
      self.packed_codecs = {}
      for fields in self.messages.values():
         for field in fields:
            name = field['type']
            dimensions = tuple(field['dimensions'])
            key = (name, dimensions)
            if (field['modifier'] in ('packed', 'repeated') and key not in self.packed_codecs
                  and (name in self.messages or name in self.enums or name == 'bool' or dimensions)):
               plan = _fixed_codec(self, name, dimensions)
               if plan is not None:
                  self.packed_codecs[key] = plan
      self.native_packed = {}
      self.native_layouts = {}
      self.native_enabled = _sdl_native is not None and not os.environ.get('SDL_PYTHON_NO_NATIVE')
      if self.native_enabled:
         for fields in self.messages.values():
            for field in fields:
               if field['modifier'] not in ('packed', 'repeated'):
                  continue
               key = (field['type'], tuple(field['dimensions']))
               if key in self.native_packed:
                  continue
               spec = _native_fixed_spec(self, *key)
               if spec is not None:
                  self.native_packed[key] = _sdl_native.prepare(spec)
                  self.native_layouts[key] = spec
      self.native_messages = {}
      self.native_message_ids = {}
      if self.native_enabled:
         for name in self.classes:
            if name not in self.messages:
               continue
            plan = _native_message_plan(self, name)
            if plan is not None:
               self.native_messages[name] = plan
               self.native_message_ids[self.ids[name]] = plan
      self.description = text


def _native_message_plan(ctx, name):
   remote = ctx.messages[name]
   local = ctx.local_fields[name]
   if not remote or len(local) != len(remote):
      return None
   fields = []
   has_packed = False
   for field in remote:
      binding = local.get(field['id'])
      if binding is None or binding[2] != field['modifier']:
         return None
      if field['modifier'] == 'required' and field['type'] == 'string' and not field['dimensions']:
         fields.append((binding[1], None, None))
      elif field['modifier'] == 'packed':
         key = (field['type'], tuple(field['dimensions']))
         if key not in ctx.native_packed:
            return None
         fields.append((binding[1], ctx.native_packed[key], ctx.native_layouts[key]))
         has_packed = True
      else:
         return None
   if not has_packed:
      return None
   return _sdl_native.prepare_message(ctx.ids[name], ctx.classes[name], PackedArray, tuple(fields))


def _buffer_plan(ctx, name, dimensions=()):
   if not isinstance(ctx, Context) or not ctx.native_enabled:
      raise CodecError('Packed buffer API requires an enabled native extension')
   if not isinstance(name, str):
      raise CodecError('type name must be a string')
   dimensions = tuple(dimensions)
   if any(type(n) is not int or n <= 0 for n in dimensions):
      raise CodecError('invalid fixed array dimensions')
   key = (name, dimensions)
   if key not in ctx.native_packed:
      spec = _native_fixed_spec(ctx, name, dimensions)
      if spec is None:
         raise CodecError('type has no exact native fixed layout')
      ctx.native_packed[key] = _sdl_native.prepare(spec)
      ctx.native_layouts[key] = spec
   return ctx.native_packed[key], ctx.native_layouts[key]


class PackedArray:
   """Immutable, validated fixed records; object creation is lazy.

   `buffer` exposes readonly storage in `byteorder`; `wire_buffer` is big-endian. `tolist()` restores
   the ordinary Python representation. Buffers supplied to `from_buffer` must
   follow the SDL field order, without C struct padding.
   """
   __slots__ = ('_wire', '_count', '_plan', '_spec', '_little')

   def __init__(self, *args, **kwargs):
      raise TypeError('use PackedArray.from_values or PackedArray.from_buffer')

   def __setattr__(self, name, value):
      raise AttributeError('PackedArray is immutable')

   @classmethod
   def _make(cls, wire, count, plan, spec, little=False):
      result = object.__new__(cls)
      for name, value in (('_wire', memoryview(wire)), ('_count', count),
                          ('_plan', plan), ('_spec', spec), ('_little', little)):
         object.__setattr__(result, name, value)
      return result

   @classmethod
   def from_values(cls, ctx, type_name, values, dimensions=()):
      dimensions = tuple(dimensions)
      plan, spec = _buffer_plan(ctx, type_name, dimensions)
      try:
         wire = _sdl_native.encode(plan, values)
      except (ValueError, TypeError, AttributeError, OverflowError) as error:
         raise CodecError('invalid Packed values') from error
      size = ctx.fixed_sizes[type_name] * math.prod(dimensions)
      return cls._make(wire, len(wire)//size, plan, spec)

   @classmethod
   def from_buffer(cls, ctx, type_name, buffer, *, byteorder='native', dimensions=(), defer=False):
      dimensions = tuple(dimensions)
      plan, spec = _buffer_plan(ctx, type_name, dimensions)
      try:
         # Mutable exporters must be frozen before validation and later reuse.
         view = memoryview(buffer)
         if not view.c_contiguous:
            raise CodecError('Packed input buffer must be contiguous')
         size = ctx.fixed_sizes[type_name] * math.prod(dimensions)
         if byteorder not in ('big', 'little', 'native'):
            raise CodecError('invalid byteorder')
         little = byteorder == 'little' or (byteorder == 'native' and sys.byteorder == 'little')
         if defer:
            wire = buffer if isinstance(buffer, bytes) else view.tobytes()
            _sdl_native.validate(plan, wire, len(wire)//size)
         elif isinstance(buffer, bytes) and byteorder == 'big':
            wire = buffer
            _sdl_native.validate(plan, wire, len(wire)//size)
         else:
            # C holds the GIL while copying mutable exporters into immutable storage.
            wire = _sdl_native.convert(plan, buffer if isinstance(buffer, bytes) else view, byteorder)
         count = len(wire)//size
      except (ValueError, TypeError, BufferError, OverflowError) as error:
         raise CodecError('invalid Packed buffer') from error
      return cls._make(wire, count, plan, spec, bool(defer and little))

   @property
   def buffer(self):
      return memoryview(self._wire)

   @property
   def byteorder(self):
      return 'little' if self._little else 'big'

   @property
   def wire_buffer(self):
      if self._little:
         return memoryview(_sdl_native.convert(self._plan, self._wire, 'little'))
      return self.buffer

   def __len__(self):
      return self._count

   def tolist(self):
      return _sdl_native.decode(self._plan, self.wire_buffer, self._count)

   def __getitem__(self, index):
      size = len(self._wire)//self._count if self._count else 0
      if isinstance(index, slice):
         start, stop, step = index.indices(self._count)
         if step != 1:
            return self.tolist()[index]
         count = max(stop-start, 0)
         return self._make(self._wire[start*size:(start+count)*size], count, self._plan, self._spec, self._little)
      index = operator.index(index)
      if index < 0:
         index += self._count
      if not 0 <= index < self._count:
         raise IndexError('Packed index out of range')
      return self[index:index+1].tolist()[0]

   def __eq__(self, other):
      if isinstance(other, PackedArray):
         return self._spec == other._spec and self.wire_buffer == other.wire_buffer
      if isinstance(other, (list, tuple)):
         return self.tolist() == list(other)
      return NotImplemented

   def __repr__(self):
      return 'PackedArray(count={}, bytes={})'.format(self._count, len(self._wire))


def _native_fixed_spec(ctx, name, dimensions=(), _depth=0):
   # Only exact fixed local records bypass the ordinary evolution decoder.
   if _depth > 64:
      return None
   if dimensions:
      child = _native_fixed_spec(ctx, name, dimensions[1:], _depth + 1)
      size = ctx.fixed_sizes.get(name)
      if child is None or size is None or size * math.prod(dimensions) > 65536:
         return None
      return ('array', dimensions[0], child)
   if name in ctx.messages:
      if _fixed_codec(ctx, name) is None or not ctx.fixed_sizes[name] or ctx.fixed_sizes[name] > 65536:
         return None
      fields = []
      for field in ctx.messages[name]:
         local = ctx.local_fields[name][field['id']]
         child = _native_fixed_spec(ctx, field['type'], tuple(field['dimensions']), _depth + 1)
         if child is None:
            return None
         fields.append((local[1], child))
      return ('record', ctx.classes[name], tuple(fields))
   if name in ctx.enums:
      return None  # Preserve enum membership and local-schema checks in Python.
   if name in ('c32', 'c64'):
      return (name, Complex32 if name == 'c32' else Complex64)
   if name in _PRIMITIVE_WIRE_SIZES:
      return (name,)
   return None


def _fixed_codec(ctx, name, dimensions=()):
   """Compile fixed records once; binary conversion uses one Struct per record.

   Object construction preserves the public list/message API. Incompatible
   catalogues use the generic decoder so schema evolution remains supported.
   """
   formats = {'bool': 'B', 'int8': 'b', 'int16': 'h', 'int32': 'i',
      'int64': 'q', 'fl32': 'f', 'fl64': 'd', 'c32': 'ff', 'c64': 'dd'}

   def node(type_name, dimensions=()):
      if dimensions:
         child = node(type_name, dimensions[1:])
         if child is None or dimensions[0] * len(child[0]) > 65536:
            return None
         fmt, emit, read = child
         length = dimensions[0]
         def emit_array(value, output):
            if len(value) != length:
               raise CodecError('invalid fixed array dimensions')
            for item in value:
               emit(item, output)
         def read_array(values):
            return [read(values) for unused in range(length)]
         return fmt * length, emit_array, read_array
      if type_name in ctx.messages:
         cls = ctx.classes.get(type_name)
         fields = ctx.local_fields.get(type_name, {})
         remote = ctx.messages[type_name]
         if cls is None or ctx.fixed_sizes[type_name] is None or len(fields) != len(remote):
            return None
         children = []
         for field in remote:
            local = fields.get(field['id'])
            child = node(field['type'], field['dimensions'])
            if local is None or child is None:
               return None
            children.append((local[1], child))
         fmt = ''.join(child[0] for unused, child in children)
         if len(fmt) > 65536:
            return None
         def emit_message(value, output):
            if not isinstance(value, SdlMessage) or value._SDL_NAME != type_name:
               raise CodecError('wrong message type')
            for field_name, child in children:
               child[1](getattr(value, field_name), output)
         def read_message(values):
            result = cls.__new__(cls)
            for field_name, child in children:
               setattr(result, field_name, child[2](values))
            return result
         return fmt, emit_message, read_message
      if type_name in ctx.enums:
         enum_cls = next((ns.get(re.sub(r'\W', '_', type_name)) for ns in ctx.namespaces.values()
            if re.sub(r'\W', '_', type_name) in ns), None)
         if enum_cls is None:
            return None
         def emit_enum(value, output):
            if not isinstance(value, enum_cls):
               raise CodecError('invalid enum value for ' + type_name)
            output.append(int(value))
         def read_enum(values):
            value = next(values)
            if value not in ctx.enums[type_name]:
               raise CodecError('invalid enum value')
            try:
               return enum_cls(value)
            except ValueError as error:
               raise CodecError('enum value missing from local schema') from error
         return 'i', emit_enum, read_enum
      fmt = formats.get(type_name)
      if fmt is None:
         return None
      if type_name == 'bool':
         def emit_bool(value, output):
            if not isinstance(value, bool):
               raise CodecError('boolean field requires bool')
            output.append(value)
         def read_bool(values):
            value = next(values)
            if value not in (0, 1):
               raise CodecError('invalid boolean value')
            return bool(value)
         return fmt, emit_bool, read_bool
      if type_name in ('c32', 'c64'):
         cls = Complex32 if type_name == 'c32' else Complex64
         def emit_complex(value, output):
            output.extend((value.real, value.imag))
         def read_complex(values):
            return cls(next(values), next(values))
         return fmt, emit_complex, read_complex
      def emit_scalar(value, output):
         output.append(value)
      def read_scalar(values):
         return next(values)
      return fmt, emit_scalar, read_scalar

   if ctx.fixed_sizes.get(name) is None:
      return None
   plan = node(name, dimensions)
   if plan is None:
      return None
   fmt, emit, read = plan
   return struct.Struct('>' + fmt), emit, read


def _cardinality(modifier):
   return 'sequence' if modifier in ('packed', 'repeated') else modifier


def prepare(text, local_types=()):
   return Context(text, local_types)


def _write_count(output, value):
   if type(value) is not int or not 0 <= value <= 0xffffffff:
      raise CodecError('counter exceeds uint32')
   while value >= 128:
      output.append((value & 127) | 128)
      value >>= 7
   output.append(value)


class _Reader:
   def __init__(self, data, packed='objects'):
      self.packed = packed
      self.data = memoryview(data)
      self.offset = 0

   def take(self, n):
      if n > len(self.data) - self.offset:
         raise CodecError('truncated payload')
      start = self.offset
      self.offset += n
      return self.data[start:self.offset]

   def count(self):
      value = 0
      for i in range(5):
         b = self.take(1)[0]
         if i == 4 and b > 15:
            raise CodecError('counter exceeds uint32')
         value |= (b & 127) << (7 * i)
         if b < 128:
            if i and b == 0:
               raise CodecError('noncanonical counter')
            return value
      raise CodecError('invalid counter')


def _write_value(ctx, type_name, value, output, namespace, dimensions=()):
   if dimensions:
      if len(value) != dimensions[0]:
         raise CodecError('invalid fixed array dimensions')
      for item in value:
         _write_value(ctx, type_name, item, output, namespace, dimensions[1:])
   elif type_name in ctx.messages:
      _write_message(ctx, type_name, value, output)
   else:
      payload = _pack_value(type_name, value, namespace)
      if type_name == 'string':
         _write_count(output, len(payload))
      output.extend(payload)


def _write_message(ctx, name, message, output):
   if not isinstance(message, SdlMessage) or message._SDL_NAME != name:
      raise CodecError('wrong message type')
   fields = ctx.local_fields.get(name)
   if fields is None or len(fields) != len(ctx.messages[name]):
      raise CodecError('encoding requires the local emission catalogue')
   namespace = ctx.namespaces[name]
   for f in ctx.messages[name]:
      local = fields.get(f['id'])
      if local is None:
         raise CodecError('encoding requires matching fields')
      value = getattr(message, local[1])
      modifier = f['modifier']
      if modifier == 'optional':
         _write_count(output, 0 if value is None else 1)
         values = () if value is None else (value,)
      elif modifier in ('packed', 'repeated'):
         _write_count(output, len(value))
         values = value
      else:
         values = (value,)
      # One struct conversion per numeric array instead of one per element.
      fmt = {'int8':'b', 'int16':'h', 'int32':'i', 'int64':'q', 'fl32':'f', 'fl64':'d'}.get(f['type'])
      plan = ctx.packed_codecs.get((f['type'], tuple(f['dimensions']))) if modifier in ('packed', 'repeated') else None
      native = ctx.native_packed.get((f['type'], tuple(f['dimensions']))) if modifier in ('packed', 'repeated') else None
      if isinstance(value, PackedArray) and modifier in ('packed', 'repeated'):
         spec = ctx.native_layouts.get((f['type'], tuple(f['dimensions'])))
         if spec is None:
            spec = _native_fixed_spec(ctx, f['type'], tuple(f['dimensions']))
         if spec != value._spec:
            raise CodecError('Packed buffer layout does not match the field')
         output.extend(value.wire_buffer)
      elif native is not None:
         try:
            output.extend(_sdl_native.encode(native, value))
         except (ValueError, TypeError, AttributeError, OverflowError) as error:
            raise CodecError('invalid native packed value') from error
      elif plan is not None:
         record, emit, unused_read = plan
         try:
            components = []
            for item in value:
               emit(item, components)
            output.extend(struct.pack('>' + record.format[1:] * len(value), *components))
         except (struct.error, TypeError, AttributeError, OverflowError) as error:
            raise CodecError('invalid packed record') from error
      elif modifier in ('packed', 'repeated') and fmt and not f['dimensions'] and value:
         try:
            output.extend(struct.pack('>' + str(len(value)) + fmt, *value))
         except (struct.error, TypeError, OverflowError) as error:
            raise CodecError('invalid numeric array') from error
      elif modifier in ('packed', 'repeated') and f['type'] in ('c32', 'c64') and not f['dimensions'] and value:
         try:
            # Convert each component column in bulk, then interleave raw words.
            # Native integer views only copy bytes; wire endianness stays big-endian.
            single = f['type'] == 'c32'
            payload = bytearray(len(value) * (8 if single else 16))
            words = memoryview(payload).cast('I' if single else 'Q')
            fmt = '>' + str(len(value)) + ('f' if single else 'd')
            words[::2] = memoryview(struct.pack(fmt, *[z.real for z in value])).cast('I' if single else 'Q')
            words[1::2] = memoryview(struct.pack(fmt, *[z.imag for z in value])).cast('I' if single else 'Q')
            output.extend(payload)
         except (struct.error, AttributeError, TypeError, OverflowError) as error:
            raise CodecError('invalid complex array') from error
      else:
         for item in values:
            _write_value(ctx, f['type'], item, output, namespace, f['dimensions'])


def encode(ctx, message):
   if not isinstance(ctx, Context) or not isinstance(message, SdlMessage):
      raise CodecError('encode expects a prepared context and an SDL message')
   native = ctx.native_messages.get(message._SDL_NAME)
   if native is not None:
      try:
         result = _sdl_native.encode_message(native, message)
      except (ValueError, TypeError, BufferError, AttributeError, OverflowError) as error:
         raise CodecError('invalid native Packed message') from error
      if result is not NotImplemented:
         return result
   output = bytearray()
   try:
      _write_count(output, ctx.ids[message._SDL_NAME])
   except KeyError as error:
      raise CodecError('message is not in the catalogue') from error
   _write_message(ctx, message._SDL_NAME, message, output)
   return bytes(output)


class _BufferWriter:
   def __init__(self, buffer, offset):
      try:
         self.view = memoryview(buffer).cast('B')
      except (TypeError, ValueError) as error:
         raise CodecError('output must be a contiguous writable buffer') from error
      if self.view.readonly or type(offset) is not int or not 0 <= offset <= len(self.view):
         raise CodecError('invalid output buffer or offset')
      self.offset = offset

   def append(self, byte):
      if self.offset == len(self.view):
         raise CodecError('output buffer too small')
      self.view[self.offset] = byte
      self.offset += 1

   def extend(self, data):
      length = len(data)
      if length > len(self.view)-self.offset:
         raise CodecError('output buffer too small')
      self.view[self.offset:self.offset+length] = data
      self.offset += length


def encode_into(ctx, message, buffer, offset=0):
   """Write into caller storage; return bytes written. Errors may leave a prefix."""
   if not isinstance(ctx, Context) or not isinstance(message, SdlMessage):
      raise CodecError('encode_into expects a prepared context and an SDL message')
   if type(offset) is not int or offset < 0:
      raise CodecError('invalid output offset')
   native = ctx.native_messages.get(message._SDL_NAME)
   if native is not None:
      try:
         result = _sdl_native.encode_message(native, message, buffer, offset)
      except (ValueError, TypeError, BufferError, AttributeError, OverflowError) as error:
         raise CodecError('invalid native Packed message or output buffer') from error
      if result is not NotImplemented:
         return result
   output = _BufferWriter(buffer, offset)
   try:
      _write_count(output, ctx.ids[message._SDL_NAME])
   except KeyError as error:
      raise CodecError('message is not in the catalogue') from error
   _write_message(ctx, message._SDL_NAME, message, output)
   return output.offset-offset


def _read_value(ctx, name, reader, dimensions=(), dynamic=False, skip=False):
   if dimensions:
      values = []
      for unused in range(dimensions[0]):
         item = _read_value(ctx, name, reader, dimensions[1:], dynamic, skip)
         if not skip:
            values.append(item)
      return None if skip else values
   if name in ctx.messages:
      return _read_message(ctx, name, reader, dynamic, skip)
   size = reader.count() if name == 'string' else _PRIMITIVE_WIRE_SIZES.get(name, 4)
   payload = reader.take(size)
   if name in ctx.enums:
      value = struct.unpack('>i', payload)[0]
      if value not in ctx.enums[name]:
         raise CodecError('invalid enum value')
      if skip:
         return None
      if dynamic:
         return SdlDynamicEnum(name, value, ctx.enums[name][value])
      enum_cls = next((ns.get(re.sub(r'\W', '_', name)) for ns in ctx.namespaces.values()
         if re.sub(r'\W', '_', name) in ns), None)
      if enum_cls is None:
         raise CodecError('unknown local enum')
      try:
         return enum_cls(value)
      except ValueError as error:
         raise CodecError('enum value missing from local schema') from error
   value = _unpack_value(name, bytes(payload) if name == 'string' else payload, {})
   return None if skip else value


def _read_message(ctx, name, reader, dynamic=False, skip=False):
   cls = ctx.classes.get(name)
   if not dynamic and not skip and cls is None:
      raise CodecError('unknown local message type: ' + name)
   result = {} if dynamic else (None if skip else cls())
   fields = ctx.local_fields.get(name, {})
   for f in ctx.messages[name]:
      modifier = f['modifier']
      count = reader.count() if modifier != 'required' else 1
      if modifier == 'optional' and count > 1:
         raise CodecError('optional counter must be 0 or 1')
      size = ctx.fixed_sizes.get(f['type'])
      if size is not None:
         for n in f['dimensions']:
            size *= n
         if count > (len(reader.data) - reader.offset) // max(size, 1):
            raise CodecError('truncated array')
      elif count > 1048576:
         raise CodecError('variable sequence too large')
      local = fields.get(f['id'])
      discard = skip or (not dynamic and local is None)
      values = []
      fmt = {'int8':'b', 'int16':'h', 'int32':'i', 'int64':'q', 'fl32':'f', 'fl64':'d'}.get(f['type'])
      plan = ctx.packed_codecs.get((f['type'], tuple(f['dimensions']))) if modifier in ('packed', 'repeated') and not dynamic else None
      native = ctx.native_packed.get((f['type'], tuple(f['dimensions']))) if modifier in ('packed', 'repeated') and not dynamic else None
      if native is not None and reader.packed == 'view' and modifier == 'packed' and not discard:
         payload = reader.take(count * size)
         try:
            _sdl_native.validate(native, payload, count)
         except (ValueError, TypeError, OverflowError) as error:
            raise CodecError('invalid native Packed payload') from error
         values = PackedArray._make(payload, count, native,
            ctx.native_layouts[(f['type'], tuple(f['dimensions']))])
      elif native is not None:
         payload = reader.take(count * size)
         # Discarded leaves still need boolean validation.
         try:
            values = _sdl_native.decode(native, payload, count)
         except (ValueError, TypeError, OverflowError) as error:
            raise CodecError('invalid native packed payload') from error
      elif plan is not None:
         record, unused_emit, read = plan
         payload = reader.take(count * record.size)
         if not discard:
            values = [read(iter(parts)) for parts in record.iter_unpack(payload)]
      elif modifier in ('packed', 'repeated') and fmt and not f['dimensions']:
         payload = reader.take(count * size)
         if not discard:
            values = list(struct.unpack('>' + str(count) + fmt, payload))
      elif modifier in ('packed', 'repeated') and f['type'] in ('c32', 'c64') and not f['dimensions']:
         payload = reader.take(count * size)
         if not discard:
            cls = Complex32 if f['type'] == 'c32' else Complex64
            fmt = '>ff' if f['type'] == 'c32' else '>dd'
            values = [cls(*z) for z in struct.iter_unpack(fmt, payload)]
      else:
         for unused in range(count):
            item = _read_value(ctx, f['type'], reader, f['dimensions'], dynamic, discard)
            if not discard:
               values.append(item)
      if discard:
         continue
      value = values if modifier in ('packed', 'repeated') else (values[0] if values else None)
      if dynamic:
         result[f['name']] = value
      else:
         setattr(result, local[1], value)
   return SdlDynamicMessage(name, result) if dynamic and not skip else result


def _decode(ctx, data, dynamic, packed='objects'):
   if packed not in ('objects', 'view'):
      raise CodecError('packed must be objects or view')
   if packed == 'view':
      if not isinstance(ctx, Context) or not ctx.native_enabled:
         raise CodecError('Packed views require an enabled native extension')
      if not isinstance(data, bytes):
         data = memoryview(data).tobytes()
      native = ctx.native_message_ids.get(data[0]) if data and data[0] < 128 else None
      if native is not None:
         try:
            return _sdl_native.decode_message(native, data)
         except (ValueError, TypeError, BufferError, OverflowError) as error:
            raise CodecError('invalid native Packed message') from error
   reader = _Reader(data, packed)
   type_id = reader.count()
   if not 1 <= type_id <= len(ctx.names):
      raise CodecError('unknown message type ID')
   result = _read_message(ctx, ctx.names[type_id - 1], reader, dynamic)
   if reader.offset != len(reader.data):
      raise CodecError('trailing data')
   return result


def decode(ctx, data, *, packed='objects'):
   return _decode(ctx, data, False, packed)


def decode_dynamic(ctx, data):
   return _decode(ctx, data, True)


def _default_fixed_array(type_name, dimensions, namespace):
   if dimensions:
      return [_default_fixed_array(type_name, dimensions[1:], namespace)
         for unused in range(dimensions[0])]
   cls = namespace.get(re.sub(r'\W', '_', type_name))
   if isinstance(cls, type) and issubclass(cls, SdlMessage):
      return cls()
   return default_value(type_name, namespace)
