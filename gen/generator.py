#!/usr/bin/env python3
"""SDL parser and code generator entry point."""

import re
import argparse
import os
import sys
import math
import warnings
import inspect
from decimal import Decimal
from collections import deque, namedtuple


MAX_SCHEMA_DESCRIPTOR_SIZE = 1024 * 1024
MAX_SCHEMA_NESTING = 64


WIRE_KEYS = ('bits', 'q', 'encoding', 'align', 'pad_after')


def parse_wire_options(text):
   options = {}
   if text is None:
      return options
   for item in text.split(','):
      match = re.fullmatch(r'\s*(bits|q|encoding|align|pad_after)\s*=\s*([^\s]+)\s*', item)
      if not match or match[1] in options:
         raise ValueError('invalid or duplicate wire option: ' + item)
      key, value = match.groups()
      if key == 'q':
         if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
            raise ValueError('wire q requires a positive finite decimal')
         quantum = float(value)
         if not math.isfinite(quantum) or quantum <= 0:
            raise ValueError('wire q requires a positive finite decimal')
         value = format(Decimal(value), 'f')
         if '.' in value:
            value = value.rstrip('0').rstrip('.')
         value = value.lstrip('+').lstrip('0')
         if value.startswith('.'):
            value = '0' + value
      elif key == 'encoding':
         if value not in ('varint', 'zigzag'):
            raise ValueError('unknown wire encoding: ' + value)
      else:
         if not value.isascii() or not value.isdecimal():
            raise ValueError('wire option requires an integer: ' + key)
         value = int(value)
      options[key] = value
   return options


def wire_suffix(options):
   return '@' + ','.join({'bits':'b','q':'q','align':'a','pad_after':'p'}[key] + str(options[key]) if key != 'encoding' else {'varint':'v','zigzag':'z'}[options[key]] for key in WIRE_KEYS if key in options) if options else ''


def validate_wire_options(options, type_name, enum=False):
   width = {'bool': 1, 'int8': 8, 'uint8': 8, 'int16': 16, 'uint16': 16, 'int32': 32, 'uint32': 32, 'int64': 64, 'uint64': 64}.get(type_name, 32 if enum else 0)
   if 'q' in options:
      if type_name not in ('fl32', 'fl64') or 'bits' not in options or 'encoding' in options:
         raise ValueError('wire q requires fl32/fl64 with bits and excludes encoding')
      width = 32 if type_name == 'fl32' else 64
   if 'bits' in options and not 1 <= options['bits'] <= width:
      if type_name in ('fl32', 'fl64') and 'q' not in options:
         raise ValueError('wire bits on a floating type require q: ' + type_name)
      raise ValueError('wire bits must fit the type width: ' + type_name)
   if 'q' in options:
      q = float(options['q'])
      maximum = float.fromhex('0x1.fffffep127') if type_name == 'fl32' else sys.float_info.max
      minimum = float.fromhex('0x1p-149') / 2 if type_name == 'fl32' else 0
      if not math.isfinite(q) or q <= minimum or q * 2 ** (options['bits'] - 1) > maximum:
         raise ValueError('wire q range must fit the floating type: ' + type_name)
   if options.get('encoding') == 'zigzag' and type_name.startswith('uint'):
      raise ValueError('zigzag requires a signed integer or enum')
   if 'encoding' in options and (not width or type_name == 'bool' or 'bits' in options):
      raise ValueError('wire encoding requires an integer or enum and excludes bits')
   align = options.get('align', 1)
   if align < 1 or align > 256 or align & (align - 1):
      raise ValueError('wire align must be a power of two from 1 to 256')
   if not 0 <= options.get('pad_after', 0) <= 65535:
      raise ValueError('wire pad_after must fit uint16')


