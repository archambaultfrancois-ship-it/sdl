use std::convert::TryInto;

fn wire_u32(value: u32) -> [u8; 4] {
   if cfg!(feature = "wire-little-endian") {
      value.to_le_bytes()
   } else {
      value.to_be_bytes()
   }
}

fn wire_u64(value: u64) -> [u8; 8] {
   if cfg!(feature = "wire-little-endian") {
      value.to_le_bytes()
   } else {
      value.to_be_bytes()
   }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CodecError {
   Truncated,
   TypeMismatch,
   LengthOverflow,
   InvalidBoolean,
   InvalidEnum,
   InvalidUtf8,
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
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError>;
   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError>;
}

pub trait FixedWire: Sized {
   const WIRE_SIZE: usize;

   fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError>;
   fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError>;
}

pub trait SdlMessage: WireValue + Default {
   const HASH: u32;

   fn encode_fields(&self, output: &mut Vec<u8>) -> Result<(), CodecError>;
   fn decode_field(&mut self, id: u32, payload: &[u8]) -> Result<(), CodecError>;

   fn encode_body(&self) -> Result<Vec<u8>, CodecError> {
      let mut output = Vec::new();
      self.encode_fields(&mut output)?;
      Ok(output)
   }

   fn decode_body(input: &[u8]) -> Result<Self, CodecError> {
      let mut value = Self::default();
      let mut offset = 0;
      while offset < input.len() {
         if input.len() - offset < 8 {
            return Err(CodecError::Truncated);
         }
         let id = read_u32(&input[offset..offset + 4])?;
         let length = read_u32(&input[offset + 4..offset + 8])? as usize;
         offset += 8;
         if length > input.len() - offset {
            return Err(CodecError::Truncated);
         }
         value.decode_field(id, &input[offset..offset + length])?;
         offset += length;
      }
      Ok(value)
   }
}

pub fn encode<T: SdlMessage>(value: &T) -> Result<Vec<u8>, CodecError> {
   let body = value.encode_body()?;
   let mut output = Vec::with_capacity(4 + body.len());
   output.extend_from_slice(&wire_u32(T::HASH));
   output.extend_from_slice(&body);
   Ok(output)
}

pub fn decode<T: SdlMessage>(input: &[u8]) -> Result<T, CodecError> {
   if input.len() < 4 {
      return Err(CodecError::Truncated);
   }
   let hash = read_u32(&input[..4])?;
   if hash != T::HASH {
      return Err(CodecError::TypeMismatch);
   }
   T::decode_body(&input[4..])
}

pub fn write_field<T: WireValue>(
   id: u32,
   value: &T,
   output: &mut Vec<u8>,
) -> Result<(), CodecError> {
   let payload = value.encode_payload()?;
   let length: u32 = payload.len().try_into().map_err(|_| CodecError::LengthOverflow)?;
   output.extend_from_slice(&wire_u32(id));
   output.extend_from_slice(&wire_u32(length));
   output.extend_from_slice(&payload);
   Ok(())
}

pub fn write_packed_field<T: FixedWire>(
   id: u32,
   values: &[T],
   output: &mut Vec<u8>,
) -> Result<(), CodecError> {
   if values.is_empty() {
      return Ok(());
   }
   if T::WIRE_SIZE == 0 || values.len() > u32::MAX as usize / T::WIRE_SIZE {
      return Err(CodecError::LengthOverflow);
   }
   let payload_length = values.len() * T::WIRE_SIZE;
   output.extend_from_slice(&wire_u32(id));
   output.extend_from_slice(&wire_u32(payload_length as u32));
   for value in values {
      value.encode_fixed(output)?;
   }
   Ok(())
}

pub fn read_packed_field<T: FixedWire>(payload: &[u8]) -> Result<Vec<T>, CodecError> {
   if T::WIRE_SIZE == 0 || payload.len() % T::WIRE_SIZE != 0 {
      return Err(CodecError::TypeMismatch);
   }
   let mut values = Vec::with_capacity(payload.len() / T::WIRE_SIZE);
   for item in payload.chunks_exact(T::WIRE_SIZE) {
      values.push(T::decode_fixed(item)?);
   }
   Ok(values)
}

fn read_u32(bytes: &[u8]) -> Result<u32, CodecError> {
   let bytes: [u8; 4] = bytes.try_into().map_err(|_| CodecError::Truncated)?;
   Ok(if cfg!(feature = "wire-little-endian") {
      u32::from_le_bytes(bytes)
   } else {
      u32::from_be_bytes(bytes)
   })
}

fn read_u64(bytes: &[u8]) -> Result<u64, CodecError> {
   let bytes: [u8; 8] = bytes.try_into().map_err(|_| CodecError::Truncated)?;
   Ok(if cfg!(feature = "wire-little-endian") {
      u64::from_le_bytes(bytes)
   } else {
      u64::from_be_bytes(bytes)
   })
}

macro_rules! fixed_integer {
   ($type:ty, $size:expr) => {
      impl WireValue for $type {
         fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
            let bytes = if cfg!(feature = "wire-little-endian") {
               self.to_le_bytes()
            } else {
               self.to_be_bytes()
            };
            Ok(bytes.to_vec())
         }

         fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
            let bytes: [u8; $size] = payload.try_into().map_err(|_| CodecError::TypeMismatch)?;
            Ok(if cfg!(feature = "wire-little-endian") {
               <$type>::from_le_bytes(bytes)
            } else {
               <$type>::from_be_bytes(bytes)
            })
         }
      }

      impl FixedWire for $type {
         const WIRE_SIZE: usize = $size;

         fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError> {
            let bytes = if cfg!(feature = "wire-little-endian") {
               self.to_le_bytes()
            } else {
               self.to_be_bytes()
            };
            output.extend_from_slice(&bytes);
            Ok(())
         }

         fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError> {
            <Self as WireValue>::decode_payload(payload)
         }
      }
   };
}

