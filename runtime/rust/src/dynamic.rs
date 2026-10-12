use crate::wire::{CodecError, Complex32, Complex64};
use std::collections::{BTreeMap, HashMap, HashSet};

const MAX_DESCRIPTOR_SIZE: usize = 1024 * 1024;
const MAX_NESTING: usize = 64;

#[derive(Clone, Debug, PartialEq)]
pub enum DynamicValue {
   Null,
   Bool(bool),
   Integer(i64),
   Float(f64),
   Complex32(Complex32),
   Complex64(Complex64),
   String(String),
   Enum { type_name: String, name: String, value: i32 },
   Array(Vec<DynamicValue>),
   Message(DynamicMessage),
}

#[derive(Clone, Debug, PartialEq)]
pub struct DynamicMessage {
   pub type_name: String,
   pub fields: BTreeMap<String, DynamicValue>,
}

#[derive(Clone)]
struct FieldDesc {
   id: u32,
   name: String,
   modifier: u8,
   type_name: String,
   dimensions: Vec<usize>,
}

#[derive(Clone)]
struct MessageDesc {
   fields: Vec<FieldDesc>,
}

struct SchemaDesc {
   root: String,
   messages: HashMap<String, MessageDesc>,
   enums: HashMap<String, Vec<(String, i32)>>,
}

struct Reader<'a> {
   bytes: &'a [u8],
   offset: usize,
}

impl<'a> Reader<'a> {
   fn new(bytes: &'a [u8]) -> Self {
      Self { bytes, offset: 0 }
   }

   fn take(&mut self, size: usize) -> Result<&'a [u8], CodecError> {
      if size > self.bytes.len().saturating_sub(self.offset) {
         return Err(CodecError::InvalidDescriptor);
      }
      let start = self.offset;
      self.offset += size;
      Ok(&self.bytes[start..self.offset])
   }

   fn u8(&mut self) -> Result<u8, CodecError> {
      Ok(self.take(1)?[0])
   }

   fn u16(&mut self) -> Result<u16, CodecError> {
      let bytes: [u8; 2] = self.take(2)?.try_into()
         .map_err(|_| CodecError::InvalidDescriptor)?;
      Ok(u16::from_be_bytes(bytes))
   }

   fn u32(&mut self) -> Result<u32, CodecError> {
      let bytes: [u8; 4] = self.take(4)?.try_into()
         .map_err(|_| CodecError::InvalidDescriptor)?;
      Ok(u32::from_be_bytes(bytes))
   }

   fn i32(&mut self) -> Result<i32, CodecError> {
      let bytes: [u8; 4] = self.take(4)?.try_into()
         .map_err(|_| CodecError::InvalidDescriptor)?;
      Ok(i32::from_be_bytes(bytes))
   }

   fn text(&mut self) -> Result<String, CodecError> {
      let size = self.u16()? as usize;
      std::str::from_utf8(self.take(size)?)
         .map(str::to_owned)
         .map_err(|_| CodecError::InvalidDescriptor)
   }
}

pub fn decode_dynamic(input: &[u8]) -> Result<DynamicMessage, CodecError> {
   if input.len() < 8 {
      return Err(CodecError::Truncated);
   }
   let descriptor_size = read_wire_u32(&input[..4])? as usize;
   if descriptor_size > MAX_DESCRIPTOR_SIZE || descriptor_size > input.len() - 8 {
      return Err(CodecError::Truncated);
   }
   let descriptor_end = 4 + descriptor_size;
   let descriptor_bytes = &input[4..descriptor_end];
   let hash = read_wire_u32(&input[descriptor_end..descriptor_end + 4])?;
   if fnv1a_32(descriptor_bytes) != hash {
      return Err(CodecError::DescriptorHashMismatch);
   }
   let schema = parse_descriptor(descriptor_bytes)?;
   decode_message(&schema.root, &input[descriptor_end + 4..], &schema, 0)
}

