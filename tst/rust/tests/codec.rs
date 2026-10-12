use sdl_runtime::{decode, decode_dynamic, display, encode, write_field, write_packed_field, CodecError, Complex32,
   Complex64, DynamicValue, SdlMessage};
use sdl_schema_tests::codec_cases::{CodecCases, EnumRecord, EnumRecordBatch, State};
use sdl_schema_tests::empty_message::EmptyMessage;
use sdl_schema_tests::schema::{
   AnonymousEnvelope, AnonymousEnvelope1, AnonymousEnvelope2, AnonymousEnvelope3,
   FixedBoard, FixedItem, FixedRow, FixedVector, RootPayload, VarItem,
};

#[cfg(feature = "wire-little-endian")]
const C_ROOT_PAYLOAD_WIRE: &[u8] = include_bytes!("../../fixtures/root_payload.bin");
#[cfg(not(feature = "wire-little-endian"))]
const C_ROOT_PAYLOAD_WIRE: &[u8] = include_bytes!("../../fixtures/root_payload_be.bin");

fn wire_u32(value: u32) -> [u8; 4] {
   if cfg!(feature = "wire-little-endian") {
      value.to_le_bytes()
   } else {
      value.to_be_bytes()
   }
}

fn read_wire_u32(bytes: &[u8]) -> u32 {
   let bytes: [u8; 4] = bytes.try_into().unwrap();
   if cfg!(feature = "wire-little-endian") {
      u32::from_le_bytes(bytes)
   } else {
      u32::from_be_bytes(bytes)
   }
}

fn reverse_body_fields(wire: &[u8]) -> Vec<u8> {
   let descriptor_size = read_wire_u32(&wire[..4]) as usize;
   let body_offset = 8 + descriptor_size;
   let mut fields: Vec<(u32, Vec<&[u8]>)> = Vec::new();
   let mut offset = body_offset;
   while offset < wire.len() {
      let length = read_wire_u32(&wire[offset + 4..offset + 8]) as usize;
      let end = offset + 8 + length;
      assert!(end <= wire.len(), "test input contains a truncated field");
      let field_id = read_wire_u32(&wire[offset..offset + 4]);
      if let Some((previous_id, group)) = fields.last_mut() {
         if *previous_id == field_id {
            group.push(&wire[offset..end]);
         } else {
            fields.push((field_id, vec![&wire[offset..end]]));
         }
      } else {
         fields.push((field_id, vec![&wire[offset..end]]));
      }
      offset = end;
   }
   let mut reversed = wire[..body_offset].to_vec();
   for (_, group) in fields.into_iter().rev() {
      for field in group {
         reversed.extend_from_slice(field);
      }
   }
   reversed
}

#[test]
fn absent_required_fields_decode_to_default_values() {
   let encoded = encode(&codec_cases()).unwrap();
   let descriptor_size = read_wire_u32(&encoded[..4]) as usize;
   let empty_body = &encoded[..8 + descriptor_size];
   assert_eq!(decode::<CodecCases>(empty_body).unwrap(), CodecCases::default());
   assert_eq!(decode_dynamic(empty_body).unwrap().type_name, "CodecCases");
}

#[test]
fn s01_field_order_is_independent_of_schema_order() {
   let original = codec_cases();
   let reordered = reverse_body_fields(&encode(&original).unwrap());
   assert_eq!(decode::<CodecCases>(&reordered).unwrap(), original);
   assert_eq!(decode_dynamic(&reordered).unwrap().fields.get("required_zero"),
      Some(&DynamicValue::Integer(0)));
}

#[test]
fn s02_unicode_strings_and_float_special_values_round_trip() {
   let text = format!("{}{}tail", "SDL-é-📦-".repeat(128), '\0');
   let original = RootPayload {
      header: Some(text.clone()),
      ..RootPayload::default()
   };
   assert_eq!(decode::<RootPayload>(&encode(&original).unwrap()).unwrap(), original);

   let values = CodecCases {
      ratio: Some(f32::INFINITY),
      precise: Some(f64::NAN),
      point: Some(Complex32 { real: f32::NEG_INFINITY, imag: f32::NAN }),
      position: Some(Complex64 { real: f64::INFINITY, imag: f64::NEG_INFINITY }),
      ..CodecCases::default()
   };
   let decoded = decode::<CodecCases>(&encode(&values).unwrap()).unwrap();
   assert_eq!(decoded.ratio, Some(f32::INFINITY));
   assert!(decoded.precise.unwrap().is_nan());
   assert_eq!(decoded.point.unwrap().real, f32::NEG_INFINITY);
   assert!(decoded.point.unwrap().imag.is_nan());
   assert_eq!(decoded.position.unwrap().real, f64::INFINITY);
   assert_eq!(decoded.position.unwrap().imag, f64::NEG_INFINITY);

   let smallest_f32 = f32::from_bits(1);
   let smallest_f64 = f64::from_bits(1);
   let subnormals = CodecCases {
      ratio: Some(smallest_f32),
      precise: Some(smallest_f64),
      point: Some(Complex32 { real: -smallest_f32, imag: -0.0 }),
      position: Some(Complex64 { real: smallest_f64, imag: -0.0 }),
      ..CodecCases::default()
   };
   let decoded = decode::<CodecCases>(&encode(&subnormals).unwrap()).unwrap();
   assert_eq!(decoded.ratio, Some(smallest_f32));
   assert_eq!(decoded.precise, Some(smallest_f64));
   assert_eq!(decoded.point.unwrap().real, -smallest_f32);
   assert!(decoded.point.unwrap().imag.is_sign_negative());
   assert_eq!(decoded.position.unwrap().real, smallest_f64);
   assert!(decoded.position.unwrap().imag.is_sign_negative());
}

