use crate::wire::{CodecError, Complex32, Complex64};
use std::collections::{BTreeMap, HashMap, HashSet};

const MAX_DESCRIPTOR_SIZE: usize = 1024 * 1024;
const MAX_NESTING: usize = 64;

#[derive(Clone, Debug, PartialEq)]
pub enum DynamicValue {
    Null,
    Bool(bool),
    Integer(i64),
    Unsigned(u64),
    Float(f64),
    Complex32(Complex32),
    Complex64(Complex64),
    String(String),
    Enum {
        type_name: String,
        name: String,
        value: i32,
    },
    Array(Vec<DynamicValue>),
    Message(DynamicMessage),
}

#[derive(Clone, Debug, PartialEq)]
pub struct DynamicMessage {
    pub type_name: String,
    pub fields: BTreeMap<String, DynamicValue>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct FieldDesc {
    pub id: u32,
    pub name: String,
    pub modifier: u8,
    pub type_name: String,
    pub dimensions: Vec<usize>,
    pub wire: crate::representation::WireOptions,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct MessageDesc {
    pub fields: Vec<FieldDesc>,
}

pub(crate) struct SchemaDesc {
    pub messages: HashMap<String, MessageDesc>,
    pub enums: HashMap<String, Vec<(String, i32)>>,
}

fn identifier(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 65535
        && name
            .chars()
            .all(|c| c.is_alphanumeric() || c == '_' || c == '$')
}
fn number(text: &str) -> Result<u32, CodecError> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(CodecError::InvalidDescriptor);
    }
    text.parse().map_err(|_| CodecError::InvalidDescriptor)
}
pub(crate) fn parse_descriptor(text: &str) -> Result<SchemaDesc, CodecError> {
    if text.len() > MAX_DESCRIPTOR_SIZE || !text.starts_with("SDL2|")
        || text.contains('\0') || text.chars().any(char::is_whitespace) {
        return Err(CodecError::InvalidDescriptor);
    }
    let mut messages = HashMap::new();
    let mut enums = HashMap::new();
    let mut previous_message = String::new();
    let mut previous_enum = String::new();
    for declaration in text[5..].split('|') {
        let (name, body) = declaration.split_once('{').ok_or(CodecError::InvalidDescriptor)?;
        let body = body.strip_suffix('}').ok_or(CodecError::InvalidDescriptor)?;
        let enumeration = name.starts_with('#');
        let name = if enumeration { &name[1..] } else { name };
        if !identifier(name) { return Err(CodecError::InvalidDescriptor); }
        let rows: Vec<_> = if body.is_empty() { Vec::new() } else {
            body.strip_suffix(';').ok_or(CodecError::InvalidDescriptor)?.split(';').collect()
        };
        if rows.len() > 65535 { return Err(CodecError::InvalidDescriptor); }
        let mut names = HashSet::new();
        if enumeration {
            if name <= previous_enum.as_str() || enums.len() >= 65535 || rows.is_empty() {
                return Err(CodecError::InvalidDescriptor);
            }
            previous_enum = name.to_owned();
            let mut values = Vec::new();
            let mut numbers = HashSet::new();
            for row in rows {
                let (item, value) = row.split_once('=').ok_or(CodecError::InvalidDescriptor)?;
                let digits = value.strip_prefix('-').unwrap_or(value);
                if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
                    return Err(CodecError::InvalidDescriptor);
                }
                let value: i32 = value.parse().map_err(|_| CodecError::InvalidDescriptor)?;
                if !identifier(item) || !names.insert(item.to_owned()) || !numbers.insert(value) {
                    return Err(CodecError::InvalidDescriptor);
                }
                values.push((item.to_owned(), value));
            }
            enums.insert(name.to_owned(), values);
        } else {
            if name <= previous_message.as_str() || messages.len() >= 65535 {
                return Err(CodecError::InvalidDescriptor);
            }
            previous_message = name.to_owned();
            let mut fields = Vec::new();
            let mut previous_id = 0;
            for row in rows {
                let mut pieces = row.split(':');
                let id = pieces.next().ok_or(CodecError::InvalidDescriptor)?;
                let typ = pieces.next().ok_or(CodecError::InvalidDescriptor)?;
                let tail = pieces.next().ok_or(CodecError::InvalidDescriptor)?;
                if pieces.next().is_some() || id.is_empty() { return Err(CodecError::InvalidDescriptor); }
                let (id, modifier) = match id.as_bytes().last() {
                    Some(b'?') => (&id[..id.len()-1], 1),
                    Some(b'*') => (&id[..id.len()-1], 2),
                    Some(b'+') => (&id[..id.len()-1], 3),
                    _ => (id, 0),
                };
                let id = number(id)?;
                let mut pieces = typ.split('[');
                let type_name = pieces.next().unwrap().to_owned();
                let mut dimensions = Vec::new();
                for piece in pieces {
                    let n = number(piece.strip_suffix(']').ok_or(CodecError::InvalidDescriptor)?)?;
                    if n == 0 || dimensions.len() >= 255 { return Err(CodecError::InvalidDescriptor); }
                    dimensions.push(n as usize);
                }
                let (field_name, wire) = tail.split_once('@').map_or((tail, None), |(n,w)| (n,Some(w)));
                let wire = match wire {
                    Some(w) => crate::representation::WireOptions::parse(w)?,
                    None => Default::default(),
                };
                if id <= previous_id || !identifier(&type_name) || !identifier(field_name)
                    || !names.insert(field_name.to_owned()) || (!dimensions.is_empty() && modifier != 0) {
                    return Err(CodecError::InvalidDescriptor);
                }
                previous_id = id;
                fields.push(FieldDesc { id, name: field_name.to_owned(), modifier, type_name, dimensions, wire });
            }
            messages.insert(name.to_owned(), MessageDesc { fields });
        }
    }
    if messages.is_empty() || messages.keys().any(|n| enums.contains_key(n)) {
        return Err(CodecError::InvalidDescriptor);
    }
    if messages.keys().any(|name| is_builtin_type(name))
        || enums.keys().any(|name| is_builtin_type(name))
    {
        return Err(CodecError::InvalidDescriptor);
    }
    let schema = SchemaDesc { messages, enums };
    for message in schema.messages.values() {
        for field in &message.fields {
            if !known_type(&field.type_name, &schema) {
                return Err(CodecError::InvalidDescriptor);
            }
            if !field.dimensions.is_empty() || field.modifier == 3 {
                let mut size =
                    match fixed_type_size(&field.type_name, &schema, &mut HashSet::new(), 0) {
                        Some(size) => size,
                        None => return Err(CodecError::InvalidDescriptor),
                    };
                for dimension in &field.dimensions {
                    size = size
                        .checked_mul(*dimension)
                        .filter(|size| *size <= u32::MAX as usize)
                        .ok_or(CodecError::InvalidDescriptor)?;
                }
                if size == 0 {
                    return Err(CodecError::InvalidDescriptor);
                }
            }
        }
    }
    for m in schema.messages.values() { for f in &m.fields { f.wire.validate(&f.type_name,schema.enums.contains_key(&f.type_name))?; } }
    let mut visiting = HashSet::new();
    let mut visited = HashMap::new();
    for name in schema.messages.keys() {
        validate_message_dependencies(name, &schema, &mut visiting, &mut visited, 0)?;
    }
    Ok(schema)
}
fn validate_message_dependencies(
    name: &str,
    schema: &SchemaDesc,
    visiting: &mut HashSet<String>,
    visited: &mut HashMap<String, usize>,
    depth: usize,
) -> Result<(), CodecError> {
    if depth > MAX_NESTING {
        return Err(CodecError::InvalidDescriptor);
    }
    if let Some(height) = visited.get(name) {
        return if depth + height <= MAX_NESTING {
            Ok(())
        } else {
            Err(CodecError::InvalidDescriptor)
        };
    }
    if !visiting.insert(name.to_owned()) {
        return Err(CodecError::InvalidDescriptor);
    }
    let message = schema
        .messages
        .get(name)
        .ok_or(CodecError::InvalidDescriptor)?;
    let mut height = 0;
    for field in &message.fields {
        if schema.messages.contains_key(&field.type_name) {
            validate_message_dependencies(&field.type_name, schema, visiting, visited, depth + 1)?;
            height = height.max(visited[&field.type_name] + 1);
        }
    }
    if depth + height > MAX_NESTING {
        return Err(CodecError::InvalidDescriptor);
    }
    visiting.remove(name);
    visited.insert(name.to_owned(), height);
    Ok(())
}

