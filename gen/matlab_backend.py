#!/usr/bin/env python3
"""Matlab and GNU Octave code generation for SDL schemas."""

import os
import re

from generator import (canonical_type_descriptor,
   clear_generated_outputs, parse_schemas)


def matlab_identifier(name):
   result = re.sub(r'\W', '_', name)
   if not result or result[0].isdigit():
      result = '_' + result
   if result.startswith('__'):
      result = 'sdl' + result
   return result


def _quote(value):
   return "'" + value.replace("'", "''") + "'"


class MatlabBackend:
   def __init__(self, schema):
      self.schema = schema

   def generate_message(self, type_name):
      message = self.schema.messages[type_name]
      fields = sorted(message.fields, key=lambda item: item.index)
      out = ['function varargout = ' + matlab_identifier(type_name) +
         '(action,varargin)\n']
      out.append("if nargin==0, action='new'; end\nswitch action\n")
      out.append("case 'new'\n value=struct();\n")
      for field in fields:
         fname = matlab_identifier(field.name)
         if field.array_dimensions:
            # SDL dimensions are written outermost-first, while Matlab stores
            # the first dimension contiguously. Reverse them for Matlab shape
            # and force a column vector for one-dimensional arrays.
            dimensions = list(reversed(field.array_dimensions))
            if len(dimensions) == 1:
               dimensions.append(1)
            dims = ','.join(str(value) for value in dimensions)
            default = 'repmat(' + self._default(field.type_name) + ',[' + dims + '])'
         elif field.modifier == 'packed' and field.type_name in self.schema.messages:
            default = self._soa_empty(field.type_name)
         elif field.modifier == 'packed':
            default = self._packed_empty(field.type_name)
         elif field.modifier == 'repeated':
            default = 'cell(0,1)'
         elif field.modifier == 'optional':
            default = '[]'
         else:
            default = self._default(field.type_name)
         out.append(' value.' + fname + '=' + default + ';\n')
      out.append(" if ~isempty(varargin), supplied=varargin{1}; names=fieldnames(supplied); for k=1:numel(names), value.(names{k})=supplied.(names{k}); end, end\n varargout{1}=value;\n")
      fields_meta = self._metadata(fields)
      descriptor = '[' + ' '.join(str(byte) for byte in
         canonical_type_descriptor(self.schema, type_name)) + ']'
      out.append("case 'description'\n varargout{1}=native2unicode(uint8(" + descriptor + "),'UTF-8');\n")
      out.append("case 'fields'\n varargout{1}=" + fields_meta + ";\n")
      out.append("case 'name'\n varargout{1}=" + _quote(type_name) + ";\n")
      out.append("case 'encode'\n varargout{1}=sdl_matlab_runtime('encode',varargin{1}," + _quote(type_name) + ",varargin{2});\n")
      out.append("case 'decode'\n varargout{1}=sdl_matlab_runtime('decode',varargin{1},varargin{2});\n")
      out.append("case 'display'\n varargout{1}=sdl_matlab_runtime('display',varargin{1});\n")
      out.append("otherwise, error('SDL:InvalidAction','unknown action');\nend\nend\n")
      return ''.join(out)

   def _soa_empty(self, type_name):
      tree = {}
      for path in self._terminal_paths(type_name):
         node = tree
         parts = path.split('.')
         for part in parts[:-1]:
            node = node.setdefault(part, {})
         node[parts[-1]] = self._packed_empty(self._path_type(type_name, path))
      def render(node):
         items = []
         for key, value in node.items():
            items.extend([_quote(key), render(value) if isinstance(value, dict) else value])
         return 'struct(' + ','.join(items) + ')'
      return render(tree)

   def _packed_empty(self, type_name):
      if type_name in self.schema.enums:
         return 'int32(zeros(0,1))'
      return {'bool':'false(0,1)','int8':'int8(zeros(0,1))',
         'int16':'int16(zeros(0,1))','int32':'int32(zeros(0,1))',
         'int64':'int64(zeros(0,1))','fl32':'single(zeros(0,1))',
         'fl64':'zeros(0,1)','c32':'complex(single(zeros(0,1)))',
         'c64':'complex(zeros(0,1))'}[type_name]

   def generate_enum(self, enum):
      fn = matlab_identifier(enum.name)
      out = ['function value = ' + fn + '(name)\n']
      out.append('% Enum constants are int32 values; unknown numeric values are preserved.\n')
      out.append('if nargin==0, value=struct();\n')
      for item, number in enum.pairs:
         out.append(' value.' + matlab_identifier(item) + '=int32(' + number + ');\n')
      out.append(' return;\nend\n')
      out.append('if isnumeric(name), value=int32(name); return; end\nswitch name\n')
      for item, number in enum.pairs:
         out.append('case ' + _quote(item) + ', value=int32(' + number + ');\n')
      out.append("otherwise, error('SDL:InvalidEnum','unknown enum constant');\nend\nend\n")
      return ''.join(out)

   def _default(self, type_name):
      primitive = {'bool':'false','int8':'int8(0)','int16':'int16(0)',
         'int32':'int32(0)','int64':'int64(0)','fl32':'single(0)',
         'fl64':'double(0)','c32':'complex(single(0))','c64':'complex(0)',
         'string':"''"}
      if type_name in self.schema.enums:
         enum = self.schema.enums[type_name]
         return matlab_identifier(type_name) + '().' + matlab_identifier(enum.pairs[0][0])
      if type_name in self.schema.messages:
         return matlab_identifier(type_name) + "('new')"
      return primitive[type_name]

   def _metadata(self, fields):
      entries = []
      for field in fields:
         dims = '[' + ' '.join(str(x) for x in field.array_dimensions) + ']'
         enum_flag = 'true' if field.type_name in self.schema.enums else 'false'
         nested = self.schema.messages.get(field.type_name)
         terminal = [] if nested is None else self._terminal_paths(field.type_name)
         terminal_items = []
         for path in terminal:
            terminal_type = self._path_type(field.type_name, path)
            terminal_dims = self._path_dimensions(field.type_name, path)
            terminal_items.append("struct('path'," + _quote(path) + ",'type'," +
               _quote(terminal_type) + ",'enum'," +
               ('true' if terminal_type in self.schema.enums else 'false') +
               ",'dims',[" + ' '.join(str(size) for size in terminal_dims) + '])')
         terminal_meta = '{' + ','.join(terminal_items) + '}'
         entries.append("struct('id',uint32(" + str(field.index) + "),'name'," +
            _quote(matlab_identifier(field.name)) + ",'modifier'," +
            _quote(field.modifier) + ",'type'," + _quote(field.type_name) +
            ",'enum'," + enum_flag + ",'dims'," + dims + ",'terminal',{" +
            terminal_meta + '})')
      return '{' + ','.join(entries) + '}'

   def _string_cell(self, values):
      return '{' + ','.join(_quote(value) for value in values) + '}'

   def _terminal_paths(self, type_name, prefix=''):
      paths = []
      for field in sorted(self.schema.messages[type_name].fields,
            key=lambda item: item.index):
         field_name = matlab_identifier(field.name)
         path = field_name if not prefix else prefix + '.' + field_name
         if field.type_name in self.schema.messages:
            paths.extend(self._terminal_paths(field.type_name, path))
         else:
            paths.append(path)
      return paths

   def _path_type(self, root_type, path):
      current = root_type
      for segment in path.split('.'):
         field = next(item for item in self.schema.messages[current].fields
            if matlab_identifier(item.name) == segment)
         current = field.type_name
      return current

   def _path_dimensions(self, root_type, path):
      current = root_type
      dimensions = []
      for segment in path.split('.'):
         field = next(item for item in self.schema.messages[current].fields
            if matlab_identifier(item.name) == segment)
         dimensions.extend(field.array_dimensions)
         current = field.type_name
      return dimensions


