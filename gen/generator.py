#!/usr/bin/env python3
"""SDL parser and code generator entry point."""

import re
import argparse
import os
import sys


def fnv1a_32(string_data):
   value = 2166136261
   for char in string_data:
      value = ((value ^ ord(char)) * 16777619) & 0xFFFFFFFF
   return value


def c_identifier(value):
   identifier = re.sub(r'\W', '_', value)
   if not identifier or identifier[0].isdigit():
      identifier = '_' + identifier
   return identifier


class Field:
   def __init__(self, index, modifier, type_name, name, array_dimensions=None):
      self.index = int(index)
      self.modifier = modifier
      self.type_name = type_name
      self.name = name
      self.array_dimensions = array_dimensions or []


class Message:
   def __init__(self, name):
      self.name = name
      self.fields = []


class Enum:
   def __init__(self, name):
      self.name = name
      self.pairs = []


class MsgParser:
   BUILTINS = {'bool', 'int8', 'int16', 'int32', 'int64', 'fl32', 'fl64',
      'c32', 'c64', 'string'}

   def __init__(self):
      self.enums = {}
      self.messages = {}
      self.message_order = []

   def parse_text(self, text):
      current_enum = None
      current_msg = None
      anonymous_stack = []
      anonymous_counters = {}
      for raw_line in text.splitlines():
         line = re.sub(r'//.*', '', raw_line).strip()
         if not line:
            continue
         match = re.match(r'enum\s+(\w+)\s*\{?$', line)
         if match:
            name = match.group(1)
            if name in self.enums or name in self.messages:
               raise ValueError('duplicate type name: ' + name)
            current_enum = Enum(name)
            self.enums[name] = current_enum
            current_msg = None
            continue
         match = re.match(r'message\s+(\w+)\s*\{?$', line)
         if match:
            name = match.group(1)
            if name in self.enums or name in self.messages:
               raise ValueError('duplicate type name: ' + name)
            current_msg = Message(name)
            self.messages[name] = current_msg
            self.message_order.append(name)
            current_enum = None
            continue
         if line == '}':
            if anonymous_stack:
               raise ValueError('anonymous struct must close with its field name')
            current_enum = None
            current_msg = None
            continue
         match = re.match(r'(\d+):\s+(optional|required|repeated|packed)\s+struct\s*\{\s*$', line)
         if match and current_msg is not None:
            root_name = anonymous_stack[0]['root'] if anonymous_stack else current_msg.name
            anonymous_counters[root_name] = anonymous_counters.get(root_name, 0) + 1
            anonymous_name = root_name + '$' + str(anonymous_counters[root_name])
            anonymous_stack.append({
               'parent': current_msg,
               'field_id': match.group(1),
               'modifier': match.group(2),
               'message': Message(anonymous_name),
               'root': root_name,
            })
            current_msg = anonymous_stack[-1]['message']
            continue
         match = re.match(r'}\s+(\w+)\s*;', line)
         if match and anonymous_stack:
            frame = anonymous_stack.pop()
            anonymous_message = frame['message']
            self.messages[anonymous_message.name] = anonymous_message
            self.message_order.append(anonymous_message.name)
            field_name = match.group(1)
            frame['parent'].fields.append(Field(frame['field_id'],
               frame['modifier'], anonymous_message.name, field_name))
            current_msg = frame['parent']
            continue
         if current_enum is not None:
            match = re.match(r'(\w+)\s*=\s*(-?\d+)\s*;', line)
            if not match:
               raise ValueError('invalid enum entry: ' + line)
            current_enum.pairs.append((match.group(1), match.group(2)))
            continue
         if current_msg is not None:
            match = re.match(r'(\d+):\s+(optional|required|repeated|packed)\s+(\w+(?:\[\d+\])*)\s+(\w+)\s*;', line)
            if not match:
               raise ValueError('invalid field declaration: ' + line)
            declared_type = match.group(3)
            type_name = re.match(r'\w+', declared_type).group(0)
            dimensions = [int(value) for value in re.findall(r'\[(\d+)\]', declared_type)]
            current_msg.fields.append(Field(match.group(1), match.group(2),
               type_name, match.group(4), dimensions))
            continue
         raise ValueError('unexpected SDL statement: ' + line)
      if anonymous_stack:
         raise ValueError('unterminated anonymous struct in ' + anonymous_stack[0]['root'])
      self.validate()
      self._order_embedded_messages()

   def _order_embedded_messages(self):
      ordered = []
      visiting = set()
      visited = set()

      def visit(name):
         if name in visited:
            return
         if name in visiting:
            raise ValueError('recursive message dependency involving ' + name)
         visiting.add(name)
         message = self.messages[name]
         for field in message.fields:
            if (field.type_name in self.messages and field.type_name != name and
                  (field.modifier not in ('repeated', 'packed') or '$' in field.type_name)):
               visit(field.type_name)
         visiting.remove(name)
         visited.add(name)
         ordered.append(name)

      for name in self.message_order:
         visit(name)
      self.message_order = ordered

   def validate(self):
      generated_symbols = {}
      for type_name in list(self.enums) + list(self.messages):
         symbol = c_identifier(type_name).upper()
         if symbol in generated_symbols and generated_symbols[symbol] != type_name:
            raise ValueError('generated type identifier collision: ' + type_name +
               ' and ' + generated_symbols[symbol])
         generated_symbols[symbol] = type_name
      for message in self.messages.values():
         ids = set()
         names = set()
         for field in message.fields:
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
         names = set()
         values = set()
         for name, value in enum.pairs:
            if name in names or int(value) in values:
               raise ValueError('duplicate enum name or value in ' + enum.name)
            names.add(name)
            values.add(int(value))

   def fixed_wire_size(self, type_name, active=None):
      primitive_sizes = {
         'bool': 1, 'int8': 1, 'int16': 2, 'int32': 4, 'int64': 8,
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
      schema_paths = [os.path.join(input_path, item) for item in sorted(os.listdir(input_path))
         if item.endswith('.sdl') and os.path.isfile(os.path.join(input_path, item))]
   else:
      schema_paths = [input_path]
   if not schema_paths:
      raise ValueError('no .sdl files found in ' + input_path)

   parsed = []
   seen_type_names = set()
   seen_symbols = set()
   seen_bases = set()
   for schema_path in schema_paths:
      base_name = os.path.splitext(os.path.basename(schema_path))[0]
      identifier = c_identifier(base_name)
      if identifier in seen_bases:
         raise ValueError('SDL filenames map to the same identifier: ' + identifier)
      seen_bases.add(identifier)
      parser = MsgParser()
      with open(schema_path, 'r', encoding='utf-8') as input_file:
         parser.parse_text(input_file.read())
      for name in list(parser.enums) + list(parser.messages):
         symbol = c_identifier(name).upper()
         if name in seen_type_names or symbol in seen_symbols:
            raise ValueError('duplicate type name across SDL files: ' + name)
         seen_type_names.add(name)
         seen_symbols.add(symbol)
      parsed.append((base_name, identifier, parser))
   return parsed


def main():
   argument_parser = argparse.ArgumentParser(description='Generate code from SDL schemas.')
   argument_parser.add_argument('-c', action='store_true', help='generate C code')
   argument_parser.add_argument('-rust', action='store_true', help='generate Rust code')
   argument_parser.add_argument('-python', action='store_true', help='generate Python 3 code')
   argument_parser.add_argument('input', nargs='?', default='sdl', help='SDL file or directory')
   argument_parser.add_argument('output', nargs='?', default='build/generated',
      help='output directory (language subdirectory is added automatically)')
   arguments = argument_parser.parse_args()
   if not arguments.c and not arguments.rust and not arguments.python:
      argument_parser.error('select at least one backend with -c, -rust or -python')
   sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
   try:
      if arguments.c:
         from c_backend import generate_c
         generate_c(arguments.input, os.path.join(arguments.output, 'c'))
      if arguments.rust:
         from rust_backend import generate_rust
         generate_rust(arguments.input, os.path.join(arguments.output, 'rust'))
      if arguments.python:
         from python_backend import generate_python
         generate_python(arguments.input, os.path.join(arguments.output, 'python'))
   except ValueError as error:
      argument_parser.error(str(error))


if __name__ == '__main__':
   main()
