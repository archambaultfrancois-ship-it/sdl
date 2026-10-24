package org.sdlchain.client;

import java.io.IOException;
import java.util.Map;
import org.sdlchain.Component;
import org.sdlchain.Json;
import sdl.runtime.SdlCodec;

/** Parameter-only TCP/mTLS client backed by the shared native SDK. */
public final class ParameterClient implements AutoCloseable {
    private static final byte[] EMPTY = new byte[0];
    private long handle;
    private static class Native {
        static { System.loadLibrary("sdl_chain_jni"); }
        static native long connect(byte[][] options, int port) throws IOException;
        static native byte[][] request(long handle, byte[] operation, byte[] arguments, byte[] payload) throws IOException;
        static native void free(long handle);
    }
    private ParameterClient(long handle) { this.handle = handle; }
    private static byte[] bytes(String value) throws IOException {
        if (value != null && value.indexOf('\0') >= 0) throw new IOException("NUL in parameter client string");
        return value == null ? null : Component.utf8(value);
    }
    public static ParameterClient connect(String host, int port, String client, String token) throws IOException {
        return connect(host, port, client, token, null, null, null);
    }
    public static ParameterClient connect(String host, int port, String client, String token,
                                          String ca, String certificate, String key) throws IOException {
        if (port < 1 || port > 65535) throw new IOException("invalid parameter service port");
        return new ParameterClient(Native.connect(new byte[][]{bytes(host), bytes(client), bytes(token), bytes(ca), bytes(certificate), bytes(key)}, port));
    }
    private static String quote(String text) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < text.length(); ++i) {
            char c = text.charAt(i);
            if (c == '"' || c == '\\') out.append('\\').append(c);
            else if (c < 32) out.append(String.format("\\u%04x", (int)c));
            else out.append(c);
        }
        return out.append('"').toString();
    }
    public static final class Packet {
        public final Map<?, ?> header; public final byte[] payload;
        Packet(byte[][] data) throws IOException {
            Object header = Json.parse(Component.text(data[0]));
            if (!(header instanceof Map)) throw new IOException("invalid parameter reply");
            this.header = (Map<?, ?>)header; payload = data[1];
        }
    }
    public synchronized Packet request(String operation, String arguments, byte[] payload) throws IOException {
        if (handle == 0) throw new IOException("parameter client closed");
        return new Packet(Native.request(handle, bytes(operation), bytes(arguments), payload));
    }
    public Object list() throws IOException { return request("parameters_list", "{}", EMPTY).header.get("blocks"); }
    public <T extends SdlCodec.Message> Block<T> block(String path, Class<T> type) throws IOException {
        return new Block<T>(this, path, type);
    }
    public void subscribe(String... paths) throws IOException {
        StringBuilder args = new StringBuilder("{\"paths\":[");
        for (int i = 0; i < paths.length; ++i) { if (i != 0) args.append(','); args.append(quote(paths[i])); }
        request("parameters_subscribe", args.append("]}").toString(), EMPTY);
    }
    /** Returns null when the bounded long-poll has no pending update. */
    public Packet next() throws IOException {
        Packet result = request("parameters_next", "{}", EMPTY);
        return Boolean.TRUE.equals(result.header.get("empty")) ? null : result;
    }
    public void unsubscribe() throws IOException { request("parameters_unsubscribe", "{}", EMPTY); }
    public synchronized void close() {
        if (handle != 0) { Native.free(handle); handle = 0; }
    }
    public static final class Block<T extends SdlCodec.Message> {
        private final ParameterClient client; private final Class<T> type;
        private final String arguments; private final SdlCodec.Context codec;
        public final String path;
        Block(ParameterClient client, String path, Class<T> type) throws IOException {
            this.client = client; this.path = path; this.type = type;
            arguments = "{\"path\":" + quote(path) + "}";
            Packet result = client.request("parameters_describe", arguments, EMPTY);
            try {
                if (!type.getField("SDL_NAME").get(null).equals(result.header.get("root")))
                    throw new IOException("wrong parameter message root");
            } catch (ReflectiveOperationException error) { throw new IOException("expected generated SDL message type", error); }
            String catalogue = Component.text(result.payload);
            if (!SdlCodec.compareCatalogues(catalogue, SdlCodec.description(type), (String)result.header.get("root"), true).compatible())
                throw new IOException("incompatible parameter message type");
            codec = SdlCodec.prepare(catalogue, type);
        }
        public T decode(Packet update) throws IOException {
            if (!path.equals(update.header.get("path"))) throw new IOException("wrong parameter block");
            Object value = SdlCodec.decode(codec, update.payload);
            if (!type.isInstance(value)) throw new IOException("wrong parameter message root");
            return type.cast(value);
        }
        public T get() throws IOException { return decode(client.request("parameters_get", arguments, EMPTY)); }
        public void set(T value) throws IOException {
            if (!type.isInstance(value)) throw new IOException("wrong parameter message type");
            client.request("parameters_set", arguments, SdlCodec.encode(codec, value));
        }
    }
}
