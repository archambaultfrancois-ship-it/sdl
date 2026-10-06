public final class SdlJavaBench {
   private static int readIterations() {
      String setting=System.getenv("SDL_BENCH_ITERATIONS");
      if(setting==null||setting.length()==0)return 200;
      for(int i=0;i<setting.length();i++)if(setting.charAt(i)<'0'||setting.charAt(i)>'9')return 200;
      try { int value=Integer.parseInt(setting);return value>0?value:200; }
      catch(NumberFormatException ignored) { return 200; }
   }
   public static void main(String[] args) {
      int iterations=readIterations();
      int samples=5000;benchmark.BenchPayload input=new benchmark.BenchPayload();StringBuilder header=new StringBuilder();for(int i=0;i<200;i++)header.append('H');input.header=header.toString();
      float[] re=new float[samples],im=new float[samples];for(int i=0;i<samples;i++){re[i]=i*0.25f;im[i]=-(i%97)*0.5f;}input.samples=new SdlCodec.Complex32Array(re,im);
      byte[] wire=input.encode();if(wire.length!=40224+benchmark.BenchPayload.SDL_DESCRIPTOR.length)throw new IllegalStateException("unexpected benchmark frame size");benchmark.BenchPayload.decode(wire);long start=System.nanoTime();long bytes=0;
      for(int i=0;i<iterations;i++)bytes+=input.encode().length;long encodeTime=System.nanoTime()-start;
      start=System.nanoTime();for(int i=0;i<iterations;i++)benchmark.BenchPayload.decode(wire);long decodeTime=System.nanoTime()-start;
      double secEncode=encodeTime/1.0e9,secDecode=decodeTime/1.0e9;
      System.out.printf("Java SDL benchmark: %d samples, %d iterations, %d bytes/message%n",samples,iterations,wire.length);
      System.out.printf("  encode: %.1f msg/s, %.2f MiB/s%n",iterations/secEncode,(bytes/secEncode)/(1024.0*1024.0));
      System.out.printf("  decode: %.1f msg/s, %.2f MiB/s%n",iterations/secDecode,((double)wire.length*iterations/secDecode)/(1024.0*1024.0));
   }
}
