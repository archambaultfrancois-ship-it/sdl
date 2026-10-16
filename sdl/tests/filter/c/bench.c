#define _POSIX_C_SOURCE 200809L
#include "telemetry/model.h"
#include "sdl_registry.h"
#include "type_engine.h"
#include "sdl_filter_native.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#define Vector sdlq_9_74656c656d65747279_6_566563746f72
#define Sample sdlq_9_74656c656d65747279_6_53616d706c65
static volatile int sink;
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec*1e-9;}
static SdlFilterBytes text(const char *s){return (SdlFilterBytes){(const uint8_t *)s,strlen(s)};}
static void bench(const char *catalogue,const char *name,const void *value,const char *label,const char *expression,int n){
    SdlContext *context=type_prepare(catalogue,strlen(catalogue));const SdlTypeDesc *desc=sdl_filter_native_descriptor(name);assert(context&&desc);
    size_t size;uint8_t *wire=type_encode(context,name,value,&size);assert(wire);
    SdlFilter *filter=sdl_filter_prepare(text(catalogue),text(name),text(expression));assert(filter);
    assert(sdl_filter_evaluate_native(filter,desc,value)==1&&sdl_filter_evaluate_wire(filter,(SdlFilterBytes){wire,size})==1);
    double timings[4];
    for(int action=0;action<4;++action){
        double trials[5];
        for(int trial=-1;trial<5;++trial){
            int iterations=trial<0?1000:n;double start=now();
            for(int i=0;i<iterations;++i){
                if(action==0){size_t output_size;void *out=type_encode(context,name,value,&output_size);assert(out);sink=(int)output_size;type_free(out);}
                else if(action==1){void *out=type_decode(context,wire,size);assert(out);sink=out!=NULL;type_free(out);}
                else if(action==2)sink=sdl_filter_evaluate_native(filter,desc,value);
                else sink=sdl_filter_evaluate_wire(filter,(SdlFilterBytes){wire,size});
            }
            if(trial>=0)trials[trial]=(now()-start)*1e9/n;
        }
        for(int i=0;i<5;++i)for(int j=i+1;j<5;++j)if(trials[j]<trials[i]){double t=trials[i];trials[i]=trials[j];trials[j]=t;}
        timings[action]=trials[2];
    }
    printf("BENCH C %s %.1f %.1f %.1f %.1f\n",label,timings[0],timings[1],timings[2],timings[3]);sdl_filter_free(filter);
    type_free(wire);type_context_free(context);
}
int main(void){
    assert(register_all_types());const char *catalogue="SDL2|telemetry.Vector{1:fl64:x;2:fl64:y;}";const char *name="telemetry.Vector";Vector value={3,4};
    bench(catalogue,name,&value,"scalar","x>0",100000);
    bench(catalogue,name,&value,"arithmetic","x*x+y*y<=25",100000);
    Sample sample={0};Vector points[256];for(size_t i=0;i<256;++i)points[i]=value;
    sample.temperature=12.34;sample.position=value;sample.points=points;sample.points_count=256;sample.label="";
    name="telemetry.Sample";size_t size;char *full=type_description(&name,1,&size);assert(full);
    bench(full,name,&sample,"collection256","all(points,p=>p.x>=0&&p.y>=0)",1000);type_free(full);return 0;
}