fn is_builtin_type(name: &str) -> bool {
    matches!(
        name,
        "bool" | "int8" | "uint8" | "int16" | "uint16" | "int32" | "uint32" | "int64" | "uint64" | "fl32" | "fl64" | "c32" | "c64" | "string"
    )
}

fn known_type(name: &str, schema: &SchemaDesc) -> bool {
    matches!(
        name,
        "bool" | "int8" | "uint8" | "int16" | "uint16" | "int32" | "uint32" | "int64" | "uint64" | "fl32" | "fl64" | "c32" | "c64" | "string"
    ) || schema.messages.contains_key(name)
        || schema.enums.contains_key(name)
}

pub(crate) fn fixed_type_size(
    name: &str,
    schema: &SchemaDesc,
    active: &mut HashSet<String>,
    depth: usize,
) -> Option<usize> {
    fixed_type_size_cached(name, schema, active, depth, &mut HashMap::new())
}
fn fixed_type_size_cached(
    name: &str,
    schema: &SchemaDesc,
    active: &mut HashSet<String>,
    depth: usize,
    cache: &mut HashMap<String, Option<usize>>,
) -> Option<usize> {
    if depth > MAX_NESTING {
        return None;
    }
    if let Some(size) = cache.get(name) {
        return *size;
    }
    let result = (|| {
        let primitive = match name {
            "bool" | "int8" | "uint8" => Some(1),
            "int16" | "uint16" => Some(2),
            "int32" | "uint32" | "fl32" => Some(4),
            "int64" | "uint64" | "fl64" => Some(8),
            "c32" => Some(8),
            "c64" => Some(16),
            "string" => return None,
            _ => None,
        };
        if primitive.is_some() {
            return primitive;
        }
        if schema.enums.contains_key(name) {
            return Some(4);
        }
        let message = schema.messages.get(name)?;
        if message.fields.is_empty() || !active.insert(name.to_owned()) {
            return None;
        }
        let mut total = 0usize;
        for field in &message.fields {
            if field.modifier != 0 {
                active.remove(name);
                return None;
            }
            let mut size =
                fixed_type_size_cached(&field.type_name, schema, active, depth + 1, cache)?;
            for dimension in &field.dimensions {
                size = size.checked_mul(*dimension)?;
            }
            total = total.checked_add(size)?;
            if total > u32::MAX as usize {
                active.remove(name);
                return None;
            }
        }
        Some(total)
    })();
    active.remove(name);
    cache.insert(name.to_owned(), result);
    result
}