def canonical_type_descriptor(schema, root_name=None):
   """Compact canonical SDL2 UTF-8 catalogue (all schema declarations)."""
   if root_name is not None and root_name not in schema.messages:
      raise ValueError('unknown root message: ' + root_name)
   declarations = []
   markers = {'required':'', 'optional':'?', 'repeated':'*', 'packed':'+'}
   messages, enums = schema.catalogue_types()
   for name in sorted(messages, key=lambda value: value.encode('utf-8')):
      fields = []
      for field in sorted(messages[name].fields, key=lambda item: item.index):
         dimensions = ''.join('[' + str(n) + ']' for n in field.array_dimensions)
         fields.append('%d%s:%s%s:%s%s;' % (field.index, markers[field.modifier], field.type_name, dimensions, field.name, wire_suffix(field.wire)))
      declarations.append(name + '{' + ''.join(fields) + '}')
   for name in sorted(enums, key=lambda value: value.encode('utf-8')):
      declarations.append('#' + name + '{' + ''.join(item + '=' + str(int(value)) + ';' for item, value in enums[name].pairs) + '}')
   result = ('SDL2|' + '|'.join(declarations)).encode('utf-8')
   if len(result) > MAX_SCHEMA_DESCRIPTOR_SIZE:
      raise ValueError('wire descriptor exceeds 1 MiB')
   return result


def c_identifier(value):
   if '.' in value:
      # Length-delimited UTF-8 segments: a.b_c cannot collide with a_b.c.
      return 'sdlq_' + '_'.join(str(len(part.encode('utf-8'))) + '_' +
         part.encode('utf-8').hex() for part in value.split('.'))
   identifier = re.sub(r'\W', '_', value)
   if not identifier or identifier[0].isdigit():
      identifier = '_' + identifier
   return identifier


def local_type_name(name):
   return name.rsplit('.', 1)[-1]


def package_output(output_dir, package, matlab=False):
   path = os.path.join(output_dir, *[(('+' if matlab else '') + part)
      for part in package.split('.') if part])
   os.makedirs(path, exist_ok=True)
   return path


def validate_package_paths(schemas, language, segment_validator, module_name=None):
   packages = set()
   for base, _, schema in schemas:
      parts = schema.package.split('.') if schema.package else []
      for part in parts:
         if not segment_validator(part):
            raise ValueError('invalid ' + language + ' package segment: ' + part)
      packages.update('.'.join(parts[:i]) for i in range(1, len(parts) + 1))
   if module_name is not None:
      for base, _, schema in schemas:
         module = '.'.join(filter(None, (schema.package, module_name(base))))
         if module in packages:
            raise ValueError(language + ' package conflicts with schema module: ' + module)


def validate_file_dependencies(schemas, language):
   """C includes and Ada with clauses cannot express circular unit dependencies."""
   dependencies = {(schema.package, base): schema.dependencies() for base, _, schema in schemas}
   visited, active = set(), set()

   def visit(unit):
      if unit in active:
         raise ValueError(language + ' circular schema file dependency: ' + '.'.join(filter(None, unit)))
      if unit in visited:
         return
      active.add(unit)
      for dependency in dependencies[unit]:
         visit(dependency)
      active.remove(unit)
      visited.add(unit)

   for unit in dependencies:
      visit(unit)


class TypeTable(dict):
   """Iterate file declarations, but resolve references across the input set."""
   external = None

   def __contains__(self, key):
      return super().__contains__(key) or (self.external is not None and key in self.external)

   def __getitem__(self, key):
      if super().__contains__(key):
         return super().__getitem__(key)
      if self.external is not None:
         return self.external[key]
      raise KeyError(key)

   def get(self, key, default=None):
      try:
         return self[key]
      except KeyError:
         return default


class Field:
   def __init__(self, index, modifier, type_name, name, array_dimensions=None, wire=None):
      self.index = int(index)
      self.modifier = modifier
      self.type_name = type_name
      self.name = name
      self.array_dimensions = array_dimensions or []
      self.wire = wire or {}
      self.doc = ''


class Message:
   def __init__(self, name):
      self.name = name
      self.fields = []
      self.doc = ''


class Enum:
   def __init__(self, name):
      self.name = name
      self.pairs = []
      self.doc = ''
      self.value_docs = {}


_SourceToken = namedtuple('_SourceToken', 'text line column kind end_line', defaults=('symbol', None))
_SOURCE_TOKENS = re.compile(
   r'(?P<space>\s+)|(?P<comment>//[^\r\n]*)|'
   r'(?P<block>/\*[\s\S]*?\*/)|(?P<unclosed>/\*)|'
   r'(?P<number>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?(?!\w))|'
   r'(?P<name>\w+)|(?P<symbol>\S)')


