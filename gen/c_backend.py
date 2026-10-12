#!/usr/bin/env python3
"""C code generation for SDL schemas."""

import os

from generator import (c_identifier, canonical_type_descriptor,
   clear_generated_outputs, parse_schemas)


_C_KEYWORDS = set((
   'auto break case char const continue default do double else enum extern float '
   'for goto if inline int long register restrict return short signed sizeof '
   'static struct switch typedef union unsigned void volatile while '
   '_Bool _Complex _Imaginary').split())
_C_RESERVED_ORDINARY_NAMES = set((
   'bool int8_t uint8_t int16_t uint16_t int32_t uint32_t int64_t uint64_t '
   'size_t ptrdiff_t SdlTypeKind SdlTypeDesc SdlFieldDesc SdlEnumValueDesc '
   'SdlUInt32Alignment SdlDynamicKind SdlDynamicComplex SdlDynamicEnum '
   'SdlDynamicString SdlDynamicArray SdlDynamicField SdlDynamicMessage '
   'SdlContext type_description type_prepare type_context_free type_message_name '
   'SdlDynamicValue type_encode_size type_encode type_decode_size type_decode '
   'type_clone type_display type_free type_decode_dynamic type_dynamic_free '
   'type_dynamic_get sdl_register_type').split())
_C_MACROS = set((
   'bool true false complex imaginary I NULL offsetof SDL_NO_OFFSET '
   'SDL_FIELD_OPTIONAL SDL_FIELD_REPEATED SDL_FIELD_PACKED '
   'SDL_UINT32_ALIGNMENT').split())
_C_RESERVED_HEADER_GUARDS = set((
   'SDL_CONTEXT_H SDL_DYNAMIC_H SDL_TYPE_DESCRIPTORS_H SDL_TYPE_ENGINE_H '
   'SDL_TYPE_PRIVATE_H SDL_TYPE_REGISTRY_H SDL_WIRE_H SDL_REGISTRY_H').split())


def _is_ascii_c_identifier(value):
   if not value or not value.isascii():
      return False
   first = value[0]
   if not (first.isalpha() or first == '_'):
      return False
   return all(character.isalnum() or character == '_' for character in value[1:])


