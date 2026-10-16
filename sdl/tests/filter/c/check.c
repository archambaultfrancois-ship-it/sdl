#include "telemetry/model.h"
#include "sdl_registry.h"
#include "type_engine.h"
#include "sdl_filter_native.h"
#include "test_cases.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#define Sample sdlq_9_74656c656d65747279_6_53616d706c65
#define Vector sdlq_9_74656c656d65747279_6_566563746f72
static SdlFilterBytes text(const char *s){return (SdlFilterBytes){(const uint8_t *)s,strlen(s)};}
int main(void){
    assert(register_all_types());
    Vector points[]={{1,2},{10,20},{-1,.5}};int16_t counts[]={-2,0,7};float complex samples[]={1+2*I,-3+.5*I};
    Sample value={0};value.sequence=UINT64_MAX;value.temperature=12.34;value.active=true;value.has_detail=true;value.detail.code=0;value.detail.label="ok";value.position=(Vector){3,4};value.points=points;value.points_count=3;
    int32_t matrix[2][3]={{1,2,3},{4,5,6}};memcpy(value.matrix,matrix,sizeof(matrix));value.label="café";value.counts=counts;value.counts_count=3;value.samples=samples;value.samples_count=2;value.state=SDLQ_9_74656C656D65747279_5_5374617465_ON;
    const char *names[]={"telemetry.Sample"};size_t cn,wn;char *catalogue=type_description(names,1,&cn);assert(catalogue);SdlContext *context=type_prepare(catalogue,cn);assert(context);uint8_t *wire=type_encode(context,names[0],&value,&wn);assert(wire);
    FILE *f=fopen("build/filter/golden.bin","rb");assert(f);uint8_t golden[1024];size_t n=fread(golden,1,sizeof(golden),f);fclose(f);assert(n==wn&&!memcmp(wire,golden,n));
    const SdlTypeDesc *desc=sdl_filter_native_descriptor(names[0]);assert(desc);
    for(size_t i=0;i<FILTER_CASE_COUNT;++i){SdlFilter *filter=sdl_filter_prepare((SdlFilterBytes){(uint8_t *)catalogue,cn},text(names[0]),text(filter_cases[i].expression));assert(filter);
        int a=sdl_filter_evaluate_native(filter,desc,&value),b=sdl_filter_evaluate_wire(filter,(SdlFilterBytes){wire,wn});
        if(a!=filter_cases[i].object||b!=filter_cases[i].wire){fprintf(stderr,"%s: object=%d wire=%d %s\n",filter_cases[i].expression,a,b,sdl_filter_error());return 1;}sdl_filter_free(filter);
    }
    value.has_detail=false;value.points_count=0;
    const char *empty[]={"!exists(detail)","all(points,p=>false)","!any(points,p=>true)"};
    for(size_t i=0;i<3;++i){SdlFilter *p=sdl_filter_prepare((SdlFilterBytes){(uint8_t *)catalogue,cn},text(names[0]),text(empty[i]));assert(p&&sdl_filter_evaluate_native(p,desc,&value)==1);sdl_filter_free(p);}
    value.label=NULL;
    SdlFilter *empty_string=sdl_filter_prepare((SdlFilterBytes){(uint8_t *)catalogue,cn},text(names[0]),text("len(label)==0"));
    assert(empty_string&&sdl_filter_evaluate_native(empty_string,desc,&value)==1);sdl_filter_free(empty_string);
    type_free(wire);type_context_free(context);type_free(catalogue);puts("C filters and wire oracle passed.");return 0;
}