def normalize_documentation(block):
   lines = block[3:-2].splitlines()
   for index in range(1, len(lines)):
      lines[index] = re.sub(r'^[ \t]*\*(?:[ \t]|$)', '', lines[index], count=1)
   return inspect.cleandoc('\n'.join(lines)).strip()


def documentation_comment(text, language, indent='', module=False):
   if not text:
      return ''
   if language in ('c', 'java'):
      # Java interprets Unicode escapes even inside comments.
      if language == 'java':
         text = text.replace('\\', '&#92;')
      text = text.replace('*/', '*&#47;')
      return indent + '/**\n' + ''.join(indent + ' * ' + line + '\n'
         for line in text.splitlines()) + indent + ' */\n'
   prefix = {'python': '#:', 'rust': '//!' if module else '///', 'matlab': '%', 'ada': '--'}[language]
   return ''.join(indent + prefix + ' ' + line + '\n' for line in text.splitlines())


def schema_documentation(schema):
   parts = [schema.doc] if schema.doc else []
   constants = [name + ' = ' + str(schema.constants[name]) + '\n' + text
      for name, text in schema.constant_docs.items() if text]
   if constants:
      parts.append('Dimension constants:\n\n' + '\n\n'.join(constants))
   return '\n\n'.join(parts)


def message_documentation(message):
   parts = [message.doc] if message.doc else []
   fields = [field.name + ':\n' + field.doc for field in sorted(message.fields,
      key=lambda field: field.index) if field.doc]
   if fields:
      parts.append('Fields:\n\n' + '\n\n'.join(fields))
   return '\n\n'.join(parts)


