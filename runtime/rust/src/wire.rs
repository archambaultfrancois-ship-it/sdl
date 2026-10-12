use crate::dynamic::{fixed_type_size, parse_descriptor, FieldDesc, SchemaDesc};
use std::collections::{BTreeMap, HashSet};
use std::convert::TryInto;
use std::fmt::Write as FmtWrite;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CodecError {
    Truncated,
    TypeMismatch,
    LengthOverflow,
    InvalidBoolean,
    InvalidEnum,
    InvalidUtf8,
    InvalidDescriptor,
}
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Complex32 {
    pub real: f32,
    pub imag: f32,
}
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Complex64 {
    pub real: f64,
    pub imag: f64,
}

pub trait WireValue: Sized {
    /// Fixed binary layout emitted by the generator; independent of Rust memory layout.
    const FIXED_SIZE: Option<usize> = None;
    fn decode_fixed(_bytes: &[u8]) -> Result<Self, CodecError> {
        Err(CodecError::TypeMismatch)
    }
    /// Decode a validated fixed-layout block. Types may provide a bulk conversion loop.
    fn decode_fixed_sequence(bytes: &[u8]) -> Result<Vec<Self>, CodecError> {
        let size = Self::FIXED_SIZE.filter(|size| *size != 0)
            .ok_or(CodecError::TypeMismatch)?;
        if bytes.len() % size != 0 { return Err(CodecError::Truncated); }
        let mut values = Vec::new();
        values.try_reserve(bytes.len() / size).map_err(|_| CodecError::LengthOverflow)?;
        for record in bytes.chunks_exact(size) { values.push(Self::decode_fixed(record)?); }
        Ok(values)
    }
    fn encode_fixed(&self, _bytes: &mut [u8]) -> Result<(), CodecError> {
        Err(CodecError::TypeMismatch)
    }
    fn encode_value(&self, out: &mut Vec<u8>) -> Result<(), CodecError>;
    fn decode_value(reader: &mut Reader<'_>, type_name: &str) -> Result<Self, CodecError>;
    fn encoded_size(&self) -> Result<usize, CodecError>;
}
/// Formats generated SDL values as an indented, structured string.
pub trait SdlDisplay {
    fn fmt_sdl(&self, output: &mut String, indent_width: usize, depth: usize);
}

/// Render an SDL value using `indent_width` spaces per nesting level.
pub fn display<T: SdlDisplay + ?Sized>(value: &T, indent_width: usize) -> String {
    let mut output = String::new();
    value.fmt_sdl(&mut output, indent_width, 0);
    output.push('\n');
    output
}

pub fn display_struct_start(output: &mut String, name: &str, _: usize, _: usize) {
    let _ = writeln!(output, "{} {{", name);
}

pub fn display_struct_field<T: SdlDisplay + ?Sized>(
    output: &mut String,
    name: &str,
    value: &T,
    depth: usize,
    indent_width: usize,
) {
    display_indent(output, depth.saturating_add(1), indent_width);
    let _ = write!(output, "{}: ", name);
    value.fmt_sdl(output, indent_width, depth.saturating_add(1));
    output.push('\n');
}

pub fn display_struct_end(output: &mut String, depth: usize, indent_width: usize) {
    display_indent(output, depth, indent_width);
    output.push('}');
}

fn display_indent(output: &mut String, depth: usize, indent_width: usize) {
    for _ in 0..depth.saturating_mul(indent_width) {
        output.push(' ');
    }
}

macro_rules! display_scalar {
   ($($type:ty),*) => {$(
      impl SdlDisplay for $type {
         fn fmt_sdl(&self, output: &mut String, _: usize, _: usize) {
            let _ = write!(output, "{}", self);
         }
      }
   )*};
}

display_scalar!(bool, i8, i16, i32, i64, u8, u16, u32, u64, f32, f64);

impl SdlDisplay for String {
    fn fmt_sdl(&self, output: &mut String, _: usize, _: usize) {
        let _ = write!(output, "{:?}", self);
    }
}

impl SdlDisplay for str {
    fn fmt_sdl(&self, output: &mut String, _: usize, _: usize) {
        let _ = write!(output, "{:?}", self);
    }
}

impl SdlDisplay for Complex32 {
    fn fmt_sdl(&self, output: &mut String, _: usize, _: usize) {
        let _ = write!(output, "({}, {})", self.real, self.imag);
    }
}

impl SdlDisplay for Complex64 {
    fn fmt_sdl(&self, output: &mut String, _: usize, _: usize) {
        let _ = write!(output, "({}, {})", self.real, self.imag);
    }
}

impl<T: SdlDisplay> SdlDisplay for Option<T> {
    fn fmt_sdl(&self, output: &mut String, indent_width: usize, depth: usize) {
        match self {
            Some(value) => value.fmt_sdl(output, indent_width, depth),
            None => output.push_str("null"),
        }
    }
}

impl<T: SdlDisplay> SdlDisplay for Vec<T> {
    fn fmt_sdl(&self, output: &mut String, indent_width: usize, depth: usize) {
        if self.is_empty() {
            output.push_str("[]");
            return;
        }
        output.push_str("[\n");
        for (index, value) in self.iter().enumerate() {
            display_indent(output, depth.saturating_add(1), indent_width);
            value.fmt_sdl(output, indent_width, depth.saturating_add(1));
            if index + 1 < self.len() {
                output.push(',');
            }
            output.push('\n');
        }
        display_indent(output, depth, indent_width);
        output.push(']');
    }
}

impl<T: SdlDisplay, const N: usize> SdlDisplay for [T; N] {
    fn fmt_sdl(&self, output: &mut String, indent_width: usize, depth: usize) {
        if self.is_empty() {
            output.push_str("[]");
            return;
        }
        output.push_str("[\n");
        for (index, value) in self.iter().enumerate() {
            display_indent(output, depth.saturating_add(1), indent_width);
            value.fmt_sdl(output, indent_width, depth.saturating_add(1));
            if index + 1 < N {
                output.push(',');
            }
            output.push('\n');
        }
        display_indent(output, depth, indent_width);
        output.push(']');
    }
}

pub trait SdlMessage: WireValue + Default + 'static {
    const NAME: &'static str;
    const DESCRIPTOR: &'static str;
    fn type_info() -> TypeInfo {
        TypeInfo {
            name: Self::NAME,
            descriptor: Self::DESCRIPTOR,
            decoder: decode_box::<Self>,
            type_id: std::any::TypeId::of::<Self>(),
        }
    }
}
#[derive(Clone, Copy)]
pub struct TypeInfo {
    pub name: &'static str,
    pub descriptor: &'static str,
    type_id: std::any::TypeId,
    decoder: for<'a> fn(&mut Reader<'a>) -> Result<Box<dyn std::any::Any>, CodecError>,
}
fn decode_box<T: SdlMessage>(r: &mut Reader<'_>) -> Result<Box<dyn std::any::Any>, CodecError> {
    Ok(Box::new(T::decode_value(r, T::NAME)?))
}
pub struct Context {
    pub(crate) schema: SchemaDesc,
    names: Vec<String>,
    locals: BTreeMap<String, TypeInfo>,
    emissions: BTreeMap<String, bool>,
    sizes: BTreeMap<String, Option<usize>>,
    fixed_matches: HashSet<String>,
}
fn merge(catalogues: &[&str]) -> Result<SchemaDesc, CodecError> {
    let mut result = SchemaDesc {
        messages: Default::default(),
        enums: Default::default(),
    };
    for text in catalogues {
        let schema = parse_descriptor(text)?;
        for (name, value) in schema.messages {
            if let Some(previous) = result.messages.get(&name) {
                if previous != &value {
                    return Err(CodecError::InvalidDescriptor);
                }
            }
            result.messages.insert(name, value);
        }
        for (name, value) in schema.enums {
            if let Some(previous) = result.enums.get(&name) {
                if previous != &value {
                    return Err(CodecError::InvalidDescriptor);
                }
            }
            result.enums.insert(name, value);
        }
    }
    Ok(result)
}
pub fn description(types: &[TypeInfo]) -> Result<String, CodecError> {
    let schema = merge(&types.iter().map(|t| t.descriptor).collect::<Vec<_>>())?;
    let text = crate::dynamic::render(&schema);
    parse_descriptor(&text)?;
    Ok(text)
}
pub fn prepare(text: &str, types: &[TypeInfo]) -> Result<Context, CodecError> {
    let schema = parse_descriptor(text)?;
    let local_schema = merge(&types.iter().map(|t| t.descriptor).collect::<Vec<_>>())?;
    let mut locals = BTreeMap::new();
    for info in types {
        if locals.insert(info.name.to_owned(), *info).is_some() {
            return Err(CodecError::InvalidDescriptor);
        }
    }
    for (name, message) in &schema.messages {
        if let Some(local) = local_schema.messages.get(name) {
            for f in &message.fields {
                if let Some(l) = local.fields.iter().find(|l| l.id == f.id) {
                    if f.type_name != l.type_name
                        || f.dimensions != l.dimensions
                        || f.modifier.min(2) != l.modifier.min(2)
                    {
                        return Err(CodecError::TypeMismatch);
                    }
                }
            }
        }
    }
    let mut names: Vec<_> = schema.messages.keys().cloned().collect();
    names.sort();
    let sizes = schema
        .messages
        .keys()
        .chain(schema.enums.keys())
        .map(|name| {
            (
                name.clone(),
                fixed_type_size(name, &schema, &mut HashSet::new(), 0),
            )
        })
        .collect();
    let mut emissions = BTreeMap::new();
    for name in &names {
        emission_match(&schema, &local_schema, name, &mut emissions);
    }
    let mut fixed_cache = BTreeMap::new();
    for name in schema.messages.keys().chain(schema.enums.keys()) {
        fixed_match(&schema, &local_schema, name, &mut fixed_cache);
    }
    let fixed_matches = fixed_cache.into_iter().filter_map(|(name, matches)| {
        if matches { Some(name) } else { None }
    }).collect();
    Ok(Context {
        schema,
        names,
        locals,
        emissions,
        sizes,
        fixed_matches,
    })
}
// Match binary layouts once, including enum value sets. Field/variant renames
// do not change a layout. Added/removed fields or enum values retain the generic
// decoder and its remote/local validation rules.
fn fixed_match(schema: &SchemaDesc, local: &SchemaDesc, name: &str,
               cache: &mut BTreeMap<String, bool>) -> bool {
    if matches!(name, "bool" | "int8" | "int16" | "int32" | "int64" |
                "fl32" | "fl64" | "c32" | "c64") {
        return true;
    }
    if let Some(value) = cache.get(name) { return *value; }
    let result = if let (Some(remote), Some(native)) =
        (schema.enums.get(name), local.enums.get(name)) {
        remote.len() == native.len() && remote.iter().all(|(_, value)|
            native.iter().any(|(_, local_value)| local_value == value))
    } else if let (Some(remote), Some(native)) =
        (schema.messages.get(name), local.messages.get(name)) {
        !remote.fields.is_empty() && remote.fields.len() == native.fields.len() &&
        remote.fields.iter().zip(&native.fields).all(|(f, l)|
            f.modifier == 0 && l.modifier == 0 && f.id == l.id &&
            f.type_name == l.type_name && f.dimensions == l.dimensions &&
            fixed_match(schema, local, &f.type_name, cache))
    } else { false };
    cache.insert(name.to_owned(), result);
    result
}

