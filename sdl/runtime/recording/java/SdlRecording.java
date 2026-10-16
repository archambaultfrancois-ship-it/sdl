import java.io.IOException;
import java.nio.charset.Charset;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Optional MCAP/PCAPNG services; inputs are catalogues and complete SDL2 byte payloads. */
public final class SdlRecording {
    private static final Charset UTF8 = Charset.forName("UTF-8");
    static { System.loadLibrary("sdl_recording_jni"); }
    private SdlRecording() {}
    private static byte[] text(String value) {
        if (value == null) throw new NullPointerException("text");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (Character.isHighSurrogate(c)) {
                if (++i >= value.length() || !Character.isLowSurrogate(value.charAt(i)))
                    throw new IllegalArgumentException("unpaired UTF-16 surrogate");
            } else if (Character.isLowSurrogate(c)) {
                throw new IllegalArgumentException("unpaired UTF-16 surrogate");
            }
        }
        return value.getBytes(UTF8);
    }
    private static byte[][] metadata(Map<String, String> values) {
        if (values == null) throw new NullPointerException("metadata");
        byte[][] result = new byte[values.size() * 2][];
        int i = 0;
        for (Map.Entry<String, String> entry : values.entrySet()) {
            result[i++] = text(entry.getKey()); result[i++] = text(entry.getValue());
        }
        return result;
    }
    /** Timestamps use the unsigned uint64 bit pattern in a Java long. */
    public static long now() throws IOException { return nNow(); }
    public static long validate(String path) throws IOException { return nValidate(text(path)); }
    public enum Format {
        MCAP(0), PCAPNG(1);
        private final int code;
        Format(int code) { this.code = code; }
    }
    public interface Consumer { void accept(Record record) throws IOException; }
    public static final class Record {
        public final int channelId, schemaId;
        public final String topic, rootType, catalogue;
        public final byte[] payload;
        public final Map<String, String> metadata;
        public final long logTimeNs, publishTimeNs, sequence;
        // Called by JNI; arrays own their data independently of the reader.
        private Record(int channel, int schema, byte[] topic, byte[] root, byte[] catalogue,
                       byte[] payload, byte[][] pairs, long log, long publish, long sequence) {
            this.channelId = channel; this.schemaId = schema;
            this.topic = new String(topic, UTF8); this.rootType = new String(root, UTF8);
            this.catalogue = new String(catalogue, UTF8); this.payload = payload;
            this.logTimeNs = log; this.publishTimeNs = publish; this.sequence = sequence;
            Map<String, String> map = new LinkedHashMap<String, String>();
            for (int i = 0; i < pairs.length; i += 2)
                map.put(new String(pairs[i], UTF8), new String(pairs[i + 1], UTF8));
            this.metadata = Collections.unmodifiableMap(map);
        }
    }
    public static final class Writer implements AutoCloseable {
        private long handle;
        public Writer(String path) throws IOException { this(path, false, 1024 * 1024); }
        public Writer(String path, boolean overwrite, long chunkSize) throws IOException {
            this(path, overwrite, chunkSize, Format.PCAPNG);
        }
        public Writer(String path, Format format) throws IOException { this(path, false, 1024 * 1024, format); }
        public Writer(String path, boolean overwrite, long chunkSize, Format format) throws IOException {
            if (format == null) throw new NullPointerException("format");
            handle = nOpenFormat(text(path), overwrite, chunkSize, format.code);
        }
        private long handle() { if (handle == 0) throw new IllegalStateException("closed writer"); return handle; }
        public int channel(String topic, String catalogue, String root) throws IOException {
            return channel(topic, catalogue, root, Collections.<String, String>emptyMap());
        }
        public int channel(String topic, String catalogue, String root, Map<String, String> metadata) throws IOException {
            return nChannel(handle(), text(topic), text(catalogue), text(root), SdlRecording.metadata(metadata));
        }
        public void metadata(String name, Map<String, String> metadata) throws IOException {
            nMetadata(handle(), text(name), SdlRecording.metadata(metadata));
        }
        public void validatePayload(boolean enabled) throws IOException { nValidatePayload(handle(), enabled); }
        public void write(int channel, byte[] payload) throws IOException {
            long timestamp = now(); write(channel, payload, timestamp, timestamp, 0);
        }
        public void write(int channel, byte[] payload, long log, long publish, long sequence) throws IOException {
            if (channel < 0 || channel > 65535 || sequence < 0 || sequence > 0xffffffffL)
                throw new IllegalArgumentException("channel/sequence out of range");
            if (payload == null) throw new NullPointerException("payload");
            nWrite(handle(), channel, payload, log, publish, sequence);
        }
        public void flush(boolean sync) throws IOException { nFlush(handle(), sync); }
        public void close() throws IOException {
            if (handle != 0) { long old = handle; handle = 0; nClose(old, true); }
        }
    }
    public static final class Reader implements AutoCloseable {
        private long handle;
        private boolean replaying;
        public Reader(String path) throws IOException { handle = nOpen(text(path), false, false, 0); }
        private long handle() {
            if (handle == 0 || replaying) throw new IllegalStateException("closed or replaying reader");
            return handle;
        }
        public void filter(String topic, Long start, Long end) throws IOException {
            nFilter(handle(), text(topic == null ? "" : topic), (start == null ? 0 : 1) | (end == null ? 0 : 2),
                    start == null ? 0 : start, end == null ? 0 : end);
        }
        public Record next() throws IOException { return nNext(handle()); }
        public long replay(double speed, Consumer consumer) throws IOException {
            if (consumer == null) throw new NullPointerException("consumer");
            long old = handle(); replaying = true;
            try { return nReplay(old, speed, consumer); } finally { replaying = false; }
        }
        public void close() throws IOException {
            if (replaying) throw new IllegalStateException("reader is replaying");
            if (handle != 0) { long old = handle; handle = 0; nClose(old, false); }
        }
    }
    // Serialization protects the gateway's token registry. Core C handles remain single-threaded.
    private static synchronized native long nOpen(byte[] path, boolean writer, boolean overwrite, long chunk) throws IOException;
    private static synchronized native long nOpenFormat(byte[] path, boolean overwrite, long chunk, int format) throws IOException;
    private static synchronized native int nChannel(long handle, byte[] topic, byte[] catalogue, byte[] root, byte[][] metadata) throws IOException;
    private static synchronized native void nMetadata(long handle, byte[] name, byte[][] metadata) throws IOException;
    private static synchronized native void nValidatePayload(long handle, boolean validate) throws IOException;
    private static synchronized native void nWrite(long handle, int channel, byte[] payload, long log, long publish, long sequence) throws IOException;
    private static synchronized native void nFlush(long handle, boolean sync) throws IOException;
    private static synchronized native void nClose(long handle, boolean writer) throws IOException;
    private static synchronized native void nFilter(long handle, byte[] topic, int flags, long start, long end) throws IOException;
    private static synchronized native Record nNext(long handle) throws IOException;
    private static synchronized native long nReplay(long handle, double speed, Consumer consumer) throws IOException;
    private static synchronized native long nValidate(byte[] path) throws IOException;
    private static synchronized native long nNow() throws IOException;
}
