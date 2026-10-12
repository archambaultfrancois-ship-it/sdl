import java.util.Arrays;
public final class SdlJavaBench {
   static volatile Object sink;
   static int iterations() {
      String s = System.getenv("SDL_BENCH_ITERATIONS");
      if (s == null || !s.matches("[0-9]+"))
         return 200;
      try {
         int n = Integer.parseInt(s);
         return n > 0 ? n : 200;
      } catch (NumberFormatException e) {
         return 200;
      }
   }
   static void bench(String label, SdlCodec.Message value, int useful) {
      int minIterations = iterations();
      String description = SdlCodec.description(value.getClass());
      long start = System.nanoTime();
      SdlCodec.Context ctx = SdlCodec.prepare(description, value.getClass());
      double preparation = (System.nanoTime() - start) / 1e3;
      byte[] wire = SdlCodec.encode(ctx, value);
      SdlCodec.Message copy = SdlCodec.decode(ctx, wire);
      if (!Arrays.equals(wire, SdlCodec.encode(ctx, copy)))
         throw new AssertionError("round trip differs: " + label);
      for (int i = 0; i < 1000; i++) {
         sink = SdlCodec.encode(ctx, value);
         sink = SdlCodec.decode(ctx, wire);
      }
      System.out.printf("%s metadata: %d description bytes, %.1f us prepare%n", label,
                        description.getBytes(java.nio.charset.StandardCharsets.UTF_8).length,
                        preparation);
      for (String operation : new String[] {"encode", "decode"}) {
         double[] rates = new double[3];
         for (int trial = 0; trial < 3; trial++) {
            start = System.nanoTime();
            int n = 0;
            while (n < minIterations || System.nanoTime() - start < 100000000L) {
               sink = operation.equals("encode") ? SdlCodec.encode(ctx, value)
                                                 : SdlCodec.decode(ctx, wire);
               n++;
            }
            rates[trial] = n / ((System.nanoTime() - start) / 1e9);
         }
         Arrays.sort(rates);
         System.out.printf("%s %s %.1f msg/s %.4f MiB/s (%d bytes/message)%n", label, operation,
                           rates[1], rates[1] * useful / 1048576., wire.length);
      }
   }
   public static void main(String[] args) {
      bench_cases.BenchSmall small = new bench_cases.BenchSmall();
      small.active = true;
      small.sequence = 123;
      small.code = (short)-2;
      bench("small", small, 7);
      bench_cases.BenchOptionals optional = new bench_cases.BenchOptionals();
      optional.a = 1;
      bench("optional_sparse", optional, 4);
      optional.b = 2;
      optional.c = 3;
      optional.d = 4;
      optional.e = 5;
      optional.f = 6;
      optional.g = 7;
      optional.h = 8;
      bench("optional_dense", optional, 32);
      bench_cases.BenchVariable variable = new bench_cases.BenchVariable();
      StringBuilder h = new StringBuilder();
      for (int i = 0; i < 40; i++)
         h.append('V');
      variable.header = h.toString();
      for (int i = 0; i < 8; i++) {
         bench_cases.BenchEntry entry = new bench_cases.BenchEntry();
         entry.label = "entry" + i;
         entry.number = (long)i;
         variable.entries.add(entry);
      }
      bench("variable", variable, 152);
      benchmark.BenchPayload packed = new benchmark.BenchPayload();
      h = new StringBuilder();
      for (int i = 0; i < 200; i++)
         h.append('H');
      packed.header = h.toString();
      float[] re = new float[5000], im = new float[5000];
      for (int i = 0; i < 5000; i++) {
         re[i] = i * 0.25f;
         im[i] = -(i % 97) * 0.5f;
      }
      packed.samples = new SdlCodec.Complex32Array(re, im);
      bench("packed", packed, 40200);
      bench_cases.BenchRecordBatch batch = new bench_cases.BenchRecordBatch();
      for (int i = 0; i < 1000; i++) {
         bench_cases.BenchRecord record = new bench_cases.BenchRecord();
         record.id = i;
         record.pose.position.values = new float[]{i * .25f, -i * .5f, (float)(i % 97)};
         record.pose.rotation = new float[]{0.f, 0.f, 0.f, 1.f};
         record.measures = new float[]{i * .125f, -(i % 31) * .5f};
         batch.records.add(record);
      }
      bench("packed_struct", batch, 40000);
   }
}