class _SourceParser:
   """Token-based SDL source reader; whitespace never delimits declarations."""

   def __init__(self, schema, text):
      self.schema = schema
      self.tokens = self._tokens(text)
      self.pending = deque()
      self.eof = _SourceToken('', 1, 1)
      self.anonymous_counters = {}
      self.last_token = self.eof

   def _tokens(self, text):
      line, column = 1, 1
      for match in _SOURCE_TOKENS.finditer(text):
         value = match[0]
         if match.lastgroup == 'unclosed':
            raise ValueError('unterminated block comment at line %d, column %d' % (line, column))
         if match.lastgroup == 'block':
            if value.startswith('/**'):
               yield _SourceToken(normalize_documentation(value), line, column, 'doc', line + value.count('\n'))
         elif match.lastgroup not in ('space', 'comment'):
            yield _SourceToken(value, line, column, 'symbol', line + value.count('\n'))
         newlines = value.count('\n')
         if newlines:
            line += newlines
            column = len(value) - value.rfind('\n')
         else:
            column += len(value)
      self.eof = _SourceToken('', line, column)

   def raw_peek(self, index=0):
      while len(self.pending) <= index:
         token = next(self.tokens, None)
         if token is None:
            return self.eof
         self.pending.append(token)
      return self.pending[index]

   def peek(self, index=0):
      position = 0
      while True:
         token = self.raw_peek(position)
         if token.kind != 'doc':
            if index == 0 or token == self.eof:
               return token
            index -= 1
         position += 1

   def take(self):
      token = self.raw_peek()
      if token.kind == 'doc':
         self.fail('documentation comment must precede a declaration or follow its terminator', token)
      if self.pending:
         self.pending.popleft()
      self.last_token = token
      return token.text

   def leading_documentation(self):
      docs = []
      while self.raw_peek().kind == 'doc':
         docs.append(self.pending.popleft())
      return docs

   def finish_documentation(self, leading):
      docs = list(leading)
      while self.raw_peek().kind == 'doc' and self.raw_peek().line == self.last_token.end_line:
         docs.append(self.pending.popleft())
      return '\n\n'.join(token.text for token in docs if token.text)

   def reject_orphan_documentation(self, docs):
      if docs:
         self.fail('documentation comment has no following declaration', docs[0])

   def fail(self, message, token=None):
      token = token or self.peek()
      raise ValueError(message + ' at line %d, column %d' % (token.line, token.column))

   def expect(self, value, message):
      if self.peek().text != value:
         self.fail(message)
      self.take()

   def name(self, message):
      if not re.fullmatch(r'\w+', self.peek().text):
         self.fail(message)
      return self.take()

   def reference(self, message):
      leading_dot = self.peek().text == '.'
      if leading_dot:
         self.take()
      parts = [self.name(message)]
      while self.peek().text == '.':
         self.take()
         parts.append(self.name(message))
      return ('.' if leading_dot else '') + '.'.join(parts)

   def named_declaration(self):
      return (self.peek().text in ('enum', 'message', 'structure', 'struct') and
         re.fullmatch(r'\w+', self.peek(1).text) and self.peek(2).text == '{')

   def reject_nested_declaration(self):
      if self.named_declaration():
         self.fail('missing closing brace before declaration: ' +
            self.peek().text + ' ' + self.peek(1).text + ' {')

   def wire(self):
      if self.peek().text != 'wire':
         return {}
      location = self.peek()
      self.take()
      self.expect('(', 'invalid wire clause')
      options = []
      if self.peek().text == ')':
         self.fail('invalid or duplicate wire option: empty clause')
      while True:
         key = self.name('invalid wire option')
         self.expect('=', 'invalid wire option')
         value = self.take()
         options.append(key + '=' + value)
         if self.peek().text == ')':
            self.take()
            try:
               return parse_wire_options(','.join(options))
            except ValueError as error:
               self.fail(str(error), location)
         self.expect(',', 'invalid wire option')

   def fields(self, message, root_name, depth=0):
      while True:
         docs = self.leading_documentation()
         if self.peek().text == '}':
            self.reject_orphan_documentation(docs)
            break
         if not self.peek().text:
            self.reject_orphan_documentation(docs)
            self.fail(('unterminated anonymous struct in ' + root_name) if depth else
               ('unterminated message declaration: ' + message.name))
         self.reject_nested_declaration()
         label = 'invalid field declaration'
         explicit_index = None
         if self.peek(1).text == ':':
            explicit_index = self.take()
            if not re.fullmatch(r'[0-9]+', explicit_index):
               self.fail(label)
            self.take()
         modifier = self.take() if self.peek().text in ('optional', 'required', 'repeated', 'packed') else 'required'
         dimensions = []
         anonymous = self.peek().text in ('message', 'structure', 'struct') and self.peek(1).text == '{'
         if anonymous:
            if depth >= MAX_SCHEMA_NESTING:
               self.fail('message dependency nesting exceeds 64 levels: ' + root_name)
            self.take()
            self.take()
            counter = self.anonymous_counters.get(root_name, 0) + 1
            self.anonymous_counters[root_name] = counter
            type_name = root_name + '$' + str(counter)
            child = Message(type_name)
            self.fields(child, root_name, depth + 1)
            self.schema.messages[type_name] = child
            self.schema.message_order.append(type_name)
         else:
            type_name = self.reference(label)
            while self.peek().text == '[':
               self.take()
               token = self.peek().text
               if re.fullmatch(r'[0-9]+', token):
                  dimensions.append(int(self.take()))
               elif re.fullmatch(r'[^\W\d]\w*', token) or token == '.':
                  dimensions.append(self.reference(label))
               else:
                  self.fail(label)
               self.expect(']', label)
         field_name = self.name('anonymous struct must close with its field name' if anonymous else label)
         options = self.wire()
         self.expect(';', label)
         field = Field(self.schema._field_index(message, explicit_index),
            modifier, type_name, field_name, dimensions, options)
         field.doc = self.finish_documentation(docs)
         if anonymous:
            child.doc = field.doc
         message.fields.append(field)
      self.take()

   def constant(self):
      self.take()
      label = 'invalid dimension constant declaration'
      name = self.name(label)
      if not re.fullmatch(r'[^\W\d]\w*', name):
         self.fail(label)
      if name in self.schema.BUILTINS:
         self.fail('dimension constant name conflicts with built-in type: ' + name)
      if name in self.schema.constants or name in self.schema.messages or name in self.schema.enums:
         self.fail('duplicate declaration name: ' + name)
      self.expect('=', label)
      value = self.take()
      if not re.fullmatch(r'[0-9]+', value) or not 1 <= int(value) <= 0xFFFFFFFF:
         self.fail('dimension constant must be a positive uint32: ' + name)
      self.expect(';', label)
      self.schema.constants[name] = int(value)
      return name

   def parse(self):
      package_seen = False
      while True:
         docs = self.leading_documentation()
         keyword = self.peek().text
         if not keyword:
            self.reject_orphan_documentation(docs)
            return
         if keyword == 'package':
            label = 'invalid, duplicate or misplaced package declaration'
            if package_seen or self.schema.messages or self.schema.enums or self.schema.constants:
               self.fail(label)
            self.take()
            package = self.reference(label)
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*', package):
               self.fail(label)
            self.expect(';', label)
            self.schema.package = package
            self.schema.doc = self.finish_documentation(docs)
            package_seen = True
         elif keyword == 'const':
            name = self.constant()
            self.schema.constant_docs[name] = self.finish_documentation(docs)
         elif keyword in ('enum', 'message', 'structure', 'struct'):
            self.take()
            name = self.name('unexpected SDL statement: ' + keyword)
            self.expect('{', 'unexpected SDL statement: ' + keyword + ' ' + name)
            if name in self.schema.messages or name in self.schema.enums:
               self.fail('duplicate type name: ' + name)
            if name in self.schema.constants:
               self.fail('duplicate declaration name: ' + name)
            if keyword == 'enum':
               enumeration = Enum(name)
               self.schema.enums[name] = enumeration
               while True:
                  item_docs = self.leading_documentation()
                  if self.peek().text == '}':
                     self.reject_orphan_documentation(item_docs)
                     break
                  if not self.peek().text:
                     self.reject_orphan_documentation(item_docs)
                     self.fail('unterminated enum declaration: ' + name)
                  self.reject_nested_declaration()
                  item = self.name('invalid enum entry')
                  self.expect('=', 'invalid enum entry')
                  value = self.take()
                  if not re.fullmatch(r'-?[0-9]+', value):
                     self.fail('invalid enum entry')
                  self.expect(';', 'invalid enum entry')
                  enumeration.pairs.append((item, str(int(value, 10))))
                  enumeration.value_docs[item] = self.finish_documentation(item_docs)
               self.take()
               enumeration.doc = self.finish_documentation(docs)
            else:
               message = Message(name)
               self.schema.messages[name] = message
               self.schema.message_order.append(name)
               self.fields(message, name)
               message.doc = self.finish_documentation(docs)
         elif keyword == '}':
            self.fail('unexpected closing brace')
         else:
            self.fail('unexpected SDL statement: ' + keyword)


