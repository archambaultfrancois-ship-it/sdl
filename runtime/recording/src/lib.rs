//! Optional SDL2 MCAP recording engine. Generated bindings remain independent.

use mcap::{
    records::{self, MessageHeader, Record as McapRecord},
    sans_io::{LinearReadEvent, LinearReader, LinearReaderOptions},
    WriteOptions,
};
use sdl_runtime::{decode_dynamic, prepare, Context, DynamicMessage};
use std::{
    collections::BTreeMap,
    fs::{File, OpenOptions},
    io::{BufReader, BufWriter, Read, Write},
    path::Path,
    sync::Arc,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub mod ffi;

pub const PROFILE: &str = "sdl2-recording-v1";
pub const ENCODING: &str = "sdl2";
pub const MAX_RECORD_SIZE: usize = 64 * 1024 * 1024;
pub const MAX_PAYLOAD_SIZE: usize = 32 * 1024 * 1024;
pub const MAX_TEXT_SIZE: usize = 1024 * 1024;

#[derive(Debug)]
pub struct Error(pub String);
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self(e.to_string())
    }
}
impl From<mcap::McapError> for Error {
    fn from(e: mcap::McapError) -> Self {
        Self(e.to_string())
    }
}
pub type Result<T> = std::result::Result<T, Error>;
fn fail<T>(message: &str) -> Result<T> {
    Err(Error(message.into()))
}
fn context(text: &str) -> Result<Arc<Context>> {
    if text.len() > MAX_TEXT_SIZE {
        return fail("catalogue exceeds 1 MiB");
    }
    prepare(text, &[])
        .map(Arc::new)
        .map_err(|e| Error(format!("invalid SDL2 catalogue: {e:?}")))
}
fn root<'a>(ctx: &'a Context, data: &[u8]) -> Result<&'a str> {
    let mut id = 0usize;
    for (i, &byte) in data.iter().take(5).enumerate() {
        if i == 4 && byte > 15 {
            break;
        }
        id |= ((byte & 127) as usize) << (7 * i);
        if byte < 128 {
            if i > 0 && byte == 0 {
                break;
            }
            if id > 0 {
                if let Some(name) = ctx.message_names().get(id - 1) {
                    return Ok(name);
                }
            }
            break;
        }
    }
    fail("invalid SDL2 root message ID")
}

pub struct Channel {
    pub id: u16,
    pub schema_id: u16,
    pub topic: String,
    pub root_type: String,
    pub catalogue: String,
    pub metadata: BTreeMap<String, String>,
    pub context: Arc<Context>,
}
#[derive(Clone)]
pub struct Record {
    pub channel: Arc<Channel>,
    pub payload: Vec<u8>,
    pub log_time_ns: u64,
    pub publish_time_ns: u64,
    pub sequence: u32,
}
impl Record {
    pub fn decode(&self) -> Result<DynamicMessage> {
        decode_dynamic(&self.channel.context, &self.payload)
            .map_err(|e| Error(format!("invalid SDL2 payload: {e:?}")))
    }
}

