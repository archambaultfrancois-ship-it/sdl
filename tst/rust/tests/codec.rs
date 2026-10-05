use sdl_runtime::{decode, encode, CodecError, Complex32, Complex64};
use sdl_schema_tests::codec_cases::{CodecCases, State};
use sdl_schema_tests::schema::{FixedItem, RootPayload, VarItem};

const C_ROOT_PAYLOAD_WIRE: &[u8] = include_bytes!("../../fixtures/root_payload.bin");

#[test]
fn root_payload_deep_clone_and_wire_round_trip() {
   let original = RootPayload {
      header: Some("Mission_Data_Packet".to_owned()),
      fixed_array: vec![
         FixedItem { x: 1.1, y: 2.2 },
         FixedItem { x: 3.3, y: 4.4 },
      ],
      var_array: vec![
         VarItem { name: Some("Variable_Node_A".to_owned()), id: 99999 },
         VarItem { name: Some("Variable_Node_B".to_owned()), id: 77777 },
      ],
   };
   let cloned = original.clone();
   assert_eq!(cloned, original);
   assert_ne!(cloned.header.as_ref().unwrap().as_ptr(), original.header.as_ref().unwrap().as_ptr());
   assert_ne!(cloned.var_array[0].name.as_ref().unwrap().as_ptr(), original.var_array[0].name.as_ref().unwrap().as_ptr());

   let wire = encode(&cloned).unwrap();
   assert_eq!(wire.as_slice(), C_ROOT_PAYLOAD_WIRE);
   let decoded: RootPayload = decode(&wire).unwrap();
   assert_eq!(decoded, original);
   let decoded_from_c: RootPayload = decode(C_ROOT_PAYLOAD_WIRE).unwrap();
   assert_eq!(decoded_from_c, original);
}

fn codec_cases() -> CodecCases {
   CodecCases {
      tiny: Some(i8::MIN),
      small: Some(i16::MIN),
      signed_value: Some(i32::MIN),
      wide: Some(i64::MAX),
      ratio: Some(-0.0),
      precise: Some(1.0 / 3.0),
      point: Some(Complex32 { real: 1.25, imag: -2.5 }),
      position: Some(Complex64 { real: -3.125, imag: 4.75 }),
      state: Some(State::Ready),
      empty_text: Some(String::new()),
      required_zero: 0,
      samples: vec![i16::MIN, -1, i16::MAX],
      measurements: vec![0.125, -1024.5],
      labels: vec![String::new(), "alpha".to_owned(), "omega".to_owned()],
      points: vec![
         Complex32 { real: 5.5, imag: -6.25 },
         Complex32 { real: 0.0, imag: 9.0 },
      ],
      empty_values: Vec::new(),
      required_enabled: true,
      optional_enabled: Some(false),
      bool_flags: vec![true, false, true],
   }
}

#[test]
fn codec_scalars_optionals_enums_arrays_and_empty_values() {
   let input = codec_cases();
   let wire = encode(&input).unwrap();
   let decoded: CodecCases = decode(&wire).unwrap();
   assert_eq!(decoded, input);
   assert!(decoded.ratio.unwrap().is_sign_negative());
}

#[test]
fn absent_optionals_and_empty_arrays_round_trip() {
   let input = CodecCases::default();
   let wire = encode(&input).unwrap();
   let decoded: CodecCases = decode(&wire).unwrap();
   assert_eq!(decoded, input);
   assert!(decoded.tiny.is_none());
   assert!(decoded.optional_enabled.is_none());
   assert!(decoded.bool_flags.is_empty());
}

#[test]
fn unknown_fields_are_skipped_and_truncated_fields_fail() {
   let wire = encode(&codec_cases()).unwrap();
   let mut extended = wire.clone();
   extended.extend_from_slice(&999_u32.to_le_bytes());
   extended.extend_from_slice(&3_u32.to_le_bytes());
   extended.extend_from_slice(&[0xA1, 0xB2, 0xC3]);
   let decoded: CodecCases = decode(&extended).unwrap();
   assert_eq!(decoded, codec_cases());
   assert_eq!(decode::<CodecCases>(&wire[..wire.len() - 1]), Err(CodecError::Truncated));
}

#[test]
fn noncanonical_boolean_payload_is_rejected() {
   let mut wire = encode(&codec_cases()).unwrap();
   wire.extend_from_slice(&17_u32.to_le_bytes());
   wire.extend_from_slice(&1_u32.to_le_bytes());
   wire.push(2);
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::InvalidBoolean));
}

#[test]
fn wrong_type_hash_is_rejected() {
   let mut wire = encode(&codec_cases()).unwrap();
   wire[0] ^= 0x80;
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::TypeMismatch));
}