class MsgParser:
   BUILTINS = {'uint8', 'uint16', 'uint32', 'uint64', 'bool', 'int8', 'int16', 'int32', 'int64', 'fl32', 'fl64',
      'c32', 'c64', 'string'}

   def __init__(self):
      self.enums = TypeTable()
      self.messages = TypeTable()
      self.constants = TypeTable()
      self.constant_docs = {}
      self.doc = ''
      self.message_order = []
      self.package = ''
      self.type_owners = {}
      self.base_name = ''

   def parse_text(self, text, validate=True):
      _SourceParser(self, text).parse()
      self._qualify_types()
      if validate:
         self.validate()

   def _qualify_types(self):
      def qualify(name):
         if name.startswith('.'):
            return name[1:]
         if name in self.BUILTINS or '.' in name or not self.package:
            return name
         return self.package + '.' + name
      for table in (self.messages, self.enums):
         declarations = list(table.values())
         table.clear()
         for declaration in declarations:
            if declaration.name in self.BUILTINS:
               raise ValueError('type name conflicts with built-in type: ' + declaration.name)
            declaration.name = qualify(declaration.name)
            table[declaration.name] = declaration
      self.message_order = [qualify(name) for name in self.message_order]
      constants = list(self.constants.items())
      self.constants.clear()
      self.constants.update((qualify(name), value) for name, value in constants)
      self.constant_docs = {qualify(name): doc for name, doc in self.constant_docs.items()}
      for message in self.messages.values():
         for field in message.fields:
            field.type_name = qualify(field.type_name)
            field.array_dimensions = [qualify(value) if isinstance(value, str) else value
               for value in field.array_dimensions]

   def _resolve_dimensions(self):
      for message in self.messages.values():
         for field in message.fields:
            dimensions = []
            for value in field.array_dimensions:
               if isinstance(value, str):
                  constant = self.constants.get(value)
                  if constant is None:
                     raise ValueError('unknown dimension constant ' + value + ' in ' +
                        message.name + '.' + field.name)
                  value = constant
               dimensions.append(value)
            field.array_dimensions = dimensions

   def catalogue_types(self):
      messages, enums = dict(self.messages), dict(self.enums)
      pending = list(messages.values())
      while pending:
         for field in pending.pop().fields:
            name = field.type_name
            if name in self.messages and name not in messages:
               messages[name] = self.messages[name]
               pending.append(messages[name])
            elif name in self.enums and name not in enums:
               enums[name] = self.enums[name]
      return messages, enums

   def dependencies(self):
      messages, enums = self.catalogue_types()
      return sorted({self.type_owners[name] for name in list(messages) + list(enums)
         if name in self.type_owners and self.type_owners[name] != (self.package, self.base_name)})

   @staticmethod
   def _field_index(message, explicit_index):
      if explicit_index is not None:
         return int(explicit_index)
      return message.fields[-1].index + 1 if message.fields else 1

   def _order_embedded_messages(self):
      ordered = []
      visiting = set()
      visited = {}

      def visit(name, depth):
         if depth > MAX_SCHEMA_NESTING:
            raise ValueError('message dependency nesting exceeds 64 levels: ' + name)
         if name in visited:
            if depth + visited[name] > MAX_SCHEMA_NESTING:
               raise ValueError('message dependency nesting exceeds 64 levels: ' + name)
            return visited[name]
         if name in visiting:
            raise ValueError('recursive message dependency involving ' + name)
         visiting.add(name)
         message = self.messages[name]
         height = 0
         for field in message.fields:
            if field.type_name in self.messages:
               height = max(height, 1 + visit(field.type_name, depth + 1))
         visiting.remove(name)
         visited[name] = height
         if dict.__contains__(self.messages, name):
            ordered.append(name)
         return height

      for name in self.message_order:
         visit(name, 0)
      self.message_order = ordered

   def validate(self):
      self._resolve_dimensions()
      if len(self.constants) > 0xFFFF:
         raise ValueError('too many dimension constants in schema')
      for name in self.constants:
         if len(name.encode('utf-8')) > 0xFFFF:
            raise ValueError('dimension constant name exceeds 65535 UTF-8 bytes: ' + name)
      if len(self.messages) > 0xFFFF:
         raise ValueError('too many messages in schema descriptor')
      if len(self.enums) > 0xFFFF:
         raise ValueError('too many enums in schema descriptor')

      def validate_text(value, label):
         if len(value.encode('utf-8')) > 0xFFFF:
            raise ValueError(label + ' exceeds 65535 UTF-8 bytes in schema catalogue')

      generated_symbols = {}
      for type_name in list(self.enums) + list(self.messages):
         if type_name in self.BUILTINS:
            raise ValueError('type name conflicts with built-in type: ' + type_name)
         validate_text(type_name, 'type name')
         symbol = c_identifier(type_name).upper()
         if symbol in generated_symbols and generated_symbols[symbol] != type_name:
            raise ValueError('generated type identifier collision: ' + type_name +
               ' and ' + generated_symbols[symbol])
         generated_symbols[symbol] = type_name
      self._order_embedded_messages()
      for message in self.messages.values():
         if len(message.fields) > 0xFFFF:
            raise ValueError('too many fields in ' + message.name +
               ' for schema descriptor')
         ids = set()
         names = set()
         for field in message.fields:
            validate_wire_options(field.wire, field.type_name, field.type_name in self.enums)
            validate_text(field.name, 'field name in ' + message.name)
            validate_text(field.type_name, 'field type name in ' + message.name)
            if len(field.array_dimensions) > 0xFF:
               raise ValueError('too many array dimensions in ' + message.name +
                  '.' + field.name)
            if field.index <= 0 or field.index > 0xFFFFFFFF:
               raise ValueError('field ID must fit in a nonzero uint32')
            if field.index in ids or field.name in names:
               raise ValueError('duplicate field ID or name in ' + message.name)
            if field.type_name not in self.BUILTINS and field.type_name not in self.enums and field.type_name not in self.messages:
               raise ValueError('unknown type ' + field.type_name + ' in ' + message.name)
            if any(dimension <= 0 or dimension > 0xFFFFFFFF for dimension in field.array_dimensions):
               raise ValueError('fixed array dimensions must be positive uint32 values in ' +
                  message.name + '.' + field.name)
            if field.array_dimensions and field.modifier != 'required':
               raise ValueError('fixed arrays require the required modifier in ' +
                  message.name + '.' + field.name)
            if field.array_dimensions:
               array_size = self.fixed_wire_size(field.type_name)
               if array_size is None:
                  raise ValueError('fixed array element has variable wire size in ' +
                     message.name + '.' + field.name)
               for dimension in field.array_dimensions:
                  if array_size > 0xFFFFFFFF // dimension:
                     raise ValueError('fixed array wire size exceeds uint32 in ' +
                        message.name + '.' + field.name)
                  array_size *= dimension
            if field.array_dimensions and field.modifier == 'packed':
               raise ValueError('packed fields cannot also declare fixed dimensions in ' +
                  message.name + '.' + field.name)
            ids.add(field.index)
            names.add(field.name)
            if field.modifier == 'packed' and self.fixed_wire_size(field.type_name) is None:
               raise ValueError('packed field type must have a fixed wire size: ' +
                  message.name + '.' + field.name)
      for enum in self.enums.values():
         if len(enum.pairs) > 0xFFFF:
            raise ValueError('too many values in enum ' + enum.name +
               ' for schema descriptor')
         names = set()
         values = set()
         if not enum.pairs:
            raise ValueError('enum must declare at least one value: ' + enum.name)
         for name, value in enum.pairs:
            validate_text(name, 'enum item name in ' + enum.name)
            numeric_value = int(value)
            if numeric_value < -0x80000000 or numeric_value > 0x7FFFFFFF:
               raise ValueError('enum value must fit signed int32 in ' + enum.name)
            if name in names or numeric_value in values:
               raise ValueError('duplicate enum name or value in ' + enum.name)
            names.add(name)
            values.add(numeric_value)
      for root_name in self.message_order:
         canonical_type_descriptor(self, root_name)
      self._warn_packed_encodings()

   def _warn_packed_encodings(self):
      encoding_sources = {}
      for name in self.message_order:
         for field in self.messages[name].fields:
            source = None
            if 'encoding' in field.wire:
               source = (field.name, field.wire['encoding'])
            elif field.type_name in encoding_sources:
               path, encoding = encoding_sources[field.type_name]
               source = (field.name + '.' + path, encoding)
            if source is None:
               continue
            encoding_sources.setdefault(name, source)
            if field.modifier == 'packed':
               path, encoding = source
               warnings.warn('packed field ' + name + '.' + field.name +
                  ' uses wire(encoding=' + encoding + ') at ' + name + '.' + path +
                  '; transmitted element size may vary and representation conversion '
                  'may reduce runtime optimizations', UserWarning, stacklevel=3)

   def fixed_wire_size(self, type_name, active=None):
      primitive_sizes = {
         'bool': 1, 'int8': 1, 'uint8': 1, 'int16': 2, 'uint16': 2, 'int32': 4, 'uint32': 4, 'int64': 8, 'uint64': 8,
         'fl32': 4, 'fl64': 8, 'c32': 8, 'c64': 16,
      }
      if type_name in primitive_sizes:
         return primitive_sizes[type_name]
      if type_name in self.enums:
         return 4
      message = self.messages.get(type_name)
      if message is None:
         return None
      if active is None:
         active = set()
      if type_name in active or not message.fields:
         return None
      active = set(active)
      active.add(type_name)
      total = 0
      for field in message.fields:
         if field.modifier != 'required':
            return None
         field_size = self.fixed_wire_size(field.type_name, active)
         if field_size is None:
            return None
         for dimension in field.array_dimensions:
            field_size *= dimension
            if field_size > 0xFFFFFFFF:
               return None
         total += field_size
         if total > 0xFFFFFFFF:
            return None
      return total

