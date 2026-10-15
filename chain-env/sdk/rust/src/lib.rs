//! Binary stdio component SDK. No transport or codec dependencies.
use std::io::{self, Read, Write};

pub const MAX_SIZE: usize = 1024 * 1024;
pub struct Event { pub op: u8, pub data: Vec<u8> }
pub struct Input<'a> { pub port: &'a str, pub catalogue: &'a str, pub payload: &'a [u8] }
impl Input<'_> {
    /// Explicit null input from the adapter, allowed only on a required timed input.
    pub fn is_null(&self) -> bool { self.catalogue.is_empty() && self.payload.is_empty() }
}

pub fn receive() -> io::Result<Event> {
    let mut reader = io::stdin().lock();
    let mut prefix = [0; 4];
    reader.read_exact(&mut prefix)?;
    let size = u32::from_be_bytes(prefix) as usize;
    if size == 0 || size > MAX_SIZE { return Err(invalid("invalid IPC size")); }
    let mut body = vec![0; size];
    reader.read_exact(&mut body)?;
    Ok(Event { op: body[0], data: body[1..].to_vec() })
}
pub fn send(op: u8, data: &[u8]) -> io::Result<()> {
    if data.len() >= MAX_SIZE { return Err(invalid("IPC too large")); }
    let mut writer = io::stdout().lock();
    writer.write_all(&((data.len()+1) as u32).to_be_bytes())?;
    writer.write_all(&[op])?;
    writer.write_all(data)?;
    writer.flush()
}
pub fn publish(port: &str, payload: &[u8]) -> io::Result<()> {
    if port.len() > u16::MAX as usize { return Err(invalid("port too long")); }
    let mut data = Vec::new();
    data.extend_from_slice(&(port.len() as u16).to_be_bytes());
    data.extend_from_slice(port.as_bytes()); data.extend_from_slice(payload);
    send(b'P', &data)
}
pub fn input(event: &Event) -> io::Result<Input<'_>> {
    let d = &event.data;
    if event.op != b'D' || d.len() < 6 { return Err(invalid("expected input")); }
    let n = u16::from_be_bytes([d[0],d[1]]) as usize;
    if n > d.len()-6 { return Err(invalid("truncated port")); }
    let c = u32::from_be_bytes(d[2+n..6+n].try_into().unwrap()) as usize;
    if c > d.len()-6-n { return Err(invalid("truncated catalogue")); }
    Ok(Input { port: std::str::from_utf8(&d[2..2+n]).map_err(|_| invalid("port UTF8"))?,
        catalogue: std::str::from_utf8(&d[6+n..6+n+c]).map_err(|_| invalid("catalogue UTF8"))?,
        payload: &d[6+n+c..] })
}
pub fn initialize() -> io::Result<Vec<u8>> {
    let event = receive()?;
    if event.op != b'I' { return Err(invalid("expected initialization")); }
    send(b'R', &[])?;
    Ok(event.data)
}
fn invalid(text: &str) -> io::Error { io::Error::new(io::ErrorKind::InvalidData, text) }

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn truncated_inputs_are_rejected() {
        for n in 0..6 { assert!(input(&Event {op:b'D',data:vec![0;n]}).is_err()); }
        assert!(input(&Event {op:b'D',data:vec![255;6]}).is_err());
    }
}
