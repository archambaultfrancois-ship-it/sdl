//! Binary stdio component SDK. No transport or codec dependencies.
use std::io::{self, Read, Write};

pub const MAX_SIZE: usize = 1024 * 1024;
pub const TICK_CONTEXT: &[u8] = &[1];
#[derive(Default, Clone, Copy, Debug, PartialEq, Eq)]
pub struct TickContext { pub time_ns: u64, pub tick_index: u64 }
pub fn tick(event: &Event, previous_index: u64) -> io::Result<TickContext> {
    if event.op != b'T' { return Err(invalid("expected tick")); }
    if event.data.is_empty() {
        return Ok(TickContext { time_ns: 0, tick_index: previous_index + 1 });
    }
    if event.data.len() != 16 { return Err(invalid("invalid tick context")); }
    let time_ns = u64::from_be_bytes(event.data[..8].try_into().unwrap());
    let tick_index = u64::from_be_bytes(event.data[8..].try_into().unwrap());
    if tick_index == 0 { return Err(invalid("tick index must be positive")); }
    Ok(TickContext { time_ns, tick_index })
}
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
    fn timestamped_ticks_and_legacy_frames() {
        assert_eq!(tick(&Event { op: b'T', data: vec![] }, 2).unwrap(), TickContext { time_ns: 0, tick_index: 3 });
        let data = [123456789_u64.to_be_bytes(), 4_u64.to_be_bytes()].concat();
        assert_eq!(tick(&Event { op: b'T', data }, 0).unwrap(), TickContext { time_ns: 123456789, tick_index: 4 });
        for size in [1, 15, 17] {
            assert!(tick(&Event { op: b'T', data: vec![0; size] }, 0).is_err());
        }
        assert!(tick(&Event { op: b'T', data: vec![0; 16] }, 0).is_err());
    }
    #[test]
    fn truncated_inputs_are_rejected() {
        for n in 0..6 { assert!(input(&Event {op:b'D',data:vec![0;n]}).is_err()); }
        assert!(input(&Event {op:b'D',data:vec![255;6]}).is_err());
    }
}