class CBackend:
   def __init__(self, schema):
      self.schema = schema

   def _c_type(self, name):
      return {'bool': 'bool', 'int8': 'int8_t', 'uint8': 'uint8_t', 'int16': 'int16_t', 'uint16': 'uint16_t', 'int32': 'int32_t', 'uint32': 'uint32_t',
         'int64': 'int64_t', 'uint64': 'uint64_t', 'fl32': 'float', 'fl64': 'double',
         'c32': 'float complex', 'c64': 'double complex',
         'string': 'const char *'}.get(name, c_identifier(name))

   def _type_desc(self, name):
      descriptors = {'bool': 'SDL_BOOL_DESC', 'int8': 'SDL_INT8_DESC', 'uint8': 'SDL_UINT8_DESC',
         'int16': 'SDL_INT16_DESC', 'uint16': 'SDL_UINT16_DESC', 'int32': 'SDL_INT32_DESC', 'uint32': 'SDL_UINT32_DESC',
         'int64': 'SDL_INT64_DESC', 'uint64': 'SDL_UINT64_DESC', 'fl32': 'SDL_FLOAT32_DESC',
         'fl64': 'SDL_FLOAT64_DESC', 'c32': 'SDL_COMPLEX32_DESC',
         'c64': 'SDL_COMPLEX64_DESC', 'string': 'SDL_STRING_DESC'}
      if name in descriptors:
         return '&' + descriptors[name]
      if name in self.schema.enums:
         return '&' + self._symbols[('enum_desc', name)]
      return '&' + self._symbols[('desc', name)]

   def _array_desc(self, message_name, field_name):
      return '&' + self._symbols[('array', message_name, field_name)] + '_desc_0'

   def _allocate_symbols(self, identifier):
      used = (_C_RESERVED_ORDINARY_NAMES | _C_KEYWORDS | _C_MACROS |
         {c_identifier(name) for name in self.schema.messages} |
         set(self.schema.enums) | {'register_' + identifier + '_types'})
      used.update(enum.name.upper() + '_' + name
         for enum in self.schema.enums.values() for name, unused in enum.pairs)
      self._symbols = {}

      def allocate(key, base, suffixes=('',)):
         candidate = base
         number = 2
         while any(candidate + suffix in used for suffix in suffixes):
            candidate = base + '_' + str(number)
            number += 1
         used.update(candidate + suffix for suffix in suffixes)
         self._symbols[key] = candidate

      for enum in self.schema.enums.values():
         for kind in ('desc', 'values', 'align'):
            allocate(('enum_' + kind, enum.name),
               'sdl_enum_' + enum.name.lower() + '_' + kind)
      for name in self.schema.message_order:
         for kind in ('desc', 'fields', 'align', 'schema_descriptor'):
            allocate((kind, name), c_identifier(name).lower() + '_' + kind)
         for field in self.schema.messages[name].fields:
            if field.array_dimensions:
               suffixes = tuple('_' + kind + '_' + str(index)
                  for index in range(len(field.array_dimensions))
                  for kind in ('type', 'align', 'desc'))
               allocate(('array', name, field.name),
                  c_identifier(name).lower() + '_' + field.name.lower() + '_array',
                  suffixes)

   @staticmethod
   def _descriptor_literal(descriptor):
      # Escape bytes rather than relying on the compiler's source encoding.
      escaped = {34: '\\"', 92: '\\\\', 63: '\\?'}
      chunks = []
      for offset in range(0, len(descriptor), 96):
         chunks.append('   "' + ''.join(escaped.get(value,
            chr(value) if 32 <= value < 127 else '\\%03o' % value)
            for value in descriptor[offset:offset + 96]) + '"')
      return '\n'.join(chunks)

   def _validate_c_identifiers(self):
      for enum_name, enumeration in self.schema.enums.items():
         if (not _is_ascii_c_identifier(enum_name) or enum_name.startswith('_')):
            raise ValueError('C backend cannot emit portable enum type name: ' +
               enum_name)
         if c_identifier(enum_name) in _C_KEYWORDS:
            raise ValueError('C backend cannot use C keyword as enum name: ' +
               enum_name)
         if enum_name in _C_RESERVED_ORDINARY_NAMES or enum_name in _C_MACROS:
            raise ValueError('C backend cannot reuse a C or runtime name: ' +
               enum_name)
         for item_name, unused_value in enumeration.pairs:
            if (not item_name.isascii() or
                not all(character.isalnum() or character == '_' for character in item_name)):
               raise ValueError('C backend cannot emit portable enum item name: ' +
                  enum_name + '.' + item_name)
      for message_name in self.schema.message_order:
         c_name = c_identifier(message_name)
         if not _is_ascii_c_identifier(c_name) or c_name.startswith('_'):
            raise ValueError('C backend cannot emit portable type name: ' +
               message_name)
         if c_name in _C_KEYWORDS:
            raise ValueError('C backend cannot use C keyword as type name: ' +
               message_name)
         if c_name in _C_RESERVED_ORDINARY_NAMES or c_name in _C_MACROS:
            raise ValueError('C backend cannot reuse a C or runtime name: ' +
               message_name)
         for field in self.schema.messages[message_name].fields:
            if (not _is_ascii_c_identifier(field.name) or
                field.name.startswith('_')):
               raise ValueError('C backend cannot emit portable field name: ' +
                  message_name + '.' + field.name)
            if field.name in _C_KEYWORDS:
               raise ValueError('C backend cannot use C keyword as field name: ' +
                  message_name + '.' + field.name)
            if field.name in _C_MACROS:
               raise ValueError('C backend cannot use C macro as field name: ' +
                  message_name + '.' + field.name)

   def generate_files(self, base_name):
      self._validate_c_identifiers()
      identifier = c_identifier(base_name)
      self._allocate_symbols(identifier)
      presence_members = {}
      count_members = {}
      string_length_members = {}
      for message_name in self.schema.message_order:
         message_fields = self.schema.messages[message_name].fields
         used_members = {field.name for field in message_fields}

         def allocate_member(base_member):
            member = base_member
            suffix = 2
            while member in used_members:
               member = base_member + '_' + str(suffix)
               suffix += 1
            used_members.add(member)
            return member

         for field in message_fields:
            if field.modifier == 'optional':
               presence_members[(message_name, field.index)] = allocate_member(
                  'has_' + field.name)
            if field.modifier in ('repeated', 'packed'):
               count_members[(message_name, field.index)] = allocate_member(
                  field.name + '_count')
            if field.type_name == 'string':
               string_length_members[(message_name, field.index)] = allocate_member(
                  'sdl_string_length_' + str(field.index))
      header = [
         '/* Generated by the SDL C backend. */\n',
         '#ifndef ' + identifier.upper() + '_H\n#define ' + identifier.upper() + '_H\n\n',
         '#include <stdbool.h>\n#include <stddef.h>\n#include <stdint.h>\n',
         '#include <complex.h>\n\n']
      source = ['/* Generated by the SDL C backend. */\n',
         '#include "' + base_name + '.h"\n#include "type_registry.h"\n#include <stddef.h>\n\n']

      for enum in self.schema.enums.values():
         header.append('typedef enum {\n')
         for name, value in enum.pairs:
            header.append('   ' + enum.name.upper() + '_' + name + ' = ' + value + ',\n')
         header.append('} ' + enum.name + ';\n')
         header.append('\n')
         enum_values_name = self._symbols[('enum_values', enum.name)]
         enum_alignment_name = self._symbols[('enum_align', enum.name)]
         source.append('static const SdlEnumValueDesc ' + enum_values_name +
            '[] = {\n')
         if enum.pairs:
            for item_name, value in enum.pairs:
               source.append('   { ' + value + ', "' + item_name + '" },\n')
         else:
            source.append('   { 0, NULL },\n')
         source.append('};\n')
         source.append('typedef struct { char prefix; ' + enum.name +
            ' value; } ' + enum_alignment_name + ';\n')
         source.append('static const SdlTypeDesc ' +
            self._symbols[('enum_desc', enum.name)] + ' = {\n')
         source.append('   .kind = SDL_TYPE_ENUM, .size = sizeof(' + enum.name +
            '), .alignment = offsetof(' + enum_alignment_name + ', value),\n')
         source.append('   .name = "' + enum.name + '",\n')
         source.append('   .detail.enumeration = { ' + str(len(enum.pairs)) +
            ', ' + enum_values_name + ' }\n};\n\n')

      for name in self.schema.message_order:
         message = self.schema.messages[name]
         c_name = c_identifier(name)
         header.append('typedef struct {\n')
         fields = sorted(message.fields, key=lambda item: item.index)
         if not fields:
            header.append('   uint8_t sdl_empty_placeholder;\n')
         for field in fields:
            c_type = self._c_type(field.type_name)
            if field.modifier == 'optional':
               header.append('   bool ' + presence_members[(name, field.index)] + ';\n')
            if field.modifier in ('repeated', 'packed'):
               header.append('   uint32_t ' + count_members[(name, field.index)] + ';\n')
               header.append('   ' + c_type + ' *' + field.name + ';\n')
            elif field.array_dimensions:
               suffix = ''.join('[' + str(size) + ']' for size in field.array_dimensions)
               header.append('   ' + c_type + ' ' + field.name + suffix + ';\n')
            else:
               header.append('   ' + c_type + ' ' + field.name + ';\n')
         for field in fields:
            if field.type_name != 'string':
               continue
            length_member = string_length_members[(name, field.index)]
            if field.modifier in ('repeated', 'packed'):
               header.append('   /* UTF-8 byte lengths, parallel to ' + field.name + '. */\n')
               header.append('   uint32_t *' + length_member + ';\n')
            else:
               header.append('   /* UTF-8 byte length; zero infers strlen for ordinary C strings. */\n')
               header.append('   uint32_t ' + length_member + ';\n')
         header.append('} ' + c_name + ';\n\n')


      header.append('bool register_' + identifier + '_types(void);\n\n#endif\n')
      for name in self.schema.message_order:
         c_name = c_identifier(name)
         fields = sorted(self.schema.messages[name].fields, key=lambda item: item.index)
         for field in fields:
            if not field.array_dimensions:
               continue
            prefix = self._symbols[('array', name, field.name)]
            for dimension_index in range(len(field.array_dimensions) - 1, -1, -1):
               alias = prefix + '_type_' + str(dimension_index)
               count_value = field.array_dimensions[dimension_index]
               child_type = (self._c_type(field.type_name) if
                  dimension_index == len(field.array_dimensions) - 1 else
                  prefix + '_type_' + str(dimension_index + 1))
               child_desc = (self._type_desc(field.type_name) if
                  dimension_index == len(field.array_dimensions) - 1 else
                  '&' + prefix + '_desc_' + str(dimension_index + 1))
               desc = prefix + '_desc_' + str(dimension_index)
               source.append('typedef ' + child_type + ' ' + alias + '[' +
                  str(count_value) + '];\n')
               alignment_type = prefix + '_align_' + str(dimension_index)
               source.append('typedef struct { char prefix; ' + alias + ' value; } ' +
                  alignment_type + ';\n')
               source.append('static const SdlTypeDesc ' + desc + ' = {\n')
               source.append('   .kind = SDL_TYPE_ARRAY, .size = sizeof(' + alias +
                  '), .alignment = offsetof(' + alignment_type +
                  ', value), .name = "' + desc + '",\n')
               source.append('   .detail.array = { ' + child_desc + ', ' +
                  str(count_value) + ' }\n};\n')
         fields_name = self._symbols[('fields', name)]
         alignment_name = self._symbols[('align', name)]
         descriptor_name = self._symbols[('schema_descriptor', name)]
         source.append('static const SdlFieldDesc ' + fields_name + '[] = {\n')
         if not fields:
            source.append('   { 0, NULL, NULL, 0, SDL_NO_OFFSET, SDL_NO_OFFSET, SDL_NO_OFFSET, 0 }\n')
         for field in fields:
            presence = ('offsetof(' + c_name + ', ' +
               presence_members[(name, field.index)] + ')'
               if field.modifier == 'optional' else 'SDL_NO_OFFSET')
            count = ('offsetof(' + c_name + ', ' +
               count_members[(name, field.index)] + ')'
               if field.modifier in ('repeated', 'packed') else 'SDL_NO_OFFSET')
            flags = 'SDL_FIELD_OPTIONAL' if field.modifier == 'optional' else '0'
            if field.modifier in ('repeated', 'packed'):
               flags += ' | SDL_FIELD_REPEATED'
            if field.modifier == 'packed':
               flags += ' | SDL_FIELD_PACKED'
            length_offset = ('offsetof(' + c_name + ', ' +
               string_length_members[(name, field.index)] + ')'
               if field.type_name == 'string' else 'SDL_NO_OFFSET')
            source.append('   { ' + str(field.index) + 'U, "' + field.name + '", ' +
               (self._array_desc(name, field.name) if field.array_dimensions else
                self._type_desc(field.type_name)) + ', offsetof(' + c_name + ', ' + field.name +
               '), ' + presence + ', ' + count + ', ' + length_offset + ', ' + flags + ' },\n')
         source.append('};\n')
         source.append('typedef struct { char prefix; ' + c_name +
            ' value; } ' + alignment_name + ';\n')
         descriptor = canonical_type_descriptor(self.schema, name)
         source.append('static const uint8_t ' + descriptor_name + '[] =\n' +
            self._descriptor_literal(descriptor) + ';\n')
         source.append('static const SdlTypeDesc ' + self._symbols[('desc', name)] + ' = {\n')
         source.append('   SDL_TYPE_STRUCT, sizeof(' + c_name + '), offsetof(' +
            alignment_name + ', value),\n')
         source.append('   "' + name + '", ' +
            descriptor_name + ', sizeof(' + descriptor_name + ') - 1,\n')
         source.append('   { { ' + str(len(fields)) + ', ' + fields_name + ' } }\n};\n\n')
      source.append('bool register_' + identifier + '_types(void) {\n')
      for name in self.schema.message_order:
         source.append('   if (!sdl_register_type(&' + self._symbols[('desc', name)] + ')) return false;\n')
      source.append('   return true;\n')
      source.append('}\n')
      return ''.join(header), ''.join(source)


