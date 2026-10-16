#[path = "../../build/generated/rust/radio_capture.rs"]
mod radio_capture;

use radio_capture::{CaptureMetadata, CaptureMode, RadioCapture};
use sdl_runtime::{
    decode, decode_dynamic, description, display, encode, prepare, CodecError, Complex32, Context,
    DynamicValue, SdlMessage,
};
use std::env;
use std::fs;

fn codec_io_error(error: CodecError) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, format!("{:?}", error))
}

fn example_capture() -> RadioCapture {
    RadioCapture {
        metadata: CaptureMetadata {
            receiver_id: "north-ridge-rx-02".to_owned(),
            sequence: 8472,
            captured_at_ns: 1_780_000_000_123_456_789,
            center_frequency_hz: 915_000_000.0,
            sample_rate_hz: 2_400_000.0,
            mode: CaptureMode::Live,
            dc_offset: [0.001, -0.002],
        },
        operator_note: Some("interference burst near channel edge".to_owned()),
        iq_samples: vec![
            Complex32 {
                real: 1.0,
                imag: 0.25,
            },
            Complex32 {
                real: -0.5,
                imag: 0.75,
            },
            Complex32 {
                real: 0.125,
                imag: -1.0,
            },
        ],
        trigger_offsets: vec![128, 4096, 12000],
    }
}

fn check_capture(
    ctx: &Context,
    wire: &[u8],
    indent_width: usize,
    show_display: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    let decoded: RadioCapture = decode(ctx, wire).map_err(codec_io_error)?;
    let generic = decode_dynamic(ctx, wire).map_err(codec_io_error)?;
    assert_eq!(decoded.metadata.receiver_id, "north-ridge-rx-02");
    assert_eq!(decoded.metadata.sequence, 8472);
    assert_eq!(decoded.metadata.mode, CaptureMode::Live);
    assert_eq!(decoded.metadata.dc_offset, [0.001, -0.002]);
    assert_eq!(
        decoded.operator_note.as_deref(),
        Some("interference burst near channel edge")
    );
    assert_eq!(decoded.iq_samples.len(), 3);
    assert_eq!(decoded.iq_samples[1].real, -0.5);
    assert_eq!(decoded.iq_samples[1].imag, 0.75);
    assert_eq!(decoded.trigger_offsets, [128, 4096, 12000]);
    assert_eq!(generic.type_name, "RadioCapture");
    match generic.fields.get("iq_samples") {
        Some(DynamicValue::Array(samples)) => assert_eq!(samples.len(), 3),
        other => panic!("unexpected I/Q sample field: {:?}", other),
    }
    println!(
        "Rust decoded {} I/Q samples and 3 trigger offsets ({} wire bytes)",
        decoded.iq_samples.len(),
        wire.len()
    );
    if show_display {
        print!("{}", display(&decoded, indent_width));
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    let mode = args.get(1).map(String::as_str).unwrap_or("roundtrip");
    let indent_width = if mode == "display" || mode == "roundtrip" {
        args.get(2)
            .map(|value| value.parse::<usize>())
            .transpose()?
            .unwrap_or(3)
    } else {
        3
    };
    if mode == "display" {
        print!("{}", display(&example_capture(), indent_width));
        return Ok(());
    }
    let text = if mode == "decode" {
        fs::read_to_string(format!(
            "{}.sdl2",
            args.get(2).ok_or("decode requires a wire path")?
        ))?
    } else {
        description(&[RadioCapture::type_info()]).map_err(codec_io_error)?
    };
    let ctx = prepare(&text, &[RadioCapture::type_info()]).map_err(codec_io_error)?;
    let wire = if mode == "decode" {
        fs::read(args.get(2).ok_or("decode requires a wire file path")?)?
    } else {
        let wire = encode(&ctx, &example_capture()).map_err(codec_io_error)?;
        if mode == "encode" {
            let path = args.get(2).ok_or("encode requires an output file path")?;
            fs::write(path, &wire)?;
            fs::write(format!("{}.sdl2", path), text.as_bytes())?;
            println!("Rust wrote {} wire bytes to {}", wire.len(), path);
            return Ok(());
        }
        wire
    };
    check_capture(&ctx, &wire, indent_width, mode != "decode")
}
