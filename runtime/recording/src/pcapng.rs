//! Single-section SDL recording profile over PCAPNG LINKTYPE_USER0.
//! Application packet bodies reuse the Schema/Channel/Message/Metadata layouts
//! from MCAP, avoiding an additional encoding of SDL catalogues and payloads.
use crate::{fail, Error, Result, MAX_PAYLOAD_SIZE, MAX_RECORD_SIZE, MAX_TEXT_SIZE};
use crc32fast::Hasher;
use mcap::records::{self, Record};
use std::{
    collections::BTreeMap,
    fs::File,
    io::{BufWriter, Read, Write},
};

pub const PROFILE: &str = "sdl2-recording-pcapng-v1";
pub const MAGIC: &[u8; 4] = b"\x0a\x0d\x0d\x0a";
const SHB: u32 = 0x0a0d0d0a;
const IDB: u32 = 1;
const EPB: u32 = 6;

fn text(out: &mut Vec<u8>, value: &str) {
    out.extend_from_slice(&(value.len() as u32).to_le_bytes());
    out.extend_from_slice(value.as_bytes());
}
fn map(out: &mut Vec<u8>, values: &BTreeMap<String, String>) {
    let size = values
        .iter()
        .map(|(k, v)| k.len() + v.len() + 8)
        .sum::<usize>();
    out.extend_from_slice(&(size as u32).to_le_bytes());
    for (key, value) in values {
        text(out, key);
        text(out, value);
    }
}
fn block(kind: u32, body: &[u8]) -> Vec<u8> {
    let size = (body.len() + 12) as u32;
    let mut out = Vec::with_capacity(size as usize);
    out.extend_from_slice(&kind.to_le_bytes());
    out.extend_from_slice(&size.to_le_bytes());
    out.extend_from_slice(body);
    out.extend_from_slice(&size.to_le_bytes());
    out
}

