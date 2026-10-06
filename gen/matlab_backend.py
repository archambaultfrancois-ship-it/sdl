#!/usr/bin/env python3
"""Matlab and GNU Octave code generation for SDL schemas."""

import os
import re

from generator import (canonical_type_descriptor, canonical_type_hash,
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


def _soa_assign(base, path, value):
   """Build nested struct expression assigning an empty SoA terminal."""
   field = path[-1]
   if len(path) == 1:
      return "setfield(" + base + "," + _quote(field) + "," + value + ")"
   child = _soa_assign('struct()', path[1:], value)
   return "setfield(" + base + "," + _quote(path[0]) + "," + child + ")"


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
      type_hash = canonical_type_hash(self.schema, type_name)
      out.append("case 'encode_payload'\n varargout{1}=sdl_matlab_runtime('encode_payload',varargin{1}," + fields_meta + ");\n")
      out.append("case 'decode_payload'\n varargout{1}=sdl_matlab_runtime('decode_payload',varargin{1}," + fields_meta + ");\n")
      out.append("case 'encode'\n varargout{1}=sdl_matlab_runtime('encode',varargin{1}," + descriptor + ',uint32(' + str(type_hash) + '),' + fields_meta + ");\n")
      out.append("case 'decode'\n varargout{1}=sdl_matlab_runtime('decode',varargin{1}," + descriptor + ',uint32(' + str(type_hash) + '),' + fields_meta + ");\n")
      out.append("case 'display'\n varargout{1}=sdl_matlab_runtime('display',varargin{1});\n")
      out.append("otherwise, error('SDL:InvalidAction','unknown action');\nend\nend\n")
      return ''.join(out)

   def _soa_empty(self, type_name):
      result = 'struct()'
      for path in self._terminal_paths(type_name):
         terminal_type = self._path_type(type_name, path)
         result = _soa_assign(result, path.split('.'), self._packed_empty(terminal_type))
      return result

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
   return r'''% Generated by the SDL Matlab backend.
function varargout=sdl_matlab_runtime(action,varargin)
switch action
case 'encode'
 p=sdl_matlab_runtime('encode_payload',varargin{1},varargin{4}); d=uint8(varargin{2}(:));
 varargout{1}=[u32(numel(d));d;u32(varargin{3});uint8(p(:))];
case 'decode'
 w=uint8(varargin{1}(:)); d=uint8(varargin{2}(:)); h=uint32(varargin{3});
 if numel(w)<8,error('SDL:Malformed','truncated frame');end
 n=double(readu(w,1,4)); if n>1048576||numel(w)<8+n,error('SDL:Malformed','invalid descriptor length');end
 if n~=numel(d)||~isequal(w(5:4+n),d),error('SDL:SchemaMismatch','descriptor mismatch');end
 if readu(w,5+n,4)~=h,error('SDL:SchemaMismatch','hash mismatch');end
 varargout{1}=sdl_matlab_runtime('decode_payload',w(9+n:end),varargin{4});
case 'encode_payload'
 v=varargin{1}; fields=varargin{2}; bytes=uint8([]);
 for k=1:numel(fields)
  f=fields{k}; x=v.(f.name); if strcmp(f.modifier,'optional')&&isempty(x)&&~(strcmp(f.type,'string')&&ischar(x)),continue;end
  if ~isempty(f.dims)
   p=pack_fixed(f.type,x,f.dims,f.enum); bytes=[bytes;u32(f.id);u32(numel(p));p(:)];
  elseif any(strcmp(f.modifier,{'packed','repeated'}))
   if strcmp(f.modifier,'packed')
    p=pack_packed_field(f,x);if ~isempty(p),bytes=[bytes;u32(f.id);u32(numel(p));p(:)];end
   else
    if ~iscell(x),x=num2cell(x(:));end
    for j=1:numel(x),p=pack_value(f.type,x{j},f.enum);bytes=[bytes;u32(f.id);u32(numel(p));p(:)];end
   end
  else
   p=pack_value(f.type,x,f.enum);bytes=[bytes;u32(f.id);u32(numel(p));p(:)];
  end
 end
 varargout{1}=bytes;
case 'decode_payload'
 bytes=uint8(varargin{1}(:));fields=varargin{2};value=struct();
 for k=1:numel(fields)
  f=fields{k}; if strcmp(f.modifier,'packed'),value.(f.name)=empty_packed_value(f);
  elseif strcmp(f.modifier,'repeated'),value.(f.name)=cell(0,1);
  elseif strcmp(f.modifier,'optional'),if strcmp(f.type,'string'),value.(f.name)=char([]);else,value.(f.name)=[];end
  elseif ~isempty(f.dims),value.(f.name)=zeros(fliplr(f.dims));
  else,value.(f.name)=default_value(f.type);end
 end
 pos=1;
 while pos<=numel(bytes)
  if numel(bytes)-pos+1<8,error('SDL:Malformed','truncated field header');end
  id=double(readu(bytes,pos,4));n=double(readu(bytes,pos+4,4));pos=pos+8;
  if n>numel(bytes)-pos+1,error('SDL:Malformed','truncated field payload');end
  ix=find(cellfun(@(f)double(f.id)==id,fields),1);part=bytes(pos:pos+n-1);
   if ~isempty(ix)
   f=fields{ix};
   if ~isempty(f.dims),x=unpack_fixed(f.type,part,f.dims,f.enum);
   elseif strcmp(f.modifier,'packed')
    decoded=unpack_packed_field(f,part);value.(f.name)=append_packed(value.(f.name),decoded,f);pos=pos+n;continue
   else,x=unpack_value(f.type,part,f.enum);end
   if strcmp(f.modifier,'repeated'),value.(f.name){end+1,1}=x;else,value.(f.name)=x;end
  end
  pos=pos+n;
 end
 varargout{1}=value;
case 'display',varargout{1}=evalc('disp(varargin{1})');
otherwise,error('SDL:InvalidAction','unknown runtime operation');
end
end
function x=default_value(t)
switch t
case 'bool',x=false;case {'int8','int16','int32','int64'},x=int32(0);
case 'fl32',x=single(0);case 'fl64',x=0;case {'c32','c64'},x=complex(0);
case 'string',x='';otherwise,x=int32(0);end
end
function b=pack_fixed(t,x,dims,is_enum)
if isempty(dims),b=pack_value(t,x,is_enum);return;end
if numel(x)~=prod(dims),error('SDL:InvalidValue','fixed array has wrong element count');end
b=pack_vector(t,x(:),is_enum);
end
function x=unpack_fixed(t,b,dims,is_enum)
sz=type_size(t)*prod(dims);if numel(b)~=sz,error('SDL:Malformed','invalid fixed array length');end
leaf=type_size(t);values=decode_many(t,b,leaf,is_enum);if iscell(values),values=cell2mat(values);end;shape=fliplr(dims);if numel(shape)==1,shape=[shape 1];end;x=reshape(values,shape);
end
function out=decode_many(t,b,sz,is_enum)
out=cell(numel(b)/sz,1);for k=1:numel(out),out{k}=unpack_value(t,b((k-1)*sz+1:k*sz),is_enum);end
if numel(out)==1,out=out{1};end
end
function b=pack_value(t,x,is_enum)
if nargin<3,is_enum=false;end
if is_enum,b=pack_raw('int32',int32(x));
elseif strcmp(t,'string'),b=unicode2native(char(x),'UTF-8');
elseif any(strcmp(t,{'bool','int8','int16','int32','int64','fl32','fl64','c32','c64'})),b=pack_raw(t,x);
else,error('SDL:InvalidType',['unsupported type ' t ', enum=' num2str(is_enum)]);end
end
function b=pack_vector(t,values,is_enum)
if strcmp(t,'string'),error('SDL:InvalidType','string has variable wire size');end
b=pack_packed(t,values,is_enum);
end
function b=pack_packed_field(f,x)
if isfield(f,'terminal')&&~isempty(f.terminal)
 n=packed_length(x,f.terminal);record_size=0;parts=cell(numel(f.terminal),1);field_sizes=zeros(numel(f.terminal),1);
 for z=1:numel(f.terminal)
  term=f.terminal{z}; vals=packed_get_path(x,term.path);
  parts{z}=pack_packed(term.type,vals,term.enum);field_sizes(z)=type_size(term.type)*max(1,prod(term.dims));record_size=record_size+field_sizes(z);
 end
 if n==0,b=uint8([]);return;end
 matrix=zeros(record_size,n,'uint8');offset=1;
 for z=1:numel(f.terminal)
  sz=field_sizes(z);matrix(offset:offset+sz-1,:)=reshape(parts{z},sz,n);offset=offset+sz;
 end
 b=matrix(:);
else
 b=pack_packed(f.type,x,f.enum);
end
end
function n=packed_length(x,paths)
n=[];for z=1:numel(paths),term=paths{z};v=packed_get_path(x,term.path);records=numel(v)/max(1,prod(term.dims));if records~=fix(records),error('SDL:InvalidValue','SoA terminal array has invalid size');end;if isempty(n),n=records;elseif n~=records,error('SDL:InvalidValue','packed struct array columns have unequal lengths');end,end
if isempty(n),n=0;end
end
function v=packed_get_path(x,path)
parts=strsplit(path,'.');v=x;for z=1:numel(parts),v=v.(parts{z});end
end
function b=pack_packed(t,values,is_enum)
if isempty(values),b=uint8([]);return;end
if is_enum
 if iscell(values),nums=cell2mat(values(:)');else,nums=values(:);end;b=typecast(int32(nums),'uint8');unit=4;
elseif any(strcmp(t,{'c32','c64'}))
 if iscell(values),nums=cellfun(@real,values);imags=cellfun(@imag,values);else,nums=real(values);imags=imag(values);end
 if strcmp(t,'c32'),nums=single(nums);imags=single(imags);else,nums=double(nums);imags=double(imags);end
 raw=[nums(:)';imags(:)'];b=typecast(raw(:),'uint8'); unit=type_size(t)/2;
else
 if iscell(values),nums=cell2mat(values(:)');else,nums=values(:);end
 if strcmp(t,'bool'),b=uint8(logical(nums));elseif strcmp(t,'fl32'),b=typecast(single(nums),'uint8');elseif strcmp(t,'fl64'),b=typecast(double(nums),'uint8');else,b=typecast(cast(nums,t),'uint8');end;unit=type_size(t);
end
if little_endian()&&unit>1,b=reshape(b,unit,[]);b=flipud(b);b=b(:);end
b=uint8(b(:));
end
function b=pack_raw(t,x)
switch t
case 'bool',b=uint8(logical(x));
case {'int8','int16','int32','int64'},b=typecast(cast(x,t),'uint8');
case {'fl32','fl64'},if strcmp(t,'fl32'),x=single(x);else,x=double(x);end;b=typecast(x,'uint8');
case {'c32','c64'},if strcmp(t,'c32'),z=single([real(x),imag(x)]);else,z=double([real(x),imag(x)]);end;b=typecast(z,'uint8');
end
if little_endian()
 if any(strcmp(t,{'c32','c64'})),unit=type_size(t)/2;else,unit=type_size(t);end
 if unit>1,b=reshape(b,unit,[]);b=flipud(b);b=b(:);end
end
b=uint8(b(:));
end
function x=unpack_value(t,b,is_enum)
sz=type_size(t);if (~isempty(sz)&&numel(b)~=sz),error('SDL:Malformed',['invalid scalar size for ' t]);end
if little_endian()
 if any(strcmp(t,{'c32','c64'})),unit=sz/2;else,unit=sz;end
 if unit>1,b=reshape(b,unit,[]);b=flipud(b);b=b(:);end
end
switch t
case 'bool',if b(1)>1,error('SDL:Malformed','invalid bool');end;x=logical(b(1));
case {'int8','int16','int32','int64'},x=typecast(uint8(b),t);
case {'fl32','fl64'},if strcmp(t,'fl32'),x=typecast(uint8(b),'single');else,x=typecast(uint8(b),'double');end
case {'c32','c64'},if strcmp(t,'c32'),z=typecast(uint8(b),'single');else,z=typecast(uint8(b),'double');end;x=complex(z(1),z(2));
case 'string',if isempty(b),x='';else,x=native2unicode(uint8(b(:))','UTF-8');end
otherwise,x=typecast(uint8(b),'int32');
end
end
function out=unpack_packed_field(f,b)
if ~isempty(f.terminal)
 recsize=0;sizes=zeros(numel(f.terminal),1);for z=1:numel(f.terminal),term=f.terminal{z};sizes(z)=type_size(term.type)*max(1,prod(term.dims));recsize=recsize+sizes(z);end
 if recsize==0||mod(numel(b),recsize),error('SDL:Malformed','invalid packed struct payload length');end
 n=numel(b)/recsize;records=reshape(uint8(b(:)),recsize,n);out=struct();pos=1;
 for z=1:numel(f.terminal)
  term=f.terminal{z};sz=sizes(z);column_bytes=records(pos:pos+sz-1,:);
  shape=[fliplr(term.dims),n];if isempty(term.dims),shape=[n,1];end
  out=set_path(out,term.path,reshape(unpack_packed(term.type,column_bytes(:),term.enum),shape));pos=pos+sz;
 end
else
 sz=type_size(f.type);if isempty(sz)||mod(numel(b),sz),error('SDL:Malformed','invalid packed payload length');end
 count=numel(b)/sz;if any(strcmp(f.type,{'c32','c64'})),column_bytes=reshape(b,sz,count);else,column_bytes=reshape(b,sz,count);end;out=unpack_packed(f.type,column_bytes,f.enum);
end
end
function values=unpack_packed(t,columns,is_enum)
sz=type_size(t);if is_enum,t='int32';end
if little_endian()
 if any(strcmp(t,{'c32','c64'})),unit=sz/2;else,unit=sz;end
 if unit>1,columns=reshape(columns,unit,[]);columns=flipud(columns);columns=columns(:);end
end
if strcmp(t,'bool')
 if any(columns(:)>1),error('SDL:Malformed','invalid packed bool value');end;values=logical(columns(:));
elseif any(strcmp(t,{'c32','c64'}))
 if strcmp(t,'c32'),raw=typecast(uint8(columns(:)),'single');else,raw=typecast(uint8(columns(:)),'double');end
 components=reshape(raw,2,[]);values=complex(components(1,:),components(2,:)).';
else
 if strcmp(t,'fl32'),values=typecast(uint8(columns(:)),'single');elseif strcmp(t,'fl64'),values=typecast(uint8(columns(:)),'double');else,values=typecast(uint8(columns(:)),t);end;values=values(:);
end
end
function v=empty_terminal(t,n)
if any(strcmp(t,{'c32','c64'})),v=complex(zeros(n,1));elseif strcmp(t,'string'),v=cell(n,1);else,v=zeros(n,1);end
end
function s=set_path(s,path,value)
parts=strsplit(path,'.');if numel(parts)==1,s.(parts{1})=value;else,if ~isfield(s,parts{1}),s.(parts{1})=struct();end;s.(parts{1})=set_path(s.(parts{1}),strjoin(parts(2:end),'.'),value);end
end
function target=append_packed(target,source,f)
if isempty(f.terminal)
 if isempty(target),target=source;else,target=[target(:);source(:)];end
else
 for z=1:numel(f.terminal),term=f.terminal{z};path=term.path;vals=packed_get_path(source,path);old=packed_get_path(target,path);if isempty(old),joined=vals;else,joined=cat(numel(term.dims)+1,old,vals);end;target=set_path(target,path,joined);end
end
end
function value=empty_packed_value(f)
if isempty(f.terminal),value=[];else,value=struct();for z=1:numel(f.terminal),term=f.terminal{z};value=set_path(value,term.path,empty_terminal(term.type,0));end,end
end
function n=type_size(t)
switch t
case {'bool','int8'},n=1;case 'int16',n=2;case {'int32','fl32'},n=4;
case {'int64','fl64','c32'},n=8;case 'c64',n=16;case 'string',n=[];
otherwise,n=4; % SDL enums are signed int32 values.
end
end
function yes=little_endian(),setting=getenv('SDL_WIRE_ENDIAN');if isempty(setting),yes=false;else,yes=strcmpi(setting,'little');end,end
function b=u32(x),b=typecast(uint32(x),'uint8');if little_endian(),b=flipud(b(:));else,b=b(:);end,end
function x=readu(b,p,n),q=b(p:p+n-1);if little_endian(),q=flipud(q);end;x=typecast(uint8(q),['uint' num2str(8*n)]);end
'''