#[test]
fn s03_invalid_frame_and_field_lengths_are_rejected() {
   let valid = encode(&codec_cases()).unwrap();
   let mut bad_descriptor_length = valid.clone();
   bad_descriptor_length[..4].copy_from_slice(&wire_u32(u32::MAX));
   assert_eq!(decode::<CodecCases>(&bad_descriptor_length), Err(CodecError::Truncated));
   assert_eq!(decode_dynamic(&bad_descriptor_length), Err(CodecError::Truncated));

   let mut oversized_descriptor = valid.clone();
   oversized_descriptor[..4].copy_from_slice(&wire_u32(1024 * 1024 + 1));
   assert_eq!(decode_dynamic(&oversized_descriptor), Err(CodecError::Truncated));

   let descriptor_size = read_wire_u32(&valid[..4]) as usize;
   let body_offset = 8 + descriptor_size;
   let mut bad_field_length = valid.clone();
   bad_field_length[body_offset + 4..body_offset + 8]
      .copy_from_slice(&wire_u32(u32::MAX));
   assert_eq!(decode::<CodecCases>(&bad_field_length), Err(CodecError::Truncated));
   assert_eq!(decode_dynamic(&bad_field_length), Err(CodecError::Truncated));
   assert_eq!(decode::<CodecCases>(&valid[..7]), Err(CodecError::Truncated));
   assert_eq!(decode_dynamic(&valid[..7]), Err(CodecError::Truncated));

   let mut wrong_scalar_length = encode(&CodecCases::default()).unwrap();
   wrong_scalar_length.extend_from_slice(&wire_u32(11));
   wrong_scalar_length.extend_from_slice(&wire_u32(3));
   wrong_scalar_length.extend_from_slice(&[1, 2, 3]);
   assert_eq!(decode::<CodecCases>(&wrong_scalar_length), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&wrong_scalar_length), Err(CodecError::TypeMismatch));
}

#[test]
fn primitive_payload_widths_are_checked() {
   let cases = [(1, 0), (1, 2), (2, 1), (2, 3), (3, 3), (3, 5),
      (4, 7), (4, 9), (5, 3), (5, 5), (6, 7), (6, 9),
      (7, 7), (7, 9), (8, 15), (8, 17), (18, 0), (18, 2)];
   for (field_id, length) in cases {
      let mut wire = encode(&CodecCases::default()).unwrap();
      wire.extend_from_slice(&wire_u32(field_id));
      wire.extend_from_slice(&wire_u32(length));
      wire.resize(wire.len() + length as usize, 0);
      assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::TypeMismatch),
         "typed field {}", field_id);
      assert_eq!(decode_dynamic(&wire), Err(CodecError::TypeMismatch),
         "dynamic field {}", field_id);
   }
}

#[test]
fn invalid_utf8_in_wire_descriptor_strings_is_rejected() {
   let descriptor: &[u8] = &[
      0x53, 0x44, 0x44, 0x31, 0x00, 0x01, 0xFF, 0x00,
      0x01, 0x00, 0x01, 0xFF, 0x00, 0x00, 0x00, 0x00,
   ];
   let hash = descriptor.iter().fold(2166136261u32, |hash, byte|
      (hash ^ u32::from(*byte)).wrapping_mul(16777619));
   let mut frame = wire_u32(descriptor.len() as u32).to_vec();
   frame.extend_from_slice(descriptor);
   frame.extend_from_slice(&wire_u32(hash));
   assert_eq!(decode_dynamic(&frame), Err(CodecError::InvalidDescriptor));
}

#[test]
fn nul_in_wire_descriptor_strings_is_rejected() {
   let descriptor: &[u8] = &[
      0x53, 0x44, 0x44, 0x31, 0x00, 0x02, 0x41, 0x00,
      0x00, 0x01, 0x00, 0x02, 0x41, 0x00, 0x00, 0x00,
      0x00, 0x00,
   ];
   let hash = descriptor.iter().fold(2166136261u32, |hash, byte|
      (hash ^ u32::from(*byte)).wrapping_mul(16777619));
   let mut frame = wire_u32(descriptor.len() as u32).to_vec();
   frame.extend_from_slice(descriptor);
   frame.extend_from_slice(&wire_u32(hash));
   assert_eq!(decode_dynamic(&frame), Err(CodecError::InvalidDescriptor));
}

