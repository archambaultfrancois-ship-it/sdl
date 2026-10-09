#!/usr/bin/env python3
"""Rust code generator for the shared SDL schema model."""

import os
import json

from generator import (c_identifier, canonical_type_descriptor,
   clear_generated_outputs, parse_schemas)


def rust_variant(name):
   parts = [part for part in name.replace('-', '_').split('_') if part]
   return ''.join(part[:1].upper() + part[1:].lower() for part in parts)


def rust_type_name(name):
   if '$' in name:
      parent, sequence = name.split('$', 1)
      return c_identifier(parent) + sequence
   return c_identifier(name)


_RUST_KEYWORDS = set((
   'as break const continue crate else enum extern false fn for if impl in let '
   'loop match mod move mut pub ref return self Self static struct super trait '
   'true type unsafe use where while union async await dyn abstract become box do '
   'final macro override priv typeof unsized virtual yield try').split())
_RUST_IMPORTED_TYPES = set((
   'CodecError Complex32 Complex64 Reader SdlDisplay SdlMessage WireValue '
   'String Option Vec Result bool char str i8 i16 i32 i64 i128 isize '
   'u8 u16 u32 u64 u128 usize f32 f64').split())


class RustBackend:
   def __init__(self, schema):
      self.schema = schema
      self._fixed_counts = {}

   def _validate_rust_identifiers(self):
      type_names = {}
      for name in list(self.schema.enums) + list(self.schema.message_order):
         generated = (c_identifier(name) if name in self.schema.enums else
            rust_type_name(name))
         if not generated.isidentifier():
            raise ValueError('Rust backend cannot emit invalid type identifier: ' +
               name)
         if generated in _RUST_KEYWORDS:
            raise ValueError('Rust backend cannot use reserved word as type name: ' +
               name)
         if generated in _RUST_IMPORTED_TYPES:
            raise ValueError('Rust type name conflicts with a runtime or standard type: ' +
               name)
         if generated in type_names:
            raise ValueError('Rust type name collision after conversion: ' + name +
               ' and ' + type_names[generated])
         type_names[generated] = name

      for enumeration in self.schema.enums.values():
         variants = {}
         for name, unused_value in enumeration.pairs:
            generated = rust_variant(name)
            if not generated.isidentifier():
               raise ValueError('Rust backend cannot emit invalid enum variant: ' +
                  enumeration.name + '.' + name)
            if generated in _RUST_KEYWORDS:
               raise ValueError('Rust backend cannot use reserved word as enum variant: ' +
                  enumeration.name + '.' + name)
            if generated in variants:
               raise ValueError('Rust enum variant collision after conversion in ' +
                  enumeration.name + ': ' + name + ' and ' + variants[generated])
            variants[generated] = name

      for message_name in self.schema.message_order:
         fields = {}
         for field in self.schema.messages[message_name].fields:
            generated = c_identifier(field.name)
            if not generated.isidentifier():
               raise ValueError('Rust backend cannot emit invalid field identifier: ' +
                  message_name + '.' + field.name)
            if generated in _RUST_KEYWORDS:
               raise ValueError('Rust backend cannot use reserved word as field name: ' +
                  message_name + '.' + field.name)
            if generated in fields:
               raise ValueError('Rust field name collision after conversion in ' +
                  message_name + ': ' + field.name + ' and ' + fields[generated])
            fields[generated] = field.name

   def _rust_type(self, name):
      mapping = {
         'bool': 'bool',
         'int8': 'i8',
         'int16': 'i16',
         'int32': 'i32',
         'int64': 'i64',
         'fl32': 'f32',
         'fl64': 'f64',
         'c32': 'Complex32',
         'c64': 'Complex64',
         'string': 'String',
      }
      return mapping.get(name, rust_type_name(name))

   def _rust_field_type(self, field):
      value_type = self._rust_type(field.type_name)
      for dimension in reversed(field.array_dimensions):
         value_type = '[' + value_type + '; ' + str(dimension) + ']'
      return value_type

   def _fixed_leaf_count(self, name):
      if name in self._fixed_counts:
         return self._fixed_counts[name]
      if name not in self.schema.messages:
         return 1
      total = 0
      for field in self.schema.messages[name].fields:
         count = self._fixed_leaf_count(field.type_name)
         for dim in field.array_dimensions:
            count *= dim
         total += count
         if total > 4096:
            break
      self._fixed_counts[name] = total
      return total

   def _fixed_decode(self, name, dimensions, offset):
      if dimensions:
         parts = []
         for unused in range(dimensions[0]):
            expr, offset = self._fixed_decode(name, dimensions[1:], offset)
            parts.append(expr)
         return '[' + ','.join(parts) + ']', offset
      size = self.schema.fixed_wire_size(name)
      expr = '<' + self._rust_type(name) + ' as WireValue>::decode_fixed(&bytes[' + str(offset) + '..' + str(offset + size) + '])?'
      return expr, offset + size

   def _fixed_encode(self, name, dimensions, attr, offset):
      if dimensions:
         parts = []
         for i in range(dimensions[0]):
            statements, offset = self._fixed_encode(name, dimensions[1:], attr + '[' + str(i) + ']', offset)
            parts.append(statements)
         return ''.join(parts), offset
      size = self.schema.fixed_wire_size(name)
      statement = '      ' + attr + '.encode_fixed(&mut bytes[' + str(offset) + '..' + str(offset + size) + '])?;\n'
      return statement, offset + size

   def _fixed_message_codec(self, name, fields, size):
      out = ['   const FIXED_SIZE:Option<usize>=Some(' + str(size) + ');\n',
         '   fn decode_fixed(bytes:&[u8])->Result<Self,CodecError>{\n',
         '      if bytes.len()!=' + str(size) + '{return Err(CodecError::Truncated); }\n      Ok(Self {\n']
      offset = 0
      for field in fields:
         expr, offset = self._fixed_decode(field.type_name, field.array_dimensions, offset)
         out.append('         ' + c_identifier(field.name) + ': ' + expr + ',\n')
      out.append('      })\n   }\n   #[inline]\n   fn encode_fixed(&self,bytes:&mut[u8])->Result<(),CodecError>{\n')
      out.append('      if bytes.len()!=' + str(size) + '{return Err(CodecError::Truncated); }\n')
      offset = 0
      for field in fields:
         statements, offset = self._fixed_encode(field.type_name, field.array_dimensions,
            'self.' + c_identifier(field.name), offset)
         out.append(statements)
      out.append('      Ok(())\n   }\n')
      return ''.join(out)

   def generate(self):
      self._validate_rust_identifiers()
      out = ['// Generated by the SDL Rust backend.\n',
         '#[allow(unused_imports)]\nuse sdl_runtime::{CodecError,Complex32,Complex64,WireValue,SdlMessage,SdlDisplay,Reader,write_count,count_size,add_size,encode_sequence};\n']
      for enum in self.schema.enums.values():
         name = c_identifier(enum.name)
         variants = [(rust_variant(n), v) for n, v in enum.pairs]
         out.append('#[derive(Clone,Copy,Debug,PartialEq)]\n#[repr(i32)]\npub enum ' + name + ' {\n')
         for n, v in variants:
            out.append('   ' + n + ' = ' + v + ',\n')
         out.append('}\nimpl Default for ' + name + ' { fn default()->Self {Self::' + variants[0][0] + '} }\n')
         out.append('impl WireValue for ' + name + ' {\n')
         out.append('   const FIXED_SIZE:Option<usize>=Some(4);\n')
         out.append('   #[inline]\n   fn encode_fixed(&self,bytes:&mut[u8])->Result<(),CodecError>{(*self as i32).encode_fixed(bytes)}\n')
         out.append('   fn decode_fixed(bytes:&[u8])->Result<Self,CodecError>{match i32::decode_fixed(bytes)? {\n')
         for n,v in variants:
            out.append('      ' + v + '=>Ok(Self::' + n + '),\n')
         out.append('      _=>Err(CodecError::InvalidEnum),\n   }}\n')
         out.append('   fn encode_value(&self,out:&mut Vec<u8>)->Result<(),CodecError>{(*self as i32).encode_value(out)}\n')
         out.append('   fn encoded_size(&self)->Result<usize,CodecError>{Ok(4)}\n')
         out.append('   fn decode_value(r:&mut Reader<\'_>,name:&str)->Result<Self,CodecError>{let n=i32::decode_value(r,name)?;r.validate_enum(name,n)?;match n {\n')
         for n,v in variants:
            out.append('      ' + v + '=>Ok(Self::' + n + '),\n')
         out.append('      _=>Err(CodecError::InvalidEnum),\n   }}\n}\n')
         out.append('impl SdlDisplay for ' + name + ' { fn fmt_sdl(&self,out:&mut String,_:usize,_:usize){out.push_str(match self {\n')
         for (variant, unused), (original, unused_value) in zip(variants,enum.pairs):
            out.append('   Self::' + variant + '=>' + json.dumps(original, ensure_ascii=False) + ',\n')
         out.append('});}}\n')
      for message_name in self.schema.message_order:
         name=rust_type_name(message_name)
         fields=sorted(self.schema.messages[message_name].fields,key=lambda f:f.index)
         fixed_size = self.schema.fixed_wire_size(message_name)
         if fixed_size is not None and (fixed_size == 0 or self._fixed_leaf_count(message_name) > 4096):
            fixed_size = None
         out.append('#[derive(Clone,Debug,Default,PartialEq)]\npub struct ' + name + ' {\n')
         for f in fields:
            t=self._rust_field_type(f)
            if f.modifier=='optional': t='Option<' + t + '>'
            elif f.modifier in ('packed','repeated'): t='Vec<' + t + '>'
            out.append('   pub ' + c_identifier(f.name) + ': ' + t + ',\n')
         out.append('}\nimpl SdlMessage for ' + name + ' {\n   const NAME:&\'static str=' + json.dumps(message_name,ensure_ascii=False) + ';\n')
         out.append('   const DESCRIPTOR:&\'static str=' + json.dumps(canonical_type_descriptor(self.schema).decode('utf-8'),ensure_ascii=False) + ';\n}\n')
         out.append('impl WireValue for ' + name + ' {\n')
         if fixed_size is not None:
            out.append(self._fixed_message_codec(name, fields, fixed_size))
         out.append('   fn encode_value(&self,out:&mut Vec<u8>)->Result<(),CodecError>{\n')
         if fixed_size is not None:
            out.append('      return encode_sequence(std::slice::from_ref(self),out);\n')
         for f in ([] if fixed_size is not None else fields):
            attr='self.' + c_identifier(f.name)
            if f.modifier=='optional':
               out.append('      write_count(if ' + attr + '.is_some(){1}else{0},out)?;if let Some(v)=&' + attr + '{v.encode_value(out)?;}\n')
            elif f.modifier in ('packed','repeated'):
               out.append('      write_count(' + attr + '.len(),out)?;encode_sequence(&' + attr + ',out)?;\n')
            else:
               out.append('      ' + attr + '.encode_value(out)?;\n')
         if fixed_size is None:
            out.append('      let _=out;Ok(())\n')
         out.append('   }\n   fn encoded_size(&self)->Result<usize,CodecError>{\n')
         if fixed_size is not None:
            out.append('      Ok(' + str(fixed_size) + ')\n')
         else:
            out.append('      let mut total=0;\n')
         for f in ([] if fixed_size is not None else fields):
            attr='self.' + c_identifier(f.name)
            if f.modifier=='optional':
               out.append('      total=add_size(total,1)?;if let Some(v)=&' + attr + '{total=add_size(total,v.encoded_size()?)?;}\n')
            elif f.modifier in ('packed','repeated'):
               out.append('      total=add_size(total,count_size(' + attr + '.len())?)?;if let Some(size)=<' + self._rust_field_type(f) + ' as WireValue>::FIXED_SIZE {total=add_size(total,size.checked_mul(' + attr + '.len()).ok_or(CodecError::LengthOverflow)?)?;}else{for v in &' + attr + '{total=add_size(total,v.encoded_size()?)?;}}\n')
            else:
               out.append('      total=add_size(total,' + attr + '.encoded_size()?)?;\n')
         if fixed_size is None:
            out.append('      Ok(total)\n')
         out.append('   }\n   fn decode_value(r:&mut Reader<\'_>,name:&str)->Result<Self,CodecError>{\n')
         if fixed_size is not None:
            out.append('      if name==Self::NAME&&r.fixed_compatible(name){return Self::decode_fixed(r.take(' + str(fixed_size) + ')?); }\n')
         out.append('      r.enter()?;let mut value=Self::default();\n      for f in r.fields(name)? {let n=r.field_count(f)?;match f.id {\n')
         for f in fields:
            attr='value.' + c_identifier(f.name)
            t=self._rust_field_type(f)
            decode='<' + t + ' as WireValue>::decode_value(r,&f.type_name)?'
            out.append('         ' + str(f.index) + '=>{')
            if f.modifier=='optional':
               out.append('if n==1 {' + attr + '=Some(' + decode + ');}')
            elif f.modifier in ('packed','repeated'):
               out.append(attr + '=r.decode_sequence::<'+t+'>(&f.type_name,n)?;')
            else:
               out.append(attr + '=' + decode + ';')
            out.append('},\n')
         out.append('         _=>r.skip_field(f,n)?,\n      }}r.leave();Ok(value)\n   }\n}\n')
         out.append('impl SdlDisplay for ' + name + ' {fn fmt_sdl(&self,out:&mut String,indent:usize,depth:usize){\n   sdl_runtime::display_struct_start(out,' + json.dumps(message_name) + ',depth,indent);\n')
         for f in fields:
            out.append('   sdl_runtime::display_struct_field(out,' + json.dumps(c_identifier(f.name)) + ',&self.' + c_identifier(f.name) + ',depth,indent);\n')
         out.append('   sdl_runtime::display_struct_end(out,depth,indent);\n}}\n')
      return ''.join(out).replace('impl WireValue for ', '#[allow(unused_mut)]\nimpl WireValue for ')

