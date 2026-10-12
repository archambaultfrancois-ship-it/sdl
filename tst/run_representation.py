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
QUANTIZED='0382ff4001f0040081e83c5e0aa5'
QUANTUM_WIDE='02800000008000000000000000'

C=r'''
#include "packet.h"
#include "unsigned.h"
#include "arrays.h"
#include "records.h"
#include "quantized.h"
#include <math.h>
#include "sdl_registry.h"
#include "type_engine.h"
#include <assert.h>
#include <string.h>
static SdlContext *context(const char *name){
 size_t size;char *descriptor=type_description(&name,1,&size);assert(descriptor&&size==strlen(descriptor));
 SdlContext *c=type_prepare(descriptor,size);free(descriptor);assert(c);return c;
}
static void check(const char *name,const void *x,const char *hex){
 SdlContext *c=context(name);size_t n,m;void *b=type_encode(c,name,x,&n);assert(b&&n==strlen(hex)/2&&type_encode_size(c,name,x)==n);
 for(size_t i=0;i<n;i++){unsigned v;assert(sscanf(hex+2*i,"%2x",&v)==1);assert(((unsigned char*)b)[i]==v);}
 void *y=type_decode(c,b,n);assert(y);void *again=type_encode(c,name,y,&m);assert(again&&m==n&&!memcmp(b,again,n));
 SdlDynamicMessage *d=type_decode_dynamic(c,b,n);assert(d);type_dynamic_free(d);
 for(size_t i=0;i<n;i++)assert(!type_context_validate(c,b,i));
 type_free(again);type_free(y);type_free(b);type_context_free(c);
}
int main(void){
 register_all_types();
 float qs[]={0.125f,0.375f,-0.125f,-0.375f};Quantized q={0};
 q.flag=true;q.value=0.625f;q.precise=-1.25;q.has_maybe=true;q.maybe=-1.25;
 q.samples_count=4;q.samples=qs;q.matrix[0][0]=-8;q.matrix[0][1]=7.5;q.matrix[1][0]=0.75;q.matrix[1][1]=-0.75;q.child.value=2.625;q.tail=165;
 check("Quantized",&q,"@QUANTIZED@");
 SdlContext *qc=context("Quantized");size_t qn;
 float invalid[]={NAN,INFINITY,-INFINITY,16,-16.5};
 for(size_t i=0;i<5;i++){q.value=invalid[i];assert(!type_encode(qc,"Quantized",&q,&qn));}
 type_context_free(qc);
 QuantWide qw={-0x1p29f,-0x1p61};check("QuantWide",&qw,"@QUANTUM_WIDE@");
 register_all_types();Packet p={true,false,-1,UINT64_MAX,-2,42};check("Packet",&p,"@PACKET@");
 uint64_t values[]={0,UINT64_MAX};Unsigned u={UINT8_MAX,UINT16_MAX,UINT32_MAX,UINT64_MAX,2,values};check("Unsigned",&u,"@UNSIGNED@");
 uint8_t flags[]={0,7,1};Arrays a={0};a.matrix[0][0]=-2048;a.matrix[0][1]=-1;a.matrix[1][1]=2047;a.flags_count=3;a.flags=flags;a.has_code=true;a.code=-1;a.child.delta=-1;a.child.level=31;a.label="hi";check("Arrays",&a,"@ARRAYS@");
 BitRecord items[]={{-2048,true},{2047,false}};Records records={2,items,165};check("Records",&records,"@RECORDS@");
 SdlContext *rc=context("Records");size_t rn;uint8_t *rb=type_encode(rc,"Records",&records,&rn);assert(rb);for(size_t i=3;i<=5;i+=2){rb[i]|=1;assert(!type_context_validate(rc,rb,rn));rb[i]&=(uint8_t)~1;}items[0].value=2048;assert(!type_encode(rc,"Records",&records,&rn));free(rb);type_context_free(rc);
 SdlContext *c=context("Packet");size_t n;p.level=32;assert(!type_encode(c,"Packet",&p,&n));p.level=-1;uint8_t *b=type_encode(c,"Packet",&p,&n);assert(b);b[n-1]=1;assert(!type_context_validate(c,b,n));free(b);type_context_free(c);
}
'''
RUST=r'''
#[path="@GENERATED@/rust/mod.rs"] mod generated;
use generated::{packet::Packet,unsigned::Unsigned,arrays::{Arrays,Child},records::{Records,BitRecord}};
use sdl_runtime::*;
fn quantized(){
 use generated::quantized::{Quantized,QuantChild,QuantWide};
 check(&QuantWide{small:-2f32.powi(29),large:-2f64.powi(61)},"@QUANTUM_WIDE@",&[QuantWide::type_info()]);
 let mut q=Quantized{flag:true,value:0.625,precise:-1.25,maybe:Some(-1.25),samples:vec![0.125,0.375,-0.125,-0.375],matrix:[[-8.0,7.5],[0.75,-0.75]],child:QuantChild{value:2.625},tail:165};
 check(&q,"@QUANTIZED@",&[Quantized::type_info(),QuantChild::type_info()]);
 let c=prepare(Quantized::DESCRIPTOR,&[Quantized::type_info(),QuantChild::type_info()]).unwrap();
 let b=encode(&c,&q).unwrap();let y:Quantized=decode(&c,&b).unwrap();assert_eq!(y.value,0.5);assert_eq!(y.child.value,2.5);
 for x in [f32::NAN,f32::INFINITY,f32::NEG_INFINITY,16.0,-16.5]{q.value=x;assert!(encode(&c,&q).is_err());}
 for suffix in ["q0.1","b12,q0","b12,qNaN","b65,q0.1","b12,q0.1,v","b12,q0.1,q0.1","b12,q1e-1"]{
  assert!(prepare(&format!("SDL2|InvalidQ{{1:fl64:value@{suffix};}}"),&[]).is_err());
 }
 for t in ["int8","int16","int32","int64","uint8","uint16","uint32","uint64"]{
  for (marker,dimensions) in [("",""),("?",""),("*",""),("+",""),("","[2]")]{
   assert!(prepare(&format!("SDL2|InvalidQ{{1{marker}:{t}{dimensions}:value@b1;}}"),&[]).is_ok());
   for suffix in ["q0.1","b1,q0.1","q0.1,b1","v,q0.1"]{
    assert!(prepare(&format!("SDL2|InvalidQ{{1{marker}:{t}{dimensions}:value@{suffix};}}"),&[]).is_err());
   }
  }
 }
}
fn check<T:SdlMessage>(x:&T,hex:&str,types:&[TypeInfo]){let d=description(types).unwrap();assert_eq!(d,T::DESCRIPTOR);let c=prepare(&d,types).unwrap();let b=encode(&c,x).unwrap();let actual=b.iter().map(|b|format!("{b:02x}")).collect::<String>();assert_eq!(actual,hex);let y:T=decode(&c,&b).unwrap();assert_eq!(encode(&c,&y).unwrap(),b);decode_dynamic(&c,&b).unwrap();for i in 0..b.len(){assert!(decode::<T>(&c,&b[..i]).is_err());}}
fn main(){let p=Packet{active:true,alarm:false,level:-1,counter:u64::MAX,delta:-2,value:42.0};check(&p,"@PACKET@",&[Packet::type_info()]);let u=Unsigned{tiny:u8::MAX,small:u16::MAX,medium:u32::MAX,large:u64::MAX,values:vec![0,u64::MAX]};check(&u,"@UNSIGNED@",&[Unsigned::type_info()]);let a=Arrays{matrix:[[-2048,-1],[0,2047]],flags:vec![0,7,1],code:Some(-1),child:Child{delta:-1,level:31},label:"hi".to_owned()};check(&a,"@ARRAYS@",&[Arrays::type_info(),Child::type_info()]);let records=Records{items:vec![BitRecord{value:-2048,flag:true},BitRecord{value:2047,flag:false}],tail:165};check(&records,"@RECORDS@",&[Records::type_info(),BitRecord::type_info()]);let rc=prepare(Records::DESCRIPTOR,&[Records::type_info(),BitRecord::type_info()]).unwrap();let rb=encode(&rc,&records).unwrap();for i in [3,5]{let mut bad=rb.clone();bad[i]|=1;assert!(decode::<Records>(&rc,&bad).is_err());}let mut records=records;records.items[0].value=2048;assert!(encode(&rc,&records).is_err());let c=prepare(Packet::DESCRIPTOR,&[Packet::type_info()]).unwrap();let mut b=encode(&c,&p).unwrap();*b.last_mut().unwrap()=1;assert!(decode::<Packet>(&c,&b).is_err());let mut bad=p;bad.level=32;assert!(encode(&c,&bad).is_err());}
'''
JAVA=r'''
public class RepresentationCheck {
 static void quantized(){
 quantized.QuantWide wide=new quantized.QuantWide();wide.small=-Math.scalb(1.0f,29);wide.large=-Math.scalb(1.0,61);check(wide,"@QUANTUM_WIDE@",quantized.QuantWide.class);
 quantized.Quantized q=new quantized.Quantized();q.flag=true;q.value=0.625f;q.precise=-1.25;q.maybe=Double.valueOf(-1.25);
 q.samples=new float[]{0.125f,0.375f,-0.125f,-0.375f};q.matrix=new double[][]{{-8,7.5},{0.75,-0.75}};q.child.value=2.625;q.tail=(byte)165;
 check(q,"@QUANTIZED@",quantized.Quantized.class,quantized.QuantChild.class);
 SdlCodec.Context c=SdlCodec.prepare(quantized.Quantized.SDL_DESCRIPTOR,quantized.Quantized.class,quantized.QuantChild.class);
 for(float x:new float[]{Float.NaN,Float.POSITIVE_INFINITY,Float.NEGATIVE_INFINITY,16,-16.5f}){q.value=x;try{SdlCodec.encode(c,q);throw new AssertionError();}catch(SdlCodec.CodecException ok){}}
 for(String suffix:new String[]{"q0.1","b12,q0","b12,qNaN","b65,q0.1","b12,q0.1,v","b12,q0.1,q0.1","b12,q1e-1"}){try{SdlCodec.prepare("SDL2|InvalidQ{1:fl64:value@"+suffix+";}");throw new AssertionError();}catch(SdlCodec.CodecException ok){}}
 for(String t:new String[]{"int8","int16","int32","int64","uint8","uint16","uint32","uint64"}){
  for(String[] shape:new String[][]{{"",""},{"?",""},{"*",""},{"+",""},{"","[2]"}}){
   String prefix="SDL2|InvalidQ{1"+shape[0]+":"+t+shape[1]+":value@";
   SdlCodec.prepare(prefix+"b1;}");
   for(String suffix:new String[]{"q0.1","b1,q0.1","q0.1,b1","v,q0.1"}){try{SdlCodec.prepare(prefix+suffix+";}");throw new AssertionError(t+suffix);}catch(SdlCodec.CodecException ok){}}
  }
 }
 }
 static void check(SdlCodec.Message x,String hex,Class<?>...types){String d=SdlCodec.description(types);SdlCodec.Context c=SdlCodec.prepare(d,types);byte[]b=SdlCodec.encode(c,x);StringBuilder h=new StringBuilder();for(byte v:b)h.append(String.format("%02x",v&255));if(!h.toString().equals(hex))throw new AssertionError(h);SdlCodec.Message y=SdlCodec.decode(c,b);if(!java.util.Arrays.equals(SdlCodec.encode(c,y),b))throw new AssertionError();for(int i=0;i<b.length;i++){try{SdlCodec.decode(c,java.util.Arrays.copyOf(b,i));throw new AssertionError();}catch(SdlCodec.CodecException ok){}}}
 public static void main(String[]args){packet.Packet p=new packet.Packet();p.active=true;p.level=-1;p.counter=-1L;p.delta=-2;p.value=42;check(p,"@PACKET@",packet.Packet.class);unsigned.Unsigned u=new unsigned.Unsigned();u.tiny=-1;u.small=-1;u.medium=-1;u.large=-1L;u.values=new long[]{0,-1L};check(u,"@UNSIGNED@",unsigned.Unsigned.class);arrays.Arrays a=new arrays.Arrays();a.matrix=new short[][]{{-2048,-1},{0,2047}};a.flags=new byte[]{0,7,1};a.code=Byte.valueOf((byte)-1);a.child.delta=-1;a.child.level=31;a.label="hi";check(a,"@ARRAYS@",arrays.Arrays.class,arrays.Child.class);records.Records r=new records.Records();records.BitRecord first=new records.BitRecord();first.value=-2048;first.flag=true;records.BitRecord second=new records.BitRecord();second.value=2047;r.items=java.util.Arrays.asList(first,second);r.tail=(byte)165;check(r,"@RECORDS@",records.Records.class,records.BitRecord.class);SdlCodec.Context rc=SdlCodec.prepare(records.Records.SDL_DESCRIPTOR,records.Records.class,records.BitRecord.class);byte[]rb=SdlCodec.encode(rc,r);for(int i:new int[]{3,5}){byte[]bad=rb.clone();bad[i]|=1;try{SdlCodec.decode(rc,bad);throw new AssertionError();}catch(SdlCodec.CodecException ok){}}first.value=2048;try{SdlCodec.encode(rc,r);throw new AssertionError();}catch(SdlCodec.CodecException ok){}if(!SdlCodec.unsignedString(-1L,64).equals("18446744073709551615")||SdlCodec.compareUnsigned(-1L,0L,64)!=1)throw new AssertionError();SdlCodec.Context c=SdlCodec.prepare(packet.Packet.SDL_DESCRIPTOR,packet.Packet.class);byte[]b=SdlCodec.encode(c,p);b[b.length-1]=1;try{SdlCodec.decode(c,b);throw new AssertionError();}catch(SdlCodec.CodecException ok){}p.level=32;try{SdlCodec.encode(c,p);throw new AssertionError();}catch(SdlCodec.CodecException ok){}}
}
'''
MATLAB=r'''
addpath('@GENERATED@/matlab');
wide=QuantWide();wide.small=-single(2^29);wide.large=-2^61;wc=sdl_matlab_runtime('prepare',QuantWide('description'),{'QuantWide'});wb=QuantWide('encode',wc,wide);assert(strcmpi(reshape(dec2hex(wb,2)',1,[]),'@QUANTUM_WIDE@'));wy=QuantWide('decode',wc,wb);assert(wy.small==wide.small&&wy.large==wide.large);
q=Quantized();q.flag=true;q.value=single(0.625);q.precise=-1.25;q.maybe=-1.25;
q.samples=single([0.125;0.375;-0.125;-0.375]);q.matrix=[-8 0.75;7.5 -0.75];q.child.value=2.625;q.tail=uint8(165);
qc=sdl_matlab_runtime('prepare',Quantized('description'),{'Quantized','QuantChild'});qb=Quantized('encode',qc,q);
assert(strcmpi(reshape(dec2hex(qb,2)',1,[]),'@QUANTIZED@'));qy=Quantized('decode',qc,qb);assert(qy.value==0.5&&qy.child.value==2.5);assert(isequal(Quantized('encode',qc,qy),qb));
assert(strcmp(Quantized('description'),sdl_matlab_runtime('description',{'Quantized','QuantChild'})));
for invalid=[NaN Inf -Inf 16 -16.5],q.value=single(invalid);rejected=false;try,Quantized('encode',qc,q);catch,rejected=true;end;assert(rejected);end
for suffix={'q0.1','b12,q0','b12,qNaN','b65,q0.1','b12,q0.1,v','b12,q0.1,q0.1','b12,q1e-1'},rejected=false;try,sdl_matlab_runtime('prepare',['SDL2|InvalidQ{1:fl64:value@' suffix{1} ';}'],{});catch,rejected=true;end;assert(rejected);end
for t={'int8','int16','int32','int64','uint8','uint16','uint32','uint64'}
 for shape=1:5
  markers={'','?','*','+',''};dimensions='';if shape==5,dimensions='[2]';end
  prefix=['SDL2|InvalidQ{1' markers{shape} ':' t{1} dimensions ':value@'];
  sdl_matlab_runtime('prepare',[prefix 'b1;}'],{});
  for suffix={'q0.1','b1,q0.1','q0.1,b1','v,q0.1'},rejected=false;try,sdl_matlab_runtime('prepare',[prefix suffix{1} ';}'],{});catch,rejected=true;end;assert(rejected);end
 end
end
x=Packet();x.active=true;x.level=int16(-1);x.counter=intmax('uint64');x.delta=int64(-2);x.value=42;
c=sdl_matlab_runtime('prepare',Packet('description'),{'Packet'});b=Packet('encode',c,x);assert(strcmpi(reshape(dec2hex(b,2)',1,[]),'@PACKET@'),reshape(dec2hex(b,2)',1,[]));y=Packet('decode',c,b);assert(y.counter==x.counter&&y.delta==x.delta);
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
with SDL_quantized;
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
 Q : SDL_quantized.T_Quantized;
 CQ : Context := SDL_quantized.Prepare;
 QW : SDL_quantized.T_QuantWide;
begin
 QW.F_small := -2.0**29;QW.F_large := -2.0**61;
 declare D : Bytes := SDL_quantized.Encode (CQ,QW); Y : SDL_quantized.T_QuantWide := SDL_quantized.Decode (CQ,D); begin Check (D,"@QUANTUM_WIDE@");pragma Assert (Y.F_small=QW.F_small and Y.F_large=QW.F_large);end;
 Q.F_flag := True;Q.F_value := 0.625;Q.F_precise := -1.25;Q.H_maybe := True;Q.F_maybe := -1.25;
 Q.F_samples.Append (0.125);Q.F_samples.Append (0.375);Q.F_samples.Append (-0.125);Q.F_samples.Append (-0.375);
 Q.F_matrix := ((-8.0,7.5),(0.75,-0.75));Q.F_child.F_value := 2.625;Q.F_tail := 165;
 declare D : Bytes := SDL_quantized.Encode (CQ,Q); Y : SDL_quantized.T_Quantized := SDL_quantized.Decode (CQ,D); begin Check (D,"@QUANTIZED@");pragma Assert (Y.F_value=0.5 and Y.F_child.F_value=2.5);end;
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
   for k,v in {'PACKET':PACKET,'UNSIGNED':UNSIGNED,'ARRAYS':ARRAYS,'RECORDS':RECORDS,'QUANTIZED':QUANTIZED,'QUANTUM_WIDE':QUANTUM_WIDE,'GENERATED':str(generated)}.items():source=source.replace('@'+k+'@',v)
   source=source.replace('fn main(){','fn main(){quantized();').replace('main(String[]args){','main(String[]args){quantized();')
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