/// Exclusive file creation by default; applications must call close to report IO errors.
pub struct RecordingWriter {
    writer: Option<mcap::Writer<BufWriter<File>>>,
    sync_file: File,
    schemas: BTreeMap<String, (u16, Arc<Context>)>,
    channels: BTreeMap<u16, Arc<Channel>>,
    pub validate_payload: bool,
    chunk_size: u64,
    pending_bytes: u64,
}
impl RecordingWriter {
    pub fn create(path: impl AsRef<Path>, overwrite: bool, chunk_size: u64) -> Result<Self> {
        if chunk_size == 0 || chunk_size > (MAX_RECORD_SIZE - MAX_PAYLOAD_SIZE - 1024) as u64 {
            return fail("invalid chunk size (maximum 32 MiB minus 1024 bytes)");
        }
        let mut options = OpenOptions::new();
        options.write(true);
        if overwrite {
            options.create(true).truncate(true);
        } else {
            options.create_new(true);
        }
        let file = options.open(path)?;
        let sync_file = file.try_clone()?;
        let writer = WriteOptions::new()
            .profile(PROFILE)
            .library("sdl-recording/rust-v1")
            .compression(None)
            .chunk_size(None)
            .disable_seeking(true)
            .calculate_data_section_crc(true)
            .calculate_chunk_crcs(true)
            .calculate_summary_section_crc(true)
            .create(BufWriter::new(file))?;
        Ok(Self {
            writer: Some(writer),
            sync_file,
            schemas: BTreeMap::new(),
            channels: BTreeMap::new(),
            validate_payload: true,
            chunk_size,
            pending_bytes: 0,
        })
    }
    fn inner(&mut self) -> Result<&mut mcap::Writer<BufWriter<File>>> {
        self.writer
            .as_mut()
            .ok_or_else(|| Error("recording writer is closed".into()))
    }
    fn budget(&mut self, size: usize) -> Result<()> {
        // The SDK's target only checks before messages, not declarations.
        // Bound chunks even when many channels are registered before any message.
        if self.pending_bytes > 0 && self.pending_bytes + size as u64 > self.chunk_size {
            self.flush(false)?;
        }
        self.pending_bytes += size as u64;
        Ok(())
    }
    pub fn register_channel(
        &mut self,
        topic: &str,
        catalogue: &str,
        root_type: &str,
        mut metadata: BTreeMap<String, String>,
    ) -> Result<u16> {
        self.inner()?;
        if topic.is_empty() || topic.len() > MAX_TEXT_SIZE {
            return fail("invalid topic");
        }
        if metadata
            .get("root_type")
            .is_some_and(|value| value != root_type)
        {
            return fail("conflicting root_type metadata");
        }
        metadata.insert("root_type".into(), root_type.into());
        if metadata.len() > 65535
            || metadata
                .iter()
                .try_fold(0usize, |n, (k, v)| {
                    n.checked_add(k.len())
                        .and_then(|n| n.checked_add(v.len() + 8))
                })
                .filter(|&n| n <= MAX_TEXT_SIZE)
                .is_none()
        {
            return fail("metadata exceeds 1 MiB");
        }
        let existing = self.schemas.get(catalogue).cloned();
        let ctx = if let Some((_, ctx)) = &existing {
            Arc::clone(ctx)
        } else {
            context(catalogue)?
        };
        if !ctx.message_names().iter().any(|name| name == root_type) {
            return fail("root_type is not a message in the catalogue");
        }
        let schema_id = if let Some((id, _)) = existing {
            id
        } else {
            if self.schemas.len() >= 65535 {
                return fail("schema ID space exhausted");
            }
            self.budget(catalogue.len() + 64)?;
            let id = (self.schemas.len() + 1) as u16;
            self.inner()?.add_schema_with_id(
                id,
                "SDL2 catalogue",
                ENCODING,
                catalogue.as_bytes(),
            )?;
            self.schemas
                .insert(catalogue.to_owned(), (id, Arc::clone(&ctx)));
            id
        };
        if self.channels.len() >= 65535 {
            return fail("channel ID space exhausted");
        }
        let size = topic.len()
            + 64
            + metadata
                .iter()
                .map(|(k, v)| k.len() + v.len() + 8)
                .sum::<usize>();
        self.budget(size)?;
        let id = (self.channels.len() + 1) as u16;
        self.inner()?
            .add_channel_with_id(id, schema_id, topic, ENCODING, &metadata)?;
        self.channels.insert(
            id,
            Arc::new(Channel {
                id,
                schema_id,
                topic: topic.into(),
                root_type: root_type.into(),
                catalogue: catalogue.into(),
                metadata,
                context: ctx,
            }),
        );
        Ok(id)
    }
    pub fn write_metadata(&mut self, name: &str, metadata: BTreeMap<String, String>) -> Result<()> {
        if name.len() > MAX_TEXT_SIZE
            || metadata
                .iter()
                .try_fold(name.len(), |n, (k, v)| {
                    n.checked_add(k.len())
                        .and_then(|n| n.checked_add(v.len() + 8))
                })
                .filter(|&n| n <= MAX_TEXT_SIZE)
                .is_none()
        {
            return fail("metadata exceeds 1 MiB");
        }
        self.inner()?.write_metadata(&records::Metadata {
            name: name.into(),
            metadata,
        })?;
        self.pending_bytes = 0;
        Ok(())
    }
    pub fn write_encoded(
        &mut self,
        channel_id: u16,
        payload: &[u8],
        log_time_ns: u64,
        publish_time_ns: u64,
        sequence: u32,
    ) -> Result<()> {
        self.inner()?;
        if payload.len() > MAX_PAYLOAD_SIZE {
            return fail("payload exceeds 32 MiB");
        }
        let channel = self
            .channels
            .get(&channel_id)
            .ok_or_else(|| Error("unknown writer channel".into()))?;
        if root(&channel.context, payload)? != channel.root_type {
            return fail("payload root does not match channel");
        }
        if self.validate_payload {
            decode_dynamic(&channel.context, payload)
                .map_err(|e| Error(format!("invalid SDL2 payload: {e:?}")))?;
        }
        self.budget(payload.len() + 31)?;
        self.inner()?.write_to_known_channel(
            &MessageHeader {
                channel_id,
                log_time: log_time_ns,
                publish_time: publish_time_ns,
                sequence,
            },
            payload,
        )?;
        Ok(())
    }
    pub fn flush(&mut self, sync: bool) -> Result<()> {
        self.inner()?.flush()?;
        self.pending_bytes = 0;
        if sync {
            self.sync_file.sync_all()?;
        }
        Ok(())
    }
    pub fn close(&mut self) -> Result<()> {
        if let Some(mut writer) = self.writer.take() {
            let result = writer.finish().map(|_| ()).map_err(Error::from);
            let mut stream = writer.into_inner();
            result?;
            stream.flush()?;
        }
        Ok(())
    }
}
impl Drop for RecordingWriter {
    fn drop(&mut self) {
        // The SDK's implicit finish discards errors. Our public close reports them;
        // dropping an unclosed writer releases it without manufacturing a valid footer.
        if let Some(writer) = self.writer.take() {
            drop(writer.into_inner());
        }
    }
}