fixed_integer!(i8, 1);
fixed_integer!(i16, 2);
fixed_integer!(i32, 4);
fixed_integer!(i64, 8);

impl WireValue for bool {
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
      Ok(vec![if *self { 1 } else { 0 }])
   }

   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
      match payload {
         [0] => Ok(false),
         [1] => Ok(true),
         [_] => Err(CodecError::InvalidBoolean),
         _ => Err(CodecError::TypeMismatch),
      }
   }
}

impl FixedWire for bool {
   const WIRE_SIZE: usize = 1;

   fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError> {
      output.push(if *self { 1 } else { 0 });
      Ok(())
   }

   fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError> {
      <Self as WireValue>::decode_payload(payload)
   }
}

impl WireValue for f32 {
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
      Ok(wire_u32(self.to_bits()).to_vec())
   }

   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
      let bytes: [u8; 4] = payload.try_into().map_err(|_| CodecError::TypeMismatch)?;
      let bits = read_u32(&bytes)?;
      Ok(Self::from_bits(bits))
   }
}

impl FixedWire for f32 {
   const WIRE_SIZE: usize = 4;

   fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError> {
      output.extend_from_slice(&wire_u32(self.to_bits()));
      Ok(())
   }

   fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError> {
      <Self as WireValue>::decode_payload(payload)
   }
}

impl WireValue for f64 {
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
      Ok(wire_u64(self.to_bits()).to_vec())
   }

   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
      if payload.len() != 8 {
         return Err(CodecError::TypeMismatch);
      }
      let bits = read_u64(payload)?;
      Ok(Self::from_bits(bits))
   }
}

impl FixedWire for f64 {
   const WIRE_SIZE: usize = 8;

   fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError> {
      output.extend_from_slice(&wire_u64(self.to_bits()));
      Ok(())
   }

   fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError> {
      <Self as WireValue>::decode_payload(payload)
   }
}

impl WireValue for Complex32 {
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
      let mut output = Vec::with_capacity(8);
      output.extend_from_slice(&wire_u32(self.real.to_bits()));
      output.extend_from_slice(&wire_u32(self.imag.to_bits()));
      Ok(output)
   }

   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
      if payload.len() != 8 {
         return Err(CodecError::TypeMismatch);
      }
      Ok(Self {
         real: f32::from_bits(read_u32(&payload[..4])?),
         imag: f32::from_bits(read_u32(&payload[4..])?),
      })
   }
}

impl FixedWire for Complex32 {
   const WIRE_SIZE: usize = 8;

   fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError> {
      output.extend_from_slice(&wire_u32(self.real.to_bits()));
      output.extend_from_slice(&wire_u32(self.imag.to_bits()));
      Ok(())
   }

   fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError> {
      <Self as WireValue>::decode_payload(payload)
   }
}

impl WireValue for Complex64 {
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
      let mut output = Vec::with_capacity(16);
      output.extend_from_slice(&wire_u64(self.real.to_bits()));
      output.extend_from_slice(&wire_u64(self.imag.to_bits()));
      Ok(output)
   }

   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
      if payload.len() != 16 {
         return Err(CodecError::TypeMismatch);
      }
      let real = read_u64(&payload[..8])?;
      let imag = read_u64(&payload[8..])?;
      Ok(Self {
         real: f64::from_bits(real),
         imag: f64::from_bits(imag),
      })
   }
}

impl FixedWire for Complex64 {
   const WIRE_SIZE: usize = 16;

   fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError> {
      output.extend_from_slice(&wire_u64(self.real.to_bits()));
      output.extend_from_slice(&wire_u64(self.imag.to_bits()));
      Ok(())
   }

   fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError> {
      <Self as WireValue>::decode_payload(payload)
   }
}

impl WireValue for String {
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
      Ok(self.as_bytes().to_vec())
   }

   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
      String::from_utf8(payload.to_vec()).map_err(|_| CodecError::InvalidUtf8)
   }
}

impl<T: FixedWire, const N: usize> FixedWire for [T; N] {
   const WIRE_SIZE: usize = T::WIRE_SIZE * N;

   fn encode_fixed(&self, output: &mut Vec<u8>) -> Result<(), CodecError> {
      for value in self {
         value.encode_fixed(output)?;
      }
      Ok(())
   }

   fn decode_fixed(payload: &[u8]) -> Result<Self, CodecError> {
      if T::WIRE_SIZE == 0 || payload.len() != Self::WIRE_SIZE {
         return Err(CodecError::TypeMismatch);
      }
      let mut values = Vec::with_capacity(N);
      for item in payload.chunks_exact(T::WIRE_SIZE) {
         values.push(T::decode_fixed(item)?);
      }
      values.try_into().map_err(|_| CodecError::TypeMismatch)
   }
}

impl<T: FixedWire, const N: usize> WireValue for [T; N] {
   fn encode_payload(&self) -> Result<Vec<u8>, CodecError> {
      let mut output = Vec::with_capacity(Self::WIRE_SIZE);
      self.encode_fixed(&mut output)?;
      Ok(output)
   }

   fn decode_payload(payload: &[u8]) -> Result<Self, CodecError> {
      <Self as FixedWire>::decode_fixed(payload)
   }
}