/// Encode a fixed-size sequence into one output span. Generated fixed codecs
/// write endian-aware fields at binary offsets, without exposing native padding.
pub fn encode_sequence<T: WireValue>(values: &[T], out: &mut Vec<u8>) -> Result<(), CodecError> {
    if let Some(size) = T::FIXED_SIZE.filter(|s| *s != 0) {
        let bytes = size.checked_mul(values.len()).ok_or(CodecError::LengthOverflow)?;
        let start = out.len();
        let end = start.checked_add(bytes).ok_or(CodecError::LengthOverflow)?;
        out.try_reserve(bytes).map_err(|_| CodecError::LengthOverflow)?;
        out.resize(end, 0);
        for (value, record) in values.iter().zip(out[start..].chunks_exact_mut(size)) {
            value.encode_fixed(record)?;
        }
    } else {
        for value in values { value.encode_value(out)?; }
    }
    Ok(())
}

fn emission_match(
    schema: &SchemaDesc,
    local: &SchemaDesc,
    name: &str,
    cache: &mut BTreeMap<String, bool>,
) -> bool {
    if let Some(result) = cache.get(name) {
        return *result;
    }
    let Some(m) = schema.messages.get(name) else {
        return false;
    };
    let Some(l) = local.messages.get(name) else {
        return false;
    };
    let result = m.fields.len() == l.fields.len()
        && m.fields.iter().zip(&l.fields).all(|(f, l)| {
            f.id == l.id
                && f.type_name == l.type_name
                && f.dimensions == l.dimensions
                && f.modifier.min(2) == l.modifier.min(2)
                && (!schema.messages.contains_key(&f.type_name)
                    || emission_match(schema, local, &f.type_name, cache))
        });
    cache.insert(name.to_owned(), result);
    result
}
pub fn encode<T: SdlMessage>(ctx: &Context, value: &T) -> Result<Vec<u8>, CodecError> {
    if let Some(info) = ctx.locals.get(T::NAME) {
        if info.type_id != std::any::TypeId::of::<T>() {
            return Err(CodecError::TypeMismatch);
        }
    }
    let id = ctx
        .names
        .iter()
        .position(|n| n == T::NAME)
        .ok_or(CodecError::TypeMismatch)?
        + 1;
    if !ctx.emissions.get(T::NAME).copied().unwrap_or(false) {
        return Err(CodecError::TypeMismatch);
    }
    let mut out = Vec::new();
    out.try_reserve(
        value
            .encoded_size()?
            .checked_add(count_size(id)?)
            .ok_or(CodecError::LengthOverflow)?,
    )
    .map_err(|_| CodecError::LengthOverflow)?;
    write_count(id, &mut out)?;
    value.encode_value(&mut out)?;
    Ok(out)
}
pub fn decode<T: SdlMessage>(ctx: &Context, data: &[u8]) -> Result<T, CodecError> {
    if ctx.locals.get(T::NAME).map(|info| info.type_id) != Some(std::any::TypeId::of::<T>()) {
        return Err(CodecError::TypeMismatch);
    }
    let mut r = Reader::new(ctx, data);
    if r.root()? != T::NAME {
        return Err(CodecError::TypeMismatch);
    }
    let value = T::decode_value(&mut r, T::NAME)?;
    r.finish()?;
    Ok(value)
}
pub struct TypedMessage {
    pub type_name: String,
    pub value: Box<dyn std::any::Any>,
}
pub fn decode_any(ctx: &Context, data: &[u8]) -> Result<TypedMessage, CodecError> {
    let mut r = Reader::new(ctx, data);
    let name = r.root()?.to_owned();
    let info = ctx.locals.get(&name).ok_or(CodecError::TypeMismatch)?;
    let value = (info.decoder)(&mut r)?;
    r.finish()?;
    Ok(TypedMessage {
        type_name: name,
        value,
    })
}
pub fn count_size(n: usize) -> Result<usize, CodecError> {
    if n > u32::MAX as usize {
        return Err(CodecError::LengthOverflow);
    }
    let mut n = n;
    let mut bytes = 1;
    while n >= 128 {
        n >>= 7;
        bytes += 1;
    }
    Ok(bytes)
}
pub fn write_count(n: usize, out: &mut Vec<u8>) -> Result<(), CodecError> {
    count_size(n)?;
    let mut n = n;
    while n >= 128 {
        out.push((n as u8 & 127) | 128);
        n >>= 7;
    }
    out.push(n as u8);
    Ok(())
}
pub fn add_size(total: usize, n: usize) -> Result<usize, CodecError> {
    total.checked_add(n).ok_or(CodecError::LengthOverflow)
}

