use sdl_runtime::{
    decode, decode_any, decode_dynamic, description, encode, prepare, Complex32, DynamicValue,
    SdlMessage,
};
use sdl_schema_tests::codec_cases::{CodecCases, EnumRecord, EnumRecordBatch, State};
use sdl_schema_tests::empty_message::EmptyMessage;
use sdl_schema_tests::schema::{FixedBoard, FixedItem, FixedRow, RootPayload, VarItem};
use sdl_schema_tests::wire_example::Packet;
const FIXTURE: &[u8] = include_bytes!("../../fixtures/packet.bin");

#[test]
fn fixture_and_dynamic() {
    let text = description(&[Packet::type_info()]).unwrap();
    assert_eq!(text, include_str!("../../fixtures/packet.sdl2"));
    let ctx = prepare(&text, &[Packet::type_info()]).unwrap();
    let message = Packet {
        active: true,
        code: Some(-2),
        label: "été".into(),
        samples: vec![300, -1],
    };
    assert_eq!(encode(&ctx, &message).unwrap(), FIXTURE);
    assert_eq!(decode::<Packet>(&ctx, FIXTURE).unwrap(), message);
    let d = decode_dynamic(&ctx, FIXTURE).unwrap();
    assert_eq!(
        d.fields["samples"],
        DynamicValue::Array(vec![DynamicValue::Integer(300), DynamicValue::Integer(-1)])
    );
}
#[test]
fn catalogues_and_empty_values() {
    let types = [Packet::type_info(), EmptyMessage::type_info()];
    let ctx = prepare(&description(&types).unwrap(), &types).unwrap();
    let packet = Packet {
        label: "a\0b\0😀".into(),
        ..Default::default()
    };
    let bytes = encode(&ctx, &packet).unwrap();
    assert_eq!(decode::<Packet>(&ctx, &bytes).unwrap(), packet);
    assert!(decode_any(&ctx, &bytes)
        .unwrap()
        .value
        .downcast::<Packet>()
        .is_ok());
    let bytes = encode(&ctx, &EmptyMessage::default()).unwrap();
    assert!(decode_any(&ctx, &bytes)
        .unwrap()
        .value
        .downcast::<EmptyMessage>()
        .is_ok());
    assert!(decode::<Packet>(&ctx, &bytes).is_err());
}
#[test]
fn scalars_and_enum_arrays() {
    let ctx = prepare(CodecCases::DESCRIPTOR, &[CodecCases::type_info()]).unwrap();
    let message = CodecCases {
        tiny: Some(-128),
        small: Some(32767),
        signed_value: Some(i32::MIN),
        wide: Some(i64::MIN),
        ratio: Some(f32::INFINITY),
        precise: Some(-0.0),
        point: Some(Complex32 {
            real: 1.,
            imag: -2.,
        }),
        state: Some(State::Minimum),
        labels: vec!["".into(), "a\0b".into()],
        bool_flags: vec![true, false],
        fixed_states: [State::Unknown, State::Ready, State::Negative],
        packed_states: vec![State::Minimum, State::Maximum],
        high_id_value: Some(i32::MAX),
        ..Default::default()
    };
    let copy: CodecCases = decode(&ctx, &encode(&ctx, &message).unwrap()).unwrap();
    assert_eq!(copy, message);
    assert!(copy.precise.unwrap().is_sign_negative());
    let ctx = prepare(EnumRecordBatch::DESCRIPTOR, &[EnumRecordBatch::type_info()]).unwrap();
    let message = EnumRecordBatch {
        records: vec![EnumRecord {
            state: State::Negative,
            code: 7,
        }],
    };
    assert_eq!(
        decode::<EnumRecordBatch>(&ctx, &encode(&ctx, &message).unwrap()).unwrap(),
        message
    );
}
#[test]
fn nested_and_fixed_arrays() {
    let ctx = prepare(RootPayload::DESCRIPTOR, &[RootPayload::type_info()]).unwrap();
    let m = RootPayload {
        header: Some("nested".into()),
        fixed_array: vec![FixedItem { x: 1., y: 2. }],
        var_array: vec![
            VarItem { name: None, id: 7 },
            VarItem {
                name: Some("".into()),
                id: 8,
            },
        ],
    };
    assert_eq!(
        decode::<RootPayload>(&ctx, &encode(&ctx, &m).unwrap()).unwrap(),
        m
    );
    let ctx = prepare(FixedBoard::DESCRIPTOR, &[FixedBoard::type_info()]).unwrap();
    let mut board = FixedBoard::default();
    board.rows[0].vectors[0].coords = [1., 2.];
    board.rows[1].vectors[0].grid = [[1, -2, 3], [4, 5, 6]];
    board.packed_rows.push(FixedRow::default());
    assert_eq!(
        decode::<FixedBoard>(&ctx, &encode(&ctx, &board).unwrap()).unwrap(),
        board
    );
}
#[test]
fn evolution_and_unknown_fields() {
    let remote = Packet::DESCRIPTOR
        .replace("bool active;", "bool enabled;")
        .replace(
            "packed int16 samples;",
            "repeated int16 samples;\n  5: required string extra;",
        );
    let ctx = prepare(&remote, &[Packet::type_info()]).unwrap();
    let mut bytes = FIXTURE.to_vec();
    bytes.extend_from_slice(b"\x02ok");
    let p: Packet = decode(&ctx, &bytes).unwrap();
    assert!(p.active);
    assert_eq!(p.samples, vec![300, -1]);
    assert!(encode(&ctx, &p).is_err());
    bytes[18] = 255;
    assert!(decode::<Packet>(&ctx, &bytes).is_err());
    let ctx = prepare(
        "SDL2\nmessage Packet {\n  1: required bool active;\n}\n",
        &[Packet::type_info()],
    )
    .unwrap();
    assert_eq!(
        decode::<Packet>(&ctx, &[1, 1]).unwrap(),
        Packet {
            active: true,
            ..Default::default()
        }
    );
    assert!(prepare(
        &Packet::DESCRIPTOR.replace("bool active;", "int32 active;"),
        &[Packet::type_info()]
    )
    .is_err());
}
#[test]
fn nested_evolution() {
    let text = RootPayload::DESCRIPTOR
        .replace("fl32 x;", "fl32 renamed_x;")
        .replace("fl32 y;", "fl32 y;\n  3: required int32 extra;");
    let ctx = prepare(&text, &[RootPayload::type_info()]).unwrap();
    let id = ctx_id(&text, "RootPayload");
    let mut data = vec![id, 0, 1];
    data.extend_from_slice(&1f32.to_be_bytes());
    data.extend_from_slice(&2f32.to_be_bytes());
    data.extend_from_slice(&7i32.to_be_bytes());
    data.push(0);
    assert_eq!(
        decode::<RootPayload>(&ctx, &data).unwrap().fixed_array,
        vec![FixedItem { x: 1., y: 2. }]
    );
}
fn ctx_id(text: &str, name: &str) -> u8 {
    text.lines()
        .filter(|l| l.starts_with("message "))
        .position(|l| l == format!("message {} {{", name))
        .unwrap() as u8
        + 1
}
#[test]
fn malformed_data() {
    let ctx = prepare(Packet::DESCRIPTOR, &[Packet::type_info()]).unwrap();
    for n in 0..FIXTURE.len() {
        assert!(decode::<Packet>(&ctx, &FIXTURE[..n]).is_err());
    }
    let mut data = FIXTURE.to_vec();
    data.push(0);
    assert!(decode::<Packet>(&ctx, &data).is_err());
    for data in [
        &[0][..],
        &[129, 0],
        &[255, 255, 255, 255, 16],
        &[128, 128, 128, 128, 128],
        &[2],
    ] {
        assert!(decode::<Packet>(&ctx, data).is_err());
    }
    let mut data = FIXTURE.to_vec();
    data[2] = 2;
    assert!(decode::<Packet>(&ctx, &data).is_err());
    data[2] = 1;
    data[1] = 2;
    assert!(decode_dynamic(&ctx, &data).is_err());
    data[1] = 1;
    data[7] = 255;
    assert!(decode::<Packet>(&ctx, &data).is_err());
}
#[test]
fn malformed_catalogues() {
    for s in [
        "SDL1\n",
        "SDL2\n",
        "SDL2\nmessage A {\n  1: required A a;\n}\n",
        "SDL2\nmessage A {\n  1: required Missing a;\n}\n",
        "SDL2\nmessage A {\n  1: packed string a;\n}\n",
        "SDL2\nmessage A {\n}\nenum E {\n}\n",
    ] {
        assert!(prepare(s, &[]).is_err());
    }
}
#[test]
fn counter_boundaries() {
    let ctx = prepare(Packet::DESCRIPTOR, &[Packet::type_info()]).unwrap();
    for n in [0, 1, 127, 128, 16383, 16384] {
        let m = Packet {
            label: "x".repeat(n),
            samples: vec![-1; n],
            ..Default::default()
        };
        assert_eq!(
            decode::<Packet>(&ctx, &encode(&ctx, &m).unwrap()).unwrap(),
            m
        );
    }
    assert!(decode::<Packet>(&ctx, &[1, 0, 0, 0, 255, 255, 255, 255, 15]).is_err());
}

