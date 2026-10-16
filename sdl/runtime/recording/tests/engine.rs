use sdl_recording::{replay, validate, Filter, RecordingFormat, RecordingReader, RecordingWriter};
use std::{
    collections::BTreeMap,
    fs,
    sync::atomic::{AtomicUsize, Ordering},
};
static SERIAL: AtomicUsize = AtomicUsize::new(0);
struct Temp(std::path::PathBuf);
impl Temp {
    fn new() -> Self {
        Self(std::env::temp_dir().join(format!(
            "sdl-recording-{}-{}.mcap",
            std::process::id(),
            SERIAL.fetch_add(1, Ordering::Relaxed)
        )))
    }
}
impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
#[test]
fn default_writer_uses_pcapng_regardless_of_extension() {
    let path = Temp::new();
    let mut writer = RecordingWriter::create(&path.0, false, 128).unwrap();
    let channel = writer
        .register_channel("radio", "SDL2|Packet{}", "Packet", BTreeMap::new())
        .unwrap();
    writer.write_encoded(channel, &[1], 10, 9, 3).unwrap();
    writer.close().unwrap();
    assert_eq!(&fs::read(&path.0).unwrap()[..4], &[0x0a, 0x0d, 0x0d, 0x0a]);
    assert_eq!(validate(&path.0).unwrap(), 1);
    let record = RecordingReader::open(&path.0)
        .unwrap()
        .next_record()
        .unwrap()
        .unwrap();
    assert_eq!(record.payload, [1]);
    assert_eq!(
        (record.log_time_ns, record.publish_time_ns, record.sequence),
        (10, 9, 3)
    );
}
#[test]
fn native_api_roundtrip_filter_and_replay() {
    for format in [RecordingFormat::Mcap, RecordingFormat::Pcapng] {
        let path = Temp::new();
        let mut writer = RecordingWriter::create_format(&path.0, false, 128, format).unwrap();
        let a = writer
            .register_channel("radio", "SDL2|Packet{}", "Packet", BTreeMap::new())
            .unwrap();
        let b = writer
            .register_channel("health", "SDL2|Packet{}", "Packet", BTreeMap::new())
            .unwrap();
        assert!(writer.write_encoded(a, &[2], 1, 1, 0).is_err());
        writer
            .write_encoded(a, &[1], u64::MAX - 1, u64::MAX - 2, u32::MAX)
            .unwrap();
        writer
            .write_encoded(b, &[1], u64::MAX, u64::MAX, 0)
            .unwrap();
        writer.flush(true).unwrap();
        writer.close().unwrap();
        assert_eq!(validate(&path.0).unwrap(), 2);
        let mut reader = RecordingReader::open(&path.0).unwrap();
        let first = reader.next_record().unwrap().unwrap();
        assert_eq!(first.payload, [1]);
        assert_eq!(first.sequence, u32::MAX);
        first.decode().unwrap();
        let second = reader.next_record().unwrap().unwrap();
        assert_eq!(first.channel.schema_id, second.channel.schema_id);
        assert!(reader.next_record().unwrap().is_none());
        let mut reader = RecordingReader::open(&path.0).unwrap();
        reader.filter = Filter {
            topic: Some("radio".into()),
            ..Default::default()
        };
        let mut seen = Vec::new();
        assert_eq!(
            replay(&mut reader, 0.0, |r| {
                seen.push(r.payload.clone());
                Ok(())
            })
            .unwrap(),
            1
        );
        assert_eq!(seen, [vec![1]]);
    }
}
#[test]
fn unclosed_writer_is_not_a_complete_recording() {
    for format in [RecordingFormat::Mcap, RecordingFormat::Pcapng] {
        let path = Temp::new();
        {
            let mut writer = RecordingWriter::create_format(&path.0, false, 128, format).unwrap();
            let c = writer
                .register_channel("t", "SDL2|Packet{}", "Packet", BTreeMap::new())
                .unwrap();
            writer.write_encoded(c, &[1], 0, 0, 0).unwrap();
            writer.flush(false).unwrap();
        }
        assert!(validate(&path.0).is_err());
        assert!(RecordingWriter::create_format(&path.0, false, 128, format).is_err());
    }
}
