import java.io.IOException;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.Map;

public class SdlRecordingTests {
    private static void check(boolean value) { if (!value) throw new AssertionError(); }
    public static void main(String[] args) throws Exception {
        String path = args[0], topic = "radio/é📻\u0000iq";
        Map<String, String> metadata = new LinkedHashMap<String, String>();
        metadata.put("origin", "é📻\u0000");
        try (SdlRecording.Writer writer = new SdlRecording.Writer(path)) {
            int channel = writer.channel(topic, "SDL2|Packet{}", "Packet", metadata);
            writer.metadata("run", metadata);
            writer.write(channel, new byte[] {1}, -1L, -2L, 0xffffffffL);
            writer.write(channel, new byte[] {1}, -1L, -2L, 1);
            writer.flush(true);
            try { writer.write(channel, new byte[] {2}, 0, 0, 0); throw new AssertionError(); }
            catch (IOException expected) {}
        }
        check(SdlRecording.validate(path) == 2);
        try (final SdlRecording.Reader reader = new SdlRecording.Reader(path)) {
            reader.filter(topic, Long.valueOf(-1L), null);
            SdlRecording.Record first = reader.next(), second = reader.next();
            check(first.topic.equals(topic)); check(first.metadata.get("origin").equals(metadata.get("origin")));
            check(first.logTimeNs == -1L && first.publishTimeNs == -2L && first.sequence == 0xffffffffL);
            check(Arrays.equals(first.payload, new byte[] {1})); check(second.sequence == 1);
            check(reader.next() == null);
            check(first.catalogue.equals("SDL2|Packet{}")); // First record still owns its buffers.
        }
        final int[] seen = {0};
        try (final SdlRecording.Reader reader = new SdlRecording.Reader(path)) {
            check(reader.replay(0, new SdlRecording.Consumer() {
                public void accept(SdlRecording.Record record) throws IOException {
                    seen[0]++;
                    try { reader.close(); throw new AssertionError(); }
                    catch (IllegalStateException expected) {}
                    try { reader.next(); throw new AssertionError(); }
                    catch (IllegalStateException expected) {}
                }
            }) == 2);
        }
        check(seen[0] == 2);
        try (SdlRecording.Reader reader = new SdlRecording.Reader(path)) {
            try {
                reader.replay(0, new SdlRecording.Consumer() {
                    public void accept(SdlRecording.Record record) throws IOException { throw new IOException("consumer-marker"); }
                });
                throw new AssertionError();
            } catch (IOException expected) { check(expected.getMessage().equals("consumer-marker")); }
            check(reader.next().sequence == 1); // Callback failure restores reader usability.
        }
        // Read a Python-generated recording, retaining exact quantum text and bytes.
        try (SdlRecording.Reader reader = new SdlRecording.Reader(args[1])) {
            SdlRecording.Record r = reader.next();
            check(r.rootType.equals("Sample")); check(r.payload.length > 1);
            check(r.catalogue.contains("q0.25")); check(r.topic.equals(topic));
        }
        System.out.println("Java JNI recording tests passed");
    }
}