#[test]
fn shared_subtypes_and_depth() {
    fn graph(count: usize, shared: bool) -> String {
        let mut text = String::from("SDL2\n");
        for i in 0..count {
            text.push_str(&format!("message N{:03} {{\n", i));
            if i == 0 {
                text.push_str("  1: required int8 value;\n");
            } else {
                text.push_str(&format!("  1: optional N{:03} left;\n", i - 1));
                if shared {
                    text.push_str(&format!("  2: optional N{:03} right;\n", i - 1));
                }
            }
            text.push_str("}\n");
        }
        text
    }
    assert!(prepare(&graph(30, true), &[]).is_ok());
    assert!(prepare(&graph(66, false), &[]).is_err());
}

#[test]
fn context_rejects_other_local_rust_type_with_same_logical_name() {
    #[derive(Default)]
    struct WrongPacket;
    impl sdl_runtime::WireValue for WrongPacket {
        fn encoded_size(&self) -> Result<usize, sdl_runtime::CodecError> {
            Ok(0)
        }
        fn encode_value(&self, _: &mut Vec<u8>) -> Result<(), sdl_runtime::CodecError> {
            Ok(())
        }
        fn decode_value(
            _: &mut sdl_runtime::Reader<'_>,
            _: &str,
        ) -> Result<Self, sdl_runtime::CodecError> {
            Ok(Self)
        }
    }
    impl SdlMessage for WrongPacket {
        const NAME: &'static str = "Packet";
        const DESCRIPTOR: &'static str = Packet::DESCRIPTOR;
    }
    let ctx = prepare(Packet::DESCRIPTOR, &[WrongPacket::type_info()]).unwrap();
    assert!(decode::<Packet>(&ctx, FIXTURE).is_err());
    assert!(encode(&ctx, &Packet::default()).is_err());
}