#[test]
fn recursive_wire_descriptors_are_rejected() {
   let descriptor: &[u8] = &[
      0x53, 0x44, 0x44, 0x31, 0x00, 0x01, 0x41, 0x00,
      0x01, 0x00, 0x01, 0x41, 0x00, 0x01, 0x00, 0x00,
      0x00, 0x01, 0x00, 0x04, 0x6E, 0x65, 0x78, 0x74,
      0x01, 0x00, 0x01, 0x41, 0x00, 0x00, 0x00,
   ];
   let hash = descriptor.iter().fold(2166136261u32, |hash, byte|
      (hash ^ u32::from(*byte)).wrapping_mul(16777619));
   let mut frame = wire_u32(descriptor.len() as u32).to_vec();
   frame.extend_from_slice(descriptor);
   frame.extend_from_slice(&wire_u32(hash));
   assert_eq!(decode_dynamic(&frame), Err(CodecError::InvalidDescriptor));
}

#[test]
fn display_formats_typed_objects_with_configurable_indentation() {
   let original = codec_cases();
   let rendered = display(&original, 2);
   assert!(rendered.starts_with("CodecCases {\n"));
   assert!(rendered.contains("  tiny: -128\n"));
   assert!(rendered.contains("  state: READY\n"));
   assert!(rendered.contains("  samples: [\n    -32768,\n"));

   let absent = display(&CodecCases::default(), 4);
   assert!(absent.contains("    tiny: null\n"));
}

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
   let descriptor_size = read_wire_u32(&wire[..4]) as usize;
   let descriptor_end = 4 + descriptor_size;
   assert_eq!(&wire[4..descriptor_end], RootPayload::DESCRIPTOR);
   assert_eq!(wire.as_slice(), C_ROOT_PAYLOAD_WIRE);
   let decoded: RootPayload = decode(&wire).unwrap();
   assert_eq!(decoded, original);
   let decoded_from_c: RootPayload = decode(C_ROOT_PAYLOAD_WIRE).unwrap();
   assert_eq!(decoded_from_c, original);

   let generic = decode_dynamic(&wire).unwrap();
   assert_eq!(generic.type_name, "RootPayload");
   assert_eq!(generic.fields.get("header"), Some(&DynamicValue::String(
      "Mission_Data_Packet".to_owned())));
   match generic.fields.get("var_array").unwrap() {
      DynamicValue::Array(values) => match &values[0] {
         DynamicValue::Message(item) => assert_eq!(item.fields.get("id"),
            Some(&DynamicValue::Integer(99999))),
         other => panic!("unexpected dynamic repeated value: {:?}", other),
      },
      other => panic!("unexpected dynamic repeated field: {:?}", other),
   }
   let mut modified_descriptor_hash = wire.clone();
   modified_descriptor_hash[descriptor_end] ^= 1;
   assert_eq!(decode_dynamic(&modified_descriptor_hash),
      Err(CodecError::DescriptorHashMismatch));
}

#[test]
fn nested_fixed_arrays_round_trip() {
   let vector = |base: f32| FixedVector {
      coords: [base, base + 1.0],
      grid: [[base as i16, base as i16 + 1, base as i16 + 2],
         [base as i16 + 3, base as i16 + 4, base as i16 + 5]],
   };
   let row = |base: f32| FixedRow {
      vectors: [vector(base), vector(base + 10.0)],
   };
   let input = FixedBoard {
      rows: [row(0.0), row(100.0)],
      packed_rows: vec![row(200.0), row(300.0)],
   };
   let wire = encode(&input).unwrap();
   let decoded: FixedBoard = decode(&wire).unwrap();
   assert_eq!(decoded, input);
   let generic = decode_dynamic(&wire).unwrap();
   match generic.fields.get("rows").unwrap() {
      DynamicValue::Array(rows) => match &rows[1] {
         DynamicValue::Message(row) => match row.fields.get("vectors").unwrap() {
            DynamicValue::Array(vectors) => match &vectors[0] {
               DynamicValue::Message(vector) => assert_eq!(vector.fields.get("coords"),
               Some(&DynamicValue::Array(vec![DynamicValue::Float(100.0),
                  DynamicValue::Float(101.0)]))),
               other => panic!("unexpected fixed vector: {:?}", other),
            },
            other => panic!("unexpected row vector array: {:?}", other),
         },
         other => panic!("unexpected fixed row value: {:?}", other),
      },
      other => panic!("unexpected fixed board rows: {:?}", other),
   }
   match generic.fields.get("packed_rows").unwrap() {
      DynamicValue::Array(rows) => match &rows[1] {
         DynamicValue::Message(row) => match row.fields.get("vectors").unwrap() {
            DynamicValue::Array(vectors) => match &vectors[0] {
               DynamicValue::Message(vector) => assert_eq!(vector.fields.get("grid"),
                  Some(&DynamicValue::Array(vec![
                     DynamicValue::Array(vec![DynamicValue::Integer(300),
                        DynamicValue::Integer(301), DynamicValue::Integer(302)]),
                     DynamicValue::Array(vec![DynamicValue::Integer(303),
                        DynamicValue::Integer(304), DynamicValue::Integer(305)]),
                  ]))),
               other => panic!("unexpected packed fixed vector: {:?}", other),
            },
            other => panic!("unexpected packed row vector array: {:?}", other),
         },
         other => panic!("unexpected packed fixed row: {:?}", other),
      },
      other => panic!("unexpected packed fixed rows: {:?}", other),
   }

   let mut malformed = wire;
   malformed.extend_from_slice(&wire_u32(1));
   malformed.extend_from_slice(&wire_u32(1));
   malformed.push(0);
   assert_eq!(decode::<FixedBoard>(&malformed), Err(CodecError::TypeMismatch));
}

