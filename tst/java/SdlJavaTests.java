import java.io.File;
import java.io.FileInputStream;
import java.io.ByteArrayOutputStream;
import java.util.ArrayList;
import java.util.Arrays;

public final class SdlJavaTests {
   private static void check(boolean value,String message){if(!value)throw new AssertionError(message);}
   private static byte[] readFile(String name)throws Exception{FileInputStream in=new FileInputStream(new File(name));ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] b=new byte[1024];int n;while((n=in.read(b))>=0)out.write(b,0,n);in.close();return out.toByteArray();}
   private static void fails(Runnable action){boolean failed=false;try{action.run();}catch(RuntimeException e){failed=true;}check(failed,"expected codec failure");}
   private static byte[] wireInt(long value,int size){byte[] out=new byte[size];boolean little="little".equalsIgnoreCase(System.getenv("SDL_WIRE_ENDIAN"));for(int i=0;i<size;i++){int shift=(little?i:size-1-i)*8;out[i]=(byte)(value>>>shift);}return out;}
   private static byte[] field(long id,byte[] payload){ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] a=wireInt(id,4),b=wireInt(payload.length,4);out.write(a,0,a.length);out.write(b,0,b.length);out.write(payload,0,payload.length);return out.toByteArray();}
   private static byte[] concat(byte[]... arrays){ByteArrayOutputStream out=new ByteArrayOutputStream();for(byte[] a:arrays)out.write(a,0,a.length);return out.toByteArray();}
   private static byte[] complex32(float re,float im){return concat(wireInt(Float.floatToIntBits(re),4),wireInt(Float.floatToIntBits(im),4));}
   public static void main(String[] args)throws Exception{
      codec_cases.CodecCases v=new codec_cases.CodecCases();
      v.tiny=Byte.valueOf((byte)-128);v.small=Short.valueOf((short)32767);v.signed_value=Integer.valueOf(-2147483647);
      v.wide=Long.valueOf(Long.MIN_VALUE+9);v.ratio=Float.valueOf(1.25f);v.precise=Double.valueOf(1.0/7.0);
      v.point=new SdlCodec.Complex32(1.5f,-2.25f);v.position=new SdlCodec.Complex64(-3.5,4.75);
      v.state=codec_cases.State.NEGATIVE;v.empty_text="";v.required_zero=Integer.valueOf(0);
      v.samples.add(Short.valueOf((short)-32768));v.samples.add(Short.valueOf((short)32767));
      v.measurements.add(Double.valueOf(0.125));v.measurements.add(Double.valueOf(-1024.5));
      v.labels.add("");v.labels.add("été 🌍");v.points=new SdlCodec.Complex32Array(new float[]{1.0f,-3.0f},new float[]{2.0f,4.5f});
      v.required_enabled=Boolean.TRUE;v.optional_enabled=Boolean.FALSE;v.bool_flags.add(Boolean.TRUE);v.bool_flags.add(Boolean.FALSE);
      v.fixed_states=new codec_cases.State[]{codec_cases.State.UNKNOWN,codec_cases.State.READY,codec_cases.State.NEGATIVE};
      v.packed_states.add(codec_cases.State.MINIMUM);v.packed_states.add(codec_cases.State.MAXIMUM);v.packed_flags.add(Boolean.FALSE);v.packed_flags.add(Boolean.TRUE);v.high_id_value=Integer.valueOf(42);
      codec_cases.CodecCases decoded=codec_cases.CodecCases.decode(v.encode());
      check(decoded.tiny.equals(v.tiny)&&decoded.small.equals(v.small)&&decoded.signed_value.equals(v.signed_value)&&decoded.wide.equals(v.wide),"integer round trip");
      check(decoded.ratio.equals(v.ratio)&&decoded.precise.equals(v.precise)&&decoded.point.equals(v.point)&&decoded.position.equals(v.position),"float and complex round trip");
      check(decoded.state==v.state&&decoded.labels.equals(v.labels)&&decoded.samples.equals(v.samples),"enum/string/repeated round trip");
      check(Arrays.equals(decoded.points.re,v.points.re)&&Arrays.equals(decoded.points.im,v.points.im)&&decoded.packed_states.equals(v.packed_states)&&decoded.packed_flags.equals(v.packed_flags),"packed round trip");
      check(Arrays.equals(decoded.fixed_states,v.fixed_states)&&decoded.high_id_value.equals(Integer.valueOf(42)),"fixed and high field id round trip");
      schema.RootPayload fixtureValue=new schema.RootPayload();fixtureValue.header="Mission_Data_Packet";
      schema.FixedItem fx1=new schema.FixedItem();fx1.x=1.1f;fx1.y=2.2f;schema.FixedItem fx2=new schema.FixedItem();fx2.x=3.3f;fx2.y=4.4f;
      fixtureValue.fixed_array.add(fx1);fixtureValue.fixed_array.add(fx2);
      schema.VarItem va=new schema.VarItem();va.name="Variable_Node_A";va.id=Long.valueOf(99999);schema.VarItem vb=new schema.VarItem();vb.name="Variable_Node_B";vb.id=Long.valueOf(77777);fixtureValue.var_array.add(va);fixtureValue.var_array.add(vb);
      String fixture="little".equalsIgnoreCase(System.getenv("SDL_WIRE_ENDIAN"))?"tst/fixtures/root_payload.bin":"tst/fixtures/root_payload_be.bin";
      check(Arrays.equals(fixtureValue.encode(),readFile(fixture)),"cross-language golden fixture");

      codec_cases.CodecCases defaults=new codec_cases.CodecCases();
      check(defaults.tiny==null&&defaults.optional_enabled==null&&defaults.samples.isEmpty()&&defaults.points.size()==0,"defaults");
      codec_cases.CodecCases empty=codec_cases.CodecCases.decodePayload(new byte[0]);check(empty.required_zero.intValue()==0&&!empty.required_enabled.booleanValue(),"missing required defaults");
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decode(new byte[3]);}});
      final byte[] frame=v.encode();final byte[] bad=frame.clone();bad[4+codec_cases.CodecCases.SDL_DESCRIPTOR.length]^=1;fails(new Runnable(){public void run(){codec_cases.CodecCases.decode(bad);}});
      fails(new Runnable(){public void run(){codec_cases.EnumRecordBatch.decode(frame);}});
      codec_cases.CodecCases unknown=codec_cases.CodecCases.decodePayload(field(999,new byte[]{1,2,3}));check(unknown.required_zero.intValue()==0,"unknown field skipped");
      byte[] duplicate=concat(field(11,wireInt(7,4)),field(11,wireInt(-42,4)),field(12,wireInt(-9,2)),field(12,wireInt(1234,2)));
      codec_cases.CodecCases duplicateOut=codec_cases.CodecCases.decodePayload(duplicate);check(duplicateOut.required_zero.intValue()==-42&&duplicateOut.samples.size()==2&&duplicateOut.samples.get(1).shortValue()==1234,"duplicate field rules");
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(new byte[]{1,2,3});}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(1,new byte[]{1,2}));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(17,new byte[]{2}));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(9,wireInt(99,4)));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(15,new byte[]{1,2,3}));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(20,new byte[8]));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(10,new byte[]{(byte)0xff}));}});
      codec_cases.CodecCases packedOccurrences=codec_cases.CodecCases.decodePayload(concat(field(22,new byte[0]),field(22,new byte[]{0}),field(22,new byte[]{1})));
      check(packedOccurrences.packed_flags.size()==2&&!packedOccurrences.packed_flags.get(0).booleanValue()&&packedOccurrences.packed_flags.get(1).booleanValue(),"packed field occurrences concatenate");
      codec_cases.CodecCases complexOccurrences=codec_cases.CodecCases.decodePayload(concat(field(15,complex32(1.25f,-2.5f)),field(15,complex32(3.5f,4.75f))));
      check(Arrays.equals(complexOccurrences.points.re,new float[]{1.25f,3.5f})&&Arrays.equals(complexOccurrences.points.im,new float[]{-2.5f,4.75f}),"packed complex occurrences concatenate in SoA form");
      fails(new Runnable(){public void run(){new SdlCodec.Complex32Array(new float[]{1.0f},new float[0]);}});
      check(empty_message.EmptyMessage.decode(new empty_message.EmptyMessage().encode())!=null,"empty message frame");

      schema.RootPayload root=new schema.RootPayload();root.header="java";
      schema.FixedItem a=new schema.FixedItem();a.x=1.25f;a.y=-2.5f;schema.FixedItem b=new schema.FixedItem();b.x=3.5f;b.y=4.75f;root.fixed_array.add(a);root.fixed_array.add(b);
      schema.RootPayload rootOut=schema.RootPayload.decode(root.encode());check(rootOut.fixed_array.size()==2&&rootOut.fixed_array.get(1).y.equals(4.75f),"packed struct round trip");
      schema.FixedRowBatch rows=new schema.FixedRowBatch();schema.FixedRow row=new schema.FixedRow();
      for(int i=0;i<2;i++){schema.FixedVector vector=new schema.FixedVector();vector.coords=new Float[]{(float)(i+1),(float)(i+2)};vector.grid=new Short[][]{{1,2,3},{4,5,6}};row.vectors[i]=vector;}
      rows.rows.add(row);schema.FixedRowBatch rowsOut=schema.FixedRowBatch.decode(rows.encode());check(rowsOut.rows.get(0).vectors[1].grid[1][2].shortValue()==6,"nested packed fixed arrays");
      schema.AnonymousEnvelope env=new schema.AnonymousEnvelope();env.metadata.code=17;env.metadata.detail=new schema.AnonymousEnvelope_2();env.metadata.detail.text="nested";
      schema.AnonymousEnvelope envOut=schema.AnonymousEnvelope.decode(env.encode());check(envOut.metadata.detail.text.equals("nested"),"anonymous struct round trip");
      check(v.toString().indexOf("CodecCases")>=0,"display");
      testComplex32SoaRoundTrip();
      testComplex32EmptyRoundTrip();
      testComplex32RejectsUnequalConstructorArrays();
      testComplex32RejectsUnequalEncodeArrays();
      testComplex32RejectsMalformedPackedLength();
      testComplex32ConcatenatesPackedOccurrences();
      testComplex32IgnoresEmptyOccurrence();
      testComplex32WireComponentOrder();
      testComplexScalarsKeepObjectRepresentation();
      testComplex32FloatingPointEdges();
      testStandardIntegerBoundaries();
      testStandardNegativeAndBoundaryEnums();
      testStandardUnicodeAndEmbeddedNulString();
      testStandardSpecialAndSubnormalFloats();
      testStandardFieldsDecodeInReverseOrder();
      testStandardInvalidPrimitiveWidths();
      testStandardMissingAndUnknownFields();
      testStandardSingularDuplicatesAndRepeatedAppend();
      testStandardOptionalAndRepeatedEmptyValues();
      testStandardInvalidBooleanEnumAndUtf8();
      testStandardFrameValidation();
      testStandardRepeatedBooleansAndPackedEnums();
      testStandardMalformedComplexScalars();
      testStandardTruncatedFieldHeaders();
      testStandardUnknownFieldsAroundKnownFields();
      testStandardRequiredAndOptionalBooleanValues();
      testStandardDisplayFormatting();
      testStandardFixedEnumsRejectInvalidValuesAndLengths();
      testStandardPackedStructEnumValidation();
      testStandardMalformedEarlierDuplicateIsRejected();
      testStandardUtf8InvalidSequences();
      testStandardFrameDescriptorLengthLimits();
      testStandardEmptyMessageFrameLayout();
      System.out.println("Java codec tests passed (wire endian: "+System.getenv("SDL_WIRE_ENDIAN")+")");
   }

   private static void testComplex32SoaRoundTrip(){
      codec_cases.CodecCases value=new codec_cases.CodecCases();
      value.points=new SdlCodec.Complex32Array(new float[]{1.25f,-2.5f},new float[]{-3.75f,4.5f});
      codec_cases.CodecCases result=codec_cases.CodecCases.decode(value.encode());
      check(Arrays.equals(result.points.re,value.points.re)&&Arrays.equals(result.points.im,value.points.im),"unit: packed c32 SoA round trip");
   }
   private static void testComplex32EmptyRoundTrip(){
      codec_cases.CodecCases result=codec_cases.CodecCases.decode(new codec_cases.CodecCases().encode());
      check(result.points.size()==0&&result.points.re.length==0&&result.points.im.length==0,"unit: empty packed c32");
   }
   private static void testComplex32RejectsUnequalConstructorArrays(){
      fails(new Runnable(){public void run(){new SdlCodec.Complex32Array(new float[]{1.0f},new float[0]);}});
   }
   private static void testComplex32RejectsUnequalEncodeArrays(){
      final codec_cases.CodecCases value=new codec_cases.CodecCases();
      value.points=new SdlCodec.Complex32Array();
      value.points.im=new float[]{1.0f};
      fails(new Runnable(){public void run(){value.encodePayload();}});
   }
   private static void testComplex32RejectsMalformedPackedLength(){
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(15,new byte[7]));}});
   }
   private static void testComplex32ConcatenatesPackedOccurrences(){
      byte[] payload=concat(field(15,complex32(1.0f,2.0f)),field(15,complex32(3.0f,4.0f)));
      SdlCodec.Complex32Array values=codec_cases.CodecCases.decodePayload(payload).points;
      check(Arrays.equals(values.re,new float[]{1.0f,3.0f})&&Arrays.equals(values.im,new float[]{2.0f,4.0f}),"unit: packed c32 occurrences append");
   }
   private static void testComplex32IgnoresEmptyOccurrence(){
      SdlCodec.Complex32Array values=codec_cases.CodecCases.decodePayload(field(15,new byte[0])).points;
      check(values.size()==0,"unit: zero-length packed c32 occurrence");
   }
   private static void testComplex32WireComponentOrder(){
      codec_cases.CodecCases value=new codec_cases.CodecCases();value.points=new SdlCodec.Complex32Array(new float[]{1.25f},new float[]{-2.5f});
      byte[] expected=concat(field(11,wireInt(0,4)),field(15,complex32(1.25f,-2.5f)),field(17,new byte[]{0}),field(20,concat(wireInt(0,4),wireInt(0,4),wireInt(0,4))));
      check(Arrays.equals(value.encodePayload(),expected),"unit: packed c32 re/im wire order");
   }
   private static void testComplexScalarsKeepObjectRepresentation(){
      codec_cases.CodecCases value=new codec_cases.CodecCases();value.point=new SdlCodec.Complex32(2.0f,-5.0f);value.position=new SdlCodec.Complex64(7.0,-9.0);
      codec_cases.CodecCases result=codec_cases.CodecCases.decode(value.encode());
      check(result.point.equals(value.point)&&result.position.equals(value.position),"unit: scalar complex values remain objects");
   }
   private static void testComplex32FloatingPointEdges(){
      codec_cases.CodecCases value=new codec_cases.CodecCases();
      value.points=new SdlCodec.Complex32Array(new float[]{-0.0f,Float.NaN,Float.POSITIVE_INFINITY},new float[]{Float.POSITIVE_INFINITY,Float.NEGATIVE_INFINITY,-0.0f});
      SdlCodec.Complex32Array result=codec_cases.CodecCases.decode(value.encode()).points;
      check(Float.floatToRawIntBits(result.re[0])==Float.floatToRawIntBits(-0.0f),"unit: packed c32 preserves negative zero");
      check(Float.isNaN(result.re[1])&&result.im[1]==Float.NEGATIVE_INFINITY,"unit: packed c32 preserves NaN/infinity");
      check(result.re[2]==Float.POSITIVE_INFINITY&&Float.floatToRawIntBits(result.im[2])==Float.floatToRawIntBits(-0.0f),"unit: packed c32 edge components");
   }
   private static void testStandardIntegerBoundaries(){
      int[][] values={{-128,-32768,Integer.MIN_VALUE},{127,32767,Integer.MAX_VALUE}};
      long[] wide={Long.MIN_VALUE,Long.MAX_VALUE};
      for(int i=0;i<values.length;i++){
         codec_cases.CodecCases input=new codec_cases.CodecCases();
         input.tiny=Byte.valueOf((byte)values[i][0]);input.small=Short.valueOf((short)values[i][1]);
         input.signed_value=Integer.valueOf(values[i][2]);input.wide=Long.valueOf(wide[i]);input.high_id_value=Integer.valueOf(values[i][2]);
         codec_cases.CodecCases result=codec_cases.CodecCases.decode(input.encode());
         check(result.tiny.equals(input.tiny)&&result.small.equals(input.small)&&result.signed_value.equals(input.signed_value)&&result.wide.equals(input.wide)&&result.high_id_value.equals(input.high_id_value),"unit: standard integer min/max round trip");
      }
   }
   private static void testStandardNegativeAndBoundaryEnums(){
      codec_cases.State[] states={codec_cases.State.NEGATIVE,codec_cases.State.MINIMUM,codec_cases.State.MAXIMUM};
      for(codec_cases.State state:states){codec_cases.CodecCases input=new codec_cases.CodecCases();input.state=state;check(codec_cases.CodecCases.decode(input.encode()).state==state,"unit: enum negative/int32 boundary round trip");}
   }
   private static void testStandardUnicodeAndEmbeddedNulString(){
      String value="SDL-é-📦-".repeat(128)+"\u0000tail";
      schema.RootPayload input=new schema.RootPayload();input.header=value;
      check(schema.RootPayload.decode(input.encode()).header.equals(value),"unit: long Unicode string with embedded NUL");
   }
   private static void testStandardSpecialAndSubnormalFloats(){
      codec_cases.CodecCases input=new codec_cases.CodecCases();input.ratio=Float.valueOf(Float.NEGATIVE_INFINITY);input.precise=Double.valueOf(Double.NaN);
      input.point=new SdlCodec.Complex32(Float.NEGATIVE_INFINITY,Float.NaN);input.position=new SdlCodec.Complex64(Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY);
      codec_cases.CodecCases result=codec_cases.CodecCases.decode(input.encode());
      check(result.ratio.floatValue()==Float.NEGATIVE_INFINITY&&Double.isNaN(result.precise.doubleValue()),"unit: standard float infinities and NaN");
      check(result.point.real==Float.NEGATIVE_INFINITY&&Float.isNaN(result.point.imag)&&result.position.real==Double.POSITIVE_INFINITY&&result.position.imag==Double.NEGATIVE_INFINITY,"unit: scalar complex special values");
      input.ratio=Float.valueOf(Float.intBitsToFloat(1));input.precise=Double.valueOf(Double.longBitsToDouble(1));
      input.point=new SdlCodec.Complex32(-Float.intBitsToFloat(1),-0.0f);input.position=new SdlCodec.Complex64(Double.longBitsToDouble(1),-0.0);
      result=codec_cases.CodecCases.decode(input.encode());
      check(Float.floatToRawIntBits(result.ratio.floatValue())==1&&Double.doubleToRawLongBits(result.precise.doubleValue())==1L,"unit: smallest subnormal scalar values");
      check(Float.floatToRawIntBits(result.point.imag)==Float.floatToRawIntBits(-0.0f)&&Double.doubleToRawLongBits(result.position.imag)==Double.doubleToRawLongBits(-0.0),"unit: scalar complex preserves negative zero");
   }
   private static void testStandardFieldsDecodeInReverseOrder(){
      codec_cases.CodecCases input=new codec_cases.CodecCases();input.tiny=Byte.valueOf((byte)-17);input.signed_value=Integer.valueOf(0x12345678);
      input.labels.add("first");input.labels.add("second");
      byte[] reversed=concat(field(3,wireInt(0x12345678,4)),field(12,wireInt(0x1234,2)),field(12,wireInt(0x5678,2)),field(1,wireInt(-17,1)));
      codec_cases.CodecCases result=codec_cases.CodecCases.decodePayload(reversed);
      check(result.tiny.byteValue()==-17&&result.signed_value.intValue()==0x12345678&&result.samples.size()==2&&result.samples.get(1).shortValue()==0x5678,"unit: standard fields decode independent of field order");
   }
   private static void testStandardInvalidPrimitiveWidths(){
      final int[][] cases={{1,0},{1,2},{3,3},{3,5},{4,7},{4,9},{8,15},{8,17}};
      for(final int[] test:cases)fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(test[0],new byte[test[1]]));}});
   }
   private static void testStandardMissingAndUnknownFields(){
      codec_cases.CodecCases missing=codec_cases.CodecCases.decodePayload(new byte[0]);
      check(missing.required_zero.intValue()==0&&!missing.required_enabled.booleanValue()&&missing.samples.isEmpty(),"unit: absent required and repeated field defaults");
      codec_cases.CodecCases unknown=codec_cases.CodecCases.decodePayload(field(123456,new byte[]{1,2,3}));
      check(unknown.required_zero.intValue()==0&&unknown.labels.isEmpty(),"unit: unknown field payload skipped");
   }
   private static void testStandardSingularDuplicatesAndRepeatedAppend(){
      byte[] payload=concat(field(11,wireInt(1,4)),field(12,wireInt(-2,2)),field(11,wireInt(-99,4)),field(12,wireInt(300,2)));
      codec_cases.CodecCases result=codec_cases.CodecCases.decodePayload(payload);
      check(result.required_zero.intValue()==-99&&result.samples.size()==2&&result.samples.get(0).shortValue()==-2&&result.samples.get(1).shortValue()==300,"unit: last singular wins and repeated fields append in wire order");
   }
   private static void testStandardOptionalAndRepeatedEmptyValues(){
      codec_cases.CodecCases absent=codec_cases.CodecCases.decodePayload(new byte[0]);
      check(absent.tiny==null&&absent.empty_text==null&&absent.optional_enabled==null,"unit: absent optional fields stay null");
      codec_cases.CodecCases result=codec_cases.CodecCases.decodePayload(concat(field(10,new byte[0]),field(14,new byte[0]),field(14,new byte[0])));
      check("".equals(result.empty_text)&&result.labels.size()==2&&"".equals(result.labels.get(0))&&"".equals(result.labels.get(1)),"unit: empty scalar and repeated strings are retained");
      codec_cases.CodecCases encoded=new codec_cases.CodecCases();encoded.empty_text="";encoded.labels.add("");
      codec_cases.CodecCases roundTrip=codec_cases.CodecCases.decode(encoded.encode());
      check("".equals(roundTrip.empty_text)&&roundTrip.labels.size()==1&&"".equals(roundTrip.labels.get(0)),"unit: empty strings encode and decode");
   }
   private static void testStandardInvalidBooleanEnumAndUtf8(){
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(17,new byte[]{2}));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(9,wireInt(123,4)));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(10,new byte[]{(byte)0xc3,0x28}));}});
   }
   private static void testStandardFrameValidation(){
      final byte[] valid=new codec_cases.CodecCases().encode();
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decode(new byte[7]);}});
      final byte[] truncated=valid.clone();truncated[0]=127;
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decode(truncated);}});
      final byte[] wrongHash=valid.clone();wrongHash[4+codec_cases.CodecCases.SDL_DESCRIPTOR.length]^=1;
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decode(wrongHash);}});
      fails(new Runnable(){public void run(){codec_cases.EnumRecordBatch.decode(valid);}});
   }
   private static void testStandardRepeatedBooleansAndPackedEnums(){
      byte[] payload=concat(field(19,new byte[]{1}),field(19,new byte[]{0}),field(21,concat(wireInt(-7,4),wireInt(1,4))));
      codec_cases.CodecCases result=codec_cases.CodecCases.decodePayload(payload);
      check(result.bool_flags.size()==2&&result.bool_flags.get(0).booleanValue()&&!result.bool_flags.get(1).booleanValue(),"unit: repeated scalar booleans preserve order");
      check(result.packed_states.size()==2&&result.packed_states.get(0)==codec_cases.State.NEGATIVE&&result.packed_states.get(1)==codec_cases.State.READY,"unit: packed enum values decode");
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(21,wireInt(12345,4)));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(22,new byte[]{2}));}});
   }
   private static void testStandardMalformedComplexScalars(){
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(7,new byte[7]));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(8,new byte[15]));}});
      codec_cases.CodecCases value=codec_cases.CodecCases.decodePayload(concat(field(7,complex32(-0.0f,3.25f)),field(8,concat(wireInt(Double.doubleToLongBits(-2.5),8),wireInt(Double.doubleToLongBits(9.0),8)))));
      check(Float.floatToRawIntBits(value.point.real)==Float.floatToRawIntBits(-0.0f)&&value.point.imag==3.25f,"unit: scalar c32 component ordering");
      check(value.position.real==-2.5&&value.position.imag==9.0,"unit: scalar c64 component ordering");
   }
   private static void testStandardTruncatedFieldHeaders(){
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(new byte[]{0,0,0,1,0,0,0});}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(new byte[]{0,0,0,1,0,0,0,4,1});}});
   }
   private static void testStandardUnknownFieldsAroundKnownFields(){
      byte[] payload=concat(field(800,new byte[]{9}),field(1,wireInt(23,1)),field(801,new byte[0]),field(11,wireInt(6,4)));
      codec_cases.CodecCases result=codec_cases.CodecCases.decodePayload(payload);
      check(result.tiny.byteValue()==23&&result.required_zero.intValue()==6,"unit: unknown fields before and between known fields are skipped");
   }
   private static void testStandardRequiredAndOptionalBooleanValues(){
      codec_cases.CodecCases value=new codec_cases.CodecCases();value.required_enabled=Boolean.FALSE;value.optional_enabled=Boolean.TRUE;
      codec_cases.CodecCases result=codec_cases.CodecCases.decode(value.encode());
      check(!result.required_enabled.booleanValue()&&result.optional_enabled.booleanValue(),"unit: required false and optional true booleans round trip");
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(19,new byte[]{(byte)0xff}));}});
   }
   private static void testStandardDisplayFormatting(){
      codec_cases.CodecCases value=new codec_cases.CodecCases();value.tiny=Byte.valueOf((byte)-128);value.state=codec_cases.State.READY;value.samples.add(Short.valueOf((short)-32768));
      String rendered=value.toString();
      check(rendered.startsWith("CodecCases{")&&rendered.indexOf("tiny=-128")>=0&&rendered.indexOf("state=READY")>=0&&rendered.indexOf("samples=[-32768]")>=0,"unit: display includes typed scalar, enum and repeated values");
      check(new codec_cases.CodecCases().toString().indexOf("tiny=null")>=0,"unit: display marks absent optionals");
   }
   private static void testStandardFixedEnumsRejectInvalidValuesAndLengths(){
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(20,concat(wireInt(1,4),wireInt(99,4),wireInt(0,4))));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(20,concat(wireInt(1,4),wireInt(2,4))));}});
   }
   private static void testStandardPackedStructEnumValidation(){
      codec_cases.EnumRecordBatch batch=new codec_cases.EnumRecordBatch();codec_cases.EnumRecord record=new codec_cases.EnumRecord();record.state=codec_cases.State.READY;record.code=Integer.valueOf(42);batch.records.add(record);
      codec_cases.EnumRecordBatch decoded=codec_cases.EnumRecordBatch.decode(batch.encode());
      check(decoded.records.size()==1&&decoded.records.get(0).state==codec_cases.State.READY&&decoded.records.get(0).code.intValue()==42,"unit: packed fixed struct containing enum round trip");
      fails(new Runnable(){public void run(){codec_cases.EnumRecordBatch.decodePayload(field(1,concat(wireInt(99,4),wireInt(42,4))));}});
      fails(new Runnable(){public void run(){codec_cases.EnumRecordBatch.decodePayload(field(1,new byte[7]));}});
   }
   private static void testStandardMalformedEarlierDuplicateIsRejected(){
      final byte[] payload=concat(field(11,new byte[3]),field(11,wireInt(42,4)));
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(payload);}});
      final byte[] packed=concat(field(15,new byte[1]),field(15,complex32(1.0f,2.0f)));
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(packed);}});
   }
   private static void testStandardUtf8InvalidSequences(){
      final byte[][] invalid={{(byte)0xc0,(byte)0xaf},{(byte)0xe2,(byte)0x82,0x41},{(byte)0xed,(byte)0xa0,(byte)0x80},{(byte)0xf4,(byte)0x90,(byte)0x80,(byte)0x80},{(byte)0x80,0x41}};
      for(final byte[] bytes:invalid)fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(10,bytes));}});
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decodePayload(field(10,new byte[]{(byte)0xe2,(byte)0x82}));}});
   }
   private static void testStandardFrameDescriptorLengthLimits(){
      final byte[] valid=new codec_cases.CodecCases().encode();
      final byte[] oversized=valid.clone();byte[] length=wireInt(0xffffffffL,4);System.arraycopy(length,0,oversized,0,4);
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decode(oversized);}});
      final byte[] tooLarge=new byte[valid.length];byte[] max=wireInt(1024*1024+1,4);System.arraycopy(max,0,tooLarge,0,4);
      fails(new Runnable(){public void run(){codec_cases.CodecCases.decode(tooLarge);}});
   }
   private static void testStandardEmptyMessageFrameLayout(){
      empty_message.EmptyMessage value=new empty_message.EmptyMessage();byte[] wire=value.encode();
      check(wire.length==8+empty_message.EmptyMessage.SDL_DESCRIPTOR.length,"unit: empty message frame has descriptor and hash only");
      check(empty_message.EmptyMessage.decode(wire)!=null,"unit: empty message frame decodes");
   }
}
