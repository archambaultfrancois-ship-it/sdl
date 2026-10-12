#!/usr/bin/env python3
"""Compile all bindings against independent representation wire oracles."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PACKET='01bf'+'ff'*9+'01'+'03'+'00'*4+'4045000000000000'+'0000'
UNSIGNED='01'+'ff'*15+'02'+'00'*8+'ff'*8
ARRAYS='01800fff0007ff031c8001f000ff00000002686900'
RECORDS='020280087ff0a5'

C=r'''
#include "packet.h"
#include "unsigned.h"
#include "arrays.h"
#include "records.h"
#include "sdl_registry.h"
#include "type_engine.h"
#include <assert.h>
#include <string.h>
static void check(const char *name,const uint8_t *descriptor,size_t ds,const void *x,const char *hex){
 SdlContext *c=type_prepare(descriptor,ds);assert(c);size_t n,m;void *b=type_encode(c,name,x,&n);assert(b&&n==strlen(hex)/2&&type_encode_size(c,name,x)==n);
 for(size_t i=0;i<n;i++){unsigned v;assert(sscanf(hex+2*i,"%2x",&v)==1);assert(((unsigned char*)b)[i]==v);}
 void *y=type_decode(c,b,n);assert(y);void *again=type_encode(c,name,y,&m);assert(again&&m==n&&!memcmp(b,again,n));
 SdlDynamicMessage *d=type_decode_dynamic(c,b,n);assert(d);type_dynamic_free(d);
 for(size_t i=0;i<n;i++)assert(!type_context_validate(c,b,i));
 char *desc=type_description(&name,1,&m);assert(desc&&m==ds&&!memcmp(desc,descriptor,ds));free(desc);
 type_free(again);type_free(y);type_free(b);type_context_free(c);
}
int main(void){
 register_all_types();Packet p={true,false,-1,UINT64_MAX,-2,42};check("Packet",PACKET_SCHEMA_DESCRIPTOR,PACKET_SCHEMA_DESCRIPTOR_SIZE,&p,"@PACKET@");
 uint64_t values[]={0,UINT64_MAX};Unsigned u={UINT8_MAX,UINT16_MAX,UINT32_MAX,UINT64_MAX,2,values};check("Unsigned",UNSIGNED_SCHEMA_DESCRIPTOR,UNSIGNED_SCHEMA_DESCRIPTOR_SIZE,&u,"@UNSIGNED@");
 uint8_t flags[]={0,7,1};Arrays a={0};a.matrix[0][0]=-2048;a.matrix[0][1]=-1;a.matrix[1][1]=2047;a.flags_count=3;a.flags=flags;a.has_code=true;a.code=-1;a.child.delta=-1;a.child.level=31;a.label="hi";check("Arrays",ARRAYS_SCHEMA_DESCRIPTOR,ARRAYS_SCHEMA_DESCRIPTOR_SIZE,&a,"@ARRAYS@");
 BitRecord items[]={{-2048,true},{2047,false}};Records records={2,items,165};check("Records",RECORDS_SCHEMA_DESCRIPTOR,RECORDS_SCHEMA_DESCRIPTOR_SIZE,&records,"@RECORDS@");
 SdlContext *rc=type_prepare(RECORDS_SCHEMA_DESCRIPTOR,RECORDS_SCHEMA_DESCRIPTOR_SIZE);size_t rn;uint8_t *rb=type_encode(rc,"Records",&records,&rn);assert(rb);for(size_t i=3;i<=5;i+=2){rb[i]|=1;assert(!type_context_validate(rc,rb,rn));rb[i]&=(uint8_t)~1;}items[0].value=2048;assert(!type_encode(rc,"Records",&records,&rn));free(rb);type_context_free(rc);
 SdlContext *c=type_prepare(PACKET_SCHEMA_DESCRIPTOR,PACKET_SCHEMA_DESCRIPTOR_SIZE);size_t n;p.level=32;assert(!type_encode(c,"Packet",&p,&n));p.level=-1;uint8_t *b=type_encode(c,"Packet",&p,&n);assert(b);b[n-1]=1;assert(!type_context_validate(c,b,n));free(b);type_context_free(c);
}
'''
RUST=r'''
#[path="@GENERATED@/rust/mod.rs"] mod generated;
use generated::{packet::Packet,unsigned::Unsigned,arrays::{Arrays,Child},records::{Records,BitRecord}};
use sdl_runtime::*;
fn check<T:SdlMessage>(x:&T,hex:&str,types:&[TypeInfo]){let d=description(types).unwrap();assert_eq!(d,T::DESCRIPTOR);let c=prepare(&d,types).unwrap();let b=encode(&c,x).unwrap();let actual=b.iter().map(|b|format!("{b:02x}")).collect::<String>();assert_eq!(actual,hex);let y:T=decode(&c,&b).unwrap();assert_eq!(encode(&c,&y).unwrap(),b);decode_dynamic(&c,&b).unwrap();for i in 0..b.len(){assert!(decode::<T>(&c,&b[..i]).is_err());}}
fn main(){let p=Packet{active:true,alarm:false,level:-1,counter:u64::MAX,delta:-2,value:42.0};check(&p,"@PACKET@",&[Packet::type_info()]);let u=Unsigned{tiny:u8::MAX,small:u16::MAX,medium:u32::MAX,large:u64::MAX,values:vec![0,u64::MAX]};check(&u,"@UNSIGNED@",&[Unsigned::type_info()]);let a=Arrays{matrix:[[-2048,-1],[0,2047]],flags:vec![0,7,1],code:Some(-1),child:Child{delta:-1,level:31},label:"hi".to_owned()};check(&a,"@ARRAYS@",&[Arrays::type_info(),Child::type_info()]);let records=Records{items:vec![BitRecord{value:-2048,flag:true},BitRecord{value:2047,flag:false}],tail:165};check(&records,"@RECORDS@",&[Records::type_info(),BitRecord::type_info()]);let rc=prepare(Records::DESCRIPTOR,&[Records::type_info(),BitRecord::type_info()]).unwrap();let rb=encode(&rc,&records).unwrap();for i in [3,5]{let mut bad=rb.clone();bad[i]|=1;assert!(decode::<Records>(&rc,&bad).is_err());}let mut records=records;records.items[0].value=2048;assert!(encode(&rc,&records).is_err());let c=prepare(Packet::DESCRIPTOR,&[Packet::type_info()]).unwrap();let mut b=encode(&c,&p).unwrap();*b.last_mut().unwrap()=1;assert!(decode::<Packet>(&c,&b).is_err());let mut bad=p;bad.level=32;assert!(encode(&c,&bad).is_err());}
'''
JAVA=r'''
public class RepresentationCheck {
 static void check(SdlCodec.Message x,String hex,Class<?>...types){String d=SdlCodec.description(types);SdlCodec.Context c=SdlCodec.prepare(d,types);byte[]b=SdlCodec.encode(c,x);StringBuilder h=new StringBuilder();for(byte v:b)h.append(String.format("%02x",v&255));if(!h.toString().equals(hex))throw new AssertionError(h);SdlCodec.Message y=SdlCodec.decode(c,b);if(!java.util.Arrays.equals(SdlCodec.encode(c,y),b))throw new AssertionError();for(int i=0;i<b.length;i++){try{SdlCodec.decode(c,java.util.Arrays.copyOf(b,i));throw new AssertionError();}catch(SdlCodec.CodecException ok){}}}
 public static void main(String[]args){packet.Packet p=new packet.Packet();p.active=true;p.level=-1;p.counter=-1L;p.delta=-2;p.value=42;check(p,"@PACKET@",packet.Packet.class);unsigned.Unsigned u=new unsigned.Unsigned();u.tiny=-1;u.small=-1;u.medium=-1;u.large=-1L;u.values=new long[]{0,-1L};check(u,"@UNSIGNED@",unsigned.Unsigned.class);arrays.Arrays a=new arrays.Arrays();a.matrix=new short[][]{{-2048,-1},{0,2047}};a.flags=new byte[]{0,7,1};a.code=Byte.valueOf((byte)-1);a.child.delta=-1;a.child.level=31;a.label="hi";check(a,"@ARRAYS@",arrays.Arrays.class,arrays.Child.class);records.Records r=new records.Records();records.BitRecord first=new records.BitRecord();first.value=-2048;first.flag=true;records.BitRecord second=new records.BitRecord();second.value=2047;r.items=java.util.Arrays.asList(first,second);r.tail=(byte)165;check(r,"@RECORDS@",records.Records.class,records.BitRecord.class);SdlCodec.Context rc=SdlCodec.prepare(records.Records.SDL_DESCRIPTOR,records.Records.class,records.BitRecord.class);byte[]rb=SdlCodec.encode(rc,r);for(int i:new int[]{3,5}){byte[]bad=rb.clone();bad[i]|=1;try{SdlCodec.decode(rc,bad);throw new AssertionError();}catch(SdlCodec.CodecException ok){}}first.value=2048;try{SdlCodec.encode(rc,r);throw new AssertionError();}catch(SdlCodec.CodecException ok){}if(!SdlCodec.unsignedString(-1L,64).equals("18446744073709551615")||SdlCodec.compareUnsigned(-1L,0L,64)!=1)throw new AssertionError();SdlCodec.Context c=SdlCodec.prepare(packet.Packet.SDL_DESCRIPTOR,packet.Packet.class);byte[]b=SdlCodec.encode(c,p);b[b.length-1]=1;try{SdlCodec.decode(c,b);throw new AssertionError();}catch(SdlCodec.CodecException ok){}p.level=32;try{SdlCodec.encode(c,p);throw new AssertionError();}catch(SdlCodec.CodecException ok){}}
}
'''
MATLAB=r'''
addpath('@GENERATED@/matlab');
x=Packet();x.active=true;x.level=int16(-1);x.counter=intmax('uint64');x.delta=int64(-2);x.value=42;
c=sdl_matlab_runtime('prepare',Packet('description'),{'Packet'});b=Packet('encode',c,x);assert(strcmpi(reshape(dec2hex(b,2)',1,[]),'@PACKET@'));y=Packet('decode',c,b);assert(y.counter==x.counter&&y.delta==x.delta);
u=Unsigned();u.tiny=intmax('uint8');u.small=intmax('uint16');u.medium=intmax('uint32');u.large=intmax('uint64');u.values=[uint64(0);intmax('uint64')];
c=sdl_matlab_runtime('prepare',Unsigned('description'),{'Unsigned'});b=Unsigned('encode',c,u);assert(strcmpi(reshape(dec2hex(b,2)',1,[]),'@UNSIGNED@'));v=Unsigned('decode',c,b);assert(v.large==u.large&&isequal(v.values,u.values));
a=Arrays();a.matrix=int16([-2048 0;-1 2047]);a.flags=uint8([0;7;1]);a.code=int8(-1);a.child.delta=int8(-1);a.child.level=uint8(31);a.label='hi';
c=sdl_matlab_runtime('prepare',Arrays('description'),{'Arrays'});b=Arrays('encode',c,a);assert(strcmpi(reshape(dec2hex(b,2)',1,[]),'@ARRAYS@'));z=Arrays('decode',c,b);assert(isequal(z.matrix,a.matrix)&&z.code==a.code&&z.child.delta==a.child.delta);
for k=0:numel(b)-1,rejected=false;try,Arrays('decode',c,b(1:k));catch,rejected=true;end;assert(rejected);end
b(end)=1;rejected=false;try,Arrays('decode',c,b);catch,rejected=true;end;assert(rejected);
assert(strcmp(Arrays('description'),sdl_matlab_runtime('description',{'Arrays'})));
r=Records();r.items.value=int16([-2048;2047]);r.items.flag=logical([1;0]);r.tail=uint8(165);
c=sdl_matlab_runtime('prepare',Records('description'),{'Records'});b=Records('encode',c,r);assert(strcmpi(reshape(dec2hex(b,2)',1,[]),'@RECORDS@'),reshape(dec2hex(b,2)',1,[]));z=Records('decode',c,b);assert(z.items.value(1)==-2048&&z.items.flag(1)&&z.items.value(2)==2047&&~z.items.flag(2)&&z.tail==165);
for k=0:numel(b)-1,rejected=false;try,Records('decode',c,b(1:k));catch,rejected=true;end;assert(rejected);end
for k=[4 6],bad=b;bad(k)=bitor(bad(k),uint8(1));rejected=false;try,Records('decode',c,bad);catch,rejected=true;end;assert(rejected);end
r.items.value(1)=int16(2048);rejected=false;try,Records('encode',c,r);catch,rejected=true;end;assert(rejected);

'''
ADA=r'''
with SDL_packet;
with SDL_unsigned;
with SDL_arrays;
with SDL_records;
with SDL_Runtime; use SDL_Runtime;
with Interfaces; use Interfaces;
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
procedure Representation_Check is
 procedure Check (Data : Bytes; Hex : String) is
 begin
  pragma Assert (Data'Length = Hex'Length / 2);
  for I in Data'Range loop pragma Assert (Data (I) = Unsigned_8'Value ("16#" & Hex (2*I-1 .. 2*I) & "#")); end loop;
 end;
 P : SDL_packet.T_Packet;
 U : SDL_unsigned.T_Unsigned;
 A : SDL_arrays.T_Arrays;
 CP : Context := SDL_packet.Prepare;
 CU : Context := SDL_unsigned.Prepare;
 CA : Context := SDL_arrays.Prepare;
 CR : Context := SDL_records.Prepare;
 R : SDL_records.T_Records;
begin
 P.F_active := True; P.F_level := -1; P.F_counter := Unsigned_64'Last; P.F_delta := -2; P.F_value := 42.0;
 declare D : Bytes := SDL_packet.Encode (CP,P); Y : SDL_packet.T_Packet := SDL_packet.Decode (CP,D); begin Check (D,"@PACKET@"); pragma Assert (Y.F_counter=P.F_counter and Y.F_delta=P.F_delta); end;
 U.F_tiny := Unsigned_8'Last;U.F_small := Unsigned_16'Last;U.F_medium := Unsigned_32'Last;U.F_large := Unsigned_64'Last;U.F_values.Append (0);U.F_values.Append (Unsigned_64'Last);
 declare D : Bytes := SDL_unsigned.Encode (CU,U); Y : SDL_unsigned.T_Unsigned := SDL_unsigned.Decode (CU,D); begin Check (D,"@UNSIGNED@"); pragma Assert (Y.F_large=U.F_large and Y.F_values.Element (2)=Unsigned_64'Last); end;
 A.F_matrix := ((-2048,-1),(0,2047));A.F_flags.Append (0);A.F_flags.Append (7);A.F_flags.Append (1);A.H_code := True;A.F_code := -1;A.F_child := (-1,31);A.F_label := To_Unbounded_String ("hi");
 declare D : Bytes := SDL_arrays.Encode (CA,A); Y : SDL_arrays.T_Arrays := SDL_arrays.Decode (CA,D); begin Check (D,"@ARRAYS@");pragma Assert (Y.F_child.F_delta=-1 and Y.F_code=-1); end;
 R.F_items.Append (SDL_records.T_BitRecord'(F_value => -2048, F_flag => True));R.F_items.Append (SDL_records.T_BitRecord'(F_value => 2047, F_flag => False));R.F_tail := 165;
 declare D : Bytes := SDL_records.Encode (CR,R); Y : SDL_records.T_Records := SDL_records.Decode (CR,D); begin Check (D,"@RECORDS@");pragma Assert (Y.F_items.Element (1).F_value=-2048 and Y.F_items.Element (1).F_flag and Y.F_items.Element (2).F_value=2047 and not Y.F_items.Element (2).F_flag and Y.F_tail=165); end;
 declare D : Bytes := SDL_records.Encode (CR,R); begin
  for I in 0 .. D'Length-1 loop
   declare Y : SDL_records.T_Records; Rejected : Boolean := False; begin
    begin Y := SDL_records.Decode (CR,D(D'First .. D'First+I-1)); exception when Codec_Error => Rejected := True; end;
    pragma Assert (Rejected);
   end;
  end loop;
  for I in 1 .. 2 loop
   declare Bad : Bytes := D; Y : SDL_records.T_Records; Rejected : Boolean := False; begin
    Bad (2*I+2) := Bad (2*I+2) or 1;
    begin Y := SDL_records.Decode (CR,Bad); exception when Codec_Error => Rejected := True; end;
    pragma Assert (Rejected);
   end;
  end loop;
 end;
 R.F_items.Replace_Element (1,SDL_records.T_BitRecord'(F_value => 2048,F_flag => True));
 declare Rejected : Boolean := False; begin
  begin declare D : Bytes := SDL_records.Encode (CR,R); begin null; end; exception when Codec_Error => Rejected := True; end;
  pragma Assert (Rejected);
 end;
end Representation_Check;
'''

def run(command,cwd=None,env=None):
 result=subprocess.run(command,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 if result.returncode:
  print(result.stdout,file=sys.stderr)
  raise RuntimeError('failed: '+shlex.join(map(str,command)))


def main():
 parser=argparse.ArgumentParser();parser.add_argument('--gnatmake',default='gnatmake');parser.add_argument('--matlab');args=parser.parse_args()
 with tempfile.TemporaryDirectory(prefix='sdl-representation-') as tmp:
  work=Path(tmp);generated=work/'generated'
  run([sys.executable,str(ROOT/'gen/generator.py'),'-c','-rust','-python','-java','-ada','-matlab',str(ROOT/'tst/fixtures/representation'),str(generated)])
  def expand(source):
   for k,v in {'PACKET':PACKET,'UNSIGNED':UNSIGNED,'ARRAYS':ARRAYS,'RECORDS':RECORDS,'GENERATED':str(generated)}.items():source=source.replace('@'+k+'@',v)
   return source
  cc=shlex.split(os.environ.get('CC','gcc'));env=dict(os.environ,CCACHE_DISABLE='1')
  (work/'check.c').write_text('#include <stdio.h>\n#include <stdlib.h>\n'+expand(C))
  sources=[str(p) for p in (ROOT/'runtime/c').glob('*.c')]+[str(p) for p in (generated/'c').glob('*.c')]
  run(cc+['-std=c99','-Wall','-Wextra','-I'+str(ROOT/'runtime/c'),'-I'+str(generated/'c')]+sources+[str(work/'check.c'),'-lm','-o',str(work/'c-check')],env=env)
  run([str(work/'c-check')]);print('C representation and unsigned oracles passed.',flush=True)
  rust=work/'rust';(rust/'src').mkdir(parents=True)
  (rust/'Cargo.toml').write_text('[package]\nname="representation-check"\nversion="0.1.0"\nedition="2021"\n[dependencies]\nsdl-runtime={path="'+str(ROOT/'runtime/rust')+'"}\n')
  (rust/'src/main.rs').write_text(expand(RUST));run(shlex.split(os.environ.get('CARGO','cargo'))+['run','--offline','--manifest-path',str(rust/'Cargo.toml')]);print('Rust representation and unsigned oracles passed.',flush=True)
  (generated/'java/RepresentationCheck.java').write_text(expand(JAVA));classes=work/'classes';classes.mkdir()
  run(shlex.split(os.environ.get('JAVAC','javac'))+['-d',str(classes)]+[str(p) for p in (generated/'java').glob('*.java')]);run(shlex.split(os.environ.get('JAVA','java'))+['-cp',str(classes),'RepresentationCheck']);print('Java representation and unsigned oracles passed.',flush=True)
  (work/'check_representation.m').write_text(expand(MATLAB))
  run(shlex.split(os.environ.get('OCTAVE','octave'))+['--quiet','--no-gui','--eval',"run('"+str(work/'check_representation.m')+"')"]);print('Octave representation and unsigned oracles passed.',flush=True)
  if args.matlab:
   command="try, run('"+str(work/'check_representation.m')+"'); catch err, disp(getReport(err,'extended')); exit(1); end; exit(0)"
   run(shlex.split(args.matlab)+['-nodisplay','-nosplash','-nodesktop','-nojvm','-r',command]);print('Matlab representation and unsigned oracles passed.',flush=True)
  # Ada uses the C dynamic fallback for custom wire representations.
  library=work/'librepresentation.so'
  run(cc+['-std=c99','-fPIC','-shared','-I'+str(ROOT/'runtime/c')]+[str(p) for p in (ROOT/'runtime/c').glob('*.c')]+[str(ROOT/'runtime/ada/sdl_ada_bridge.c'),'-lm','-o',str(library)],env=env)
  (work/'representation_check.adb').write_text(expand(ADA))
  gnat=shlex.split(args.gnatmake)
  run(gnat+['--RTS='+os.environ.get('ADA_RTS','sjlj'),'-gnat2012','-gnata','-I'+str(ROOT/'runtime/ada'),'-I'+str(generated/'ada'),'representation_check.adb','-largs','-L'+str(work),'-lrepresentation','-Wl,-rpath,'+str(work)],cwd=work)
  run([str(work/'representation_check')]);print('Ada representation and unsigned oracles passed.',flush=True)
 return 0

if __name__=='__main__':
 try:sys.exit(main())
 except (RuntimeError,OSError) as error:print(error,file=sys.stderr);sys.exit(1)