pub(crate) fn render(schema: &SchemaDesc) -> String {
    let mut declarations = Vec::new();
    let mut names: Vec<_> = schema.messages.keys().collect();
    names.sort();
    for name in names {
        let mut text = format!("{}{{", name);
        for f in &schema.messages[name].fields {
            let dims: String = f.dimensions.iter().map(|n| format!("[{}]", n)).collect();
            text.push_str(&format!("{}{}:{}{}:{}{};", f.id, ["", "?", "*", "+"][f.modifier as usize], f.type_name, dims, f.name, f.wire.suffix()));
        }
        text.push('}');declarations.push(text);
    }
    let mut names: Vec<_> = schema.enums.keys().collect();names.sort();
    for name in names {
        let mut text = format!("#{}{{", name);
        for (item, n) in &schema.enums[name] {text.push_str(&format!("{}={};", item, n));}
        text.push('}');declarations.push(text);
    }
    format!("SDL2|{}", declarations.join("|"))
}

pub fn decode_dynamic(
    context: &crate::wire::Context,
    data: &[u8],
) -> Result<DynamicMessage, CodecError> {
    let normalized = context.normalize(data)?;
    let mut reader = crate::wire::Reader::new(context, normalized.as_ref());
    let name = reader.root()?.to_owned();
    let result = read_message(&name, &mut reader)?;
    reader.finish()?;
    Ok(result)
}
fn read_message(
    name: &str,
    reader: &mut crate::wire::Reader<'_>,
) -> Result<DynamicMessage, CodecError> {
    reader.enter()?;
    let mut values = BTreeMap::new();
    for f in reader.fields(name)? {
        let count = reader.field_count(f)?;
        let value = if f.modifier >= 2 {
            let mut values = Vec::new();
            values
                .try_reserve(count)
                .map_err(|_| CodecError::LengthOverflow)?;
            for _ in 0..count {
                values.push(read_value(&f.type_name, &f.dimensions, reader)?);
            }
            DynamicValue::Array(values)
        } else if count == 0 {
            DynamicValue::Null
        } else {
            read_value(&f.type_name, &f.dimensions, reader)?
        };
        values.insert(f.name.clone(), value);
    }
    reader.leave();
    Ok(DynamicMessage {
        type_name: name.to_owned(),
        fields: values,
    })
}
fn read_value(
    name: &str,
    dims: &[usize],
    r: &mut crate::wire::Reader<'_>,
) -> Result<DynamicValue, CodecError> {
    use crate::wire::WireValue;
    if !dims.is_empty() {
        let mut values = Vec::new();
        values
            .try_reserve(dims[0])
            .map_err(|_| CodecError::LengthOverflow)?;
        for _ in 0..dims[0] {
            values.push(read_value(name, &dims[1..], r)?);
        }
        return Ok(DynamicValue::Array(values));
    }
    if r.context.schema.messages.contains_key(name) {
        return Ok(DynamicValue::Message(read_message(name, r)?));
    }
    if let Some(items) = r.context.schema.enums.get(name) {
        let value = i32::decode_value(r, name)?;
        let item = items
            .iter()
            .find(|(_, n)| *n == value)
            .ok_or(CodecError::InvalidEnum)?;
        return Ok(DynamicValue::Enum {
            type_name: name.to_owned(),
            name: item.0.clone(),
            value,
        });
    }
    Ok(match name {
        "bool" => DynamicValue::Bool(bool::decode_value(r, name)?),
        "uint8" => DynamicValue::Unsigned(u8::decode_value(r, name)? as u64),
        "int8" => DynamicValue::Integer(i8::decode_value(r, name)? as i64),
        "uint16" => DynamicValue::Unsigned(u16::decode_value(r, name)? as u64),
        "int16" => DynamicValue::Integer(i16::decode_value(r, name)? as i64),
        "uint32" => DynamicValue::Unsigned(u32::decode_value(r, name)? as u64),
        "int32" => DynamicValue::Integer(i32::decode_value(r, name)? as i64),
        "uint64" => DynamicValue::Unsigned(u64::decode_value(r, name)? as u64),
        "int64" => DynamicValue::Integer(i64::decode_value(r, name)?),
        "fl32" => DynamicValue::Float(f32::decode_value(r, name)? as f64),
        "fl64" => DynamicValue::Float(f64::decode_value(r, name)?),
        "c32" => DynamicValue::Complex32(Complex32::decode_value(r, name)?),
        "c64" => DynamicValue::Complex64(Complex64::decode_value(r, name)?),
        "string" => DynamicValue::String(String::decode_value(r, name)?),
        _ => return Err(CodecError::InvalidDescriptor),
    })
}