def generate_matlab(input_path, output_dir):
   schemas = parse_schemas(input_path)
   os.makedirs(output_dir, exist_ok=True)
   clear_generated_outputs(output_dir, ('.m',),
      ('% Generated by the SDL Matlab backend.',))
   with open(os.path.join(output_dir, 'sdl_matlab_runtime.m'), 'w',
         encoding='utf-8') as runtime:
      runtime.write(_runtime_source())
   for unused_base, unused_identifier, schema in schemas:
      backend = MatlabBackend(schema)
      for enum in schema.enums.values():
         path = os.path.join(output_dir, matlab_identifier(enum.name) + '.m')
         with open(path, 'w', encoding='utf-8') as output:
            output.write('% Generated by the SDL Matlab backend.\n')
            output.write(backend.generate_enum(enum))
      for type_name in schema.message_order:
         path = os.path.join(output_dir, matlab_identifier(type_name) + '.m')
         with open(path, 'w', encoding='utf-8') as output:
            output.write('% Generated by the SDL Matlab backend.\n')
            output.write(backend.generate_message(type_name))
def _runtime_source():
   path = os.path.join(os.path.dirname(__file__), '..', 'runtime', 'matlab', 'sdl_matlab_runtime.m')
   with open(path, encoding='utf-8') as source:
      return source.read()
