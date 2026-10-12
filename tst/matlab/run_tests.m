function run_tests()
ctx=sdl_matlab_runtime('prepare',Packet('description'),{'Packet'});
m=Packet('new');m.active=true;m.code=int16(-2);m.label='été';m.samples=int16([300;-1]);wire=Packet('encode',ctx,m);
f=fopen('tst/fixtures/packet.bin','rb');fixture=fread(f,Inf,'*uint8');fclose(f);assert(isequal(wire,fixture));
f=fopen('tst/fixtures/packet.sdl2','rb');text=fread(f,Inf,'*uint8');fclose(f);description_bytes=uint8(unicode2native(Packet('description'),'UTF-8'));assert(isequal(description_bytes(:),text));
p=Packet('decode',ctx,wire);assert(p.active&&p.code==-2&&strcmp(p.label,m.label)&&isequal(p.samples,m.samples));
for n=0:numel(wire)-1,must_fail(@() Packet('decode',ctx,wire(1:n)));end
must_fail(@() Packet('decode',ctx,[wire;uint8(0)]));must_fail(@() Packet('decode',ctx,uint8([129;0])));must_fail(@() Packet('decode',ctx,uint8([255;255;255;255;16])));
bad=wire;bad(3)=2;must_fail(@() Packet('decode',ctx,bad));bad=wire;bad(2)=2;must_fail(@() Packet('decode',ctx,bad));bad=wire;bad(8)=255;must_fail(@() Packet('decode',ctx,bad));
m.code=[];m.label=char([97,0,98,0]);m.samples=int16([]);p=Packet('decode',ctx,Packet('encode',ctx,m));assert(isempty(p.code)&&isequal(m.label,p.label));
remote=strrep(Packet('description'),'bool active;','bool enabled;');remote=strrep(remote,'packed int16 samples;',sprintf('repeated int16 samples;\n  5: required string extra;'));
rctx=sdl_matlab_runtime('prepare',remote,{'Packet'});p=Packet('decode',rctx,[fixture;uint8([2;111;107])]);assert(p.active&&numel(p.samples)==2);
removed=sprintf('SDL2\nmessage Packet {\n  1: required bool active;\n}\n');rctx=sdl_matlab_runtime('prepare',removed,{'Packet'});p=Packet('decode',rctx,uint8([1;1]));assert(p.active&&isempty(p.code)&&isempty(p.samples));
must_fail(@() sdl_matlab_runtime('prepare',strrep(Packet('description'),'bool active;','int32 active;'),{'Packet'}));
text=sdl_matlab_runtime('description',{'Packet','EmptyMessage'});multi=sdl_matlab_runtime('prepare',text,{'Packet','EmptyMessage'});[p,name]=sdl_matlab_runtime('decode',multi,EmptyMessage('encode',multi,EmptyMessage('new')));assert(strcmp(name,'EmptyMessage'));
cc=sdl_matlab_runtime('prepare',CodecCases('description'),{'CodecCases'});c=CodecCases('new');c.tiny=int8(-128);c.small=int16(32767);c.signed_value=intmin('int32');c.wide=intmin('int64');c.ratio=single(Inf);c.precise=-0.0;c.point=complex(single(1),single(-2));c.position=complex(3,4);c.state=State('MINIMUM');c.labels={'',char([97,0,98])};c.bool_flags={true,false};c.fixed_states=int32([1;-2147483648;2147483647]);c.packed_states=int32([-7;1]);c.points=complex(single([1;2]),single([-1;-2]));
p=CodecCases('decode',cc,CodecCases('encode',cc,c));assert(p.wide==intmin('int64')&&p.tiny==-128&&p.state==intmin('int32')&&isequal(p.points,c.points)&&isequal(p.labels,c.labels(:)));
for x={[],''},c.empty_text=x{1};p=CodecCases('decode',cc,CodecCases('encode',cc,c));assert(isequal(p.empty_text,x{1}));end
root=sdl_matlab_runtime('prepare',RootPayload('description'),{'RootPayload'});r=RootPayload('new');r.header='nested';r.fixed_array.x=single([1;3]);r.fixed_array.y=single([2;4]);v=VarItem('new');v.name='';v.id=int64(7);r.var_array={v};p=RootPayload('decode',root,RootPayload('encode',root,r));assert(isequal(p.fixed_array,r.fixed_array)&&p.var_array{1}.id==7);
board=sdl_matlab_runtime('prepare',FixedBoard('description'),{'FixedBoard'});b=FixedBoard('new');b.rows(1).vectors(1).coords=single([1;2]);p=FixedBoard('decode',board,FixedBoard('encode',board,b));assert(isequal(p.rows,b.rows));
for n=[0,1,127,128,16383],m=Packet('new');m.label=repmat('x',1,n);m.samples=int16(-ones(n,1));p=Packet('decode',ctx,Packet('encode',ctx,m));assert(numel(p.label)==n&&numel(p.samples)==n);end
for text={sprintf('SDL1\n'),sprintf('SDL2\n'),sprintf('SDL2\nmessage A {\n  1: required A a;\n}\n'),sprintf('SDL2\nmessage A {\n  1: packed string a;\n}\n')},must_fail(@() sdl_matlab_runtime('prepare',text{1}));end
for shared=[false true]
 text=sprintf('SDL2\n');if shared,count=30;else,count=66;end
 for k=0:count-1
  text=[text sprintf('message N%03d {\n',k)];
  if k==0,text=[text sprintf('  1: required int8 value;\n')];else
   text=[text sprintf('  1: optional N%03d left;\n',k-1)];
   if shared,text=[text sprintf('  2: optional N%03d right;\n',k-1)];end
  end
  text=[text sprintf('}\n')];
 end
 if shared,sdl_matlab_runtime('prepare',text,{});else,must_fail(@() sdl_matlab_runtime('prepare',text,{}));end
end
test_mex();
test_mex_records();
fprintf('Matlab/Octave SDL2 fixtures, catalogues, evolution and malformed input passed.\n');
end
function must_fail(f)
failed=false;try,f();catch,failed=true;end;assert(failed,'invalid input accepted');
end
