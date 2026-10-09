import java.nio.file.Files;
import java.nio.file.Paths;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public final class SdlJavaTests {
   static void check(boolean value) {
      if (!value)
         throw new AssertionError();
   }
   interface Action {
      void run();
   }
   static void invalid(Action action) {
      try {
         action.run();
      } catch (SdlCodec.CodecException expected) {
         return;
      }
      throw new AssertionError("invalid input accepted");
   }
   static String graph(int count, boolean shared) {
      StringBuilder text = new StringBuilder("SDL2\n");
      for (int i = 0; i < count; ++i) {
         text.append(String.format("message N%03d {\n", i));
         if (i == 0)
            text.append("  1: required int8 value;\n");
         else {
            text.append(String.format("  1: optional N%03d left;\n", i - 1));
            if (shared)
               text.append(String.format("  2: optional N%03d right;\n", i - 1));
         }
         text.append("}\n");
      }
      return text.toString();
   }
   static int messageId(String description,String name) {
      int id=0;
      for(String line:description.split("\n")) {
         if(line.startsWith("message ")) id++;
         if(line.equals("message "+name+" {")) return id;
      }
      throw new AssertionError("missing type");
   }
   static void packedCodecs() {
      final String desc=packed_validation.PackedBatch.SDL_DESCRIPTOR;
      final SdlCodec.Context ctx=SdlCodec.prepare(desc,packed_validation.PackedBatch.class);
      java.util.Random random=new java.util.Random(137);
      // Independent big-endian wire oracle with arbitrary integer/IEEE-754 bit patterns.
      for(int trial=0;trial<16;trial++) {
         packed_validation.PackedBatch batch=new packed_validation.PackedBatch();
         int count=trial==0 ? 0 : 31;
         java.nio.ByteBuffer oracle=java.nio.ByteBuffer.allocate(2+count*80);
         oracle.put((byte)messageId(desc,"PackedBatch")).put((byte)count);
         for(int i=0;i<count;i++) {
            packed_validation.PackedRecord record=new packed_validation.PackedRecord();
            packed_validation.PackedLeaf leaf=record.leaf;
            leaf.enabled=random.nextBoolean(); oracle.put((byte)(leaf.enabled?1:0));
            leaf.tiny=(byte)random.nextInt(); oracle.put(leaf.tiny);
            leaf.small=(short)random.nextInt(); oracle.putShort(leaf.small);
            leaf.signed_value=random.nextInt(); oracle.putInt(leaf.signed_value);
            leaf.wide=random.nextLong(); oracle.putLong(leaf.wide);
            int bits=random.nextInt(); leaf.ratio=Float.intBitsToFloat(bits); oracle.putInt(bits);
            long wideBits=random.nextLong(); leaf.precise=Double.longBitsToDouble(wideBits); oracle.putLong(wideBits);
            bits=random.nextInt(); leaf.point.real=Float.intBitsToFloat(bits); oracle.putInt(bits);
            bits=random.nextInt(); leaf.point.imag=Float.intBitsToFloat(bits); oracle.putInt(bits);
            wideBits=random.nextLong(); leaf.position.real=Double.longBitsToDouble(wideBits); oracle.putLong(wideBits);
            wideBits=random.nextLong(); leaf.position.imag=Double.longBitsToDouble(wideBits); oracle.putLong(wideBits);
            leaf.state=i%2==0 ? packed_validation.PackedState.ZERO : packed_validation.PackedState.NEGATIVE;
            oracle.putInt(leaf.state.wireValue());
            for(int row=0;row<2;row++) for(int col=0;col<3;col++) {
               bits=random.nextInt(); record.grid[row][col]=Float.intBitsToFloat(bits); oracle.putInt(bits);
            }
            batch.records.add(record);
         }
         final byte[] expected=oracle.array();
         check(Arrays.equals(expected,SdlCodec.encode(ctx,batch)));
         packed_validation.PackedBatch decoded=(packed_validation.PackedBatch)SdlCodec.decode(ctx,expected);
         check(decoded.records.size()==count && Arrays.equals(expected,SdlCodec.encode(ctx,decoded)));
         // Renaming preserves compatibility; changing enum sets must fall back and validate sender values.
         SdlCodec.Context renamed=SdlCodec.prepare(desc.replace("bool enabled;","bool renamed;"),packed_validation.PackedBatch.class);
         check(Arrays.equals(expected,SdlCodec.encode(ctx,SdlCodec.decode(renamed,expected))));
         if(trial==1) {
            for(int n=0;n<expected.length;n++) {
               final byte[] truncated=Arrays.copyOf(expected,n);
               invalid(new Action(){public void run(){SdlCodec.decode(ctx,truncated);}});
            }
            final byte[] extra=Arrays.copyOf(expected,expected.length+1);
            invalid(new Action(){public void run(){SdlCodec.decode(ctx,extra);}});
            final byte[] badBool=expected.clone(); badBool[2+(count-1)*80]=2;
            invalid(new Action(){public void run(){SdlCodec.decode(ctx,badBool);}});
            final byte[] badEnum=expected.clone(); badEnum[2+(count-1)*80+55]=1;
            invalid(new Action(){public void run(){SdlCodec.decode(ctx,badEnum);}});
            final byte[] hostile={(byte)messageId(desc,"PackedBatch"),(byte)255,(byte)255,(byte)255,(byte)255,15};
            invalid(new Action(){public void run(){SdlCodec.decode(ctx,hostile);}});
            final SdlCodec.Context subset=SdlCodec.prepare(desc.replace("  NEGATIVE = -7;\n",""),packed_validation.PackedBatch.class);
            invalid(new Action(){public void run(){SdlCodec.decode(subset,expected);}});
            final SdlCodec.Context superset=SdlCodec.prepare(desc.replace("  NEGATIVE = -7;","  NEGATIVE = -7;\n  EXTRA = 9;"),packed_validation.PackedBatch.class);
            byte[] localInvalid=expected.clone(); localInvalid[57]=9;
            final byte[] badLocalEnum=localInvalid;
            invalid(new Action(){public void run(){SdlCodec.decode(superset,badLocalEnum);}});
            // A removed field uses the generic decoder and preserves local defaults.
            String removed=desc.replace("  10: required PackedState state;\n","");
            SdlCodec.Context evolution=SdlCodec.prepare(removed,packed_validation.PackedBatch.class);
            java.nio.ByteBuffer shorter=java.nio.ByteBuffer.allocate(2+count*76);
            shorter.put(expected,0,2);
            for(int i=0;i<count;i++) {
               shorter.put(expected,2+i*80,52);
               shorter.put(expected,2+i*80+56,24);
            }
            packed_validation.PackedBatch evolved=(packed_validation.PackedBatch)SdlCodec.decode(evolution,shorter.array());
            check(evolved.records.get(1).leaf.state==packed_validation.PackedState.ZERO);
            check(Float.floatToRawIntBits(evolved.records.get(1).grid[1][2])==Float.floatToRawIntBits(batch.records.get(1).grid[1][2]));
         }
      }
      final packed_validation.PackedBatch malformed=new packed_validation.PackedBatch();
      packed_validation.PackedRecord record=new packed_validation.PackedRecord(); malformed.records.add(record);
      record.grid[1]=new float[2];
      invalid(new Action(){public void run(){SdlCodec.encode(ctx,malformed);}});
      record.grid=new float[2][3]; record.leaf.state=null;
      invalid(new Action(){public void run(){SdlCodec.encode(ctx,malformed);}});
      record.leaf.state=packed_validation.PackedState.ZERO; record.leaf.point=null;
      invalid(new Action(){public void run(){SdlCodec.encode(ctx,malformed);}});
      final SdlCodec.Context complex=SdlCodec.prepare(benchmark.BenchPayload.SDL_DESCRIPTOR,benchmark.BenchPayload.class);
      final benchmark.BenchPayload samples=new benchmark.BenchPayload();
      samples.samples=new SdlCodec.Complex32Array(
         new float[]{-0.f,Float.intBitsToFloat(0x7fc01234)},
         new float[]{Float.NEGATIVE_INFINITY,Float.intBitsToFloat(0x7fa05678)});
      java.nio.ByteBuffer complexOracle=java.nio.ByteBuffer.allocate(19);
      complexOracle.put((byte)1).put((byte)0).put((byte)2);
      for(int i=0;i<2;i++) complexOracle.putInt(Float.floatToRawIntBits(samples.samples.re[i])).putInt(Float.floatToRawIntBits(samples.samples.im[i]));
      byte[] complexWire=complexOracle.array();
      check(Arrays.equals(complexWire,SdlCodec.encode(complex,samples)));
      check(Arrays.equals(complexWire,SdlCodec.encode(complex,SdlCodec.decode(complex,complexWire))));
      samples.samples.im=new float[1];
      invalid(new Action(){public void run(){SdlCodec.encode(complex,samples);}});
      final SdlCodec.Context primitive=SdlCodec.prepare(desc,packed_validation.PrimitiveBatch.class);
      packed_validation.PrimitiveBatch values=new packed_validation.PrimitiveBatch();
      values.flags=new boolean[]{true,false}; values.tiny=new byte[]{-128,127};
      values.small=new short[]{Short.MIN_VALUE,Short.MAX_VALUE};
      values.signed_values=new int[]{Integer.MIN_VALUE,Integer.MAX_VALUE};
      values.wide=new long[]{Long.MIN_VALUE,Long.MAX_VALUE};
      values.ratios=new float[]{-0.f,Float.intBitsToFloat(0x7fc01234)};
      values.precise=new double[]{-0.,Double.longBitsToDouble(0x7ff8000000001234L)};
      values.positions=new SdlCodec.Complex64Array(new double[]{-0.,Double.POSITIVE_INFINITY},new double[]{1.,-1.});
      java.nio.ByteBuffer oracle=java.nio.ByteBuffer.allocate(1+8+2+2+4+8+16+8+16+32);
      oracle.put((byte)messageId(desc,"PrimitiveBatch"));
      oracle.put((byte)2).put((byte)1).put((byte)0);
      oracle.put((byte)2).put(values.tiny);
      oracle.put((byte)2); for(short x:values.small) oracle.putShort(x);
      oracle.put((byte)2); for(int x:values.signed_values) oracle.putInt(x);
      oracle.put((byte)2); for(long x:values.wide) oracle.putLong(x);
      oracle.put((byte)2); for(float x:values.ratios) oracle.putInt(Float.floatToRawIntBits(x));
      oracle.put((byte)2); for(double x:values.precise) oracle.putLong(Double.doubleToRawLongBits(x));
      oracle.put((byte)2); for(int i=0;i<2;i++) oracle.putLong(Double.doubleToRawLongBits(values.positions.re[i])).putLong(Double.doubleToRawLongBits(values.positions.im[i]));
      final byte[] expected=oracle.array();
      check(Arrays.equals(expected,SdlCodec.encode(primitive,values)));
      check(Arrays.equals(expected,SdlCodec.encode(primitive,SdlCodec.decode(primitive,expected))));
      final byte[] badFlags=expected.clone();badFlags[3]=2;
      invalid(new Action(){public void run(){SdlCodec.decode(primitive,badFlags);}});
      for(int n=0;n<expected.length;n++) {
         final byte[] truncated=Arrays.copyOf(expected,n);
         invalid(new Action(){public void run(){SdlCodec.decode(primitive,truncated);}});
      }
      System.out.println("Java packed independent wire oracles, raw float bits, truncations and schema evolution passed.");
   }
   public static void main(String[] args) throws Exception {
      final byte[] fixture = Files.readAllBytes(Paths.get("tst/fixtures/packet.bin"));
      final SdlCodec.Context ctx = SdlCodec.prepare(SdlCodec.description(wire_example.Packet.class),
                                                    wire_example.Packet.class);
      check(SdlCodec.description(wire_example.Packet.class)
                .equals(new String(Files.readAllBytes(Paths.get("tst/fixtures/packet.sdl2")),
                                   StandardCharsets.UTF_8)));
      wire_example.Packet m = new wire_example.Packet();
      m.active = true;
      m.code = (short)-2;
      m.label = "été";
      m.samples = new short[]{300,-1};
      check(Arrays.equals(m.encode(ctx), fixture));
      wire_example.Packet p = wire_example.Packet.decode(ctx, fixture);
      check(p.active && p.code == -2 && p.label.equals("été") && Arrays.equals(p.samples,m.samples));
      m.code = null;
      m.label = "a\0b\0😀";
      m.samples=new short[0];
      p = wire_example.Packet.decode(ctx, m.encode(ctx));
      check(p.code == null && p.label.equals(m.label));
      for (int i = 0; i < fixture.length; i++) {
         final byte[] truncated = Arrays.copyOf(fixture, i);
         invalid(new Action() {
            public void run() { SdlCodec.decode(ctx, truncated); }
         });
      }
      for (final byte[] bad : new byte[][] {{0},
                                            {(byte)129, 0},
                                            {(byte)255, (byte)255, (byte)255, (byte)255, 16},
                                            Arrays.copyOf(fixture, 17)})
         invalid(new Action() {
            public void run() { SdlCodec.decode(ctx, bad); }
         });
      final byte[] bad = fixture.clone();
      bad[2] = 2;
      invalid(new Action() {
         public void run() { SdlCodec.decode(ctx, bad); }
      });
      bad[2] = 1;
      bad[1] = 2;
      invalid(new Action() {
         public void run() { SdlCodec.decode(ctx, bad); }
      });
      bad[1] = 1;
      bad[7] = (byte)255;
      invalid(new Action() {
         public void run() { SdlCodec.decode(ctx, bad); }
      });
      final String evolved =
          wire_example.Packet.SDL_DESCRIPTOR.replace("bool active;", "bool enabled;")
              .replace("packed int16 samples;",
                       "repeated int16 samples;\n  5: required string extra;");
      SdlCodec.Context remote = SdlCodec.prepare(evolved, wire_example.Packet.class);
      byte[] extended = Arrays.copyOf(fixture, 19);
      extended[16] = 2;
      extended[17] = 'o';
      extended[18] = 'k';
      p = (wire_example.Packet)SdlCodec.decode(remote, extended);
      check(p.active && p.samples.length == 2);
      remote = SdlCodec.prepare("SDL2\nmessage Packet {\n  1: required bool active;\n}\n",
                                wire_example.Packet.class);
      p = (wire_example.Packet)SdlCodec.decode(remote, new byte[] {1, 1});
      check(p.active && p.code == null && p.samples.length==0);
      invalid(new Action() {
         public void run() {
            SdlCodec.prepare(
                wire_example.Packet.SDL_DESCRIPTOR.replace("bool active;", "int32 active;"),
                wire_example.Packet.class);
         }
      });
      SdlCodec.Context multi = SdlCodec.prepare(
          SdlCodec.description(wire_example.Packet.class, empty_message.EmptyMessage.class),
          wire_example.Packet.class, empty_message.EmptyMessage.class);
      check(SdlCodec.decode(multi, SdlCodec.encode(multi, new empty_message.EmptyMessage()))
                instanceof empty_message.EmptyMessage);
      SdlCodec.Context cc =
          SdlCodec.prepare(codec_cases.CodecCases.SDL_DESCRIPTOR, codec_cases.CodecCases.class);
      codec_cases.CodecCases c = new codec_cases.CodecCases();
      c.tiny = (byte)-128;
      c.small = (short)32767;
      c.signed_value = Integer.MIN_VALUE;
      c.wide = Long.MIN_VALUE;
      c.precise = -0.0;
      c.ratio = Float.POSITIVE_INFINITY;
      c.state = codec_cases.State.MINIMUM;
      c.point = new SdlCodec.Complex32(1, -2);
      c.position = new SdlCodec.Complex64(3, 4);
      c.labels.add("");
      c.labels.add("a\0b");
      c.bool_flags.add(true);
      c.bool_flags.add(false);
      c.fixed_states = new codec_cases.State[] {codec_cases.State.READY, codec_cases.State.MINIMUM,
                                                codec_cases.State.MAXIMUM};
      c.packed_states.add(codec_cases.State.NEGATIVE);
      c.points = new SdlCodec.Complex32Array(new float[] {1, 2}, new float[] {-1, -2});
      c.high_id_value = Integer.MAX_VALUE;
      codec_cases.CodecCases copy =
          (codec_cases.CodecCases)SdlCodec.decode(cc, SdlCodec.encode(cc, c));
      check(copy.wide == Long.MIN_VALUE && copy.tiny == -128 &&
            copy.state == codec_cases.State.MINIMUM &&
            Double.doubleToRawLongBits(copy.precise) == Double.doubleToRawLongBits(-0.0) &&
            copy.labels.equals(c.labels) && copy.points.size() == 2 && copy.points.re[1] == 2);
      for (String text :
           new String[] {"SDL1\n", "SDL2\n", "SDL2\nmessage A {\n  1: required A a;\n}\n",
                         "SDL2\nmessage A {\n  1: packed string a;\n}\n",
                         "SDL2\nmessage A {\n}\nenum E {\n}\n"}) {
         final String t = text;
         invalid(new Action() {
            public void run() { SdlCodec.prepare(t); }
         });
      }
      SdlCodec.Context root =
          SdlCodec.prepare(schema.RootPayload.SDL_DESCRIPTOR, schema.RootPayload.class);
      schema.RootPayload r = new schema.RootPayload();
      r.header = "nested";
      schema.FixedItem f = new schema.FixedItem();
      f.x = 1f;
      f.y = 2f;
      r.fixed_array.add(f);
      schema.VarItem v = new schema.VarItem();
      v.name = "";
      v.id = 7L;
      r.var_array.add(v);
      schema.RootPayload q = (schema.RootPayload)SdlCodec.decode(root, SdlCodec.encode(root, r));
      check(q.fixed_array.get(0).x == 1f && q.var_array.get(0).id == 7L);
      String nested = schema.RootPayload.SDL_DESCRIPTOR.replace("fl32 x;", "fl32 renamed_x;")
                          .replace("fl32 y;", "fl32 y;\n  3: required int32 extra;");
      root = SdlCodec.prepare(nested, schema.RootPayload.class);
      int id = 1;
      for (String line : nested.split("\n")) {
         if (line.equals("message RootPayload {"))
            break;
         if (line.startsWith("message "))
            id++;
      }
      byte[] data = {(byte)id, 0, 1, 0x3f, (byte)0x80, 0, 0, 0x40, 0, 0, 0, 0, 0, 0, 7, 0};
      q = (schema.RootPayload)SdlCodec.decode(root, data);
      check(q.fixed_array.get(0).y == 2f);
      SdlCodec.Context board =
          SdlCodec.prepare(schema.FixedBoard.SDL_DESCRIPTOR, schema.FixedBoard.class);
      schema.FixedBoard b = new schema.FixedBoard();
      b.rows[0].vectors[0].coords[0] = 1f;
      b.rows[1].vectors[0].grid[1][2] = (short)-7;
      b.packed_rows.add(new schema.FixedRow());
      schema.FixedBoard out = (schema.FixedBoard)SdlCodec.decode(board, SdlCodec.encode(board, b));
      check(out.rows[1].vectors[0].grid[1][2] == -7 && out.packed_rows.size() == 1);
      for (int n : new int[] {0, 1, 127, 128, 16383, 16384}) {
         m = new wire_example.Packet();
         StringBuilder label = new StringBuilder();
         for (int i = 0; i < n; i++) {
            label.append('x');

         }
         m.samples=new short[n]; Arrays.fill(m.samples,(short)-1);
         m.label = label.toString();
         p = (wire_example.Packet)SdlCodec.decode(ctx, SdlCodec.encode(ctx, m));
         check(p.label.length() == n && p.samples.length == n);
      }
      final wire_example.Packet invalidString = new wire_example.Packet();
      invalidString.label = "\ud800";
      invalid(new Action() {
         public void run() { SdlCodec.encode(ctx, invalidString); }
      });
      SdlCodec.prepare(graph(30, true));
      invalid(new Action() {
         public void run() { SdlCodec.prepare(graph(66, false)); }
      });
      packedCodecs();
      System.out.println(
          "Java SDL2 fixtures, catalogues, evolution, arrays and malformed input passed.");
   }
}
