import sdl.filter.SdlFilter;
import sdl.runtime.SdlCodec;
import telemetry.model;
import java.util.Arrays;
import java.nio.file.Files;
import java.nio.file.Paths;
public class FilterChecks {
    public static class NamedComplex {
        public static final String SDL_NAME="complex";
        public static final SdlCodec.Field[] SDL_FIELDS={new SdlCodec.Field(1,"value","required","fl64",double.class,new int[0],false)};
        public double value=3;
    }
    public static class Holder {
        public static final String SDL_NAME="Holder";
        public static final SdlCodec.Field[] SDL_FIELDS={new SdlCodec.Field(1,"item","required","complex",NamedComplex.class,new int[0],false)};
        public NamedComplex item=new NamedComplex();
    }
    static void check(boolean value,String text){if(!value)throw new AssertionError(text);}
    public static void main(String[] args)throws Exception{
        model.Sample v=new model.Sample();v.sequence=-1L;v.temperature=12.34;v.active=true;v.detail=new model.Detail();v.detail.code=0;v.detail.label="ok";v.position.x=3;v.position.y=4;
        model.Vector p=new model.Vector();p.x=1;p.y=2;v.points.add(p);p=new model.Vector();p.x=10;p.y=20;v.points.add(p);p=new model.Vector();p.x=-1;p.y=.5;v.points.add(p);
        v.matrix=new int[][]{{1,2,3},{4,5,6}};v.label="café";v.counts.add((short)-2);v.counts.add((short)0);v.counts.add((short)7);v.samples=new SdlCodec.Complex32Array(new float[]{1,-3},new float[]{2,.5f});v.state=model.State.ON;
        String catalogue=SdlCodec.description(model.Sample.class);SdlCodec.Context context=SdlCodec.prepare(catalogue,model.Sample.class);byte[] wire=SdlCodec.encode(context,v);check(Arrays.equals(wire,Files.readAllBytes(Paths.get("build/filter/golden.bin"))),"wire oracle");
        for(int i=0;i<FilterCases.EXPRESSIONS.length;i++){String expression=FilterCases.EXPRESSIONS[i];try(SdlFilter f=new SdlFilter(catalogue,"telemetry.Sample",expression)){
            SdlFilter.Result a=f.evaluate(v),b=f.evaluateWire(wire);check(a.status==FilterCases.OBJECT[i],expression+" object "+a.status+" "+a.diagnostic);check(b.status==FilterCases.WIRE[i],expression+" wire "+b.status+" "+b.diagnostic);}}
        v.detail=null;v.points.clear();for(String expression:new String[]{"!exists(detail)","all(points,p=>false)","!any(points,p=>true)"})try(SdlFilter f=new SdlFilter(catalogue,"telemetry.Sample",expression)){check(f.evaluate(v).matched(),expression);}
        final String vectorCatalogue="SDL2|telemetry.Vector{1:fl64:x;2:fl64:y;}";
        final model.Vector vector=new model.Vector();vector.x=3;vector.y=4;
        final byte[] vectorWire=SdlCodec.encode(SdlCodec.prepare(vectorCatalogue,model.Vector.class),vector);
        final Throwable[] failures=new Throwable[4];Thread[] workers=new Thread[failures.length];
        for(int k=0;k<workers.length;++k){final int index=k;workers[k]=new Thread(new Runnable(){public void run(){
            try{for(int i=0;i<30;++i)try(SdlFilter f=new SdlFilter(vectorCatalogue,"telemetry.Vector","x*x+y*y<=25")){
                check(f.evaluate(vector).matched()&&f.evaluateWire(vectorWire).matched(),"concurrent filters");
            }}catch(Throwable error){failures[index]=error;}
        }});workers[k].start();}
        for(Thread worker:workers)worker.join();for(Throwable failure:failures)if(failure!=null)throw new AssertionError(failure);
        SdlFilter closed=new SdlFilter(vectorCatalogue,"telemetry.Vector","true");closed.close();closed.close();
        boolean rejected=false;try{closed.evaluate(vector);}catch(IllegalStateException expected){rejected=true;}check(rejected,"closed filter");
        try(SdlFilter f=new SdlFilter("SDL2|Holder{1:complex:item;}|complex{1:fl64:value;}","Holder","item.value==3")){
            check(f.evaluate(new Holder()).matched(),"message named complex");
        }
        System.out.println("Java filters and wire oracle passed.");
    }
}
