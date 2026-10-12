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
            if result in ('SDL_FIELDS', 'SDL_DESCRIPTOR', 'SDL_NAME'):
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

   def _field_type(self, field):
      if field.modifier == 'packed' and field.type_name == 'c32':
         return 'SdlCodec.Complex32Array'
      if field.modifier == 'packed' and field.type_name == 'c64':
         return 'SdlCodec.Complex64Array'
      result = self._type(field.type_name)
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
      if level + 1 < len(dims):
         child_type = self._type(type_name) + '[]' * (len(dims) - level)
         child = self._default_array(type_name, dims, level + 1)
         return 'new ' + child_type + '{' + ','.join(child for unused in range(length)) + '}'
      return 'new ' + self._type(type_name) + '[]{' + ','.join(
         self._default_value(type_name) for unused in range(length)) + '}'

   def _field_metadata(self, field):
      dimensions = 'new int[]{' + ','.join(str(item) for item in field.array_dimensions) + '}'
      enum_type = 'true' if field.type_name in self.schema.enums else 'false'
      value_type = self._field_type(field) if field.modifier == 'packed' and field.type_name in ('c32', 'c64') else self._type(field.type_name)
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
         out.append('  public ' + cls + '() {\n')
         for field in fields:
            fname = java_identifier(field.name)
            if field.array_dimensions:
               out.append('   this.' + fname + '=' + self._default_array(
                  field.type_name, field.array_dimensions) + ';\n')
            elif field.modifier in ('packed', 'repeated'):
               if field.modifier == 'packed' and field.type_name in ('c32', 'c64'):
                  complex_array = 'Complex32Array' if field.type_name == 'c32' else 'Complex64Array'
                  out.append('   this.' + fname + '=new SdlCodec.' + complex_array + '();\n')
               else:
                  out.append('   this.' + fname + '=new java.util.ArrayList<' +
                     self._type(field.type_name) + '>();\n')
            elif field.modifier == 'optional':
               out.append('   this.' + fname + '=null;\n')
            else:
               out.append('   this.' + fname + '=' + self._default_value(field.type_name) + ';\n')
         out.append('  }\n')
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