pub struct Writer {
    stream: BufWriter<File>,
    rolling: Hasher,
    packets: u64,
    messages: u64,
    failed: bool,
}
impl Writer {
    pub fn new(stream: BufWriter<File>) -> Result<Self> {
        let mut writer = Self {
            stream,
            rolling: Hasher::new(),
            packets: 0,
            messages: 0,
            failed: false,
        };
        writer.stream.write_all(&block(
            SHB,
            &[
                0x4d, 0x3c, 0x2b, 0x1a, 1, 0, 0, 0, 255, 255, 255, 255, 255, 255, 255, 255,
            ],
        ))?;
        // USER0=147, reserved=0, snaplen=0; if_tsresol=9; end-of-options.
        writer.stream.write_all(&block(
            IDB,
            &[147, 0, 0, 0, 0, 0, 0, 0, 9, 0, 1, 0, 9, 0, 0, 0, 0, 0, 0, 0],
        ))?;
        let mut body = Vec::new();
        text(&mut body, PROFILE);
        text(&mut body, "sdl-recording/rust-v1");
        writer.packet(1, &body, 0)?;
        Ok(writer)
    }
    fn packet(&mut self, kind: u8, body: &[u8], time: u64) -> Result<()> {
        if self.failed {
            return fail("PCAPNG writer has failed");
        }
        let mut packet = Vec::with_capacity(body.len() + 16);
        packet.extend_from_slice(b"S2RC");
        packet.extend_from_slice(&[1, kind, 0, 0]);
        packet.extend_from_slice(&(body.len() as u32).to_le_bytes());
        let mut crc = Hasher::new();
        crc.update(&packet);
        crc.update(body);
        packet.extend_from_slice(&crc.finalize().to_le_bytes());
        packet.extend_from_slice(body);
        let mut epb = Vec::with_capacity(packet.len() + 24);
        epb.extend_from_slice(&0u32.to_le_bytes());
        epb.extend_from_slice(&((time >> 32) as u32).to_le_bytes());
        epb.extend_from_slice(&(time as u32).to_le_bytes());
        epb.extend_from_slice(&(packet.len() as u32).to_le_bytes());
        epb.extend_from_slice(&(packet.len() as u32).to_le_bytes());
        epb.extend_from_slice(&packet);
        epb.resize((epb.len() + 3) & !3, 0);
        if let Err(error) = self.stream.write_all(&block(EPB, &epb)) {
            self.failed = true;
            return Err(error.into());
        }
        if kind != 15 {
            self.rolling.update(&packet);
            self.packets += 1;
        }
        if kind == 5 {
            self.messages += 1;
        }
        Ok(())
    }
    pub fn schema(&mut self, id: u16, name: &str, encoding: &str, data: &[u8]) -> Result<()> {
        let mut body = id.to_le_bytes().to_vec();
        text(&mut body, name);
        text(&mut body, encoding);
        body.extend_from_slice(&(data.len() as u32).to_le_bytes());
        body.extend_from_slice(data);
        self.packet(3, &body, 0)
    }
    pub fn channel(
        &mut self,
        id: u16,
        schema: u16,
        topic: &str,
        encoding: &str,
        metadata: &BTreeMap<String, String>,
    ) -> Result<()> {
        let mut body = id.to_le_bytes().to_vec();
        body.extend_from_slice(&schema.to_le_bytes());
        text(&mut body, topic);
        text(&mut body, encoding);
        map(&mut body, metadata);
        self.packet(4, &body, 0)
    }
    pub fn message(&mut self, header: &records::MessageHeader, data: &[u8]) -> Result<()> {
        let mut body = header.channel_id.to_le_bytes().to_vec();
        body.extend_from_slice(&header.sequence.to_le_bytes());
        body.extend_from_slice(&header.log_time.to_le_bytes());
        body.extend_from_slice(&header.publish_time.to_le_bytes());
        body.extend_from_slice(data);
        self.packet(5, &body, header.log_time)
    }
    pub fn metadata(&mut self, metadata: &records::Metadata) -> Result<()> {
        let mut body = Vec::new();
        text(&mut body, &metadata.name);
        map(&mut body, &metadata.metadata);
        self.packet(12, &body, 0)
    }
    pub fn flush(&mut self) -> Result<()> {
        if self.failed {
            return fail("PCAPNG writer has failed");
        }
        if let Err(error) = self.stream.flush() {
            self.failed = true;
            return Err(error.into());
        }
        Ok(())
    }
    pub fn finish(&mut self) -> Result<()> {
        let mut body = self.packets.to_le_bytes().to_vec();
        body.extend_from_slice(&self.messages.to_le_bytes());
        body.extend_from_slice(&self.rolling.clone().finalize().to_le_bytes());
        self.packet(15, &body, 0)?;
        self.flush()
    }
    pub fn into_inner(self) -> BufWriter<File> {
        self.stream
    }
}

