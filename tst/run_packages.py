#!/usr/bin/env python3
"""Package isolation, cross-file references and one wire oracle in six languages."""
import argparse
import importlib
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'gen'), str(ROOT / 'runtime/python')]
from generator import c_identifier
from ada_backend import typ

GENERATED = ROOT / 'build/packages/generated'
BUILD = ROOT / 'build/packages'
NAME = 'navigation.geometry.Sample'


def run(command, **kwargs):
   # Like the representation tests, keep compiler cache writes out of the test.
   kwargs.setdefault('env', dict(os.environ, CCACHE_DISABLE='1'))
   subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def python_check():
   sys.path.insert(0, str(GENERATED / 'python'))
   from sdl_runtime import (description, prepare, encode, decode, decode_dynamic,
      message_names, catalogue_id, CodecError)
   nav = importlib.import_module('navigation.geometry.shapes')
   radio = importlib.import_module('radio.shapes')
   glob = importlib.import_module('shapes')
   value = nav.Sample(local=nav.Point(11, nav.State.ONE), global_state=glob.State.ONE,
      remote=radio.Point(33), state=nav.State.ONE, remote_state=radio.State.ONE,
      points=[nav.Point(44), nav.Point(55)], remote_points=[radio.Point(66)],
      detail=nav.Sample_1(nav.State.ONE, 77), **{'global_point': glob.Point(22)})
   text = description([nav.Sample, radio.Point, glob.Point])
   ctx = prepare(text, [nav.Sample, radio.Point, glob.Point])
   assert set(message_names(ctx)) == {'Point', 'radio.Point', NAME,
      'navigation.geometry.Point', NAME + '$1'}
   wire = encode(ctx, value)
   import struct
   expected = bytes([3]) + struct.pack('>11i', 11, 1, 22, 33, 1, 1, 1, 44, 0, 55, 0) + bytes([1]) + struct.pack('>i', 66) + bytes([1]) + struct.pack('>2i', 1, 77)
   assert wire == expected
   restored = decode(ctx, wire)
   assert type(restored.local) is nav.Point
   assert type(getattr(restored, 'global_point')) is glob.Point
   assert type(restored.remote) is radio.Point
   assert type(restored.state) is nav.State
   assert type(restored.global_state) is glob.State
   assert type(restored.remote_state) is radio.State
   assert encode(ctx, restored) == wire
   assert decode_dynamic(ctx, wire).type_name == NAME
   assert catalogue_id(description(nav.Point)) != catalogue_id(description(glob.Point))
   for point in (glob.Point(1), radio.Point(2), nav.Point(3)):
      result = decode(ctx, encode(ctx, point))
      assert type(result) is type(point) and result.x == point.x
   bad = nav.Sample(local=glob.Point(11), **{'global_point': glob.Point(22)})
   try:
      encode(ctx, bad)
   except CodecError:
      pass
   else:
      raise AssertionError('different packages were accepted as the same type')
   (BUILD / 'catalogue.sdl2').write_text(text)
   print('Python package identity, enums, anonymous types and references passed.', flush=True)
   return wire.hex()