#[derive(Default, Clone)]
pub struct Filter {
    pub topic: Option<String>,
    pub start_time_ns: Option<u64>,
    pub end_time_ns: Option<u64>,
}
impl Filter {
    pub fn matches(&self, record: &Record) -> bool {
        self.topic
            .as_ref()
            .is_none_or(|topic| *topic == record.channel.topic)
            && self
                .start_time_ns
                .is_none_or(|start| record.log_time_ns >= start)
            && self.end_time_ns.is_none_or(|end| record.log_time_ns < end)
    }
}

pub struct RecordingReader {
    file: BufReader<File>,
    linear: LinearReader,
    schemas: BTreeMap<u16, (records::SchemaHeader, Vec<u8>, Arc<Context>)>,
    declarations: BTreeMap<u16, records::Channel>,
    channels: BTreeMap<u16, Arc<Channel>>,
    header: bool,
    data_end: bool,
    footer: bool,
    failed: bool,
    pub filter: Filter,
    pub validate_payload: bool,
}
impl RecordingReader {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let options = LinearReaderOptions::default()
            .with_record_length_limit(MAX_RECORD_SIZE)
            .with_prevalidate_chunk_crcs(true)
            .with_validate_data_section_crc(true)
            .with_validate_summary_section_crc(true)
            .with_check_finishes_after_end_magic(true);
        Ok(Self {
            file: BufReader::new(File::open(path)?),
            linear: LinearReader::new_with_options(options),
            schemas: BTreeMap::new(),
            declarations: BTreeMap::new(),
            channels: BTreeMap::new(),
            header: false,
            data_end: false,
            footer: false,
            failed: false,
            filter: Filter::default(),
            validate_payload: true,
        })
    }
    pub fn next_record(&mut self) -> Result<Option<Record>> {
        if self.failed {
            return fail("recording reader has failed");
        }
        let result = self.next_inner();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn next_inner(&mut self) -> Result<Option<Record>> {
        loop {
            let record = match self.linear.next_event() {
                Some(event) => match event? {
                    LinearReadEvent::ReadRequest(need) => {
                        let count = self.file.read(self.linear.insert(need.min(64 * 1024)))?;
                        self.linear.notify_read(count);
                        continue;
                    }
                    LinearReadEvent::Record { opcode, data } => {
                        if opcode == 0
                            || opcode == 2 && data.len() != 20
                            || opcode == 15 && data.len() != 4
                        {
                            return fail("invalid MCAP control record");
                        }
                        mcap::parse_record(opcode, data)?.into_owned()
                    }
                },
                None => {
                    if !self.header || !self.data_end || !self.footer {
                        return fail("incomplete MCAP recording");
                    }
                    return Ok(None);
                }
            };
            if !self.header && !matches!(&record, McapRecord::Header(_)) {
                return fail("MCAP header must come first");
            }
            match record {
                McapRecord::Header(header) => {
                    if self.header || header.profile != PROFILE {
                        return fail("unsupported or duplicate MCAP header");
                    }
                    self.header = true;
                }
                McapRecord::Schema { header, data } => {
                    if let Some((old_header, old_data, _)) = self.schemas.get(&header.id) {
                        if old_header != &header || old_data.as_slice() != data.as_ref() {
                            return fail("conflicting MCAP schema ID");
                        }
                    } else {
                        if header.id == 0 || header.encoding != ENCODING {
                            return fail("invalid SDL2 MCAP schema");
                        }
                        let text = std::str::from_utf8(&data)
                            .map_err(|_| Error("catalogue is not UTF-8".into()))?;
                        let ctx = context(text)?;
                        self.schemas
                            .insert(header.id, (header, data.into_owned(), ctx));
                    }
                }
                McapRecord::Channel(declaration) => {
                    if let Some(old) = self.declarations.get(&declaration.id) {
                        if old != &declaration {
                            return fail("conflicting MCAP channel ID");
                        }
                    } else {
                        if declaration.message_encoding != ENCODING || declaration.topic.is_empty()
                        {
                            return fail("invalid SDL2 MCAP channel");
                        }
                        let (_, data, ctx) = self
                            .schemas
                            .get(&declaration.schema_id)
                            .ok_or_else(|| Error("channel references an unknown schema".into()))?;
                        let root_type = declaration
                            .metadata
                            .get("root_type")
                            .ok_or_else(|| Error("channel has no root_type".into()))?;
                        if !ctx.message_names().iter().any(|name| name == root_type) {
                            return fail("invalid channel root_type");
                        }
                        self.channels.insert(
                            declaration.id,
                            Arc::new(Channel {
                                id: declaration.id,
                                schema_id: declaration.schema_id,
                                topic: declaration.topic.clone(),
                                root_type: root_type.clone(),
                                catalogue: String::from_utf8(data.clone()).unwrap(),
                                metadata: declaration.metadata.clone(),
                                context: Arc::clone(ctx),
                            }),
                        );
                        self.declarations.insert(declaration.id, declaration);
                    }
                }
                McapRecord::Message { header, data } => {
                    if self.data_end {
                        return fail("message after DataEnd");
                    }
                    let channel = self
                        .channels
                        .get(&header.channel_id)
                        .ok_or_else(|| Error("message references an unknown channel".into()))?;
                    if root(&channel.context, &data)? != channel.root_type {
                        return fail("payload root does not match channel");
                    }
                    if data.len() > MAX_PAYLOAD_SIZE {
                        return fail("payload exceeds 32 MiB");
                    }
                    let record = Record {
                        channel: Arc::clone(channel),
                        payload: data.into_owned(),
                        log_time_ns: header.log_time,
                        publish_time_ns: header.publish_time,
                        sequence: header.sequence,
                    };
                    if self.validate_payload {
                        record.decode()?;
                    }
                    if self.filter.matches(&record) {
                        return Ok(Some(record));
                    }
                }
                McapRecord::DataEnd(_) => {
                    if self.data_end {
                        return fail("duplicate DataEnd");
                    }
                    self.data_end = true;
                }
                McapRecord::Footer(_) => {
                    if !self.data_end || self.footer {
                        return fail("missing DataEnd or duplicate footer");
                    }
                    self.footer = true;
                }
                _ => (),
            }
        }
    }
}

