package org.sdlchain.client;

import java.io.IOException;
import java.math.BigInteger;
import org.sdlchain.Component;
import org.sdlchain.Json;
import sdl.runtime.SdlCodec;

/** Typed external CENV client. Network delivery is implemented by the shared Rust SDK. */
public final class Client implements AutoCloseable {
    private static final byte[] EMPTY = new byte[0];
    private final Handle handle;
    private volatile Throwable callbackFailure;
    private static final ThreadLocal<State<?>> ACTIVE = new ThreadLocal<State<?>>();
    private static class Native {
        static { System.loadLibrary("sdl_chain_jni"); }
        static native long connect(byte[] host, int port, byte[] id) throws IOException;
        static native byte[] call(long handle, int op, byte[] a, byte[] b, byte[] c) throws IOException;
        static native long subscribe(long handle, byte[] topic, byte[] root, byte[] catalogue, byte[] options, State<?> state) throws IOException;
        static native void free(long handle, boolean subscription);
    }
    /** Leases keep the native handle alive across blocking calls and concurrent close. */
    private static final class Handle {
        long pointer; int leases; boolean disposed; final boolean subscription;
        Handle(long pointer, boolean subscription) { this.pointer = pointer; this.subscription = subscription; }
        synchronized long acquire() throws IOException {
            if (disposed) throw new IOException("closed: handle disposed"); ++leases; return pointer;
        }
        void release() {
            long free = 0;
            synchronized (this) { --leases; if (disposed && leases == 0) { free = pointer; pointer = 0; } }
            if (free != 0) Native.free(free, subscription);
        }
        void close(int operation) throws IOException {
            long p;
            synchronized (this) {
                if (disposed) return;
                disposed = true; ++leases; p = pointer;
            }
            try { Native.call(p, operation, EMPTY, EMPTY, EMPTY); }
            finally { release(); }
        }
        byte[] call(int op, byte[] a, byte[] b, byte[] c) throws IOException {
            long p = acquire(); try { return Native.call(p, op, a, b, c); } finally { release(); }
        }
    }
    private static byte[] string(String value) throws IOException {
        if (value == null || value.indexOf('\0') >= 0) throw new IOException("argument: null or NUL string");
        return Component.utf8(value);
    }
    public static Client connect(String host, int port, String id) throws IOException {
        if (port < 1 || port > 65535) throw new IOException("argument: invalid port");
        return new Client(Native.connect(string(host), port, string(id)));
    }
    private Client(long pointer) { handle = new Handle(pointer, false); }
    public String listTopics() throws IOException { return Component.text(handle.call(0, EMPTY, EMPTY, EMPTY)); }
    public void run() throws IOException {
        try { handle.call(4, EMPTY, EMPTY, EMPTY); }
        catch (IOException error) { if (callbackFailure != null) error.initCause(callbackFailure); throw error; }
    }
    public void close() throws IOException {
        handle.close(5);
    }
    public static final class Options {
        public boolean latest, udp, ackReceived;
        public int capacity = 256, maxRetries = 0, ackTimeoutMs = 100, backoffMultiplier = 2, waitTimeoutMs = 100;
        public String overflow = "reject_new";
        public static Options fifo() { return new Options(); }
        public static Options latest() { Options o = new Options(); o.latest = true; o.capacity = 1; o.overflow = "drop_oldest"; return o; }
        byte[] json() throws IOException {
            if (capacity <= 0 || maxRetries < 0 || ackTimeoutMs <= 0 || backoffMultiplier < 1 || waitTimeoutMs <= 0)
                throw new IOException("qos: invalid option range");
            return string("{\"buffer\":\"" + (latest ? "latest" : "fifo") + "\",\"capacity\":" + capacity +
                ",\"overflow\":" + Json.quote(overflow) + ",\"ack\":\"" + (ackReceived ? "received" : "none") +
                "\",\"max_retries\":" + maxRetries + ",\"ack_timeout_ms\":" + ackTimeoutMs +
                ",\"backoff_multiplier\":" + backoffMultiplier + ",\"wait_timeout_ms\":" + waitTimeoutMs +
                ",\"transport\":\"" + (udp ? "udp" : "tcp") + "\"}");
        }
    }
    public interface Callback<T> { void onMessage(T message, MessageInfo info) throws Exception; }
    public static final class MessageInfo {
        public final String publicationId, topic, epoch; public final BigInteger sequence;
        MessageInfo(byte[] id, byte[] topic, byte[] epoch, long sequence) throws IOException {
            publicationId = Component.text(id); this.topic = Component.text(topic); this.epoch = Component.text(epoch);
            byte[] bytes = new byte[8]; SdlCodec.putLong(bytes, 0, sequence); this.sequence = new BigInteger(1, bytes);
        }
    }
    private static String root(Class<?> type) throws IOException {
        try { return (String)type.getField("SDL_NAME").get(null); }
        catch (ReflectiveOperationException error) { throw new IOException("argument: expected generated SDL message", error); }
    }
    public <T extends SdlCodec.Message> Publisher<T> publisher(String topic, Class<T> type) throws IOException {
        return new Publisher<T>(this, topic, type);
    }
    public static final class Publisher<T extends SdlCodec.Message> implements AutoCloseable {
        private final Client owner; private final byte[] topic; private final Class<T> type;
        private final SdlCodec.Context codec; private final long catalogueId;
        private boolean closed;
        Publisher(Client owner, String topic, Class<T> type) throws IOException {
            this.owner = owner; this.topic = string(topic); this.type = type;
            String catalogue = SdlCodec.description(type); codec = SdlCodec.prepare(catalogue, type);
            catalogueId = SdlCodec.catalogueId(catalogue);
            owner.handle.call(1, this.topic, string(root(type)), string(catalogue));
        }
        public synchronized String publish(T message) throws IOException {
            if (closed) throw new IOException("closed: publisher");
            if (!type.isInstance(message)) throw new IOException("argument: wrong message type");
            byte[] envelope = SdlCodec.packEnvelope(catalogueId, SdlCodec.encode(codec, message));
            return Component.text(owner.handle.call(3, topic, envelope, EMPTY));
        }
        public synchronized void close() throws IOException {
            if (closed) return; closed = true;
            synchronized (owner.handle) { if (owner.handle.disposed) return; }
            owner.handle.call(2, topic, EMPTY, EMPTY);
        }
    }
    private static final class State<T extends SdlCodec.Message> {
        final Client owner; final Class<T> type; final Callback<T> callback; final String catalogue, root;
        String remote; SdlCodec.Context decoder;
        State(Client owner, Class<T> type, Callback<T> callback) throws IOException {
            this.owner = owner; this.type = type; this.callback = callback;
            catalogue = SdlCodec.description(type); root = root(type);
        }
        // Called only by the subscription's admission worker, before ACK/admission.
        Object decode(byte[] cat, byte[] wire) throws Exception {
            String text = Component.text(cat);
            if (!text.equals(remote)) {
                if (!SdlCodec.compareCatalogues(text, catalogue, root, true).compatible()) throw new IOException("schema: incompatible catalogue");
                decoder = SdlCodec.prepare(text, type); remote = text;
            }
            SdlCodec.Envelope envelope = SdlCodec.unpackEnvelope(wire);
            if (envelope.catalogueId != SdlCodec.catalogueId(text)) throw new IOException("schema: catalogue identity mismatch");
            return type.cast(SdlCodec.decode(decoder, envelope.payload));
        }
        void deliver(Object value, byte[] id, byte[] topic, byte[] epoch, long sequence) throws Exception {
            ACTIVE.set(this);
            try { callback.onMessage(type.cast(value), new MessageInfo(id, topic, epoch, sequence)); }
            finally { ACTIVE.remove(); }
        }
        void failed(Throwable failure) { if (owner.callbackFailure == null) owner.callbackFailure = failure; }
    }
    public <T extends SdlCodec.Message> Subscription subscribe(String topic, Class<T> type, Options options, Callback<T> callback) throws IOException {
        if (callback == null || options == null) throw new IOException("argument: callback/options required");
        State<T> state = new State<T>(this, type, callback);
        long p = handle.acquire();
        try {
            return new Subscription(Native.subscribe(p, string(topic), string(state.root), string(state.catalogue), options.json(), state), state);
        } finally { handle.release(); }
    }
    public static final class Subscription implements AutoCloseable {
        private final Handle handle; private final State<?> state;
        Subscription(long p, State<?> state) { handle = new Handle(p, true); this.state = state; }
        public String stats() throws IOException { return Component.text(handle.call(8, EMPTY, EMPTY, EMPTY)); }
        public void unsubscribe() throws IOException { handle.call(6, EMPTY, EMPTY, EMPTY); }
        public boolean waitClosed(int timeoutMs) throws IOException {
            if (ACTIVE.get() == state) throw new IOException("argument: cannot wait from own callback");
            if (timeoutMs < 0) throw new IOException("argument: negative timeout");
            byte[] timeout = new byte[4]; SdlCodec.putInt(timeout, 0, timeoutMs);
            return handle.call(7, timeout, EMPTY, EMPTY)[0] != 0;
        }
        /** Releases this handle without waiting. Use unsubscribe/waitClosed before close to join callbacks. */
        public void close() throws IOException {
            handle.close(6);
        }
    }
}