C = r'''
#include "shapes.h"
#include "radio/shapes.h"
#include "navigation/geometry/shapes.h"
#include "sdl_registry.h"
#include "type_engine.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
 assert(register_all_types());assert(register_all_types());
 const char *names[]={"Point","radio.Point","navigation.geometry.Sample"};
 size_t n;char *text=type_description(names,3,&n);assert(text);
 SdlContext *c=type_prepare(text,n);assert(c);type_free(text);
 @SAMPLE@ x={0};x.local.x=11;x.local.state=1;x.global_point.x=22;x.remote.x=33;
 x.state=1;x.global_state=1;x.remote_state=1;x.points[0].x=44;x.points[1].x=55;
 @RADIO_POINT@ remote={66};x.remote_points_count=1;x.remote_points=&remote;
 x.has_detail=true;x.detail.state=1;x.detail.code=77;
 unsigned char *bytes=type_encode(c,"navigation.geometry.Sample",&x,&n);assert(bytes);
 const char *hex="@HEX@";assert(n==strlen(hex)/2);
 for(size_t i=0;i<n;i++){unsigned v;assert(sscanf(hex+2*i,"%2x",&v)==1);assert(bytes[i]==v);}
 @SAMPLE@ *y=type_decode(c,bytes,n);assert(y&&y->local.x==11&&y->global_point.x==22&&y->remote.x==33);
 assert(y->remote_points[0].x==66&&y->has_detail&&y->detail.code==77);
 SdlDynamicMessage *d=type_decode_dynamic(c,bytes,n);assert(d&&!strcmp(d->type_name,"navigation.geometry.Sample"));type_dynamic_free(d);
 type_free(y);type_free(bytes);
 Point p={1};@RADIO_POINT@ r={2};@NAV_POINT@ q={3,0};
 const void *values[]={&p,&r,&q};const char *types[]={"Point","radio.Point","navigation.geometry.Point"};
 for(int i=0;i<3;i++){bytes=type_encode(c,types[i],values[i],&n);assert(bytes);assert(!strcmp(type_message_name(c,bytes,n),types[i]));y=type_decode(c,bytes,n);assert(y);type_free(y);type_free(bytes);}
 type_context_free(c);
}
'''
JAVA = r'''
import sdl.runtime.SdlCodec;
import navigation.geometry.shapes;
public class PackageCheck {
 static void check(boolean condition){if(!condition)throw new AssertionError();}
 public static void main(String[]args){
  SdlCodec.Context c=SdlCodec.prepare(SdlCodec.description(shapes.Sample.class,radio.shapes.Point.class,sdl.globaltypes.shapes.Point.class),shapes.Sample.class,radio.shapes.Point.class,sdl.globaltypes.shapes.Point.class);
  shapes.Sample x=new shapes.Sample();x.local.x=11;x.local.state=shapes.State.ONE;x.global_point.x=22;x.remote.x=33;
  x.state=shapes.State.ONE;x.global_state=sdl.globaltypes.shapes.State.ONE;x.remote_state=radio.shapes.State.ONE;
  x.points[0].x=44;x.points[1].x=55;radio.shapes.Point r=new radio.shapes.Point();r.x=66;x.remote_points.add(r);
  x.detail=new shapes.Sample_1();x.detail.state=shapes.State.ONE;x.detail.code=77;
  byte[] b=SdlCodec.encode(c,x);StringBuilder hex=new StringBuilder();for(byte v:b)hex.append(String.format("%02x",v&255));check(hex.toString().equals("@HEX@"));
  shapes.Sample y=(shapes.Sample)SdlCodec.decode(c,b);check(y.local.x==11&&y.global_point.x==22&&y.remote.x==33&&y.detail.code==77&&y.remote_points.get(0).x==66);
  check(java.util.Arrays.equals(b,SdlCodec.encode(c,y)));check(SdlCodec.messageName(shapes.Point.class).equals("navigation.geometry.Point"));
  SdlCodec.Message[] points={new sdl.globaltypes.shapes.Point(),new radio.shapes.Point(),new shapes.Point()};
  for(SdlCodec.Message p:points)check(SdlCodec.decode(c,SdlCodec.encode(c,p)).getClass()==p.getClass());
 }
}
'''
RUST = r'''
#[path="@GENERATED@/rust/mod.rs"] mod generated;
use generated::navigation::geometry::shapes::{Sample,Sample1,Point,State};
use generated::radio::shapes::{Point as RadioPoint,State as RadioState};
use generated::shapes::{Point as GlobalPoint,State as GlobalState};
use sdl_runtime::*;
fn main(){
 let types=[Sample::type_info(),Point::type_info(),RadioPoint::type_info(),GlobalPoint::type_info()];
 let text=description(&types).unwrap();let c=prepare(&text,&types).unwrap();
 let x=Sample{local:Point{x:11,state:State::One},global_point:GlobalPoint{x:22},remote:RadioPoint{x:33},state:State::One,global_state:GlobalState::One,remote_state:RadioState::One,points:[Point{x:44,state:State::Zero},Point{x:55,state:State::Zero}],remote_points:vec![RadioPoint{x:66}],detail:Some(Sample1{state:State::One,code:77})};
 let b=encode(&c,&x).unwrap();assert_eq!(b.iter().map(|b|format!("{b:02x}")).collect::<String>(),"@HEX@");
 let y:Sample=decode(&c,&b).unwrap();assert_eq!(y,x);assert_eq!(encode(&c,&y).unwrap(),b);decode_dynamic(&c,&b).unwrap();
 let p=GlobalPoint{x:1};let y:GlobalPoint=decode(&c,&encode(&c,&p).unwrap()).unwrap();assert_eq!(y,p);
 let p=RadioPoint{x:2};let y:RadioPoint=decode(&c,&encode(&c,&p).unwrap()).unwrap();assert_eq!(y,p);
 let p=Point{x:3,state:State::Zero};let y:Point=decode(&c,&encode(&c,&p).unwrap()).unwrap();assert_eq!(y,p);
 assert!(decode::<Point>(&c,&encode(&c,&GlobalPoint{x:1}).unwrap()).is_err());
}
'''
MATLAB = r'''
function package_check()
types={'navigation.geometry.Sample','radio.Point','Point'};
text=sdl_matlab_runtime('description',types);ctx=sdl_matlab_runtime('prepare',text,types);
x=navigation.geometry.Sample();x.local.x=int32(11);x.local.state=int32(1);x.global_point.x=int32(22);x.remote.x=int32(33);
x.state=int32(1);x.global_state=int32(1);x.remote_state=int32(1);x.points(1).x=int32(44);x.points(2).x=int32(55);
x.remote_points.x=int32(66);x.detail=navigation.geometry.Sample_1();x.detail.state=int32(1);x.detail.code=int32(77);
b=navigation.geometry.Sample('encode',ctx,x);hex=lower(reshape(dec2hex(b,2).',1,[]));assert(strcmp(hex,'@HEX@'));
y=navigation.geometry.Sample('decode',ctx,b);assert(y.local.x==11&&y.global_point.x==22&&y.remote.x==33&&y.detail.code==77);
assert(isequal(b,navigation.geometry.Sample('encode',ctx,y)));
for name={'Point','radio.Point','navigation.geometry.Point'},p=feval(name{1});wire=feval(name{1},'encode',ctx,p);feval(name{1},'decode',ctx,wire);assert(strcmp(ctx.names{double(wire(1))},name{1}));end
end
'''
ADA = r'''
with SDL.navigation.geometry.shapes;use SDL.navigation.geometry.shapes;
with SDL_Runtime;use SDL_Runtime;
with Interfaces;use type Interfaces.Integer_32;
with Ada.Text_IO;use Ada.Text_IO;
procedure Package_Check is
 C:Context:=Prepare;
 X:@ADA_SAMPLE@;
 R:@ADA_RADIO_POINT@;
 Expected:constant Bytes:=(@ADA_BYTES@);
begin
 X.F_local.F_x:=11;X.F_local.F_state:=1;X.F_global_point.F_x:=22;X.F_remote.F_x:=33;
 X.F_state:=1;X.F_global_state:=1;X.F_remote_state:=1;
 X.F_points(1).F_x:=44;X.F_points(2).F_x:=55;R.F_x:=66;X.F_remote_points.Append(R);
 X.H_detail:=True;X.F_detail.F_state:=1;X.F_detail.F_code:=77;
 declare B:constant Bytes:=Encode(C,X);Y:constant @ADA_SAMPLE@:=Decode(C,B);begin
  pragma Assert(Y.F_local.F_x=11 and Y.F_global_point.F_x=22 and Y.F_remote.F_x=33 and Y.F_detail.F_code=77);
  pragma Assert(B=Expected);
  pragma Assert(B=Encode(C,Y));
  Put_Line("Ada package references and codec passed.");
 end;

end Package_Check;
'''