#[test]
fn packed_struct_binary_layout_and_truncation() {
    use sdl_schema_tests::bench_cases::{BenchPose, BenchRecord, BenchRecordBatch, BenchVector};
    let text = description(&[BenchRecordBatch::type_info()]).unwrap();
    let ctx = prepare(&text, &[BenchRecordBatch::type_info()]).unwrap();
    let message = BenchRecordBatch {
        records: (0..3).map(|i| BenchRecord {
            id: i - 1,
            pose: BenchPose {
                position: BenchVector { values: [i as f32 * 0.25, -0.0, -2.0] },
                rotation: [0.0, 0.0, 0.0, 1.0],
            },
            measures: [i as f32 * 0.125, -3.0],
        }).collect(),
    };
    // Independent wire oracle: numeric endian conversion, no generated codec.
    let mut expected = vec![ctx_id(&text, "BenchRecordBatch"), 3];
    for record in &message.records {
        expected.extend_from_slice(&record.id.to_be_bytes());
        for value in record.pose.position.values.iter().chain(&record.pose.rotation).chain(&record.measures) {
            expected.extend_from_slice(&value.to_be_bytes());
        }
    }
    assert_eq!(encode(&ctx, &message).unwrap(), expected);
    let copy: BenchRecordBatch = decode(&ctx, &expected).unwrap();
    assert_eq!(copy, message);
    assert!(copy.records[0].pose.position.values[1].is_sign_negative());
    for end in 0..expected.len() {
        assert!(decode::<BenchRecordBatch>(&ctx, &expected[..end]).is_err());
    }
    let mut trailing = expected.clone(); trailing.push(0);
    assert!(decode::<BenchRecordBatch>(&ctx, &trailing).is_err());
    let empty = BenchRecordBatch::default();
    assert_eq!(decode::<BenchRecordBatch>(&ctx, &encode(&ctx, &empty).unwrap()).unwrap(), empty);
    let hostile = [expected[0], 255, 255, 255, 255, 15];
    assert!(decode::<BenchRecordBatch>(&ctx, &hostile).is_err());
    // A field added to each record must retain the generic evolution path.
    let remote = text.replace("  3: required fl32[2] measures;", "  3: required fl32[2] measures;\n  4: required int8 extra;");
    let changed = prepare(&remote, &[BenchRecordBatch::type_info()]).unwrap();
    let mut evolved = expected[..2].to_vec();
    for record in expected[2..].chunks_exact(40) {
        evolved.extend_from_slice(record); evolved.push(7);
    }
    assert_eq!(decode::<BenchRecordBatch>(&changed, &evolved).unwrap(), message);
    assert!(encode(&changed, &message).is_err());
}

