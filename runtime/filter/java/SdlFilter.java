package sdl.filter;

import java.nio.charset.StandardCharsets;

/** Optional native predicate service. All expressions are bound to a catalogue. */
public final class SdlFilter implements AutoCloseable {
    static { System.loadLibrary("sdl_filter_jni"); }
    public static final int ERROR=-1, NO_MATCH=0, MATCH=1, INDETERMINATE=2;
    public static final class Result {
        public final int status;
        public final String diagnostic;
        private Result(int code,String text){status=code;diagnostic=text;}
        public boolean matched(){return status==MATCH;}
    }
    private long handle;
    public SdlFilter(String catalogue,String root,String expression){
        handle=nPrepare(utf8(catalogue),utf8(root),utf8(expression));
    }
    private static byte[] utf8(String text){return text.getBytes(StandardCharsets.UTF_8);}
    private Result result(int status){return new Result(status,status==ERROR?new String(nError(),StandardCharsets.UTF_8):"");}
    private void open(){if(handle==0)throw new IllegalStateException("filter is closed");}
    public synchronized Result evaluate(Object message){open();return result(nObject(handle,message));}
    public synchronized Result evaluateWire(byte[] bytes){open();return result(nWire(handle,bytes,bytes.length));}
    public synchronized void close(){if(handle!=0){nFree(handle);handle=0;}}
    private static native long nPrepare(byte[] catalogue,byte[] root,byte[] expression);
    private static native int nWire(long handle,byte[] bytes,int length);
    private static native int nObject(long handle,Object object);
    private static native byte[] nError();
    private static native void nFree(long handle);
}
