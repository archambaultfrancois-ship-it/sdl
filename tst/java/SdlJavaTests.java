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
      m.samples.add((short)300);
      m.samples.add((short)-1);
      check(Arrays.equals(m.encode(ctx), fixture));
      wire_example.Packet p = wire_example.Packet.decode(ctx, fixture);
      check(p.active && p.code == -2 && p.label.equals("été") && p.samples.equals(m.samples));
      m.code = null;
      m.label = "a\0b\0😀";
      m.samples.clear();
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
      check(p.active && p.samples.size() == 2);
      remote = SdlCodec.prepare("SDL2\nmessage Packet {\n  1: required bool active;\n}\n",
                                wire_example.Packet.class);
      p = (wire_example.Packet)SdlCodec.decode(remote, new byte[] {1, 1});
      check(p.active && p.code == null && p.samples.isEmpty());
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
            m.samples.add((short)-1);
         }
         m.label = label.toString();
         p = (wire_example.Packet)SdlCodec.decode(ctx, SdlCodec.encode(ctx, m));
         check(p.label.length() == n && p.samples.size() == n);
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
      System.out.println(
          "Java SDL2 fixtures, catalogues, evolution, arrays and malformed input passed.");
   }
}
