//! Stable C ABI. All non-null pointers must designate live objects of the advertised size.
//! Handles belong to this library and must not be shared concurrently or used after close.

use crate::{
    Error, Filter, Record, RecordingFormat, RecordingReader, RecordingWriter, Result,
    MAX_PAYLOAD_SIZE, MAX_TEXT_SIZE,
};
use std::{
    cell::RefCell,
    collections::BTreeMap,
    ffi::{c_char, c_int, c_void, CString},
    panic::{catch_unwind, AssertUnwindSafe},
    ptr,
};

thread_local! { static ERROR: RefCell<CString> = RefCell::new(CString::default()); }
fn set_error(message: &str) {
    ERROR.with(|error| *error.borrow_mut() = CString::new(message.replace('\0', "\\0")).unwrap());
}
fn protect<T>(fallback: T, function: impl FnOnce() -> Result<T>) -> T {
    set_error("");
    match catch_unwind(AssertUnwindSafe(function)) {
        Ok(Ok(value)) => {
            set_error("");
            value
        }
        Ok(Err(error)) => {
            set_error(&error.to_string());
            fallback
        }
        Err(_) => {
            set_error("native recording panic intercepted");
            fallback
        }
    }
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct Bytes {
    pub data: *const u8,
    pub size: usize,
}
impl Bytes {
    fn of(value: &[u8]) -> Self {
        Self {
            data: value.as_ptr(),
            size: value.len(),
        }
    }
    unsafe fn slice<'a>(self, max: usize) -> Result<&'a [u8]> {
        if self.size > max {
            return Err(Error("input size limit exceeded".into()));
        }
        if self.size == 0 {
            return Ok(&[]);
        }
        if self.data.is_null() {
            return Err(Error("null input pointer".into()));
        }
        Ok(std::slice::from_raw_parts(self.data, self.size))
    }
    unsafe fn text<'a>(self) -> Result<&'a str> {
        std::str::from_utf8(self.slice(MAX_TEXT_SIZE)?)
            .map_err(|_| Error("text must be UTF-8".into()))
    }
}
#[repr(C)]
pub struct Metadata {
    pub key: Bytes,
    pub value: Bytes,
}
unsafe fn metadata(pointer: *const Metadata, count: usize) -> Result<BTreeMap<String, String>> {
    if count > 65535 || pointer.is_null() && count != 0 {
        return Err(Error("invalid metadata pointer/count".into()));
    }
    let entries = if count == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(pointer, count)
    };
    let mut result = BTreeMap::new();
    for entry in entries {
        if result
            .insert(entry.key.text()?.to_owned(), entry.value.text()?.to_owned())
            .is_some()
        {
            return Err(Error("duplicate metadata key".into()));
        }
    }
    Ok(result)
}
fn boolean(value: u8) -> Result<bool> {
    match value {
        0 => Ok(false),
        1 => Ok(true),
        _ => Err(Error("flag must be 0 or 1".into())),
    }
}

