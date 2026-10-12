import sdl.filter.SdlFilter;
import sdl.runtime.SdlCodec;
import telemetry.model;
import java.util.Arrays;

public final class FilterBench {
    private static volatile Object sink;
    private interface Action { Object run(); }
    private static double measure(Action action, int iterations) {
        for (int i = 0; i < 10000; ++i) sink = action.run();
        double[] trials = new double[5];
        for (int trial = 0; trial < trials.length; ++trial) {
            long start = System.nanoTime();
            for (int i = 0; i < iterations; ++i) sink = action.run();
            trials[trial] = (System.nanoTime() - start) / (double) iterations;
        }
        Arrays.sort(trials);
        return trials[2];
    }
    private static void bench(final SdlCodec.Message value, String root, final SdlCodec.Context context,
                              String catalogue, String label, String expression, int iterations) {
        final byte[] wire = SdlCodec.encode(context, value);
        try (final SdlFilter filter = new SdlFilter(catalogue, root, expression)) {
            if (!filter.evaluate(value).matched() || !filter.evaluateWire(wire).matched())
                throw new AssertionError(expression);
            double enc = measure(new Action() { public Object run() { return SdlCodec.encode(context, value); } }, iterations);
            double dec = measure(new Action() { public Object run() { return SdlCodec.decode(context, wire); } }, iterations);
            double obj = measure(new Action() { public Object run() { return filter.evaluate(value); } }, iterations);
            double buf = measure(new Action() { public Object run() { return filter.evaluateWire(wire); } }, iterations);
            System.out.printf(java.util.Locale.ROOT, "BENCH Java %s %.1f %.1f %.1f %.1f%n", label, enc, dec, obj, buf);
        }
    }
    public static void main(String[] args) {
        String c = "SDL2|telemetry.Vector{1:fl64:x;2:fl64:y;}";
        model.Vector v = new model.Vector(); v.x = 3; v.y = 4;
        SdlCodec.Context context = SdlCodec.prepare(c, model.Vector.class);
        bench(v, "telemetry.Vector", context, c, "scalar", "x>0", 200000);
        bench(v, "telemetry.Vector", context, c, "arithmetic", "x*x+y*y<=25", 200000);
        model.Sample sample = new model.Sample(); sample.temperature = 12.34; sample.position = v;
        for (int i = 0; i < 256; ++i) { model.Vector point = new model.Vector(); point.x = 3; point.y = 4; sample.points.add(point); }
        c = SdlCodec.description(model.Sample.class); context = SdlCodec.prepare(c, model.Sample.class);
        bench(sample, "telemetry.Sample", context, c, "collection256", "all(points,p=>p.x>=0&&p.y>=0)", 1000);
    }
}