def main():
   parser = argparse.ArgumentParser()
   parser.add_argument('--gnatmake', default=str(Path.home() / 'opt/gnat-21.1/bin/gnatmake'))
   parser.add_argument('--matlab')
   args = parser.parse_args()
   BUILD.mkdir(parents=True, exist_ok=True)
   run([sys.executable, 'gen/generator.py', '-c', '-rust', '-python', '-java', '-matlab', '-ada',
      str(ROOT / 'tst/fixtures/packages'), str(GENERATED)])
   hex_value = python_check()
   replacements = {'HEX': hex_value, 'GENERATED': str(GENERATED),
      'SAMPLE': c_identifier(NAME), 'RADIO_POINT': c_identifier('radio.Point'),
      'NAV_POINT': c_identifier('navigation.geometry.Point'),
      'ADA_BYTES': ','.join('16#' + hex_value[i:i+2] + '#' for i in range(0, len(hex_value), 2)),
      'ADA_SAMPLE': typ(NAME), 'ADA_RADIO_POINT': typ('radio.Point')}
   def source(text):
      for key, value in replacements.items():
         text = text.replace('@' + key + '@', value)
      return text
   (BUILD / 'package_check.c').write_text(source(C))
   run(shlex.split(os.environ.get('CC', 'cc')) + ['-std=c99', '-Wall', '-Wextra', '-Werror', '-Iruntime/c',
      '-I' + str(GENERATED / 'c')] + [str(p) for p in (ROOT / 'runtime/c').glob('*.c')] +
      [str(p) for p in (GENERATED / 'c').rglob('*.c')] + [str(BUILD / 'package_check.c'), '-lm', '-o', str(BUILD / 'check-c')])
   run([str(BUILD / 'check-c')]); print('C package registry and wire oracle passed.', flush=True)
   rust = BUILD / 'rust'; (rust / 'src').mkdir(parents=True, exist_ok=True)
   (rust / 'Cargo.toml').write_text('[package]\nname="package-check"\nversion="0.1.0"\nedition="2021"\n[dependencies]\nsdl-runtime={path="' + str(ROOT / 'runtime/rust') + '"}\n')
   (rust / 'src/main.rs').write_text(source(RUST))
   run(shlex.split(os.environ.get('CARGO', 'cargo')) + ['run', '--offline', '--quiet', '--manifest-path', str(rust / 'Cargo.toml')])
   print('Rust package references and wire oracle passed.', flush=True)
   (BUILD / 'PackageCheck.java').write_text(source(JAVA)); classes = BUILD / 'classes'; classes.mkdir(exist_ok=True)
   run(shlex.split(os.environ.get('JAVAC', 'javac')) + ['-encoding', 'UTF-8', '-d', str(classes)] +
      [str(p) for p in (GENERATED / 'java').rglob('*.java')] + [str(BUILD / 'PackageCheck.java')])
   run(shlex.split(os.environ.get('JAVA', 'java')) + ['-cp', str(classes), 'PackageCheck'])
   print('Java package references and wire oracle passed.', flush=True)
   (BUILD / 'package_check.m').write_text(source(MATLAB))
   expression = "addpath('" + str(GENERATED / 'matlab') + "');addpath('" + str(BUILD) + "');package_check"
   if args.matlab:
      run([args.matlab, '-nodisplay', '-nosplash', '-nodesktop', '-nojvm', '-r',
         "try," + expression + ";catch err,disp(getReport(err,'extended'));exit(1);end;exit(0)"])
   else:
      run([os.environ.get('OCTAVE', 'octave'), '--quiet', '--no-gui', '--eval', expression])
   print('Matlab/Octave package references and wire oracle passed.', flush=True)
   ada = BUILD / 'ada'; ada.mkdir(exist_ok=True)
   (BUILD / 'package_check.adb').write_text(source(ADA))
   objects = []
   for path in list((ROOT / 'runtime/c').glob('*.c')) + [ROOT / 'runtime/ada/sdl_ada_bridge.c']:
      obj = ada / (path.stem + '.o'); objects.append(str(obj))
      run(shlex.split(os.environ.get('CC', 'cc')) + ['-std=c99', '-Iruntime/c', '-c', str(path), '-o', str(obj)])
   env = dict(os.environ, CCACHE_DISABLE='1'); env['PATH'] = str(Path(args.gnatmake).parent) + ':' + env['PATH']
   run([args.gnatmake, '--RTS=sjlj', '-gnat2012', '-gnata', '-Iruntime/ada', '-I' + str(GENERATED / 'ada'),
      '-D', str(ada), '-o', str(ada / 'check'), str(BUILD / 'package_check.adb'), '-largs'] + objects + ['-lm'], env=env)
   run([str(ada / 'check')])


if __name__ == '__main__':
   main()