pub struct CWriter {
    writer: RecordingWriter,
}
pub struct CReader {
    reader: Option<RecordingReader>,
    current: Option<OwnedView>,
}
#[repr(C)]
pub struct View {
    pub channel_id: u16,
    pub schema_id: u16,
    pub topic: Bytes,
    pub root_type: Bytes,
    pub catalogue: Bytes,
    pub payload: Bytes,
    pub metadata: *const Metadata,
    pub metadata_count: usize,
    pub log_time_ns: u64,
    pub publish_time_ns: u64,
    pub sequence: u32,
}
fn metadata_view(record: &Record) -> Vec<Metadata> {
    record
        .channel
        .metadata
        .iter()
        .map(|(key, value)| Metadata {
            key: Bytes::of(key.as_bytes()),
            value: Bytes::of(value.as_bytes()),
        })
        .collect()
}
fn view(record: &Record, metadata: &[Metadata]) -> View {
    View {
        channel_id: record.channel.id,
        schema_id: record.channel.schema_id,
        topic: Bytes::of(record.channel.topic.as_bytes()),
        root_type: Bytes::of(record.channel.root_type.as_bytes()),
        catalogue: Bytes::of(record.channel.catalogue.as_bytes()),
        payload: Bytes::of(&record.payload),
        metadata: metadata.as_ptr(),
        metadata_count: metadata.len(),
        log_time_ns: record.log_time_ns,
        publish_time_ns: record.publish_time_ns,
        sequence: record.sequence,
    }
}
struct OwnedView {
    _record: Record,
    _metadata: Vec<Metadata>,
    view: View,
}
impl OwnedView {
    fn new(record: Record) -> Self {
        let metadata = metadata_view(&record);
        let view = view(&record, &metadata);
        Self {
            _record: record,
            _metadata: metadata,
            view,
        }
    }
}
unsafe fn writer<'a>(pointer: *mut CWriter) -> Result<&'a mut RecordingWriter> {
    pointer
        .as_mut()
        .map(|handle| &mut handle.writer)
        .ok_or_else(|| Error("null writer handle".into()))
}
unsafe fn reader<'a>(pointer: *mut CReader) -> Result<&'a mut CReader> {
    let handle = pointer
        .as_mut()
        .ok_or_else(|| Error("null reader handle".into()))?;
    if handle.reader.is_none() {
        return Err(Error("reader is busy replaying".into()));
    }
    Ok(handle)
}