pub struct Reader {
    section: bool,
    interface: bool,
    header: bool,
    end: bool,
    footer: bool,
    rolling: Hasher,
    packets: u64,
    messages: u64,
}
impl Reader {
    pub fn new() -> Self {
        Self {
            section: false,
            interface: false,
            header: false,
            end: false,
            footer: false,
            rolling: Hasher::new(),
            packets: 0,
            messages: 0,
        }
    }
    pub fn next(&mut self, file: &mut impl Read) -> Result<Option<Record<'static>>> {
        if self.end && !self.footer {
            self.footer = true;
            return Ok(Some(Record::Footer(records::Footer {
                summary_start: 0,
                summary_offset_start: 0,
                summary_crc: 0,
            })));
        }
        loop {
            let mut prefix = [0u8; 8];
            let count = file.read(&mut prefix[..1])?;
            if count == 0 {
                if !self.end {
                    return fail("incomplete PCAPNG recording: missing END");
                }
                return Ok(None);
            }
            file.read_exact(&mut prefix[1..])?;
            if self.end {
                return fail("data after PCAPNG END");
            }
            let kind = u32::from_le_bytes(prefix[..4].try_into().unwrap());
            let size = u32::from_le_bytes(prefix[4..].try_into().unwrap()) as usize;
            if !(12..=MAX_RECORD_SIZE).contains(&size) || size % 4 != 0 {
                return fail("invalid PCAPNG block size");
            }
            let mut data = vec![0u8; size - 8];
            file.read_exact(&mut data)?;
            if data[data.len() - 4..] != prefix[4..] {
                return fail("PCAPNG block length mismatch");
            }
            let body = &data[..data.len() - 4];
            if !self.section && kind != SHB {
                return fail("PCAPNG section must come first");
            }
            match kind {
                SHB => {
                    if self.section
                        || body
                            != [
                                0x4d, 0x3c, 0x2b, 0x1a, 1, 0, 0, 0, 255, 255, 255, 255, 255, 255,
                                255, 255,
                            ]
                    {
                        return fail("unsupported PCAPNG section (expected single little-endian v1.0 section)");
                    }
                    self.section = true;
                }
                IDB => {
                    if self.interface
                        || body != [147, 0, 0, 0, 0, 0, 0, 0, 9, 0, 1, 0, 9, 0, 0, 0, 0, 0, 0, 0]
                    {
                        return fail("unsupported PCAPNG interface (expected USER0, nanoseconds, no snaplen)");
                    }
                    self.interface = true;
                }
                EPB => {
                    if !self.interface || body.len() < 36 {
                        return fail("invalid PCAPNG packet block");
                    }
                    let field =
                        |offset| u32::from_le_bytes(body[offset..offset + 4].try_into().unwrap());
                    let length = field(12) as usize;
                    if field(0) != 0
                        || length != field(16) as usize
                        || length < 16
                        || length > body.len() - 20
                    {
                        return fail("invalid PCAPNG interface or captured packet length");
                    }
                    let padded = (length + 3) & !3;
                    if body.len() != 20 + padded || body[20 + length..].iter().any(|&b| b != 0) {
                        return fail("invalid PCAPNG packet padding or options");
                    }
                    let packet = &body[20..20 + length];
                    if &packet[..4] != b"S2RC" || packet[4] != 1 || packet[6..8] != [0, 0] {
                        return fail("unsupported SDL recording packet");
                    }
                    let opcode = packet[5];
                    if u32::from_le_bytes(packet[8..12].try_into().unwrap()) as usize != length - 16
                    {
                        return fail("SDL recording packet length mismatch");
                    }
                    let payload = &packet[16..];
                    let mut crc = Hasher::new();
                    crc.update(&packet[..12]);
                    crc.update(payload);
                    if crc.finalize() != u32::from_le_bytes(packet[12..16].try_into().unwrap()) {
                        return fail("SDL recording packet CRC mismatch");
                    }
                    let timestamp = ((field(4) as u64) << 32) | field(8) as u64;
                    if opcode != 5 && timestamp != 0 {
                        return fail("control packet timestamp must be zero");
                    }
                    if !self.header && opcode != 1 {
                        return fail("SDL recording HEADER must come first");
                    }
                    if opcode == 15 {
                        if payload.len() != 20 {
                            return fail("invalid SDL recording END");
                        }
                        let packets = u64::from_le_bytes(payload[..8].try_into().unwrap());
                        let messages = u64::from_le_bytes(payload[8..16].try_into().unwrap());
                        let crc = u32::from_le_bytes(payload[16..20].try_into().unwrap());
                        if packets != self.packets
                            || messages != self.messages
                            || crc != self.rolling.clone().finalize()
                        {
                            return fail("SDL recording END count or stream CRC mismatch");
                        }
                        self.end = true;
                        return Ok(Some(Record::DataEnd(records::DataEnd {
                            data_section_crc: 0,
                        })));
                    }
                    if !matches!(opcode, 1 | 3 | 4 | 5 | 12) {
                        return fail("unknown SDL recording packet kind");
                    }
                    // Validate all inner lengths and reject trailing bytes before SDK parsing.
                    validate_body(opcode, payload)?;
                    let mut record = mcap::parse_record(opcode, payload)?.into_owned();
                    if let Record::Channel(channel) = &record {
                        if channel.id == 0 {
                            return fail("PCAPNG channel ID must be nonzero");
                        }
                    }
                    if let Record::Header(ref mut header) = record {
                        if self.header || header.profile != PROFILE {
                            return fail("unsupported or duplicate PCAPNG profile");
                        }
                        self.header = true;
                        header.profile = crate::PROFILE.into();
                    }
                    if let Record::Message { header, .. } = &record {
                        if header.log_time != timestamp {
                            return fail("PCAPNG timestamp differs from SDL log time");
                        }
                        self.messages += 1;
                    }
                    self.rolling.update(packet);
                    self.packets += 1;
                    return Ok(Some(record));
                }
                _ => return fail("unsupported block in SDL PCAPNG profile"),
            }
        }
    }
}

