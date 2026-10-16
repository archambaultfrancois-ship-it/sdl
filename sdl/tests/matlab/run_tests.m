function run_tests()
test_auto_fixed();
for id=[uint32(0),uint32(hex2dec('89abcdef')),uint32(4294967295)]
 wire=sdl_matlab_runtime('pack_envelope',id,uint8([1 0]));
 [received,payload]=sdl_matlab_runtime('unpack_envelope',wire);
 assert(received==id&&isequal(payload,uint8([1 0]))&&numel(wire)==11);
 assert(isequal(wire(1:5),[uint8('S2PS') uint8(2)]));
 assert(isequal(wire(6:9),uint8([bitshift(id,-24),bitand(bitshift(id,-16),255),bitand(bitshift(id,-8),255),bitand(id,255)])));
 for n=0:9,must_fail(@() sdl_matlab_runtime('unpack_envelope',wire(1:n)));end
 for i=1:5,bad=wire;bad(i)=bitxor(bad(i),uint8(1));must_fail(@() sdl_matlab_runtime('unpack_envelope',bad));end
end
must_fail(@() sdl_matlab_runtime('pack_envelope',uint32(0),uint8([])));
f=fopen('tests/fixtures/compatibility/cases.tsv','r');
while true
 line=fgetl(f);if ~ischar(line),break;end
 cols=strsplit(line,char(9),'CollapseDelimiters',false);root=cols{4};if strcmp(root,'-'),root='';end
 d=sdl_matlab_runtime('compare_catalogues',fileread(['tests/fixtures/compatibility/' cols{2}]),fileread(['tests/fixtures/compatibility/' cols{3}]),root,strcmp(cols{5},'1'));
 for i=[7,8,10,11],if strcmp(cols{i},'-'),cols{i}='';end,end
 assert(strcmp(d.code,cols{6}),cols{1});assert(strcmp(d.message,cols{7}));assert(strcmp(d.path,cols{8}));
 assert(d.field_id==uint32(str2double(cols{9})));assert(strcmp(d.publisher_type,cols{10}));assert(strcmp(d.consumer_type,cols{11}));
 assert(d.enum_value==int32(str2double(cols{12})));assert(d.compatible==strcmp(cols{6},'COMPATIBLE'));
end
fclose(f);
f=fopen('tests/fixtures/catalogue_ids.tsv','r');
while true
 line=fgetl(f);if ~ischar(line),break;end
 tab=find(line==char(9),1);raw=line(1:tab-1);data=uint8(sscanf(raw,'%2x'));expected=uint32(hex2dec(line(tab+1:end)));
 assert(sdl_matlab_runtime('catalogue_id',data)==expected);
