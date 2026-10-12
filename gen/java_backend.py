#!/usr/bin/env python3
"""Java 7 compatible code generation for SDL schemas."""

import os
import json
import re

from generator import (c_identifier, canonical_type_descriptor,
   clear_generated_outputs, parse_schemas)


_JAVA_KEYWORDS = set(('abstract assert boolean break byte case catch char class '
   'const continue default do double else enum extends final finally float for '
   'goto if implements import instanceof int interface long native new package '
   'private protected public return short static strictfp super switch '
   'synchronized this throw throws transient try void volatile while true false '
   'null').split())


def java_identifier(value):
   name = c_identifier(value)
   if name in _JAVA_KEYWORDS:
      name += '_'
   return name


def java_type_name(value):
   return java_identifier(value.replace('$', '_'))


class JavaBackend:
   def __init__(self, schema, outer_name):
      self.schema = schema
      self.outer_name = outer_name
      self._fixed_sizes = {}
      self._validate_names()

   def _validate_names(self):
      if self.outer_name == 'SdlCodec':
         raise ValueError('Java schema wrapper conflicts with runtime class SdlCodec')
      generated = {}
      for name in list(self.schema.enums) + list(self.schema.message_order):
         result = java_type_name(name)
         if result == 'SdlCodec':
            raise ValueError('Java generated type conflicts with runtime class SdlCodec: ' + name)
         if result == self.outer_name:
            raise ValueError('Java generated type conflicts with schema wrapper: ' + name)
         if result in generated:
            raise ValueError('Java type name collision after conversion: ' + name +
               ' and ' + generated[result])
         generated[result] = name
      for enum in self.schema.enums.values():
         seen = set()
         for name, unused_value in enum.pairs:
            result = java_identifier(name)
            if result in ('values', 'valueOf', 'wireValue', 'fromWire'):
               raise ValueError('Java enum item conflicts with generated member in ' +
                  enum.name + ': ' + name)
            if result in seen:
               raise ValueError('Java enum item collision after conversion in ' + enum.name)
            seen.add(result)
      for message_name in self.schema.message_order:
         seen = set()
         for field in self.schema.messages[message_name].fields:
            result = java_identifier(field.name)
            if result in ('SDL_FIELDS', 'SDL_DESCRIPTOR', 'SDL_NAME', 'SDL_FIXED'):
               raise ValueError('Java field conflicts with generated metadata in ' +
                  message_name + ': ' + field.name)
            if result in seen:
               raise ValueError('Java field collision after conversion in ' + message_name)
            seen.add(result)

   def _type(self, name):
      builtin = {'bool':'Boolean', 'int8':'Byte', 'int16':'Short',
         'int32':'Integer', 'int64':'Long', 'fl32':'Float', 'fl64':'Double',
         'c32':'SdlCodec.Complex32', 'c64':'SdlCodec.Complex64',
         'string':'String'}
      return builtin.get(name, self.outer_name + '.' + java_type_name(name))

   def _primitive(self, name):
      return {'bool':'boolean','int8':'byte','int16':'short','int32':'int',
         'int64':'long','fl32':'float','fl64':'double'}.get(name)

   def _array_type(self, name):
      return self._primitive(name) or self._type(name)

   def _fixed_size(self, name, active=None):
      if name in self._fixed_sizes: return self._fixed_sizes[name]
      sizes = {'bool':1,'int8':1,'int16':2,'int32':4,'int64':8,
         'fl32':4,'fl64':8,'c32':8,'c64':16}
      if name in sizes: return sizes[name]
      if name in self.schema.enums: return 4
      active = set() if active is None else active
      if name not in self.schema.messages or name in active: return None
      active = active | {name}
      total = 0
      for f in self.schema.messages[name].fields:
         size = self._fixed_size(f.type_name, active)
         if f.modifier != 'required' or size is None: return None
         for dim in f.array_dimensions: size *= dim
         total += size
         if total > 2147483647: return None
      self._fixed_sizes[name] = total or None
      return total or None

   def _fixed_codec(self, message, cls):
      size = self._fixed_size(message.name)
      if size is None: return ''
      out = ['  public static final SdlCodec.FixedCodec SDL_FIXED = new SdlCodec.FixedCodec(){\n',
         '   public int size(){return %d;}\n' % size,
         '   public void write(Object value, byte[] bytes, int offset){\n',
         '    if(!(value instanceof %s)) throw new SdlCodec.CodecException("invalid fixed record");\n' % cls,
         '    %s v=(%s)value;\n' % (cls, cls)]
      def emit(name, dims, expr, decode, indent='    ', level=0):
         if dims:
            if decode:
               out.append(indent + expr + '=new ' + self._array_type(name) +
                  '[%d]' % dims[0] + '[]' * (len(dims)-1) + ';\n')
            else:
               out.append(indent + 'if('+expr+'==null || '+expr+'.length!='+str(dims[0])+') throw new SdlCodec.CodecException("invalid fixed array");\n')
            var = 'i%d' % level
            out.append(indent+'for(int '+var+'=0;'+var+'<'+str(dims[0])+';'+var+'++){\n')
            emit(name,dims[1:],expr+'['+var+']',decode,indent+' ',level+1)
            out.append(indent+'}\n'); return
         n = self._fixed_size(name)
         if not decode and not self._primitive(name):
            out.append(indent+'if('+expr+'==null) throw new SdlCodec.CodecException("null required value");\n')
         if name in self.schema.messages:
            codec=self._type(name)+'.SDL_FIXED'
            line=(expr+'=('+self._type(name)+')'+codec+'.read(bytes,offset);' if decode else
               codec+'.write('+expr+',bytes,offset);')
         elif decode:
            read='SdlCodec.getWord(bytes,offset,%d)' % n
            if name in self.schema.enums: rhs=self._type(name)+'.fromWire((int)'+read+')'
            elif name=='bool': rhs='SdlCodec.getBool(bytes,offset)'
            elif name=='fl32': rhs='Float.intBitsToFloat((int)'+read+')'
            elif name=='fl64': rhs='Double.longBitsToDouble('+read+')'
            elif name in ('c32','c64'):
               half=n//2; conv='Float.intBitsToFloat((int)' if half==4 else 'Double.longBitsToDouble('
               rhs='new '+self._type(name)+'('+conv+'SdlCodec.getWord(bytes,offset,%d)), '%half+conv+'SdlCodec.getWord(bytes,offset+%d,%d)))'%(half,half)
            else: rhs='('+self._primitive(name)+')'+read
            line=expr+'='+rhs+';'
         else:
            if name in self.schema.enums: val=expr+'.wireValue()'
            elif name=='bool': val=expr+'?1:0'
            elif name=='fl32': val='Float.floatToRawIntBits('+expr+')'
            elif name=='fl64': val='Double.doubleToRawLongBits('+expr+')'
            elif name in ('c32','c64'):
               half=n//2; conv='Float.floatToRawIntBits' if half==4 else 'Double.doubleToRawLongBits'
               line='SdlCodec.putWord(bytes,offset,'+conv+'('+expr+'.real),%d); '%half+'SdlCodec.putWord(bytes,offset+%d,'%half+conv+'('+expr+'.imag),%d);'%half
               out.append(indent+line+' offset+=%d;\n'%n); return
            else: val=expr
            line='SdlCodec.putWord(bytes,offset,'+val+',%d);'%n
         out.append(indent+line+' offset+=%d;\n'%n)
      fields=sorted(message.fields,key=lambda f:f.index)
      for f in fields: emit(f.type_name,f.array_dimensions,'v.'+java_identifier(f.name),False)
      out.append('   }\n   public Object read(byte[] bytes,int offset){\n    '+cls+' v=new '+cls+'(false);\n')
      for f in fields: emit(f.type_name,f.array_dimensions,'v.'+java_identifier(f.name),True)
      out.append('    return v;\n   }\n  };\n')
      return ''.join(out)

   def _field_type(self, field):
      if field.modifier == 'packed' and field.type_name == 'c32':
         return 'SdlCodec.Complex32Array'
      if field.modifier == 'packed' and field.type_name == 'c64':
         return 'SdlCodec.Complex64Array'
      if field.modifier == 'packed' and self._primitive(field.type_name):
         return self._primitive(field.type_name) + '[]'
      result = self._array_type(field.type_name) if field.array_dimensions or field.modifier == 'required' else self._type(field.type_name)
      for unused in field.array_dimensions:
         result += '[]'
      if field.modifier in ('packed', 'repeated'):
         result = 'java.util.List<' + result + '>'
      elif field.modifier == 'optional' and not field.array_dimensions:
         result = result
      return result

   def _default_value(self, type_name):
      if type_name in self.schema.enums:
         first = self.schema.enums[type_name].pairs[0][0]
         return self.outer_name + '.' + java_type_name(type_name) + '.' + java_identifier(first)
      if type_name in self.schema.messages:
         return 'new ' + self.outer_name + '.' + java_type_name(type_name) + '()'
      return {'bool':'Boolean.FALSE','int8':'Byte.valueOf((byte)0)',
         'int16':'Short.valueOf((short)0)','int32':'Integer.valueOf(0)',
         'int64':'Long.valueOf(0L)','fl32':'Float.valueOf(0.0f)',
         'fl64':'Double.valueOf(0.0)','c32':'new SdlCodec.Complex32()',
         'c64':'new SdlCodec.Complex64()','string':'""'}[type_name]

   def _default_array(self, type_name, dims, level=0):
      length = dims[level]
      if self._primitive(type_name):
         return 'new ' + self._primitive(type_name) + ''.join('[%d]' % n for n in dims[level:])
      if level + 1 < len(dims):
         child_type = self._type(type_name) + '[]' * (len(dims) - level)
         child = self._default_array(type_name, dims, level + 1)
         return 'new ' + child_type + '{' + ','.join(child for unused in range(length)) + '}'
      return 'new ' + self._type(type_name) + '[]{' + ','.join(
         self._default_value(type_name) for unused in range(length)) + '}'

   def _field_metadata(self, field):
      dimensions = 'new int[]{' + ','.join(str(item) for item in field.array_dimensions) + '}'
      enum_type = 'true' if field.type_name in self.schema.enums else 'false'
      value_type = self._field_type(field) if field.modifier == 'packed' and field.type_name in ('c32', 'c64') else (self._array_type(field.type_name) if field.array_dimensions or field.modifier in ('packed', 'required') else self._type(field.type_name))
      return ('new SdlCodec.Field(' + str(field.index) + 'L,"' +
         java_identifier(field.name) + '","' + field.modifier + '","' +
         field.type_name + '",' + value_type + '.class,' +
         dimensions + ',' + enum_type + ')')

   def generate(self):
      out = ['// Generated by the SDL Java backend.\n',
         '// Compatible with Java 7.\n',
         'public final class ' + self.outer_name + ' {\n private ' +
         self.outer_name + '() {}\n']
      for enum in self.schema.enums.values():
         name = java_type_name(enum.name)
         out.append(' public enum ' + name + ' implements SdlCodec.EnumValue {\n')
         items = []
         for item, number in enum.pairs:
            items.append('  ' + java_identifier(item) + '(' + number + ')')
         out.append(',\n'.join(items) + ';\n  private final int wireValue;\n')
         out.append('  ' + name + '(int value) { this.wireValue=value; }\n')
         out.append('  public int wireValue() { return wireValue; }\n')
         out.append('  public static ' + name + ' fromWire(int value) {\n')
         out.append('   for (' + name + ' item : values()) if (item.wireValue==value) return item;\n')
         out.append('   throw new SdlCodec.CodecException("invalid enum value for ' + enum.name + '");\n  }\n }\n')
      for message_name in self.schema.message_order:
         message = self.schema.messages[message_name]
         cls = java_type_name(message_name)
         fields = sorted(message.fields, key=lambda item:item.index)
         out.append(' public static final class ' + cls + ' implements SdlCodec.Message {\n')
         for field in fields:
            out.append('  public ' + self._field_type(field) + ' ' +
               java_identifier(field.name) + ';\n')
         out.append('  public static final SdlCodec.Field[] SDL_FIELDS = new SdlCodec.Field[]{\n')
         out.append(',\n'.join('   ' + self._field_metadata(field) for field in fields))
         out.append('\n  };\n')
         out.append('  public static final String SDL_NAME = ' + json.dumps(message_name,ensure_ascii=False) + ';\n')
         text = canonical_type_descriptor(self.schema).decode('utf-8')
         chunks = [text[i:i + 12000] for i in range(0, len(text), 12000)]
         expression = 'new StringBuilder()' + ''.join('.append(' + json.dumps(chunk, ensure_ascii=False) + ')' for chunk in chunks) + '.toString()'
         out.append('  public static final String SDL_DESCRIPTOR = ' + expression + ';\n')
         out.append('  public ' + cls + '() { this(true); }\n')
         out.append('  private ' + cls + '(boolean defaults) { if(!defaults) return;\n')
         for field in fields:
            fname = java_identifier(field.name)
            if field.array_dimensions:
               out.append('   this.' + fname + '=' + self._default_array(
                  field.type_name, field.array_dimensions) + ';\n')
            elif field.modifier in ('packed', 'repeated'):
               if field.modifier == 'packed' and field.type_name in ('c32', 'c64'):
                  complex_array = 'Complex32Array' if field.type_name == 'c32' else 'Complex64Array'
                  out.append('   this.' + fname + '=new SdlCodec.' + complex_array + '();\n')
               elif field.modifier == 'packed' and self._primitive(field.type_name):
                  out.append('   this.' + fname + '=new ' + self._primitive(field.type_name) + '[0];\n')
               else:
                  out.append('   this.' + fname + '=new java.util.ArrayList<' +
                     self._type(field.type_name) + '>();\n')
            elif field.modifier == 'optional':
               out.append('   this.' + fname + '=null;\n')
            else:
               out.append('   this.' + fname + '=' + self._default_value(field.type_name) + ';\n')
         out.append('  }\n')
         out.append(self._fixed_codec(message, cls))
         out.append('  public byte[] encode(SdlCodec.Context context) { return SdlCodec.encode(context,this); }\n')
         out.append('  public static ' + cls + ' decode(SdlCodec.Context context,byte[] bytes) { return (' + cls + ')SdlCodec.decode(context,bytes); }\n')
         out.append('  public String toString() { return SdlCodec.display(this,SDL_FIELDS); }\n }\n')
      out.append('}\n')
      return ''.join(out)


def generate_java(input_path, output_dir):
   schemas = parse_schemas(input_path)
   os.makedirs(output_dir, exist_ok=True)
   outputs = []
   seen = set()
   for base, unused_identifier, schema in schemas:
      outer = java_identifier(base)
      if outer in seen:
         raise ValueError('SDL filenames map to the same Java wrapper: ' + outer)
      seen.add(outer)
      outputs.append((outer, JavaBackend(schema, outer).generate()))
   clear_generated_outputs(output_dir, ('.java',), (
      '// Generated by the SDL Java backend.',))
   runtime_path = os.path.join(output_dir, 'SdlCodec.java')
   with open(runtime_path, 'w', encoding='utf-8') as output:
      output.write(_runtime_source())
   for name, content in outputs:
      with open(os.path.join(output_dir, name + '.java'), 'w', encoding='utf-8') as output:
         output.write(content)


def _runtime_source():
   path = os.path.join(os.path.dirname(__file__), '..', 'runtime', 'java', 'SdlCodec.java')
   with open(os.path.abspath(path), 'r', encoding='utf-8') as source:
      return source.read()