#[test]
fn anonymous_nested_structs_round_trip() {
   let input = AnonymousEnvelope {
      metadata: AnonymousEnvelope1 {
         code: 42,
         detail: Some(AnonymousEnvelope2 {
            text: "anonymous detail".to_owned(),
         }),
      },
      points: vec![
         AnonymousEnvelope3 { x: 1.25, y: -2.5 },
         AnonymousEnvelope3 { x: 3.0, y: 4.5 },
      ],
   };
   let wire = encode(&input).unwrap();
   let decoded: AnonymousEnvelope = decode(&wire).unwrap();
   assert_eq!(decoded, input);
   let generic = decode_dynamic(&wire).unwrap();
   match generic.fields.get("metadata").unwrap() {
      DynamicValue::Message(metadata) => {
         assert_eq!(metadata.type_name, "AnonymousEnvelope$1");
         match metadata.fields.get("detail").unwrap() {
            DynamicValue::Message(detail) => assert_eq!(detail.fields.get("text"),
               Some(&DynamicValue::String("anonymous detail".to_owned()))),
            other => panic!("unexpected anonymous detail: {:?}", other),
         }
      },
      other => panic!("unexpected anonymous metadata: {:?}", other),
   }
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
      high_id_value: Some(i32::MAX),
      samples: vec![i16::MIN, -1, i16::MAX],
      measurements: vec![0.125, -1024.5],
      labels: vec![String::new(), "alpha".to_owned(), "ome\0ga\0".to_owned()],
      points: vec![
         Complex32 { real: 5.5, imag: -6.25 },
         Complex32 { real: 0.0, imag: 9.0 },
      ],
      empty_values: Vec::new(),
      required_enabled: true,
      optional_enabled: Some(false),
      bool_flags: vec![true, false, true],
      fixed_states: [State::Ready, State::Negative, State::Unknown],
      packed_states: vec![State::Ready, State::Negative],
      packed_flags: vec![false, true, false],
   }
}

#[test]
fn codec_scalars_optionals_enums_arrays_and_empty_values() {
   let input = codec_cases();
   let wire = encode(&input).unwrap();
   let decoded: CodecCases = decode(&wire).unwrap();
   assert_eq!(decoded, input);
   assert!(decoded.ratio.unwrap().is_sign_negative());
   let dynamic = decode_dynamic(&wire).unwrap();
   assert_eq!(dynamic.fields.get("state"), Some(&DynamicValue::Enum {
      type_name: "State".to_owned(), name: "READY".to_owned(), value: 1,
   }));
   assert_eq!(dynamic.fields.get("high_id_value"),
      Some(&DynamicValue::Integer(i32::MAX as i64)));
   assert_eq!(dynamic.fields.get("packed_flags"), Some(&DynamicValue::Array(vec![
      DynamicValue::Bool(false), DynamicValue::Bool(true), DynamicValue::Bool(false),
   ])));
   assert_eq!(dynamic.fields.get("labels"), Some(&DynamicValue::Array(vec![
      DynamicValue::String(String::new()), DynamicValue::String("alpha".to_owned()),
      DynamicValue::String("ome\0ga\0".to_owned()),
   ])));
}

#[test]
fn signed_integer_minimum_and_maximum_values_round_trip() {
   let boundaries = [
      [i8::MIN as i64, i16::MIN as i64, i32::MIN as i64, i64::MIN],
      [i8::MAX as i64, i16::MAX as i64, i32::MAX as i64, i64::MAX],
   ];
   let names = ["tiny", "small", "signed_value", "wide"];
   for expected in boundaries {
      let input = CodecCases {
         tiny: Some(expected[0] as i8),
         small: Some(expected[1] as i16),
         signed_value: Some(expected[2] as i32),
         wide: Some(expected[3]),
         high_id_value: Some(expected[2] as i32),
         ..CodecCases::default()
      };
      let wire = encode(&input).unwrap();
      assert_eq!(decode::<CodecCases>(&wire).unwrap(), input);
      let dynamic = decode_dynamic(&wire).unwrap();
      for (name, value) in names.iter().zip(expected.iter()) {
         assert_eq!(dynamic.fields.get(*name), Some(&DynamicValue::Integer(*value)));
      }
   }
}