def parse_schemas(input_path):
   if os.path.isdir(input_path):
      schema_paths = sorted(os.path.join(root, item) for root, dirs, files in os.walk(input_path)
         for item in files if item.endswith('.sdl'))
   else:
      schema_paths = [input_path]
   if not schema_paths:
      raise ValueError('no .sdl files found in ' + input_path)

   parsed = []
   seen_type_names = set()
   seen_constant_names = set()
   seen_symbols = set()
   seen_bases = set()
   for schema_path in schema_paths:
      base_name = os.path.splitext(os.path.basename(schema_path))[0]
      parser = MsgParser()
      with open(schema_path, 'r', encoding='utf-8') as input_file:
         parser.parse_text(input_file.read(), validate=False)
      parser.base_name = base_name
      identifier = c_identifier(parser.package + '.' + base_name if parser.package else base_name)
      if identifier in seen_bases:
         raise ValueError('SDL filenames map to the same identifier: ' + identifier)
      seen_bases.add(identifier)
      for name in list(parser.enums) + list(parser.messages):
         if name in seen_constant_names:
            raise ValueError('duplicate declaration name across SDL files: ' + name)
         symbol = c_identifier(name).upper()
         if name in seen_type_names or symbol in seen_symbols:
            raise ValueError('duplicate type name across SDL files: ' + name)
         seen_type_names.add(name)
         seen_symbols.add(symbol)
      for name in parser.constants:
         if name in seen_type_names or name in seen_constant_names:
            raise ValueError('duplicate declaration name across SDL files: ' + name)
         seen_constant_names.add(name)
      parsed.append((base_name, identifier, parser))
   messages = {name: message for _, _, schema in parsed for name, message in schema.messages.items()}
   enums = {name: enum for _, _, schema in parsed for name, enum in schema.enums.items()}
   constants = {name: value for _, _, schema in parsed for name, value in schema.constants.items()}
   owners = {name: (schema.package, base) for base, _, schema in parsed
      for name in list(schema.messages) + list(schema.enums)}
   for base, _, schema in parsed:
      schema.messages.external = messages
      schema.enums.external = enums
      schema.constants.external = constants
      schema.type_owners = owners
   # Resolve every file before validating any cross-file message dependency.
   for _, _, schema in parsed:
      schema._resolve_dimensions()
   for _, _, schema in parsed:
      schema.validate()
   return parsed