#[no_mangle]
pub extern "C" fn sdl_recording_abi_version() -> u32 {
    1
}
#[no_mangle]
pub extern "C" fn sdl_recording_error() -> *const c_char {
    ERROR.with(|error| error.borrow().as_ptr())
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_now(output: *mut u64) -> c_int {
    protect(-1, || {
        let output = output
            .as_mut()
            .ok_or_else(|| Error("null time output".into()))?;
        *output = crate::now_ns()?;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_open(
    path: Bytes,
    overwrite: u8,
    chunk_size: u64,
) -> *mut CWriter {
    protect(ptr::null_mut(), || {
        Ok(Box::into_raw(Box::new(CWriter {
            writer: RecordingWriter::create(path.text()?, boolean(overwrite)?, chunk_size)?,
        })))
    })
}
/// Same lifetime rules as writer_open; format=0 MCAP, format=1 PCAPNG.
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_open_format(
    path: Bytes,
    overwrite: u8,
    chunk_size: u64,
    format: u8,
) -> *mut CWriter {
    protect(ptr::null_mut(), || {
        Ok(Box::into_raw(Box::new(CWriter {
            writer: RecordingWriter::create_format(
                path.text()?,
                boolean(overwrite)?,
                chunk_size,
                RecordingFormat::try_from(format)?,
            )?,
        })))
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_channel(
    pointer: *mut CWriter,
    topic: Bytes,
    catalogue: Bytes,
    root_type: Bytes,
    meta: *const Metadata,
    count: usize,
    output: *mut u16,
) -> c_int {
    protect(-1, || {
        let output = output
            .as_mut()
            .ok_or_else(|| Error("null channel output".into()))?;
        *output = writer(pointer)?.register_channel(
            topic.text()?,
            catalogue.text()?,
            root_type.text()?,
            metadata(meta, count)?,
        )?;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_metadata(
    pointer: *mut CWriter,
    name: Bytes,
    meta: *const Metadata,
    count: usize,
) -> c_int {
    protect(-1, || {
        writer(pointer)?.write_metadata(name.text()?, metadata(meta, count)?)?;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_validate_payload(
    pointer: *mut CWriter,
    flag: u8,
) -> c_int {
    protect(-1, || {
        writer(pointer)?.validate_payload = boolean(flag)?;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_write(
    pointer: *mut CWriter,
    channel_id: u16,
    data: Bytes,
    log_time_ns: u64,
    publish_time_ns: u64,
    sequence: u32,
) -> c_int {
    protect(-1, || {
        writer(pointer)?.write_encoded(
            channel_id,
            data.slice(MAX_PAYLOAD_SIZE)?,
            log_time_ns,
            publish_time_ns,
            sequence,
        )?;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_flush(pointer: *mut CWriter, sync: u8) -> c_int {
    protect(-1, || {
        writer(pointer)?.flush(boolean(sync)?)?;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_writer_close(pointer: *mut CWriter) -> c_int {
    protect(-1, || {
        if pointer.is_null() {
            return Err(Error("null writer handle".into()));
        }
        let mut handle = Box::from_raw(pointer);
        handle.writer.close()?;
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_reader_open(path: Bytes) -> *mut CReader {
    protect(ptr::null_mut(), || {
        Ok(Box::into_raw(Box::new(CReader {
            reader: Some(RecordingReader::open(path.text()?)?),
            current: None,
        })))
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_reader_filter(
    pointer: *mut CReader,
    topic: Bytes,
    flags: u8,
    start_time_ns: u64,
    end_time_ns: u64,
) -> c_int {
    protect(-1, || {
        if flags & !3 != 0 {
            return Err(Error("invalid filter flags".into()));
        }
        if flags == 3 && end_time_ns < start_time_ns {
            return Err(Error("filter end precedes start".into()));
        }
        let topic = topic.text()?;
        let handle = reader(pointer)?;
        handle.current = None;
        handle.reader.as_mut().unwrap().filter = Filter {
            topic: if topic.is_empty() {
                None
            } else {
                Some(topic.into())
            },
            start_time_ns: if flags & 1 != 0 {
                Some(start_time_ns)
            } else {
                None
            },
            end_time_ns: if flags & 2 != 0 {
                Some(end_time_ns)
            } else {
                None
            },
        };
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_reader_next(
    pointer: *mut CReader,
    output: *mut *const View,
) -> c_int {
    protect(-1, || {
        let output = output
            .as_mut()
            .ok_or_else(|| Error("null record output".into()))?;
        *output = ptr::null();
        let handle = reader(pointer)?;
        handle.current = None;
        if let Some(record) = handle.reader.as_mut().unwrap().next_record()? {
            handle.current = Some(OwnedView::new(record));
            *output = &handle.current.as_ref().unwrap().view;
            Ok(1)
        } else {
            Ok(0)
        }
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_reader_close(pointer: *mut CReader) -> c_int {
    protect(-1, || {
        reader(pointer)?;
        drop(Box::from_raw(pointer));
        Ok(0)
    })
}
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_validate(path: Bytes, output: *mut u64) -> c_int {
    protect(-1, || {
        let output = output
            .as_mut()
            .ok_or_else(|| Error("null count output".into()))?;
        *output = crate::validate(path.text()?)?;
        Ok(0)
    })
}
pub type Consumer = unsafe extern "C" fn(*const View, *mut c_void) -> c_int;
#[no_mangle]
pub unsafe extern "C" fn sdl_recording_replay(
    pointer: *mut CReader,
    speed: f64,
    consumer: Option<Consumer>,
    userdata: *mut c_void,
    output: *mut u64,
) -> c_int {
    protect(-1, || {
        let consumer = consumer.ok_or_else(|| Error("null replay consumer".into()))?;
        let output = output
            .as_mut()
            .ok_or_else(|| Error("null count output".into()))?;
        let handle = reader(pointer)?;
        handle.current = None;
        let mut engine = handle.reader.take().unwrap();
        // No borrow of the C handle survives into application callbacks.
        // Reentrant next/filter/close see the busy state and return an error.
        let result = catch_unwind(AssertUnwindSafe(|| {
            crate::replay(&mut engine, speed, |record| {
                let metadata = metadata_view(record);
                let view = view(record, &metadata);
                if consumer(&view, userdata) != 0 {
                    return Err(Error("replay consumer failed".into()));
                }
                Ok(())
            })
        }));
        (*pointer).reader = Some(engine);
        *output = match result {
            Ok(result) => result?,
            Err(panic) => std::panic::resume_unwind(panic),
        };
        Ok(0)
    })
}