fn parse_descriptor(bytes: &[u8]) -> Result<SchemaDesc, CodecError> {
   let mut reader = Reader::new(bytes);
   if reader.take(4)? != b"SDD1" {
      return Err(CodecError::InvalidDescriptor);
   }
   let root = reader.text()?;
   if root.is_empty() { return Err(CodecError::InvalidDescriptor); }
   let mut messages = HashMap::new();
   let message_count = reader.u16()? as usize;
   let mut previous_message_name: Option<String> = None;
   for _ in 0..message_count {
      let name = reader.text()?;
      if name.is_empty() || previous_message_name.as_ref().map_or(false,
            |previous| name <= *previous) {
         return Err(CodecError::InvalidDescriptor);
      }
      previous_message_name = Some(name.clone());
      let field_count = reader.u16()? as usize;
      let mut fields = Vec::with_capacity(field_count);
      let mut ids = HashSet::new();
      let mut field_names = HashSet::new();
      let mut previous_field_id = 0;
      for _ in 0..field_count {
         let id = reader.u32()?;
         let field_name = reader.text()?;
         let modifier = reader.u8()?;
         let type_name = reader.text()?;
         let dimension_count = reader.u8()? as usize;
         let mut dimensions = Vec::with_capacity(dimension_count);
         for _ in 0..dimension_count {
            let dimension = reader.u32()? as usize;
            if dimension == 0 {
               return Err(CodecError::InvalidDescriptor);
            }
            dimensions.push(dimension);
         }
         if id <= previous_field_id || field_name.is_empty() || type_name.is_empty() ||
            modifier > 3 || !ids.insert(id) ||
            !field_names.insert(field_name.clone()) ||
            (!dimensions.is_empty() && modifier != 0) {
            return Err(CodecError::InvalidDescriptor);
         }
         previous_field_id = id;
         fields.push(FieldDesc { id, name: field_name, modifier, type_name, dimensions });
      }
      fields.sort_by_key(|field| field.id);
      if messages.insert(name, MessageDesc { fields }).is_some() {
         return Err(CodecError::InvalidDescriptor);
      }
   }
   let enum_count = reader.u16()? as usize;
   let mut enums = HashMap::new();
   let mut previous_enum_name: Option<String> = None;
   for _ in 0..enum_count {
      let name = reader.text()?;
      if name.is_empty() || messages.contains_key(&name) ||
         previous_enum_name.as_ref().map_or(false, |previous| name <= *previous) {
         return Err(CodecError::InvalidDescriptor);
      }
      previous_enum_name = Some(name.clone());
      let item_count = reader.u16()? as usize;
      let mut values = Vec::with_capacity(item_count);
      let mut enum_names = HashSet::new();
      let mut enum_values = HashSet::new();
      for _ in 0..item_count {
         let item_name = reader.text()?;
         let value = reader.i32()?;
         if item_name.is_empty() || !enum_names.insert(item_name.clone()) ||
            !enum_values.insert(value) {
            return Err(CodecError::InvalidDescriptor);
         }
         values.push((item_name, value));
      }
      if enums.insert(name, values).is_some() {
         return Err(CodecError::InvalidDescriptor);
      }
   }
   if reader.offset != bytes.len() || !messages.contains_key(&root) {
      return Err(CodecError::InvalidDescriptor);
   }

   let schema = SchemaDesc { root, messages, enums };
   for message in schema.messages.values() {
      for field in &message.fields {
         if !known_type(&field.type_name, &schema) {
            return Err(CodecError::InvalidDescriptor);
         }
         if !field.dimensions.is_empty() || field.modifier == 3 {
            let mut size = match fixed_type_size(&field.type_name, &schema,
                  &mut HashSet::new(), 0) {
               Some(size) => size,
               None => return Err(CodecError::InvalidDescriptor),
            };
            for dimension in &field.dimensions {
               size = size.checked_mul(*dimension)
                  .filter(|size| *size <= u32::MAX as usize)
                  .ok_or(CodecError::InvalidDescriptor)?;
            }
            if size == 0 {
               return Err(CodecError::InvalidDescriptor);
            }
         }
      }
   }
   Ok(schema)
}

