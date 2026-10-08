"""Small dependency-free runtime for SDL generated Python messages."""

import re
import struct
import json
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
      self.description = text


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
   def __init__(self, data):
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
      if modifier in ('packed', 'repeated') and fmt and value:
         try:
            output.extend(struct.pack('>' + str(len(value)) + fmt, *value))
         except (struct.error, TypeError, OverflowError) as error:
            raise CodecError('invalid numeric array') from error
      elif modifier in ('packed', 'repeated') and f['type'] in ('c32', 'c64') and value:
         try:
            components = [part for z in value for part in (z.real, z.imag)]
            fmt = 'f' if f['type'] == 'c32' else 'd'
            output.extend(struct.pack('>' + str(len(components)) + fmt, *components))
         except (struct.error, AttributeError, TypeError, OverflowError) as error:
            raise CodecError('invalid complex array') from error
      else:
         for item in values:
            _write_value(ctx, f['type'], item, output, namespace, f['dimensions'])


def encode(ctx, message):
   if not isinstance(ctx, Context) or not isinstance(message, SdlMessage):
      raise CodecError('encode expects a prepared context and an SDL message')
   output = bytearray()
   try:
      _write_count(output, ctx.ids[message._SDL_NAME])
   except KeyError as error:
      raise CodecError('message is not in the catalogue') from error
   _write_message(ctx, message._SDL_NAME, message, output)
   return bytes(output)


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
      if modifier in ('packed', 'repeated') and fmt:
         payload = reader.take(count * size)
         if not discard:
            values = list(struct.unpack('>' + str(count) + fmt, payload))
      elif modifier in ('packed', 'repeated') and f['type'] in ('c32', 'c64'):
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


def _decode(ctx, data, dynamic):
   reader = _Reader(data)
   type_id = reader.count()
   if not 1 <= type_id <= len(ctx.names):
      raise CodecError('unknown message type ID')
   result = _read_message(ctx, ctx.names[type_id - 1], reader, dynamic)
   if reader.offset != len(reader.data):
      raise CodecError('trailing data')
   return result


def decode(ctx, data):
   return _decode(ctx, data, False)


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
