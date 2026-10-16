#!/usr/bin/env python3
"""One predicate corpus, six object adapters, and one independent wire oracle."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'build/filter'

def run(command, **kwargs):
    subprocess.run(list(map(str,command)),cwd=ROOT,check=True,
        env=dict(os.environ,CCACHE_DISABLE='1'),**kwargs)

def sample():
    from telemetry.model import Sample,Vector,Detail,State
    from sdl_runtime import Complex32
    return Sample(sequence=2**64-1,temperature=12.34,active=True,
        detail=Detail(code=0,label='ok'),position=Vector(x=3.,y=4.),
        points=[Vector(x=1.,y=2.),Vector(x=10.,y=20.),Vector(x=-1.,y=.5)],
        matrix=[[1,2,3],[4,5,6]],label='café',counts=[-2,0,7],
        samples=[Complex32(1,2),Complex32(-3,.5)],state=State.ON)

def ada_string(s):
    parts=[];current=''
    for b in s.encode('utf8'):
        if 32<=b<127:current+=chr(b)
        else:
            if current:parts.append('"'+current.replace('"','""')+'"');current=''
            parts.append("Character'Val (%d)"%b)
    if current:parts.append('"'+current.replace('"','""')+'"')
    return '('+' & '.join(parts)+')'

def cases_files(cases):
    (OUT/'test_cases.h').write_text('typedef struct {const char *expression;int object,wire;} FilterCase;\n'
        +'static const FilterCase filter_cases[]={\n'+',\n'.join('{'+json.dumps(s,ensure_ascii=False)+',%d,%d}'%(a,b) for s,a,b in cases)+'\n};\n'
        +'#define FILTER_CASE_COUNT (sizeof(filter_cases)/sizeof(filter_cases[0]))\n')
    (OUT/'cases.rs').write_text('const CASES:&[(&str,i32,i32)]=&[\n'+',\n'.join('('+json.dumps(s,ensure_ascii=False)+',%d,%d)'%(a,b) for s,a,b in cases)+'\n];\n')
    (OUT/'FilterCases.java').write_text('public final class FilterCases {\npublic static final String[] EXPRESSIONS={'+','.join(json.dumps(s,ensure_ascii=False) for s,_,_ in cases)+'};\n'
        +'public static final int[] OBJECT={'+','.join(str(a) for _,a,_ in cases)+'};\n'
        +'public static final int[] WIRE={'+','.join(str(b) for _,_,b in cases)+'};\n}\n')
    (OUT/'filter_cases.m').write_text('function cases=filter_cases()\ncases={\n'+';\n'.join("native2unicode(uint8(["+' '.join(str(b) for b in s.encode('utf8'))+"]), 'UTF-8'),%d,%d"%(a,b) for s,a,b in cases)+'\n};\nend\n')
    (OUT/'filter_cases.ads').write_text('package Filter_Cases is\ntype String_Access is access constant String;\ntype Text_Array is array(Positive range <>) of String_Access;\n'
        +'Expressions:constant Text_Array:=('+',\n'.join("new String'"+ada_string(s) for s,_,_ in cases)+');\n'
        +'Object_States:constant array(Expressions\'Range) of Integer:=('+','.join(str(a) for _,a,_ in cases)+');\n'
        +'Wire_States:constant array(Expressions\'Range) of Integer:=('+','.join(str(b) for _,_,b in cases)+');\nend Filter_Cases;\n')

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gnatmake',default=os.environ.get('GNATMAKE','/home/t0005140/opt/gnat-21.1/bin/gnatmake'));parser.add_argument('--matlab');args=parser.parse_args()
    run([sys.executable,'gen/generator.py','-c','-rust','-python','-java','-ada','-matlab','tests/fixtures/filter',OUT/'generated'])
    run([sys.executable,'gen/generator.py','-matlab','tests/fixtures/filter_review',OUT/'review-generated'])
    sys.path[:0]=[str(ROOT/'runtime/filter/python'),str(OUT/'release'),str(ROOT/'runtime/python'),str(OUT/'generated/python')]
    cases=json.loads((ROOT/'tests/filter/cases.json').read_text());cases_files(cases)
    from telemetry.model import Sample
    from sdl_runtime import description,prepare,encode,decode
    from sdl_filter import Filter,Status,encode_filtered,decode_filtered
    value=sample();catalogue=description(Sample);context=prepare(catalogue,Sample);wire=encode(context,value);(OUT/'golden.bin').write_bytes(wire)
    for expression,a,b in cases:
        with Filter(catalogue,'telemetry.Sample',expression) as f:
            assert f.evaluate(value).status==a,(expression,f.evaluate(value))
            assert f.evaluate_wire(wire).status==b,(expression,f.evaluate_wire(wire))
    f=Filter(catalogue,'telemetry.Sample','temperature>12.32');encoded,status=encode_filtered(context,value,f);assert encoded==wire and status.status==Status.MATCH
    decoded,status=decode_filtered(context,wire,f);assert decoded.temperature<12.32 and status.status==Status.NO_MATCH
    value.detail=None;value.points=[]
    for expression in ('!exists(detail)','all(points,p=>false)','!any(points,p=>true)'):
        assert Filter(catalogue,'telemetry.Sample',expression).evaluate(value).matched
    class NamedComplex:
        _SDL_NAME='complex'
        _SDL_FIELDS=((1,'value'),)
        value=3.0
    class Holder:
        _SDL_NAME='Holder'
        _SDL_FIELDS=((1,'item'),)
        item=NamedComplex()
    with Filter('SDL2|Holder{1:complex:item;}|complex{1:fl64:value;}','Holder','item.value==3') as predicate:
        assert predicate.evaluate(Holder()).matched
    print('Python filters and wire oracle passed.',flush=True)
    cc=shlex.split(os.environ.get('CC','cc'));link=['-L'+str(OUT/'release'),'-lsdl_filter','-Wl,-rpath,'+str(OUT/'release')]
    c_sources=sorted((ROOT/'runtime/c').glob('*.c'))+sorted((OUT/'generated/c').rglob('*.c'))
    run(cc+['-std=c99','-Wall','-Wextra','-Werror','-O3','-Iruntime/c','-Iruntime/filter/include','-Iruntime/filter/c','-I'+str(OUT/'generated/c'),'-I'+str(OUT),
        'runtime/filter/c/sdl_filter_native.c',*c_sources,'tests/filter/c/check.c',*link,'-lm','-o',OUT/'c-check'])
    run([OUT/'c-check'])
    run([os.environ.get('CARGO','cargo'),'run','--offline','--release','--manifest-path','tests/filter/rust/Cargo.toml','--bin','filter-checks','--target-dir',OUT/'rust-check'])
    classes=OUT/'java';classes.mkdir(exist_ok=True)
    javac=shlex.split(os.environ.get('JAVAC','javac'))[0]
    run([javac,'-encoding','UTF-8','-source','7','-target','7','-Xlint:-options','-d',classes,*sorted((OUT/'generated/java').rglob('*.java')),'runtime/filter/java/SdlFilter.java',OUT/'FilterCases.java','tests/filter/java/FilterChecks.java'])
    run(shlex.split(os.environ.get('JAVA','java'))+['-Djava.library.path='+str(OUT/'release'),'-cp',classes,'FilterChecks'])
    ada=OUT/'ada';ada.mkdir(exist_ok=True);objects=[]
    for source in sorted((ROOT/'runtime/c').glob('*.c'))+[ROOT/'runtime/ada/sdl_ada_bridge.c']:
        obj=ada/(source.stem+'.o');run(cc+['-std=c99','-O3','-Iruntime/c','-c',source,'-o',obj]);objects.append(obj)
    run([args.gnatmake,*(['--RTS=' + os.environ.get('ADA_RTS', 'sjlj')] if os.environ.get('ADA_RTS', 'sjlj') else []),'-gnat2012','-gnata','-gnatn','-O3','-Iruntime/ada','-Iruntime/filter/ada','-I'+str(OUT/'generated/ada'),'-I'+str(OUT),'-D',ada,'-o',ada/'filter-check','tests/filter/ada/filter_checks.adb','-largs',*objects,*link,'-lm'])
    run([ada/'filter-check'])
    mex='matlab-mex' if args.matlab else 'mex'
    setup="addpath('build/filter/generated/matlab');addpath('runtime/filter/matlab');addpath('build/filter/"+mex+"');addpath('build/filter');addpath('tests/filter/matlab');filter_checks;"
    if args.matlab:
        run([args.matlab,'-nodisplay','-nosplash','-nodesktop','-nojvm','-r',"try,"+setup+"catch err,disp(getReport(err,'extended'));exit(1);end;exit(0)"])
    else:
        run([os.environ.get('OCTAVE','octave'),'--quiet','--no-gui','--eval',setup])
    print('All six filter adapters passed.',flush=True)

if __name__=='__main__':main()