#[test]
fn negative_enum_values_round_trip() {
   let mut input = CodecCases::default();
   input.state = Some(State::Negative);
   let wire = encode(&input).unwrap();
   let decoded: CodecCases = decode(&wire).unwrap();
   assert_eq!(decoded, input);
   let dynamic = decode_dynamic(&wire).unwrap();
   assert_eq!(dynamic.fields.get("state"), Some(&DynamicValue::Enum {
      type_name: "State".to_owned(), name: "NEGATIVE".to_owned(), value: -7,
   }));
}

#[test]
fn enum_int32_boundary_values_round_trip() {
   let cases = [(State::Minimum, i32::MIN, "MINIMUM"),
      (State::Maximum, i32::MAX, "MAXIMUM")];
   for (state, expected, name) in cases {
      let input = CodecCases { state: Some(state), ..CodecCases::default() };
      let wire = encode(&input).unwrap();
      assert_eq!(decode::<CodecCases>(&wire).unwrap(), input);
      let dynamic = decode_dynamic(&wire).unwrap();
      assert_eq!(dynamic.fields.get("state"), Some(&DynamicValue::Enum {
         type_name: "State".to_owned(), name: name.to_owned(), value: expected,
      }));
   }
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
   extended.extend_from_slice(&wire_u32(998));
   extended.extend_from_slice(&wire_u32(0));
   extended.extend_from_slice(&wire_u32(11));
   extended.extend_from_slice(&wire_u32(4));
   extended.extend_from_slice(&[0; 4]);
   extended.extend_from_slice(&wire_u32(999));
   extended.extend_from_slice(&wire_u32(3));
   extended.extend_from_slice(&[0xA1, 0xB2, 0xC3]);
   extended.extend_from_slice(&wire_u32(999));
   extended.extend_from_slice(&wire_u32(1));
   extended.push(0xD4);
   let decoded: CodecCases = decode(&extended).unwrap();
   assert_eq!(decoded, codec_cases());
   let dynamic = decode_dynamic(&extended).unwrap();
   assert_eq!(dynamic.type_name, "CodecCases");
   assert_eq!(dynamic.fields.get("required_zero"), Some(&DynamicValue::Integer(0)));
   assert_eq!(dynamic.fields.get("samples"), Some(&DynamicValue::Array(vec![
      DynamicValue::Integer(-32768), DynamicValue::Integer(-1),
      DynamicValue::Integer(32767),
   ])));
   assert_eq!(decode_dynamic(&extended[..extended.len() - 1]), Err(CodecError::Truncated));
   assert_eq!(decode::<CodecCases>(&wire[..wire.len() - 1]), Err(CodecError::Truncated));
}

#[test]
fn duplicate_singular_fields_use_last_value_and_repeated_fields_append() {
   let mut original = CodecCases::default();
   original.required_zero = 7;
   original.optional_enabled = Some(true);
   original.samples = vec![-9];
   let mut wire = encode(&original).unwrap();
   write_field(11, &42i32, &mut wire).unwrap();
   write_field(12, &1234i16, &mut wire).unwrap();
   write_field(18, &false, &mut wire).unwrap();

   let decoded: CodecCases = decode(&wire).unwrap();
   assert_eq!(decoded.required_zero, 42);
   assert_eq!(decoded.optional_enabled, Some(false));
   assert_eq!(decoded.samples, vec![-9, 1234]);
   let dynamic = decode_dynamic(&wire).unwrap();
   assert_eq!(dynamic.fields.get("required_zero"), Some(&DynamicValue::Integer(42)));
   assert_eq!(dynamic.fields.get("optional_enabled"), Some(&DynamicValue::Bool(false)));
   assert_eq!(dynamic.fields.get("samples"), Some(&DynamicValue::Array(vec![
      DynamicValue::Integer(-9), DynamicValue::Integer(1234),
   ])));

   let mut malformed_then_valid = encode(&CodecCases::default()).unwrap();
   malformed_then_valid.extend_from_slice(&wire_u32(998));
   malformed_then_valid.extend_from_slice(&wire_u32(0));
   malformed_then_valid.extend_from_slice(&wire_u32(11));
   malformed_then_valid.extend_from_slice(&wire_u32(3));
   malformed_then_valid.extend_from_slice(&[0, 0, 0]);
   write_field(11, &42i32, &mut malformed_then_valid).unwrap();
   assert_eq!(decode::<CodecCases>(&malformed_then_valid), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&malformed_then_valid), Err(CodecError::TypeMismatch));
}

#[test]
fn undeclared_enum_values_are_rejected_by_typed_and_dynamic_decoders() {
   let mut wire = encode(&CodecCases::default()).unwrap();
   write_field(9, &99i32, &mut wire).unwrap();
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::InvalidEnum));
   assert_eq!(decode_dynamic(&wire), Err(CodecError::InvalidEnum));
}