#[test]
fn packed_fixed_enum_validation_and_evolution() {
    use sdl_runtime::CodecError;
    let text = description(&[EnumRecordBatch::type_info()]).unwrap();
    let ctx = prepare(&text, &[EnumRecordBatch::type_info()]).unwrap();
    let value = EnumRecordBatch { records: vec![EnumRecord { state: State::Ready, code: -7 }] };
    let mut wire = encode(&ctx, &value).unwrap();
    assert_eq!(decode::<EnumRecordBatch>(&ctx, &wire).unwrap(), value);
    wire[2..6].copy_from_slice(&123i32.to_be_bytes());
    assert_eq!(decode::<EnumRecordBatch>(&ctx, &wire), Err(CodecError::InvalidEnum));
    // The enum subset differs although the structure layout itself is identical.
    let subset = text.replace("  READY = 1;\n", "");
    let subset_ctx = prepare(&subset, &[EnumRecordBatch::type_info()]).unwrap();
    let valid_wire = encode(&ctx, &value).unwrap();
    assert_eq!(decode::<EnumRecordBatch>(&subset_ctx, &valid_wire), Err(CodecError::InvalidEnum));
}

#[test]
fn fixed_codec_preserves_float_bits_and_rejects_invalid_bool() {
    use sdl_runtime::{CodecError, WireValue};
    use sdl_schema_tests::bench_cases::BenchSmall;
    let bits = [0x80000000u32, 0x7fc01234u32];
    let wire: Vec<u8> = bits.iter().flat_map(|value| value.to_be_bytes()).collect();
    let value = Complex32::decode_fixed(&wire).unwrap();
    assert_eq!(value.real.to_bits(), bits[0]);
    assert_eq!(value.imag.to_bits(), bits[1]);
    let mut encoded = [0u8; 8];
    value.encode_fixed(&mut encoded).unwrap();
    assert_eq!(encoded.as_slice(), wire.as_slice());
    assert_eq!(BenchSmall::decode_fixed(&[2, 0, 0, 0, 0, 0, 0]), Err(CodecError::InvalidBoolean));
    assert_eq!(BenchSmall::decode_fixed(&[0; 6]), Err(CodecError::Truncated));
}