pub fn validate(path: impl AsRef<Path>) -> Result<u64> {
    let mut reader = RecordingReader::open(path)?;
    let mut count = 0u64;
    while reader.next_record()?.is_some() {
        count += 1;
    }
    Ok(count)
}
pub fn now_ns() -> Result<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| Error(e.to_string()))?
        .as_nanos()
        .try_into()
        .map_err(|_| Error("wall clock exceeds uint64 nanoseconds".into()))
}

/// Streams records with absolute monotonic deadlines. Preflight is a separate validate call.
pub fn replay(
    reader: &mut RecordingReader,
    speed: f64,
    mut consumer: impl FnMut(&Record) -> Result<()>,
) -> Result<u64> {
    if !speed.is_finite() || speed < 0.0 {
        return fail("speed must be finite and nonnegative");
    }
    let mut origin = None;
    let mut previous = None;
    let mut count = 0;
    while let Some(record) = reader.next_record()? {
        if speed > 0.0 {
            if previous.is_some_and(|previous| record.log_time_ns < previous) {
                return fail("log time moved backwards during paced replay");
            }
            let first = *origin.get_or_insert_with(|| (record.log_time_ns, Instant::now()));
            let delay = (record.log_time_ns - first.0) as f64 / speed / 1e9;
            let duration = Duration::try_from_secs_f64(delay)
                .map_err(|_| Error("replay delay is too large".into()))?;
            let deadline = first
                .1
                .checked_add(duration)
                .ok_or_else(|| Error("replay deadline overflow".into()))?;
            while let Some(remaining) = deadline.checked_duration_since(Instant::now()) {
                std::thread::sleep(remaining.min(Duration::from_secs(1)));
            }
            previous = Some(record.log_time_ns);
        }
        consumer(&record)?;
        count += 1;
    }
    Ok(count)
}
