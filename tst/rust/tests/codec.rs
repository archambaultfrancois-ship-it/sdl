use sdl_runtime::{decode, decode_dynamic, display, encode, CodecError, Complex32,
   Complex64, DynamicValue, SdlMessage};
use sdl_schema_tests::codec_cases::{CodecCases, State};
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
}

#[test]
fn s03_invalid_frame_and_field_lengths_are_rejected() {
   let valid = encode(&codec_cases()).unwrap();
   let mut bad_descriptor_length = valid.clone();
   bad_descriptor_length[..4].copy_from_slice(&wire_u32(u32::MAX));
   assert_eq!(decode::<CodecCases>(&bad_descriptor_length), Err(CodecError::Truncated));
   assert_eq!(decode_dynamic(&bad_descriptor_length), Err(CodecError::Truncated));

   let descriptor_size = read_wire_u32(&valid[..4]) as usize;
   let body_offset = 8 + descriptor_size;
   let mut bad_field_length = valid.clone();
   bad_field_length[body_offset + 4..body_offset + 8]
      .copy_from_slice(&wire_u32(u32::MAX));
   assert_eq!(decode::<CodecCases>(&bad_field_length), Err(CodecError::Truncated));
   assert_eq!(decode_dynamic(&bad_field_length), Err(CodecError::Truncated));
   assert_eq!(decode::<CodecCases>(&valid[..7]), Err(CodecError::Truncated));
   assert_eq!(decode_dynamic(&valid[..7]), Err(CodecError::Truncated));
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
   let dynamic = decode_dynamic(&wire).unwrap();
   assert_eq!(dynamic.fields.get("state"), Some(&DynamicValue::Enum {
      type_name: "State".to_owned(), name: "READY".to_owned(), value: 1,
   }));
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
   extended.extend_from_slice(&wire_u32(999));
   extended.extend_from_slice(&wire_u32(3));
   extended.extend_from_slice(&[0xA1, 0xB2, 0xC3]);
   let decoded: CodecCases = decode(&extended).unwrap();
   assert_eq!(decoded, codec_cases());
   assert_eq!(decode::<CodecCases>(&wire[..wire.len() - 1]), Err(CodecError::Truncated));
}

#[test]
fn noncanonical_boolean_payload_is_rejected() {
   let mut wire = encode(&codec_cases()).unwrap();
   wire.extend_from_slice(&wire_u32(17));
   wire.extend_from_slice(&wire_u32(1));
   wire.push(2);
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::InvalidBoolean));
}

#[test]
fn packed_field_rejects_non_multiple_element_length() {
   let mut wire = encode(&codec_cases()).unwrap();
   wire.extend_from_slice(&wire_u32(15));
   wire.extend_from_slice(&wire_u32(1));
   wire.push(0);
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::TypeMismatch));
}

#[test]
fn wrong_type_hash_is_rejected() {
   let mut wire = encode(&codec_cases()).unwrap();
   let descriptor_size = read_wire_u32(&wire[..4]) as usize;
   wire[4 + descriptor_size] ^= 0x80;
   assert_eq!(decode::<CodecCases>(&wire), Err(CodecError::TypeMismatch));
}