// Bound every variable-length field to its containing packet. The profiles use
// the MCAP body layout, but impose strict consumption and unique metadata keys.
fn validate_body(op: u8, data: &[u8]) -> Result<()> {
    struct Cursor<'a> {
        data: &'a [u8],
        pos: usize,
    }
    impl Cursor<'_> {
        fn take(&mut self, n: usize) -> Result<&[u8]> {
            let end = self
                .pos
                .checked_add(n)
                .filter(|&n| n <= self.data.len())
                .ok_or_else(|| Error("PCAPNG field exceeds packet".into()))?;
            let value = &self.data[self.pos..end];
            self.pos = end;
            Ok(value)
        }
        fn string(&mut self) -> Result<()> {
            let n = u32::from_le_bytes(self.take(4)?.try_into().unwrap()) as usize;
            if n > MAX_TEXT_SIZE {
                return fail("PCAPNG text exceeds 1 MiB");
            }
            let value = self.take(n)?;
            std::str::from_utf8(value).map_err(|_| Error("invalid packet UTF-8".into()))?;
            Ok(())
        }
        fn map(&mut self) -> Result<()> {
            let n = u32::from_le_bytes(self.take(4)?.try_into().unwrap()) as usize;
            if n > MAX_TEXT_SIZE {
                return fail("PCAPNG metadata exceeds 1 MiB");
            }
            let raw = self.take(n)?;
            let mut map = Cursor { data: raw, pos: 0 };
            let mut keys = std::collections::BTreeSet::new();
            while map.pos < raw.len() {
                let start = map.pos;
                map.string()?;
                if !keys.insert(raw[start..map.pos].to_vec()) {
                    return fail("duplicate metadata key");
                }
                if keys.len() > 65535 {
                    return fail("too many metadata keys");
                }
                map.string()?;
            }
            Ok(())
        }
    }
    let mut c = Cursor { data, pos: 0 };
    match op {
        1 => {
            c.string()?;
            c.string()?;
        }
        3 => {
            c.take(2)?;
            c.string()?;
            c.string()?;
            let n = u32::from_le_bytes(c.take(4)?.try_into().unwrap()) as usize;
            if n > MAX_TEXT_SIZE {
                return fail("PCAPNG catalogue exceeds 1 MiB");
            }
            c.take(n)?;
        }
        4 => {
            c.take(4)?;
            c.string()?;
            c.string()?;
            c.map()?;
        }
        5 => {
            c.take(22)?;
            if data.len() - 22 > MAX_PAYLOAD_SIZE {
                return fail("PCAPNG payload exceeds 32 MiB");
            }
            c.pos = data.len();
        }
        12 => {
            c.string()?;
            c.map()?;
        }
        _ => return fail("unknown packet kind"),
    }
    if c.pos != data.len() {
        return fail("trailing data in SDL recording packet");
    }
    Ok(())
}