def generate_c(input_path, output_dir):
   schemas = parse_schemas(input_path)
   seen_guards = set()
   for base_name, unused_id, unused_schema in schemas:
      guard = c_identifier(base_name).upper() + '_H'
      if base_name == 'sdl_registry':
         raise ValueError('C schema filename sdl_registry collides with generated registry files')
      if guard in _C_RESERVED_HEADER_GUARDS:
         raise ValueError('C schema filename ' + base_name +
            ' collides with generated/runtime include guard ' + guard)
      if guard in seen_guards:
         raise ValueError('C SDL filenames map to the same header guard: ' + guard)
      seen_guards.add(guard)
   for unused_base, unused_id, schema in schemas:
      CBackend(schema)._validate_c_identifiers()
   clear_generated_outputs(output_dir, ('.c', '.h'), (
      '/* Generated by the SDL C backend. */',
      '/* Generated registry for SDL files in the input directory. */'))
   generated = []
   for base_name, identifier, schema in schemas:
      header, source = CBackend(schema).generate_files(base_name)
      with open(os.path.join(output_dir, base_name + '.h'), 'w', encoding='utf-8') as output_file:
         output_file.write(header)
      with open(os.path.join(output_dir, base_name + '.c'), 'w', encoding='utf-8') as output_file:
         output_file.write(source)
      generated.append((base_name, identifier))

   registry_header = [
      '/* Generated registry for SDL files in the input directory. */\n',
      '#ifndef SDL_REGISTRY_H\n#define SDL_REGISTRY_H\n\n',
      '#include <stdbool.h>\n\n',
      'bool register_all_types(void);\n\n#endif\n']
   registry_source = ['/* Generated registry for SDL files in the input directory. */\n',
      '#include "sdl_registry.h"\n']
   for base_name, unused_identifier in generated:
      registry_source.append('#include "' + base_name + '.h"\n')
   registry_source.append('\nbool register_all_types(void) {\n')
   for unused_base_name, identifier in generated:
      registry_source.append('   if (!register_' + identifier + '_types()) return false;\n')
   registry_source.append('   return true;\n')
   registry_source.append('}\n')
   with open(os.path.join(output_dir, 'sdl_registry.h'), 'w', encoding='utf-8') as output_file:
      output_file.write(''.join(registry_header))
   with open(os.path.join(output_dir, 'sdl_registry.c'), 'w', encoding='utf-8') as output_file:
      output_file.write(''.join(registry_source))