fn known_type(name: &str, schema: &SchemaDesc) -> bool {
   matches!(name, "bool" | "int8" | "int16" | "int32" | "int64" |
      "fl32" | "fl64" | "c32" | "c64" | "string") ||
      schema.messages.contains_key(name) || schema.enums.contains_key(name)
}

fn fixed_type_size(name: &str, schema: &SchemaDesc, active: &mut HashSet<String>,
   depth: usize) -> Option<usize> {
   if depth > MAX_NESTING { return None; }
   let primitive = match name {
      "bool" | "int8" => Some(1),
      "int16" => Some(2),
      "int32" | "fl32" => Some(4),
      "int64" | "fl64" => Some(8),
      "c32" => Some(8),
      "c64" => Some(16),
      "string" => return None,
      _ => None,
   };
   if primitive.is_some() { return primitive; }
   if schema.enums.contains_key(name) { return Some(4); }
   let message = schema.messages.get(name)?;
   if message.fields.is_empty() || !active.insert(name.to_owned()) { return None; }
   let mut total = 0usize;
   for field in &message.fields {
      if field.modifier != 0 { active.remove(name); return None; }
      let mut size = fixed_type_size(&field.type_name, schema, active, depth + 1)?;
      for dimension in &field.dimensions {
         size = size.checked_mul(*dimension)?;
      }
      total = total.checked_add(size)?;
      if total > u32::MAX as usize { active.remove(name); return None; }
   }
   active.remove(name);
   Some(total)
}

fn decode_message(type_name: &str, payload: &[u8], schema: &SchemaDesc,
   depth: usize) -> Result<DynamicMessage, CodecError> {
   if depth > MAX_NESTING { return Err(CodecError::InvalidDescriptor); }
   let message = schema.messages.get(type_name).ok_or(CodecError::InvalidDescriptor)?;
   let mut fields = BTreeMap::new();
   for field in &message.fields {
      fields.insert(field.name.clone(), if field.modifier == 2 || field.modifier == 3 {
         DynamicValue::Array(Vec::new())
      } else {
         DynamicValue::Null
      });
   }
   let by_id: HashMap<u32, &FieldDesc> = message.fields.iter()
      .map(|field| (field.id, field)).collect();
   let mut offset = 0usize;
   while offset < payload.len() {
      if payload.len() - offset < 8 { return Err(CodecError::Truncated); }
      let id = read_wire_u32(&payload[offset..offset + 4])?;
      let length = read_wire_u32(&payload[offset + 4..offset + 8])? as usize;
      offset += 8;
      if length > payload.len() - offset { return Err(CodecError::Truncated); }
      if let Some(field) = by_id.get(&id) {
         let data = &payload[offset..offset + length];
         if field.modifier == 3 {
            let item_size = fixed_type_size(&field.type_name, schema,
               &mut HashSet::new(), 0).ok_or(CodecError::InvalidDescriptor)?;
            if data.len() % item_size != 0 { return Err(CodecError::TypeMismatch); }
            let mut items = Vec::with_capacity(data.len() / item_size);
            for item in data.chunks_exact(item_size) {
               items.push(decode_fixed(&field.type_name, item, &[], schema, depth + 1)?);
            }
            if let Some(DynamicValue::Array(values)) = fields.get_mut(&field.name) {
               values.extend(items);
            }
         } else {
            let value = if !field.dimensions.is_empty() {
               decode_fixed(&field.type_name, data, &field.dimensions, schema, depth + 1)?
            } else {
               decode_value(&field.type_name, data, schema, depth + 1)?
            };
            if field.modifier == 2 {
               if let Some(DynamicValue::Array(values)) = fields.get_mut(&field.name) {
                  values.push(value);
               }
            } else {
               fields.insert(field.name.clone(), value);
            }
         }
      }
      offset += length;
   }
   Ok(DynamicMessage { type_name: type_name.to_owned(), fields })
}