#[test]
fn packed_fixed_messages_reject_nested_invalid_enum_values() {
   let valid = EnumRecordBatch {
      records: vec![EnumRecord { state: State::Ready, code: 42 }],
   };
   let wire = encode(&valid).unwrap();
   assert_eq!(decode::<EnumRecordBatch>(&wire).unwrap(), valid);
   assert_eq!(decode_dynamic(&wire).unwrap().type_name, "EnumRecordBatch");

   let mut malformed = encode(&EnumRecordBatch::default()).unwrap();
   malformed.extend_from_slice(&wire_u32(1));
   malformed.extend_from_slice(&wire_u32(8));
   malformed.extend_from_slice(&wire_u32(99));
   malformed.extend_from_slice(&wire_u32(42));
   assert_eq!(decode::<EnumRecordBatch>(&malformed), Err(CodecError::InvalidEnum));
   assert_eq!(decode_dynamic(&malformed), Err(CodecError::InvalidEnum));
}

#[test]
fn fixed_enum_arrays_reject_undeclared_values() {
   assert_eq!(decode::<CodecCases>(&encode(&codec_cases()).unwrap()).unwrap(),
      codec_cases());
   let mut malformed = encode(&CodecCases::default()).unwrap();
   malformed.extend_from_slice(&wire_u32(20));
   malformed.extend_from_slice(&wire_u32(12));
   malformed.extend_from_slice(&wire_u32(1));
   malformed.extend_from_slice(&wire_u32(99));
   malformed.extend_from_slice(&wire_u32((-7i32) as u32));
   assert_eq!(decode::<CodecCases>(&malformed), Err(CodecError::InvalidEnum));
   assert_eq!(decode_dynamic(&malformed), Err(CodecError::InvalidEnum));

   let mut wrong_length = encode(&CodecCases::default()).unwrap();
   wrong_length.extend_from_slice(&wire_u32(20));
   wrong_length.extend_from_slice(&wire_u32(8));
   wrong_length.extend_from_slice(&wire_u32(1));
   wrong_length.extend_from_slice(&wire_u32((-7i32) as u32));
   assert_eq!(decode::<CodecCases>(&wrong_length), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&wrong_length), Err(CodecError::TypeMismatch));

   let mut invalid_packed_enum = encode(&CodecCases::default()).unwrap();
   invalid_packed_enum.extend_from_slice(&wire_u32(21));
   invalid_packed_enum.extend_from_slice(&wire_u32(4));
   invalid_packed_enum.extend_from_slice(&wire_u32(99));
   assert_eq!(decode::<CodecCases>(&invalid_packed_enum), Err(CodecError::InvalidEnum));
   assert_eq!(decode_dynamic(&invalid_packed_enum), Err(CodecError::InvalidEnum));

   let mut bad_packed_length = encode(&CodecCases::default()).unwrap();
   bad_packed_length.extend_from_slice(&wire_u32(21));
   bad_packed_length.extend_from_slice(&wire_u32(3));
   bad_packed_length.extend_from_slice(&[0, 0, 1]);
   assert_eq!(decode::<CodecCases>(&bad_packed_length), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&bad_packed_length), Err(CodecError::TypeMismatch));
}

#[test]
fn invalid_utf8_strings_are_rejected_by_typed_and_dynamic_decoders() {
   let mut wire = encode(&RootPayload {
      header: Some("four".to_owned()),
      ..RootPayload::default()
   }).unwrap();
   let descriptor_size = read_wire_u32(&wire[..4]) as usize;
   let payload_offset = 8 + descriptor_size + 8;
   let invalid_sequences: &[&[u8]] = &[
      &[0xC0, 0xAF, b'A', b'B'],       // Overlong encoding.
      &[0xE2, 0x82, b'A', b'B'],       // Invalid continuation after a prefix.
      &[0xED, 0xA0, 0x80, b'A'],       // Encoded surrogate.
      &[0xF4, 0x90, 0x80, 0x80],       // Code point above U+10FFFF.
      &[0x80, b'A', b'B', b'C'],       // Isolated continuation byte.
   ];
   for invalid in invalid_sequences {
      wire[payload_offset..payload_offset + 4].copy_from_slice(invalid);
      assert_eq!(decode::<RootPayload>(&wire), Err(CodecError::InvalidUtf8));
      assert_eq!(decode_dynamic(&wire), Err(CodecError::InvalidUtf8));
   }
   wire[payload_offset - 4..payload_offset].copy_from_slice(&wire_u32(2));
   wire[payload_offset] = 0xE2;
   wire[payload_offset + 1] = 0x82;
   let truncated = &wire[..payload_offset + 2];
   assert_eq!(decode::<RootPayload>(truncated), Err(CodecError::InvalidUtf8));
   assert_eq!(decode_dynamic(truncated), Err(CodecError::InvalidUtf8));
}

#[test]
fn noncanonical_boolean_payload_is_rejected() {
   let mut wire = encode(&codec_cases()).unwrap();
   wire.extend_from_slice(&wire_u32(17));
   wire.extend_from_slice(&wire_u32(1));
   wire.push(2);
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::InvalidBoolean));
   assert_eq!(decode_dynamic(&wire), Err(CodecError::InvalidBoolean));

   let mut packed_wire = encode(&CodecCases::default()).unwrap();
   packed_wire.extend_from_slice(&wire_u32(22));
   packed_wire.extend_from_slice(&wire_u32(1));
   packed_wire.push(2);
   assert_eq!(decode::<CodecCases>(&packed_wire), Err(CodecError::InvalidBoolean));
   assert_eq!(decode_dynamic(&packed_wire), Err(CodecError::InvalidBoolean));
}