end
fclose(f);
must_fail(@() sdl_matlab_runtime('catalogue_id',uint8(255)));
namesctx=sdl_matlab_runtime('prepare',sdl_matlab_runtime('description',{'Packet','EmptyMessage'}));
assert(isequal(sdl_matlab_runtime('message_names',namesctx),{'EmptyMessage','Packet'}));
assert(strcmp(Packet('name'),'Packet'));
cases = CodecCases('new');
% Enum defaults must work in MATLAB R2017a, including fixed arrays.
defaults = State();
assert(isequal(cases.fixed_states, repmat(defaults.UNKNOWN, [3,1])));
record = EnumRecord('new');
assert(record.state == defaults.UNKNOWN);
ctx=sdl_matlab_runtime('prepare',Packet('description'),{'Packet'});
m=Packet('new');m.active=true;m.code=int16(-2);m.label='été';m.samples=int16([300;-1]);wire=Packet('encode',ctx,m);
f=fopen('tests/fixtures/packet.bin','rb');fixture=fread(f,Inf,'*uint8');fclose(f);assert(isequal(wire,fixture));
f=fopen('tests/fixtures/packet.sdl2','rb');text=fread(f,Inf,'*uint8');fclose(f);description_bytes=uint8(unicode2native(Packet('description'),'UTF-8'));assert(isequal(description_bytes(:),text));
p=Packet('decode',ctx,wire);assert(p.active&&p.code==-2&&strcmp(p.label,m.label)&&isequal(p.samples,m.samples));
for n=0:numel(wire)-1,must_fail(@() Packet('decode',ctx,wire(1:n)));end
must_fail(@() Packet('decode',ctx,[wire;uint8(0)]));must_fail(@() Packet('decode',ctx,uint8([129;0])));must_fail(@() Packet('decode',ctx,uint8([255;255;255;255;16])));
bad=wire;bad(3)=2;must_fail(@() Packet('decode',ctx,bad));bad=wire;bad(2)=2;must_fail(@() Packet('decode',ctx,bad));bad=wire;bad(8)=255;must_fail(@() Packet('decode',ctx,bad));
m.code=[];m.label=char([97,0,98,0]);m.samples=int16([]);p=Packet('decode',ctx,Packet('encode',ctx,m));assert(isempty(p.code)&&isequal(m.label,p.label));
remote=strrep(Packet('description'),'bool:active;','bool:enabled;');remote=strrep(remote,'4+:int16:samples;','4*:int16:samples;5:string:extra;');
rctx=sdl_matlab_runtime('prepare',remote,{'Packet'});p=Packet('decode',rctx,[fixture;uint8([2;111;107])]);assert(p.active&&numel(p.samples)==2);
removed='SDL2|Packet{1:bool:active;}';rctx=sdl_matlab_runtime('prepare',removed,{'Packet'});p=Packet('decode',rctx,uint8([1;1]));assert(p.active&&isempty(p.code)&&isempty(p.samples));
must_fail(@() sdl_matlab_runtime('prepare',strrep(Packet('description'),'bool:active;','int32:active;'),{'Packet'}));
text=sdl_matlab_runtime('description',{'Packet','EmptyMessage'});multi=sdl_matlab_runtime('prepare',text,{'Packet','EmptyMessage'});[p,name]=sdl_matlab_runtime('decode',multi,EmptyMessage('encode',multi,EmptyMessage('new')));assert(strcmp(name,'EmptyMessage'));
cc=sdl_matlab_runtime('prepare',CodecCases('description'),{'CodecCases'});c=CodecCases('new');c.tiny=int8(-128);c.small=int16(32767);c.signed_value=intmin('int32');c.wide=intmin('int64');c.ratio=single(Inf);c.precise=-0.0;c.point=complex(single(1),single(-2));c.position=complex(3,4);c.state=State('MINIMUM');c.labels={'',char([97,0,98])};c.bool_flags={true,false};c.fixed_states=int32([1;-2147483648;2147483647]);c.packed_states=int32([-7;1]);c.points=complex(single([1;2]),single([-1;-2]));
p=CodecCases('decode',cc,CodecCases('encode',cc,c));assert(p.wide==intmin('int64')&&p.tiny==-128&&p.state==intmin('int32')&&isequal(p.points,c.points)&&isequal(p.labels,c.labels(:)));
for x={[],''},c.empty_text=x{1};p=CodecCases('decode',cc,CodecCases('encode',cc,c));assert(isequal(p.empty_text,x{1}));end
root=sdl_matlab_runtime('prepare',RootPayload('description'),{'RootPayload'});r=RootPayload('new');r.header='nested';r.fixed_array.x=single([1;3]);r.fixed_array.y=single([2;4]);v=VarItem('new');v.name='';v.id=int64(7);r.var_array={v};p=RootPayload('decode',root,RootPayload('encode',root,r));assert(isequal(p.fixed_array,r.fixed_array)&&p.var_array{1}.id==7);
board=sdl_matlab_runtime('prepare',FixedBoard('description'),{'FixedBoard'});b=FixedBoard('new');b.rows(1).vectors(1).coords=single([1;2]);p=FixedBoard('decode',board,FixedBoard('encode',board,b));assert(isequal(p.rows,b.rows));
for n=[0,1,127,128,16383],m=Packet('new');m.label=repmat('x',1,n);m.samples=int16(-ones(n,1));p=Packet('decode',ctx,Packet('encode',ctx,m));assert(numel(p.label)==n&&numel(p.samples)==n);end
for text={sprintf('SDL1\n'),sprintf('SDL2\n'),'SDL2|A{1:A:a;}','SDL2|A{1+:string:a;}'},must_fail(@() sdl_matlab_runtime('prepare',text{1}));end
for shared=[false true]
 text='SDL2|';if shared,count=30;else,count=66;end
 for k=0:count-1
  if k>0,text=[text '|'];end;text=[text sprintf('N%03d{',k)];
  if k==0,text=[text '1:int8:value;'];else
   text=[text sprintf('1?:N%03d:left;',k-1)];
   if shared,text=[text sprintf('2?:N%03d:right;',k-1)];end
  end
  text=[text '}'];
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
function test_auto_fixed()
ctx=sdl_matlab_runtime('prepare',AutoBatch('description'),{'AutoBatch'});
assert(ctx.fixed_codecs('AutoRecord')==ctx.fixed_enabled&&ctx.fixed_codecs('AutoPoint')==ctx.fixed_enabled&&~ctx.fixed_codecs('AutoBatch'));
r=AutoRecord('new');r.id=uint32(hex2dec('01020304'));r.point.coordinates=single([1.25;-2.5]);
r.point.code=int16(-123);r.point.state=int32(-7);r.point.enabled=true;
r.grid=reshape(int16([-1,2,-3,4,-5,6]),[3,2]);
m=AutoBatch('new');m.label='x';m.records={r;r};m.samples={int16(-1);int16(2);int16(-3)};
f=fopen('tests/fixtures/auto_fixed.bin','rb');wire=fread(f,Inf,'*uint8');fclose(f);
assert(isequal(AutoBatch('encode',ctx,m),wire));assert(isequal(AutoBatch('decode',ctx,wire),m));
root=[uint8(4);wire(5:35)];assert(isequal(AutoRecord('encode',ctx,r),root));assert(isequal(AutoRecord('decode',ctx,root),r));
generic=ctx;generic.fixed_enabled=false;names=keys(generic.fixed_codecs);
generic.fixed_codecs=containers.Map(names,num2cell(false(size(names))));
assert(isequal(AutoBatch('encode',generic,m),wire));assert(isequal(AutoBatch('decode',generic,wire),m));
badvalue=m;badvalue.samples={int16([1,2])};must_fail(@() AutoBatch('encode',ctx,badvalue));
o=AutoOptional('new');o.record=r;o.tail=uint8(9);
assert(isequal(AutoOptional('decode',ctx,AutoOptional('encode',ctx,o)),o));
o.record=[];assert(isequal(AutoOptional('decode',ctx,AutoOptional('encode',ctx,o)),o));
for n=0:numel(wire)-1,must_fail(@() AutoBatch('decode',ctx,wire(1:n)));end
bad=wire;bad(23)=2;must_fail(@() AutoBatch('decode',ctx,bad));
bad=wire;bad(22)=1;must_fail(@() AutoBatch('decode',ctx,bad));
remote=strrep(AutoBatch('description'),'3:int16[2][3]:grid;','');
older=sdl_matlab_runtime('prepare',remote,{'AutoBatch'});assert(~older.fixed_codecs('AutoRecord'));
p=AutoRecord('decode',older,root(1:20));assert(isequal(p.grid,zeros(3,2,'int16'))&&p.point.code==-123);
% Older generated modules have no fixed_size action and must remain usable.
folder=tempname;mkdir(folder);cleanup=onCleanup(@() remove_legacy_fixed(folder)); %#ok<NASGU>
source=fileread(which('AutoRecord'));source=strrep(source,'case ''fixed_size''','case ''unavailable_fixed_size''');
f=fopen(fullfile(folder,'AutoRecord.m'),'w');fwrite(f,source);fclose(f);
addpath(folder,'-begin');clear AutoRecord;
legacy=sdl_matlab_runtime('prepare',AutoBatch('description'),{'AutoBatch'});
assert(~legacy.fixed_codecs('AutoRecord'));
assert(isequal(AutoBatch('encode',legacy,m),wire));assert(isequal(AutoBatch('decode',legacy,wire),m));
end
function remove_legacy_fixed(folder)
rmpath(folder);clear AutoRecord;rmdir(folder,'s');
end