fn decode_value(type_name: &str, data: &[u8], schema: &SchemaDesc,
   depth: usize) -> Result<DynamicValue, CodecError> {
   if let Some(values) = schema.enums.get(type_name) {
      if data.len() != 4 { return Err(CodecError::TypeMismatch); }
      let value = read_wire_i32(data)?;
      let name = values.iter().find(|(_, candidate)| *candidate == value)
         .map(|(name, _)| name.clone()).ok_or(CodecError::InvalidEnum)?;
      return Ok(DynamicValue::Enum { type_name: type_name.to_owned(), name, value });
   }
   if schema.messages.contains_key(type_name) {
      return Ok(DynamicValue::Message(decode_message(type_name, data, schema, depth)?));
   }
   decode_primitive(type_name, data)
}

fn decode_fixed(type_name: &str, data: &[u8], dimensions: &[usize], schema: &SchemaDesc,
   depth: usize) -> Result<DynamicValue, CodecError> {
   if depth > MAX_NESTING { return Err(CodecError::InvalidDescriptor); }
   let item_size = fixed_type_size(type_name, schema, &mut HashSet::new(), 0)
      .ok_or(CodecError::InvalidDescriptor)?;
   let count = dimensions.iter().try_fold(1usize, |total, dimension|
      total.checked_mul(*dimension)).ok_or(CodecError::LengthOverflow)?;
   if data.len() != item_size.checked_mul(count).ok_or(CodecError::LengthOverflow)? {
      return Err(CodecError::TypeMismatch);
   }
   fn nest(type_name: &str, data: &[u8], dimensions: &[usize], item_size: usize,
      schema: &SchemaDesc, depth: usize) -> Result<DynamicValue, CodecError> {
      if dimensions.is_empty() {
         return decode_fixed_plain(type_name, data, schema, depth + 1);
      }
      let remaining_count = dimensions[1..].iter().try_fold(1usize, |total, dimension|
         total.checked_mul(*dimension)).ok_or(CodecError::LengthOverflow)?;
      let child_size = item_size.checked_mul(remaining_count)
         .ok_or(CodecError::LengthOverflow)?;
      let mut values = Vec::with_capacity(dimensions[0]);
      for index in 0..dimensions[0] {
         let start = index.checked_mul(child_size).ok_or(CodecError::LengthOverflow)?;
         values.push(nest(type_name, &data[start..start + child_size],
            &dimensions[1..], item_size, schema, depth + 1)?);
      }
      Ok(DynamicValue::Array(values))
   }
   nest(type_name, data, dimensions, item_size, schema, depth)
}

fn decode_fixed_plain(type_name: &str, data: &[u8], schema: &SchemaDesc,
   depth: usize) -> Result<DynamicValue, CodecError> {
   if let Some(values) = schema.enums.get(type_name) {
      let value = read_wire_i32(data)?;
      let name = values.iter().find(|(_, candidate)| *candidate == value)
         .map(|(name, _)| name.clone()).ok_or(CodecError::InvalidEnum)?;
      return Ok(DynamicValue::Enum { type_name: type_name.to_owned(), name, value });
   }
   if let Some(message) = schema.messages.get(type_name) {
      if depth > MAX_NESTING { return Err(CodecError::InvalidDescriptor); }
      let mut fields = BTreeMap::new();
      let mut offset = 0usize;
      for field in &message.fields {
         let size = field_wire_size(field, schema)
            .ok_or(CodecError::InvalidDescriptor)?;
         if size > data.len() - offset { return Err(CodecError::Truncated); }
         let value = decode_fixed(&field.type_name, &data[offset..offset + size],
            &field.dimensions, schema, depth + 1)?;
         fields.insert(field.name.clone(), value);
         offset += size;
      }
      if offset != data.len() { return Err(CodecError::TypeMismatch); }
      return Ok(DynamicValue::Message(DynamicMessage {
         type_name: type_name.to_owned(), fields,
      }));
   }
   decode_primitive(type_name, data)
}

fn field_wire_size(field: &FieldDesc, schema: &SchemaDesc) -> Option<usize> {
   let mut size = fixed_type_size(&field.type_name, schema, &mut HashSet::new(), 0)?;
   for dimension in &field.dimensions { size = size.checked_mul(*dimension)?; }
   Some(size)
}