#[test]
fn zero_length_packed_occurrence_decodes_as_an_empty_array() {
   let mut wire = encode(&CodecCases::default()).unwrap();
   wire.extend_from_slice(&wire_u32(15));
   wire.extend_from_slice(&wire_u32(0));
   let decoded: CodecCases = decode(&wire).unwrap();
   assert!(decoded.points.is_empty());
   let dynamic = decode_dynamic(&wire).unwrap();
   assert_eq!(dynamic.fields.get("points"), Some(&DynamicValue::Array(Vec::new())));
}

#[test]
fn packed_field_occurrences_concatenate_in_wire_order() {
   let first = Complex32 { real: 1.25, imag: -2.5 };
   let second = Complex32 { real: 3.5, imag: 4.75 };
   let mut input = CodecCases::default();
   input.points.push(first);
   let mut wire = encode(&input).unwrap();
   write_packed_field(15, &[second], &mut wire).unwrap();
   wire.extend_from_slice(&wire_u32(15));
   wire.extend_from_slice(&wire_u32(0));

   let decoded: CodecCases = decode(&wire).unwrap();
   assert_eq!(decoded.points, vec![first, second]);
   let dynamic = decode_dynamic(&wire).unwrap();
   assert_eq!(dynamic.fields.get("points"), Some(&DynamicValue::Array(vec![
      DynamicValue::Complex32(first), DynamicValue::Complex32(second),
   ])));
}

#[test]
fn packed_field_rejects_non_multiple_element_length() {
   let mut wire = encode(&codec_cases()).unwrap();
   wire.extend_from_slice(&wire_u32(15));
   wire.extend_from_slice(&wire_u32(1));
   wire.push(0);
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&wire), Err(CodecError::TypeMismatch));

   let mut valid_after_bad_repeated = encode(&CodecCases::default()).unwrap();
   valid_after_bad_repeated.extend_from_slice(&wire_u32(12));
   valid_after_bad_repeated.extend_from_slice(&wire_u32(1));
   valid_after_bad_repeated.push(0);
   write_field(12, &123i16, &mut valid_after_bad_repeated).unwrap();
   assert_eq!(decode::<CodecCases>(&valid_after_bad_repeated), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&valid_after_bad_repeated), Err(CodecError::TypeMismatch));

   let mut valid_after_bad_packed = encode(&CodecCases::default()).unwrap();
   valid_after_bad_packed.extend_from_slice(&wire_u32(15));
   valid_after_bad_packed.extend_from_slice(&wire_u32(1));
   valid_after_bad_packed.push(0);
   write_packed_field(15, &[Complex32 { real: 1.25, imag: -2.5 }],
      &mut valid_after_bad_packed).unwrap();
   assert_eq!(decode::<CodecCases>(&valid_after_bad_packed), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&valid_after_bad_packed), Err(CodecError::TypeMismatch));
}

#[test]
fn wrong_type_hash_is_rejected() {
   let mut wire = encode(&codec_cases()).unwrap();
   let descriptor_size = read_wire_u32(&wire[..4]) as usize;
   wire[4 + descriptor_size] ^= 0x80;
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::TypeMismatch));
   assert_eq!(decode_dynamic(&wire), Err(CodecError::DescriptorHashMismatch));
}

#[test]
fn dynamic_descriptors_reject_fixed_arrays_of_variable_size_messages() {
   let descriptor: &[u8] = &[

      0x53, 0x44, 0x44, 0x31, 0x00, 0x06, 0x50, 0x61,
      0x63, 0x6B, 0x65, 0x74, 0x00, 0x02, 0x00, 0x05,
      0x43, 0x68, 0x69, 0x6C, 0x64, 0x00, 0x01, 0x00,
      0x00, 0x00, 0x01, 0x00, 0x04, 0x74, 0x65, 0x78,
      0x74, 0x00, 0x00, 0x06, 0x73, 0x74, 0x72, 0x69,
      0x6E, 0x67, 0x00, 0x00, 0x06, 0x50, 0x61, 0x63,
      0x6B, 0x65, 0x74, 0x00, 0x01, 0x00, 0x00, 0x00,
      0x01, 0x00, 0x05, 0x69, 0x74, 0x65, 0x6D, 0x73,
      0x00, 0x00, 0x05, 0x43, 0x68, 0x69, 0x6C, 0x64,
      0x01, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00,
   ];
   let hash = descriptor.iter().fold(2166136261u32, |hash, byte|
      (hash ^ u32::from(*byte)).wrapping_mul(16777619));
   let mut frame = wire_u32(descriptor.len() as u32).to_vec();
   frame.extend_from_slice(descriptor);
   frame.extend_from_slice(&wire_u32(hash));
   assert_eq!(decode_dynamic(&frame), Err(CodecError::InvalidDescriptor));
}

