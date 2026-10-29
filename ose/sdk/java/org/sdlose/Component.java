package org.sdlose;

import java.io.*;
import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.charset.*;
import java.util.*;
import sdl.runtime.SdlCodec;

/** CH/1 component runtime. Standard output belongs exclusively to this protocol. */
public final class Component {
    public static final int MAX_FRAME = 1024 * 1024;
    private static final Charset UTF8 = Charset.forName("UTF-8");
    private static final BigInteger MAX_U64 = BigInteger.ONE.shiftLeft(64).subtract(BigInteger.ONE);
    public static String text(byte[] bytes) throws IOException {
        return UTF8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
    }
    public static byte[] utf8(String text) throws IOException {
        ByteBuffer bytes = UTF8.newEncoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).encode(java.nio.CharBuffer.wrap(text));
        byte[] result = new byte[bytes.remaining()]; bytes.get(result); return result;
    }
    public static final class Frame {
        public final int op; public final byte[] body;
        Frame(int op, byte[] body) { this.op = op; this.body = body; }
    }
    public static Frame read(InputStream input) throws IOException {
        DataInputStream in = new DataInputStream(input);
        int size = in.readInt();
        if (size < 1 || size > MAX_FRAME) throw new IOException("invalid CH/1 frame size");
        int op = in.readUnsignedByte(); byte[] body = new byte[size - 1]; in.readFully(body);
        return new Frame(op, body);
    }
    public static void send(OutputStream output, int op, byte[] body) throws IOException {
        if (body.length >= MAX_FRAME) throw new IOException("CH/1 frame exceeds limit");
        DataOutputStream out = new DataOutputStream(output);
        out.writeInt(body.length + 1); out.writeByte(op); out.write(body); out.flush();
    }
    public static String resource(Class<?> owner, String path) throws IOException {
        InputStream in = owner.getResourceAsStream(path);
        if (in == null) throw new IOException("missing catalogue resource " + path);
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream(); byte[] buffer = new byte[4096]; int n;
            while ((n = in.read(buffer)) != -1) {
                if (out.size() + n > MAX_FRAME) throw new IOException("catalogue exceeds limit");
                out.write(buffer, 0, n);
            }
            return text(out.toByteArray());
        } finally { in.close(); }
    }
    public static final class Port {
        public final String name, root, catalogue;
        public final Class<? extends SdlCodec.Message> type;
        public final boolean required, timed;
        private String remote;
        private SdlCodec.Context decoder;
        final SdlCodec.Context encoder;
        public Port(String name, String root, String catalogue, Class<? extends SdlCodec.Message> type,
                    boolean required, boolean timed) {
            this.name = name; this.root = root; this.catalogue = catalogue; this.type = type;
            this.required = required; this.timed = timed;
            encoder = SdlCodec.prepare(catalogue, type);
        }
        Object decode(String cat, byte[] bytes) throws IOException {
            if (!cat.equals(remote)) {
                if (!SdlCodec.compareCatalogues(cat, catalogue, root, true).compatible())
                    throw new IOException("incompatible input catalogue for " + name);
                decoder = SdlCodec.prepare(cat, type); remote = cat;
            }
            Object value = SdlCodec.decode(decoder, bytes);
            if (!type.isInstance(value)) throw new IOException("wrong message root for " + name);
            return value;
        }
    }
    public static class Context {
        public final String name;
        public String configJson;
        private BigInteger time = BigInteger.ZERO, index = BigInteger.ZERO;
        private Thread owner;
        private final Port[] outputs;
        private final OutputStream stream;
        private final Map<String, Port> recordingPoints = new HashMap<>();
        private final Set<String> recordingActive = new HashSet<>();
        private boolean initializing;
        private int recordingBytes;
        private InputStream inputStream;
        private Port[] parameters = new Port[0];
        private boolean[] parameterRead = new boolean[0], parameterWrite = new boolean[0];
        private byte[][] parameterValues = new byte[0][];
        protected final void configureParameters(Port[] ports, boolean[] readable, boolean[] writable) {
            parameters = ports; parameterRead = readable; parameterWrite = writable;
            parameterValues = new byte[ports.length][];
        }
        protected final Object parameter(int slot) throws IOException {
            if (slot < 0 || slot >= parameters.length || !parameterRead[slot] || parameterValues[slot] == null)
                throw new IOException("parameter is not readable");
            return parameters[slot].decode(parameters[slot].catalogue, parameterValues[slot]);
        }
        protected final void setParameter(int slot, SdlCodec.Message value) throws IOException {
            if (owner != Thread.currentThread()) throw new IllegalStateException("parameter writes require an active callback");
            if (slot < 0 || slot >= parameters.length || !parameterWrite[slot] || !parameters[slot].type.isInstance(value))
                throw new IOException("invalid writable parameter");
            Port port = parameters[slot];
            byte[] name = utf8(port.name), payload = SdlCodec.encode(port.encoder, value);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream data = new DataOutputStream(bytes);
            data.writeShort(name.length); data.write(name); data.write(payload);
            send(stream, 'S', bytes.toByteArray());
            Frame result = read(inputStream);
            if (result.op == 'A' && Arrays.equals(result.body, new byte[]{1})) return;
            if (result.op == 'A' && result.body.length > 0 && result.body[0] == 0)
                throw new IOException(text(Arrays.copyOfRange(result.body, 1, result.body.length)));
            throw new IOException("invalid parameter write result");
        }
        private void parameterUpdate(Frame event, boolean initial) throws IOException {
            if (event.op != 'U') throw new IOException("expected parameter update");
            DataInputStream data = new DataInputStream(new ByteArrayInputStream(event.body));
            int length = data.readUnsignedShort();
            if (length == 0 || length > data.available() - 4) throw new IOException("invalid parameter alias");
            byte[] name = new byte[length]; data.readFully(name);
            String alias = text(name); int slot = -1;
            for (int i = 0; i < parameters.length; ++i) if (parameters[i].name.equals(alias)) slot = i;
            if (slot < 0 || !parameterRead[slot] || (initial && parameterValues[slot] != null))
                throw new IOException("unknown or duplicate initial parameter");
            int catSize = data.readInt();
            if (catSize < 1 || catSize > data.available()) throw new IOException("invalid parameter catalogue");
            byte[] catalogue = new byte[catSize]; data.readFully(catalogue);
            byte[] payload = new byte[data.available()]; data.readFully(payload);
            if (!parameters[slot].catalogue.equals(text(catalogue))) throw new IOException("wrong parameter catalogue");
            parameters[slot].decode(parameters[slot].catalogue, payload);
            parameterValues[slot] = payload;
        }
        public Context(String name, Port[] outputs) { this(name, outputs, System.out); }
        public Context(String name, Port[] outputs, OutputStream stream) {
            this.name = name; this.outputs = outputs; this.stream = stream;
        }
        public BigInteger timeNs() { return time; }
        public BigInteger tickIndex() { return index; }
        public double now() { return time.doubleValue() / 1000000000.0; }
        public final <T extends SdlCodec.Message> void declareRecordingPoint(
                String name, Class<T> type, String description) throws IOException {
            if (!initializing || owner != Thread.currentThread() || recordingPoints.containsKey(name) ||
                recordingPoints.size() >= 128 || name == null || name.length() > 200 ||
                !name.matches("[A-Za-z_][A-Za-z0-9_]*(\\.[A-Za-z_][A-Za-z0-9_]*)*"))
                throw new IllegalArgumentException("recording declarations require initialization and unique valid names");
            if (description == null || description.indexOf(0) >= 0 || utf8(description).length > 1024)
                throw new IllegalArgumentException("invalid recording description");
            String root = SdlCodec.messageName(type), catalogue = SdlCodec.description(type);
            Port point = new Port(name, root, catalogue, type, false, false);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream out = new DataOutputStream(bytes);
            byte[] local = utf8(name); out.writeShort(local.length); out.write(local);
            out.write(utf8(root)); out.writeByte(0); out.write(utf8(description)); out.writeByte(0); out.write(utf8(catalogue));
            send(stream, 'N', bytes.toByteArray()); recordingPoints.put(name, point);
        }
        public final boolean recordingEnabled(String name) {
            if (!recordingPoints.containsKey(name)) throw new IllegalArgumentException("unknown recording point");
            return recordingActive.contains(name);
        }
        public final RecordResult record(String name, SdlCodec.Message value) throws IOException {
            if (initializing || owner != Thread.currentThread())
                throw new IllegalStateException("recording requires the active tick callback");
            Port point = recordingPoints.get(name);
            if (point == null || !point.type.isInstance(value)) throw new IllegalArgumentException("unknown point or wrong recording type");
            if (!recordingActive.contains(name)) return RecordResult.DISABLED;
            if (recordingBytes + name.length() + 2 >= 256 * 1024) return RecordResult.DROPPED;
            byte[] payload = SdlCodec.encode(point.encoder, value), local = utf8(name);
            int size = local.length + 2 + payload.length;
            if (size > 256 * 1024 - recordingBytes) return RecordResult.DROPPED;
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream out = new DataOutputStream(bytes);
            out.writeShort(local.length); out.write(local); out.write(payload);
            send(stream, 'C', bytes.toByteArray()); recordingBytes += size;
            return RecordResult.QUEUED;
        }
        private void recordingUpdate(byte[] data) throws IOException {
            recordingActive.clear();
            if (data.length == 0) return;
            for (String name : text(data).split(String.valueOf((char)0), -1)) {
                if (!recordingPoints.containsKey(name) || !recordingActive.add(name))
                    throw new IOException("unknown or duplicate active recording point");
            }
        }
        protected final void publish(int index, SdlCodec.Message message) throws IOException {
            if (owner != Thread.currentThread()) throw new IllegalStateException("publication requires the active tick callback");
            Port port = outputs[index];
            if (!port.type.isInstance(message)) throw new IllegalArgumentException("wrong output type");
            byte[] name = utf8(port.name), payload = SdlCodec.encode(port.encoder, message);
            if (name.length > 65535 || payload.length == 0 || name.length + payload.length > MAX_FRAME - 3)
                throw new IOException("invalid publication size");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); DataOutputStream out = new DataOutputStream(bytes);
            out.writeShort(name.length); out.write(name); out.write(payload); send(stream, 'P', bytes.toByteArray());
        }
        private void tick(byte[] bytes) throws IOException {
            if (bytes.length == 0) { time = BigInteger.ZERO; index = index.add(BigInteger.ONE); }
            else if (bytes.length == 16) {
                time = new BigInteger(1, Arrays.copyOfRange(bytes, 0, 8));
                index = new BigInteger(1, Arrays.copyOfRange(bytes, 8, 16));
            } else throw new IOException("invalid TICK body");
            if (index.signum() == 0 || index.compareTo(MAX_U64) > 0) throw new IOException("invalid tick index");
        }
    }
    public enum RecordResult { QUEUED, DISABLED, DROPPED }
    public interface Callbacks {
        void start() throws Exception;
        void tick(Object[] inputs) throws Exception;
        void stop() throws Exception;
    }
    public static void run(Context ctx, Port[] inputs, Callbacks callbacks) throws Exception {
        run(ctx, inputs, callbacks, System.in);
    }
    public static void run(Context ctx, Port[] inputs, Callbacks callbacks, InputStream stream) throws Exception {
        Frame init = read(stream);
        if (init.op != 'I') throw new IOException("expected INIT");
        ctx.configJson = text(init.body);
        ctx.inputStream = stream;
        // Validate JSON before exposing INIT to user code.
        if (!(Json.parse(ctx.configJson) instanceof Map)) throw new IOException("INIT must be a JSON object");
        boolean started = false; Throwable failure = null;
        try {
            for (int i = 0; i < ctx.parameters.length; ++i)
                if (ctx.parameterRead[i]) ctx.parameterUpdate(read(stream), true);
            started = true; ctx.owner = Thread.currentThread();
            ctx.initializing = true;
            try { callbacks.start(); } finally { ctx.owner = null; }
            ctx.initializing = false;
            send(ctx.stream, 'R', new byte[]{(byte)(ctx.parameters.length == 0 ? 1 : 3)});
            Object[] selected = new Object[inputs.length]; boolean[] present = new boolean[inputs.length];
            for (;;) {
                Frame event = read(stream);
                if (event.op == 'X') { if (event.body.length != 0) throw new IOException("invalid STOP"); break; }
                if (event.op == 'U') { ctx.parameterUpdate(event, false); continue; }
                if (event.op == 'E') { ctx.recordingUpdate(event.body); continue; }
                if (event.op == 'D') {
                    DataInputStream data = new DataInputStream(new ByteArrayInputStream(event.body));
                    int size = data.readUnsignedShort();
                    if (size == 0 || size > data.available() - 4) throw new IOException("invalid input port size");
                    byte[] portBytes = new byte[size]; data.readFully(portBytes); String name = text(portBytes);
                    int slot = -1;
                    for (int i = 0; i < inputs.length; ++i) if (inputs[i].name.equals(name)) slot = i;
                    if (slot < 0 || present[slot]) throw new IOException("unknown or duplicate input " + name);
                    int catSize = data.readInt();
                    if (catSize < 0 || catSize > data.available()) throw new IOException("invalid catalogue size");
                    byte[] cat = new byte[catSize]; data.readFully(cat);
                    byte[] payload = new byte[data.available()]; data.readFully(payload);
                    Port port = inputs[slot];
                    if (catSize == 0 && payload.length == 0) {
                        if (!port.required || !port.timed) throw new IOException("unexpected null input");
                    } else {
                        if (catSize == 0 || payload.length == 0) throw new IOException("incomplete input");
                        selected[slot] = port.decode(text(cat), payload);
                    }
                    present[slot] = true;
                } else if (event.op == 'T') {
                    for (int i = 0; i < inputs.length; ++i)
                        if (inputs[i].required && !present[i]) throw new IOException("missing required input " + inputs[i].name);
                    ctx.tick(event.body); ctx.owner = Thread.currentThread();
                    ctx.recordingBytes = 0;
                    try { callbacks.tick(selected); } finally { ctx.owner = null; }
                    Arrays.fill(selected, null); Arrays.fill(present, false); send(ctx.stream, 'F', new byte[0]);
                } else throw new IOException("unexpected CH/1 opcode");
            }
        } catch (Throwable error) { failure = error; throw error; }
        finally {
            if (started) try { callbacks.stop(); }
            catch (Throwable stop) { if (failure != null) failure.addSuppressed(stop); else throw stop; }
        }
    }
}