fn decode_primitive(type_name: &str, data: &[u8]) -> Result<DynamicValue, CodecError> {
   match type_name {
      "bool" => match data {
         [0] => Ok(DynamicValue::Bool(false)),
         [1] => Ok(DynamicValue::Bool(true)),
         [_] => Err(CodecError::InvalidBoolean),
         _ => Err(CodecError::TypeMismatch),
      },
      "int8" => match data { [value] => Ok(DynamicValue::Integer(*value as i8 as i64)),
         _ => Err(CodecError::TypeMismatch) },
      "int16" => Ok(DynamicValue::Integer(read_wire_i16(data)? as i64)),
      "int32" => Ok(DynamicValue::Integer(read_wire_i32(data)? as i64)),
      "int64" => Ok(DynamicValue::Integer(read_wire_i64(data)?)),
      "fl32" => Ok(DynamicValue::Float(f32::from_bits(read_wire_u32(data)?) as f64)),
      "fl64" => Ok(DynamicValue::Float(f64::from_bits(read_wire_u64(data)?))),
      "c32" => {
         if data.len() != 8 { return Err(CodecError::TypeMismatch); }
         Ok(DynamicValue::Complex32(Complex32 {
            real: f32::from_bits(read_wire_u32(&data[..4])?),
            imag: f32::from_bits(read_wire_u32(&data[4..])?),
         }))
      },
      "c64" => {
         if data.len() != 16 { return Err(CodecError::TypeMismatch); }
         Ok(DynamicValue::Complex64(Complex64 {
            real: f64::from_bits(read_wire_u64(&data[..8])?),
            imag: f64::from_bits(read_wire_u64(&data[8..])?),
         }))
      },
      "string" => std::str::from_utf8(data).map(|value|
         DynamicValue::String(value.to_owned())).map_err(|_| CodecError::InvalidUtf8),
      _ => Err(CodecError::InvalidDescriptor),
   }
}

fn fnv1a_32(bytes: &[u8]) -> u32 {
   bytes.iter().fold(2166136261u32, |hash, byte|
      (hash ^ (*byte as u32)).wrapping_mul(16777619))
}

fn read_wire_u32(data: &[u8]) -> Result<u32, CodecError> {
   let bytes: [u8; 4] = data.try_into().map_err(|_| CodecError::Truncated)?;
   Ok(if cfg!(feature = "wire-little-endian") {
      u32::from_le_bytes(bytes)
   } else { u32::from_be_bytes(bytes) })
}

fn read_wire_i32(data: &[u8]) -> Result<i32, CodecError> {
   let bytes: [u8; 4] = data.try_into().map_err(|_| CodecError::TypeMismatch)?;
   Ok(if cfg!(feature = "wire-little-endian") {
      i32::from_le_bytes(bytes)
   } else { i32::from_be_bytes(bytes) })
}

fn read_wire_i16(data: &[u8]) -> Result<i16, CodecError> {
   let bytes: [u8; 2] = data.try_into().map_err(|_| CodecError::TypeMismatch)?;
   Ok(if cfg!(feature = "wire-little-endian") {
      i16::from_le_bytes(bytes)
   } else { i16::from_be_bytes(bytes) })
}

fn read_wire_i64(data: &[u8]) -> Result<i64, CodecError> {
   let bytes: [u8; 8] = data.try_into().map_err(|_| CodecError::TypeMismatch)?;
   Ok(if cfg!(feature = "wire-little-endian") {
      i64::from_le_bytes(bytes)
   } else { i64::from_be_bytes(bytes) })
}

fn read_wire_u64(data: &[u8]) -> Result<u64, CodecError> {
   let bytes: [u8; 8] = data.try_into().map_err(|_| CodecError::TypeMismatch)?;
   Ok(if cfg!(feature = "wire-little-endian") {
      u64::from_le_bytes(bytes)
   } else { u64::from_be_bytes(bytes) })
}