fn append_descriptor_text(output: &mut Vec<u8>, value: &str) {
   output.extend_from_slice(&(value.len() as u16).to_be_bytes());
   output.extend_from_slice(value.as_bytes());
}

fn single_field_descriptor_frame(type_name: &str, modifier: u8,
   dimensions: &[u32]) -> Vec<u8> {
   let mut descriptor = b"SDD1".to_vec();
   append_descriptor_text(&mut descriptor, "Packet");
   descriptor.extend_from_slice(&1u16.to_be_bytes());
   append_descriptor_text(&mut descriptor, "Packet");
   descriptor.extend_from_slice(&1u16.to_be_bytes());
   descriptor.extend_from_slice(&1u32.to_be_bytes());
   append_descriptor_text(&mut descriptor, "value");
   descriptor.push(modifier);
   append_descriptor_text(&mut descriptor, type_name);
   descriptor.push(dimensions.len() as u8);
   for dimension in dimensions {
      descriptor.extend_from_slice(&dimension.to_be_bytes());
   }
   descriptor.extend_from_slice(&0u16.to_be_bytes());
   let hash = descriptor.iter().fold(2166136261u32, |hash, byte|
      (hash ^ u32::from(*byte)).wrapping_mul(16777619));
   let mut frame = wire_u32(descriptor.len() as u32).to_vec();
   frame.extend_from_slice(&descriptor);
   frame.extend_from_slice(&wire_u32(hash));
   frame
}

#[test]
fn empty_message_round_trip() {
   let input = EmptyMessage {};
   let wire = encode(&input).unwrap();
   assert_eq!(wire.len(), 8 + EmptyMessage::DESCRIPTOR.len());
   assert_eq!(decode::<EmptyMessage>(&wire).unwrap(), input);
   let dynamic = decode_dynamic(&wire).unwrap();
   assert_eq!(dynamic.type_name, "EmptyMessage");
   assert!(dynamic.fields.is_empty());
}

#[test]
fn dynamic_descriptors_reject_empty_enums() {
   let mut descriptor = b"SDD1".to_vec();
   append_descriptor_text(&mut descriptor, "Packet");
   descriptor.extend_from_slice(&1u16.to_be_bytes());
   append_descriptor_text(&mut descriptor, "Packet");
   descriptor.extend_from_slice(&0u16.to_be_bytes());
   descriptor.extend_from_slice(&1u16.to_be_bytes());
   append_descriptor_text(&mut descriptor, "State");
   descriptor.extend_from_slice(&0u16.to_be_bytes());
   let hash = descriptor.iter().fold(2166136261u32, |hash, byte|
      (hash ^ u32::from(*byte)).wrapping_mul(16777619));
   let mut frame = wire_u32(descriptor.len() as u32).to_vec();
   frame.extend_from_slice(&descriptor);
   frame.extend_from_slice(&wire_u32(hash));
   assert_eq!(decode_dynamic(&frame), Err(CodecError::InvalidDescriptor));
}

#[test]
fn dynamic_descriptors_reject_builtin_type_name_collisions() {
   fn collision_frame(type_name: &str, as_enum: bool) -> Vec<u8> {
      let mut descriptor = b"SDD1".to_vec();
      append_descriptor_text(&mut descriptor, "Packet");
      let mut messages = if as_enum {
         vec!["Packet"]
      } else {
         vec!["Packet", type_name]
      };
      messages.sort();
      descriptor.extend_from_slice(&(messages.len() as u16).to_be_bytes());
      for name in messages {
         append_descriptor_text(&mut descriptor, name);
         descriptor.extend_from_slice(&0u16.to_be_bytes());
      }
      descriptor.extend_from_slice(&(if as_enum { 1u16 } else { 0u16 }).to_be_bytes());
      if as_enum {
         append_descriptor_text(&mut descriptor, type_name);
         descriptor.extend_from_slice(&1u16.to_be_bytes());
         append_descriptor_text(&mut descriptor, "VALUE");
         descriptor.extend_from_slice(&1i32.to_be_bytes());
      }
      let hash = descriptor.iter().fold(2166136261u32, |hash, byte|
         (hash ^ u32::from(*byte)).wrapping_mul(16777619));
      let mut frame = wire_u32(descriptor.len() as u32).to_vec();
      frame.extend_from_slice(&descriptor);
      frame.extend_from_slice(&wire_u32(hash));
      frame
   }

   assert_eq!(decode_dynamic(&collision_frame("int32", false)),
      Err(CodecError::InvalidDescriptor));
   assert_eq!(decode_dynamic(&collision_frame("string", true)),
      Err(CodecError::InvalidDescriptor));
}

#[test]
fn dynamic_descriptors_reject_invalid_fixed_layouts() {
   let cases: [(&str, u8, &[u32]); 3] = [
      ("string", 3, &[]),
      ("int8", 0, &[0]),
      ("int16", 0, &[u32::MAX]),
   ];
   for (type_name, modifier, dimensions) in cases {
      let frame = single_field_descriptor_frame(type_name, modifier, dimensions);
      assert!(decode_dynamic(&frame).is_err());
   }
}
