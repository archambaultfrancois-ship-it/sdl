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
   def __init__(self, index, modifier, type_name, name):
      self.index = int(index)
      self.modifier = modifier
      self.type_name = type_name
      self.name = name


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
            current_enum = None
            current_msg = None
            continue
         if current_enum is not None:
            match = re.match(r'(\w+)\s*=\s*(-?\d+)\s*;', line)
            if not match:
               raise ValueError('invalid enum entry: ' + line)
            current_enum.pairs.append((match.group(1), match.group(2)))
            continue
         if current_msg is not None:
            match = re.match(r'(\d+):\s+(optional|required|repeated|packed)\s+(\w+)\s+(\w+)\s*;', line)
            if not match:
               raise ValueError('invalid field declaration: ' + line)
            current_msg.fields.append(Field(match.group(1), match.group(2),
               match.group(3), match.group(4)))
            continue
         raise ValueError('unexpected SDL statement: ' + line)
      self.validate()

   def validate(self):
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
         total += field_size
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
         symbol = name.upper()
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
   if arguments.c:
      from c_backend import generate_c
      generate_c(arguments.input, os.path.join(arguments.output, 'c'))
   if arguments.rust:
      from rust_backend import generate_rust
      generate_rust(arguments.input, os.path.join(arguments.output, 'rust'))
   if arguments.python:
      from python_backend import generate_python
      generate_python(arguments.input, os.path.join(arguments.output, 'python'))


if __name__ == '__main__':
   main()