def clear_generated_outputs(output_dir, extensions, markers):
   """Remove stale backend files while preserving unrelated user files."""
   if not os.path.isdir(output_dir):
      os.makedirs(output_dir, exist_ok=True)
      return
   for root, dirs, files in os.walk(output_dir):
      for name in files:
         path = os.path.join(root, name)
         if not any(name.endswith(extension) for extension in extensions):
            continue
         try:
            with open(path, 'r', encoding='utf-8') as generated_file:
               marker = generated_file.readline().rstrip('\r\n')
         except (UnicodeDecodeError, OSError):
            continue
         if marker in markers:
            os.remove(path)


def main():
   argument_parser = argparse.ArgumentParser(description='Generate code from SDL schemas.')
   argument_parser.add_argument('-ada', action='store_true', help='generate Ada 2012 code')
   argument_parser.add_argument('-c', action='store_true', help='generate C code')
   argument_parser.add_argument('-rust', action='store_true', help='generate Rust code')
   argument_parser.add_argument('-python', action='store_true', help='generate Python 3 code')
   argument_parser.add_argument('-matlab', action='store_true', help='generate Matlab/Octave code')
   argument_parser.add_argument('-java', action='store_true', help='generate Java 7 compatible code')
   argument_parser.add_argument('input', nargs='?', default='sdl', help='SDL file or directory')
   argument_parser.add_argument('output', nargs='?', default='build/generated',
      help='output directory (language subdirectory is added automatically)')
   arguments = argument_parser.parse_args()
   if not arguments.c and not arguments.rust and not arguments.python and not arguments.matlab and not arguments.java and not arguments.ada:
      argument_parser.error('select at least one backend with -c, -rust, -python, -matlab -java or -ada')
   sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
   try:
      if arguments.ada:
         from ada_backend import generate_ada
         generate_ada(arguments.input, os.path.join(arguments.output, 'ada'))
      if arguments.c:
         from c_backend import generate_c
         generate_c(arguments.input, os.path.join(arguments.output, 'c'))
      if arguments.rust:
         from rust_backend import generate_rust
         generate_rust(arguments.input, os.path.join(arguments.output, 'rust'))
      if arguments.python:
         from python_backend import generate_python
         generate_python(arguments.input, os.path.join(arguments.output, 'python'))
      if arguments.matlab:
         from matlab_backend import generate_matlab
         generate_matlab(arguments.input, os.path.join(arguments.output, 'matlab'))
      if arguments.java:
         from java_backend import generate_java
         generate_java(arguments.input, os.path.join(arguments.output, 'java'))
   except ValueError as error:
      argument_parser.error(str(error))


if __name__ == '__main__':
   main()