#[test]
fn packed_struct_varied_bits_match_generic_decoder() {
    use sdl_runtime::write_count;
    use sdl_schema_tests::bench_cases::BenchRecordBatch;
    let text = description(&[BenchRecordBatch::type_info()]).unwrap();
    let fast = prepare(&text, &[BenchRecordBatch::type_info()]).unwrap();
    let mut state = 0x123456789abcdefu64;
    for trial in 0..64 {
        let mut wire = vec![ctx_id(&text, "BenchRecordBatch")];
        write_count(1000, &mut wire).unwrap();
        let start = wire.len();
        for _ in 0..40000 {
            state ^= state << 13; state ^= state >> 7; state ^= state << 17;
            wire.push(match trial { 0 => 0, 1 => 255, _ => (state >> 24) as u8 });
        }
        let fast_value: BenchRecordBatch = decode(&fast, &wire).unwrap();
        assert_eq!(fast_value.records.len(), 1000);
        let mut reconstructed = Vec::new();
        for record in &fast_value.records {
            reconstructed.extend_from_slice(&record.id.to_be_bytes());
            for field in record.pose.position.values.iter().chain(&record.pose.rotation).chain(&record.measures) {
                reconstructed.extend_from_slice(&field.to_bits().to_be_bytes());
            }
        }
        assert_eq!(reconstructed, wire[start..], "trial {trial}");
        // The independent dynamic decoder uses descriptor traversal, not fixed codecs.
        let generic = decode_dynamic(&fast, &wire).unwrap();
        fn message(value: &DynamicValue) -> &sdl_runtime::DynamicMessage {
            match value { DynamicValue::Message(value) => value, _ => panic!("expected message") }
        }
        fn floats(value: &DynamicValue, expected: &[f32]) {
            let DynamicValue::Array(values) = value else { panic!("expected array") };
            assert_eq!(values.len(), expected.len());
            for (value, expected) in values.iter().zip(expected) {
                let DynamicValue::Float(value) = value else { panic!("expected float") };
                if expected.is_nan() { assert!(value.is_nan()); }
                else { assert_eq!((*value as f32).to_bits(), expected.to_bits()); }
            }
        }
        let DynamicValue::Array(records) = &generic.fields["records"] else { panic!("expected records") };
        assert_eq!(records.len(), 1000);
        for (decoded, dynamic) in fast_value.records.iter().zip(records) {
            let record = message(dynamic);
            assert_eq!(record.fields["id"], DynamicValue::Integer(decoded.id as i64));
            let pose = message(&record.fields["pose"]);
            let position = message(&pose.fields["position"]);
            floats(&position.fields["values"], &decoded.pose.position.values);
            floats(&pose.fields["rotation"], &decoded.pose.rotation);
            floats(&record.fields["measures"], &decoded.measures);
        }
        // Also cross-check encoding against the independent original bytes.
        assert_eq!(encode(&fast, &fast_value).unwrap(), wire);
    }
}

#[test]
fn optimized_paths_reject_invalid_values_at_the_end() {
    use sdl_runtime::CodecError;
    use sdl_schema_tests::bench_cases::BenchSmall;
    let text = description(&[BenchSmall::type_info()]).unwrap();
    let ctx = prepare(&text, &[BenchSmall::type_info()]).unwrap();
    let bad = [ctx_id(&text, "BenchSmall"), 2, 0, 0, 0, 0, 0, 0];
    assert_eq!(decode::<BenchSmall>(&ctx, &bad), Err(CodecError::InvalidBoolean));

    let text = description(&[EnumRecordBatch::type_info()]).unwrap();
    let ctx = prepare(&text, &[EnumRecordBatch::type_info()]).unwrap();
    let value = EnumRecordBatch { records: vec![
        EnumRecord { state: State::Ready, code: 1 },
        EnumRecord { state: State::Negative, code: 2 },
    ] };
    let mut wire = encode(&ctx, &value).unwrap();
    wire[10..14].copy_from_slice(&123i32.to_be_bytes());
    assert_eq!(decode::<EnumRecordBatch>(&ctx, &wire), Err(CodecError::InvalidEnum));

    let text = description(&[CodecCases::type_info()]).unwrap();
    let ctx = prepare(&text, &[CodecCases::type_info()]).unwrap();
    let flags = CodecCases { packed_flags: vec![false, true], ..Default::default() };
    let mut wire = encode(&ctx, &flags).unwrap();
    let last_flag = wire.len() - 2; // Final optional field has a zero count.
    assert_eq!(wire[last_flag], 1);
    wire[last_flag] = 2;
    assert_eq!(decode::<CodecCases>(&ctx, &wire), Err(CodecError::InvalidBoolean));
}