def generate_rust(input_path, output_dir):
   schemas = parse_schemas(input_path)
   modules = []
   seen_modules = set()
   for base_name, unused_identifier, parser in schemas:
      module_name = c_identifier(base_name).lower()
      if module_name == 'mod':
         raise ValueError('Rust schema filename mod collides with generated mod.rs')
      if not module_name.isidentifier() or module_name in _RUST_KEYWORDS:
         raise ValueError('invalid Rust module name from SDL filename: ' + base_name)
      if module_name in seen_modules:
         raise ValueError('SDL filenames map to the same Rust module: ' + module_name)
      seen_modules.add(module_name)
      RustBackend(parser)._validate_rust_identifiers()
      modules.append((module_name, parser))

   clear_generated_outputs(output_dir, ('.rs',), (
      '// Generated by the SDL Rust backend.',
      '// Generated SDL modules.'))
   for module_name, parser in modules:
      with open(os.path.join(output_dir, module_name + '.rs'), 'w', encoding='utf-8') as output_file:
         output_file.write(RustBackend(parser).generate())
   module_names = [module_name for module_name, unused_parser in modules]

   with open(os.path.join(output_dir, 'mod.rs'), 'w', encoding='utf-8') as output_file:
      output_file.write('// Generated SDL modules.\n')
      for module in module_names:
         output_file.write('pub mod ' + module + ';\n')