pub struct Reader<'a> {
    pub(crate) context: &'a Context,
    data: &'a [u8],
    offset: usize,
    depth: usize,
}
impl<'a> Reader<'a> {
    pub fn new(context: &'a Context, data: &'a [u8]) -> Self {
        Self {
            context,
            data,
            offset: 0,
            depth: 0,
        }
    }
    pub fn take(&mut self, n: usize) -> Result<&'a [u8], CodecError> {
        if n > self.data.len() - self.offset {
            return Err(CodecError::Truncated);
        }
        let start = self.offset;
        self.offset += n;
        Ok(&self.data[start..self.offset])
    }
    pub fn count(&mut self) -> Result<usize, CodecError> {
        let mut n = 0u32;
        for i in 0..5 {
            let b = self.take(1)?[0];
            if i == 4 && b > 15 {
                return Err(CodecError::LengthOverflow);
            }
            n |= ((b & 127) as u32) << (7 * i);
            if b < 128 {
                if i > 0 && b == 0 {
                    return Err(CodecError::TypeMismatch);
                }
                return Ok(n as usize);
            }
        }
        Err(CodecError::LengthOverflow)
    }
    pub fn root(&mut self) -> Result<&'a str, CodecError> {
        let n = self.count()?;
        self.context
            .names
            .get(n.wrapping_sub(1))
            .map(|s| s.as_str())
            .ok_or(CodecError::TypeMismatch)
    }
    pub fn fixed_compatible(&self, name: &str) -> bool {
        matches!(name, "bool" | "int8" | "int16" | "int32" | "int64" |
                 "fl32" | "fl64" | "c32" | "c64") || self.context.fixed_matches.contains(name)
    }
    pub fn decode_sequence<T: WireValue>(&mut self, name: &str, count: usize)
        -> Result<Vec<T>, CodecError> {
        if let Some(size) = T::FIXED_SIZE.filter(|s| *s != 0) {
            if self.fixed_compatible(name) {
                let bytes = count.checked_mul(size).ok_or(CodecError::LengthOverflow)?;
                // Bounds are checked before allocation, even for hostile counts.
                let payload = self.take(bytes)?;
                return T::decode_fixed_sequence(payload);
            }
        }
        let mut values = Vec::new();
        values.try_reserve(count).map_err(|_| CodecError::LengthOverflow)?;
        for _ in 0..count { values.push(T::decode_value(self, name)?); }
        Ok(values)
    }
    pub fn fields(&self, name: &str) -> Result<&'a [FieldDesc], CodecError> {
        self.context
            .schema
            .messages
            .get(name)
            .map(|m| m.fields.as_slice())
            .ok_or(CodecError::TypeMismatch)
    }
    pub fn field_count(&mut self, f: &FieldDesc) -> Result<usize, CodecError> {
        let n = if f.modifier == 0 { 1 } else { self.count()? };
        if f.modifier == 1 && n > 1 {
            return Err(CodecError::TypeMismatch);
        }
        let size = match f.type_name.as_str() {
            "bool" | "int8" => Some(1),
            "int16" => Some(2),
            "int32" | "fl32" => Some(4),
            "int64" | "fl64" | "c32" => Some(8),
            "c64" => Some(16),
            _ => self.context.sizes.get(&f.type_name).copied().flatten(),
        };
        if let Some(size) = size {
            let size = f
                .dimensions
                .iter()
                .try_fold(size, |s, n| s.checked_mul(*n))
                .ok_or(CodecError::LengthOverflow)?;
            if n > (self.data.len() - self.offset) / size {
                return Err(CodecError::Truncated);
            }
        } else if n > 1048576 {
            return Err(CodecError::LengthOverflow);
        }
        Ok(n)
    }
    pub fn enter(&mut self) -> Result<(), CodecError> {
        self.depth += 1;
        if self.depth > 64 {
            Err(CodecError::InvalidDescriptor)
        } else {
            Ok(())
        }
    }
    pub fn leave(&mut self) {
        self.depth -= 1;
    }
    pub fn finish(&self) -> Result<(), CodecError> {
        if self.offset == self.data.len() {
            Ok(())
        } else {
            Err(CodecError::TypeMismatch)
        }
    }
    pub fn validate_enum(&self, name: &str, n: i32) -> Result<(), CodecError> {
        if self
            .context
            .schema
            .enums
            .get(name)
            .map_or(false, |items| items.iter().any(|(_, v)| *v == n))
        {
            Ok(())
        } else {
            Err(CodecError::InvalidEnum)
        }
    }
    pub fn skip_field(&mut self, f: &FieldDesc, n: usize) -> Result<(), CodecError> {
        for _ in 0..n {
            self.skip_value(&f.type_name, &f.dimensions)?;
        }
        Ok(())
    }
    fn skip_value(&mut self, name: &str, dims: &[usize]) -> Result<(), CodecError> {
        if !dims.is_empty() {
            for _ in 0..dims[0] {
                self.skip_value(name, &dims[1..])?;
            }
            return Ok(());
        }
        if self.context.schema.messages.contains_key(name) {
            self.enter()?;
            for f in self.fields(name)? {
                let n = self.field_count(f)?;
                self.skip_field(f, n)?;
            }
            self.leave();
            return Ok(());
        }
        if name == "string" {
            let n = self.count()?;
            std::str::from_utf8(self.take(n)?).map_err(|_| CodecError::InvalidUtf8)?;
            return Ok(());
        }
        if self.context.schema.enums.contains_key(name) {
            let n = i32::decode_value(self, name)?;
            return self.validate_enum(name, n);
        }
        if name == "bool" {
            bool::decode_value(self, name)?;
            return Ok(());
        }
        let n = match name {
            "int8" => 1,
            "int16" => 2,
            "int32" | "fl32" => 4,
            "int64" | "fl64" | "c32" => 8,
            "c64" => 16,
            _ => return Err(CodecError::TypeMismatch),
        };
        self.take(n)?;
        Ok(())
    }
}
macro_rules! numeric {
   ($($t:ty),*)=>{$(impl WireValue for $t {
      const FIXED_SIZE: Option<usize> = Some(std::mem::size_of::<Self>());
      #[inline]
      fn decode_fixed(bytes: &[u8])->Result<Self,CodecError>{
         Ok(Self::from_be_bytes(bytes.try_into().map_err(|_|CodecError::Truncated)?))
      }
      #[inline]
      fn encode_fixed(&self,bytes:&mut[u8])->Result<(),CodecError>{
         if bytes.len()!=std::mem::size_of::<Self>() {return Err(CodecError::Truncated);}
         bytes.copy_from_slice(&self.to_be_bytes());Ok(())
      }
      fn encode_value(&self,out:&mut Vec<u8>)->Result<(),CodecError>{out.extend_from_slice(&self.to_be_bytes());Ok(())}
      fn decode_value(r:&mut Reader<'_>,_:&str)->Result<Self,CodecError>{Ok(Self::from_be_bytes(r.take(std::mem::size_of::<Self>())?.try_into().map_err(|_|CodecError::Truncated)?))}
      fn encoded_size(&self)->Result<usize,CodecError>{Ok(std::mem::size_of::<Self>())}
   })*};
}
numeric!(i8, i16, i32, i64, f32, f64);
impl WireValue for bool {
    const FIXED_SIZE: Option<usize> = Some(1);
    #[inline]
    fn decode_fixed(bytes: &[u8]) -> Result<Self, CodecError> {
        match bytes { [0] => Ok(false), [1] => Ok(true), [_] => Err(CodecError::InvalidBoolean),
                      _ => Err(CodecError::Truncated) }
    }
    #[inline]
    fn encode_fixed(&self, bytes: &mut [u8]) -> Result<(), CodecError> {
        if bytes.len() != 1 { return Err(CodecError::Truncated); }
        bytes[0] = u8::from(*self); Ok(())
    }
    fn encode_value(&self, out: &mut Vec<u8>) -> Result<(), CodecError> {
        out.push(u8::from(*self));
        Ok(())
    }
    fn decode_value(r: &mut Reader<'_>, _: &str) -> Result<Self, CodecError> {
        match r.take(1)?[0] {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(CodecError::InvalidBoolean),
        }
    }
    fn encoded_size(&self) -> Result<usize, CodecError> {
        Ok(1)
    }
}
impl WireValue for String {
    fn encode_value(&self, out: &mut Vec<u8>) -> Result<(), CodecError> {
        write_count(self.len(), out)?;
        out.extend_from_slice(self.as_bytes());
        Ok(())
    }
    fn decode_value(r: &mut Reader<'_>, _: &str) -> Result<Self, CodecError> {
        let n = r.count()?;
        std::str::from_utf8(r.take(n)?)
            .map(str::to_owned)
            .map_err(|_| CodecError::InvalidUtf8)
    }
    fn encoded_size(&self) -> Result<usize, CodecError> {
        add_size(self.len(), count_size(self.len())?)
    }
}
macro_rules! complex {
    ($t:ty,$f:ty) => {
        impl WireValue for $t {
            const FIXED_SIZE: Option<usize> = Some(2 * std::mem::size_of::<$f>());
            #[inline]
            fn decode_fixed(bytes: &[u8]) -> Result<Self, CodecError> {
                let size = std::mem::size_of::<$f>();
                if bytes.len() != 2 * size { return Err(CodecError::Truncated); }
                Ok(Self { real: <$f>::decode_fixed(&bytes[..size])?,
                          imag: <$f>::decode_fixed(&bytes[size..])? })
            }
            fn decode_fixed_sequence(bytes: &[u8]) -> Result<Vec<Self>, CodecError> {
                let size = 2 * std::mem::size_of::<$f>();
                if bytes.len() % size != 0 { return Err(CodecError::Truncated); }
                let count = bytes.len() / size;
                let mut values = Vec::new();
                values.try_reserve_exact(count).map_err(|_| CodecError::LengthOverflow)?;
                values.resize(count, Self::default());
                // A fixed output slice avoids the capacity/length update on every push.
                for (value, record) in values.iter_mut().zip(bytes.chunks_exact(size)) {
                    *value = Self::decode_fixed(record)?;
                }
                Ok(values)
            }
            #[inline]
            fn encode_fixed(&self, bytes: &mut [u8]) -> Result<(), CodecError> {
                let size = std::mem::size_of::<$f>();
                if bytes.len() != 2 * size { return Err(CodecError::Truncated); }
                self.real.encode_fixed(&mut bytes[..size])?;
                self.imag.encode_fixed(&mut bytes[size..])
            }
            fn encode_value(&self, out: &mut Vec<u8>) -> Result<(), CodecError> {
                self.real.encode_value(out)?;
                self.imag.encode_value(out)
            }
            fn decode_value(r: &mut Reader<'_>, name: &str) -> Result<Self, CodecError> {
                Ok(Self {
                    real: <$f>::decode_value(r, name)?,
                    imag: <$f>::decode_value(r, name)?,
                })
            }
            fn encoded_size(&self) -> Result<usize, CodecError> {
                Ok(2 * std::mem::size_of::<$f>())
            }
        }
    };
}
complex!(Complex32, f32);
complex!(Complex64, f64);
impl<T: WireValue, const N: usize> WireValue for [T; N] {
    const FIXED_SIZE: Option<usize> = match T::FIXED_SIZE {
        Some(size) => size.checked_mul(N),
        None => None,
    };
    #[inline]
    fn encode_fixed(&self, bytes: &mut [u8]) -> Result<(), CodecError> {
        let size = T::FIXED_SIZE.filter(|s| *s != 0).ok_or(CodecError::TypeMismatch)?;
        if Some(bytes.len()) != Self::FIXED_SIZE { return Err(CodecError::Truncated); }
        for (value, record) in self.iter().zip(bytes.chunks_exact_mut(size)) {
            value.encode_fixed(record)?;
        }
        Ok(())
    }
    #[inline]
    fn decode_fixed(bytes: &[u8]) -> Result<Self, CodecError> {
        let size = T::FIXED_SIZE.filter(|s| *s != 0).ok_or(CodecError::TypeMismatch)?;
        if Some(bytes.len()) != Self::FIXED_SIZE { return Err(CodecError::Truncated); }
        let mut values = Vec::new();
        values.try_reserve(N).map_err(|_| CodecError::LengthOverflow)?;
        for record in bytes.chunks_exact(size) { values.push(T::decode_fixed(record)?); }
        values.try_into().map_err(|_| CodecError::TypeMismatch)
    }
    fn encode_value(&self, out: &mut Vec<u8>) -> Result<(), CodecError> {
        for x in self {
            x.encode_value(out)?;
        }
        Ok(())
    }
    fn decode_value(r: &mut Reader<'_>, name: &str) -> Result<Self, CodecError> {
        let mut v = Vec::new();
        v.try_reserve(N).map_err(|_| CodecError::LengthOverflow)?;
        for _ in 0..N {
            v.push(T::decode_value(r, name)?);
        }
        v.try_into().map_err(|_| CodecError::TypeMismatch)
    }
    fn encoded_size(&self) -> Result<usize, CodecError> {
        self.iter()
            .try_fold(0, |s, x| add_size(s, x.encoded_size()?))
    }
}
