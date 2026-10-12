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
