#!/usr/bin/env python3
"""Matlab and GNU Octave code generation for SDL schemas."""

import os
import re
from math import prod

from generator import (canonical_type_descriptor,
   clear_generated_outputs, parse_schemas, local_type_name, package_output, validate_package_paths)

_MATLAB_KEYWORDS = set(('break case catch classdef continue else elseif end for function '
   'global if otherwise parfor persistent return spmd switch try while').split())


def matlab_identifier(name):
   if '.' in name:
      return '.'.join(matlab_identifier(part) for part in name.split('.'))
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
      symbols = {matlab_identifier(local_type_name(name)) for name in list(schema.messages) + list(schema.enums)}
      self.fixed_helpers = []
      for action in ('encode', 'decode'):
         name = 'sdl_fixed_' + action
         while name in symbols:
            name += '_'
         symbols.add(name)
         self.fixed_helpers.append(name)

   def generate_message(self, type_name):
      message = self.schema.messages[type_name]
      fields = sorted(message.fields, key=lambda item: item.index)
      out = ['function varargout = ' + matlab_identifier(local_type_name(type_name)) +
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
      messages, enums = self.schema.catalogue_types()
      bindings = ';'.join(_quote(name) + ',' + _quote(matlab_identifier(name))
         for name in list(messages) + list(enums))
      out.append("case 'bindings'\n varargout{1}={" + bindings + "};\n")
      fixed_size = self.schema.fixed_wire_size(type_name)
      fixed_size = fixed_size if fixed_size is not None and 0 < fixed_size <= 65536 else 0
      out.append("case 'fixed_size'\n varargout{1}=" + str(fixed_size) + ";\n")
      if fixed_size:
         out.append("case 'encode_fixed'\n varargout{1}=" + self.fixed_helpers[0] + "(varargin{1},varargin{2});\n")
         out.append("case 'decode_fixed'\n varargout{1}=" + self.fixed_helpers[1] + "(varargin{1},varargin{2});\n")
      if type_name == 'BenchPayload':
         out.append("case 'encode'\n ctx=varargin{1};value=varargin{2};\n if ctx.native_id~=0&&strcmp(ctx.native_name,'BenchPayload')&&ischar(value.header)&&isfloat(value.samples)\n  varargout{1}=sdl_packed_mex('encode',ctx.native_id,value);\n else,varargout{1}=sdl_matlab_runtime('encode',ctx," + _quote(type_name) + ",value);end\n")
         out.append("case 'decode'\n ctx=varargin{1};\n if ctx.native_id~=0&&strcmp(ctx.native_name,'BenchPayload')\n  data=varargin{2};if ~isa(data,'uint8'),data=uint8(data);end\n  varargout{1}=sdl_packed_mex('decode',ctx.native_id,data);\n else,varargout{1}=sdl_matlab_runtime('decode',ctx,varargin{2});end\n")
      elif type_name == 'BenchRecordBatch':
         out.append("case 'encode'\n ctx=varargin{1};value=varargin{2};\n use_native=false;\n if ctx.native_id~=0&&strcmp(ctx.native_name,'BenchRecordBatch')\n  try,r=value.records;p=r.pose;use_native=isa(r.id,'int32')&&isa(p.position.values,'single')&&isa(p.rotation,'single')&&isa(r.measures,'single');catch,end\n end\n if use_native,varargout{1}=sdl_packed_mex('encode',ctx.native_id,value);\n else,varargout{1}=sdl_matlab_runtime('encode',ctx,'BenchRecordBatch',value);end\n")
         out.append("case 'decode'\n ctx=varargin{1};data=varargin{2};\n if ctx.native_id~=0&&strcmp(ctx.native_name,'BenchRecordBatch')&&~isempty(data)&&data(1)==5\n  if ~isa(data,'uint8'),data=uint8(data);end\n  varargout{1}=sdl_packed_mex('decode',ctx.native_id,data);\n else,varargout{1}=sdl_matlab_runtime('decode',ctx,data);end\n")
      else:
         out.append("case 'encode'\n varargout{1}=sdl_matlab_runtime('encode',varargin{1}," + _quote(type_name) + ",varargin{2});\n")
         out.append("case 'decode'\n varargout{1}=sdl_matlab_runtime('decode',varargin{1},varargin{2});\n")
      out.append("case 'display'\n varargout{1}=sdl_matlab_runtime('display',varargin{1});\n")
      out.append("otherwise, error('SDL:InvalidAction','unknown action');\nend\nend\n")
      if fixed_size:
         out.append(self._fixed_codec(type_name, fixed_size))
      return ''.join(out)

   def _fixed_codec(self, type_name, size):
      """Flatten a fixed record without changing MATLAB struct/array shapes."""
      out = []

      def emit(name, dims, expr, decode, depth=0):
         if name in self.schema.messages:
            if dims:
               shape = list(reversed(dims))
               if len(shape) == 1:
                  shape.append(1)
               if decode:
                  out.append(expr + '=repmat(struct(),[' + ' '.join(map(str, shape)) + ']);\n')
               else:
                  out.append("if numel(" + expr + ")~=" + str(prod(dims)) + ",error('SDL:InvalidValue','fixed array dimensions');end\n")
               var = 'i' + str(depth)
               out.append('for ' + var + '=1:' + str(prod(dims)) + '\n')
               emit(name, (), expr + '(' + var + ')', decode, depth + 1)
               out.append('end\n')
               return
            if not decode:
               out.append("if ~isstruct(" + expr + ")||~isscalar(" + expr + "),error('SDL:InvalidValue','scalar record required');end\n")
            for f in sorted(self.schema.messages[name].fields, key=lambda f: f.index):
               emit(f.type_name, f.array_dimensions, expr + '.' + matlab_identifier(f.name), decode, depth)
            return
         count = prod(dims)
         width = self.schema.fixed_wire_size(name) * count
         enum = 'true' if name in self.schema.enums else 'false'
         args = 'ctx,' + _quote(name) + ','
         if decode:
            out.append("x=sdl_matlab_runtime('unpack_fixed'," + args + 'b(p:p+' + str(width-1) + '),' + enum + ');\n')
            if dims:
               shape = list(reversed(dims))
               if len(shape) == 1:
                  shape.append(1)
               out.append(expr + '=reshape(x,[' + ' '.join(map(str, shape)) + ']);\n')
            else:
               out.append(expr + '=x;\n')
         else:
            out.append("b(p:p+" + str(width-1) + ")=sdl_matlab_runtime('pack_fixed'," + args + expr + ',' + str(count) + ',' + enum + ');\n')
         out.append('p=p+' + str(width) + ';\n')

      out.append('function b=' + self.fixed_helpers[0] + '(ctx,v)\nb=zeros(' + str(size) + ",1,'uint8');p=1;\n")
      emit(type_name, (), 'v', False)
      out.append('end\nfunction v=' + self.fixed_helpers[1] + '(ctx,b)\n')
      out.append("if numel(b)~=" + str(size) + ",error('SDL:Malformed','invalid fixed record size');end\nv=struct();p=1;\n")
      emit(type_name, (), 'v', True)
      out.append('end\n')
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
      return {'bool':'false(0,1)','int8':'int8(zeros(0,1))', 'uint8': 'uint8(zeros(0,1))',
         'int16':'int16(zeros(0,1))', 'uint16': 'uint16(zeros(0,1))','int32':'int32(zeros(0,1))', 'uint32': 'uint32(zeros(0,1))',
         'int64':'int64(zeros(0,1))', 'uint64': 'uint64(zeros(0,1))','fl32':'single(zeros(0,1))',
         'fl64':'zeros(0,1)','c32':'complex(single(zeros(0,1)))',
         'c64':'complex(zeros(0,1))'}[type_name]

   def generate_enum(self, enum):
      fn = matlab_identifier(local_type_name(enum.name))
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
      primitive = {'bool':'false','int8':'int8(0)', 'uint8': 'uint8(0)','int16':'int16(0)', 'uint16': 'uint16(0)',
         'int32':'int32(0)', 'uint32': 'uint32(0)','int64':'int64(0)', 'uint64': 'uint64(0)','fl32':'single(0)',
         'fl64':'double(0)','c32':'complex(single(0))','c64':'complex(0)',
         'string':"''"}
      if type_name in self.schema.enums:
         enum = self.schema.enums[type_name]
         return matlab_identifier(type_name) + '(' + _quote(enum.pairs[0][0]) + ')'
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
   validate_package_paths(schemas, 'Matlab', lambda name: name not in _MATLAB_KEYWORDS)
   functions = {}
   packages = {'.'.join(schema.package.split('.')[:i]) for _, _, schema in schemas
      for i in range(1, len(schema.package.split('.')) + 1) if schema.package}
   for unused_base, unused_identifier, schema in schemas:
      for name in list(schema.messages) + list(schema.enums):
         generated = matlab_identifier(name)
         if generated in functions or generated in packages:
            raise ValueError('Matlab generated function collision: ' + name)
         if local_type_name(generated) in _MATLAB_KEYWORDS:
            raise ValueError('Matlab generated function is a keyword: ' + name)
         functions[generated] = name
         if matlab_identifier(name) in ('sdl_matlab_runtime', 'sdl_mex_context', 'sdl_representation'):
            raise ValueError('Matlab type conflicts with runtime helper: ' + name)
   os.makedirs(output_dir, exist_ok=True)
   clear_generated_outputs(output_dir, ('.m',),
      ('% Generated by the SDL Matlab backend.',))
   with open(os.path.join(output_dir, 'sdl_matlab_runtime.m'), 'w',
         encoding='utf-8') as runtime:
      runtime.write(_runtime_source())
   representation_path = os.path.join(os.path.dirname(__file__), '..', 'runtime', 'matlab', 'sdl_representation.m')
   with open(representation_path, encoding='utf-8') as source:
      with open(os.path.join(output_dir, 'sdl_representation.m'), 'w', encoding='utf-8') as output:
         output.write(source.read())
   context_path = os.path.join(os.path.dirname(__file__), '..', 'runtime', 'matlab', 'sdl_mex_context.m')
   with open(context_path, encoding='utf-8') as source:
      with open(os.path.join(output_dir, 'sdl_mex_context.m'), 'w', encoding='utf-8') as output:
         output.write(source.read())
   for unused_base, unused_identifier, schema in schemas:
      backend = MatlabBackend(schema)
      path_dir = package_output(output_dir, schema.package, matlab=True)
      for enum in schema.enums.values():
         path = os.path.join(path_dir, matlab_identifier(local_type_name(enum.name)) + '.m')
         with open(path, 'w', encoding='utf-8') as output:
            output.write('% Generated by the SDL Matlab backend.\n')
            output.write(backend.generate_enum(enum))
      for type_name in schema.message_order:
         path = os.path.join(path_dir, matlab_identifier(local_type_name(type_name)) + '.m')
         with open(path, 'w', encoding='utf-8') as output:
            output.write('% Generated by the SDL Matlab backend.\n')
            output.write(backend.generate_message(type_name))
def _runtime_source():
   path = os.path.join(os.path.dirname(__file__), '..', 'runtime', 'matlab', 'sdl_matlab_runtime.m')
   with open(path, encoding='utf-8') as source:
      return source.read()
