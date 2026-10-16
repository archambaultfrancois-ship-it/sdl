//! Optional fixed S2PS/2 envelope, independent of SDL2 encoding and transport.
use crate::CodecError;
pub const ENVELOPE_HEADER_SIZE: usize = 9;
pub fn pack_envelope(id: u32, payload: &[u8]) -> Result<Vec<u8>, CodecError> {
    if payload.is_empty() { return Err(CodecError::Truncated); }
    let size = payload.len().checked_add(9).ok_or(CodecError::LengthOverflow)?;
    let mut out = Vec::new();
    out.try_reserve_exact(size).map_err(|_| CodecError::LengthOverflow)?;
    out.extend_from_slice(b"S2PS\x02"); out.extend_from_slice(&id.to_be_bytes());
    out.extend_from_slice(payload); Ok(out)
}
/// The returned payload borrows from the input. No SDL2 validation is performed.
pub fn unpack_envelope(data: &[u8]) -> Result<(u32, &[u8]), CodecError> {
    if data.len() <= 9 { return Err(CodecError::Truncated); }
    if &data[..5] != b"S2PS\x02" { return Err(CodecError::TypeMismatch); }
    Ok((u32::from_be_bytes([data[5], data[6], data[7], data[8]]), &data[9..]))
}
