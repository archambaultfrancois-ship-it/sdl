function test_mex()
if exist('sdl_packed_mex','file')~=3,return;end
previous=getenv('SDL_MATLAB_NO_MEX');cleanup=onCleanup(@() setenv('SDL_MATLAB_NO_MEX',previous)); %#ok<NASGU>
previous_payload=getenv('SDL_MATLAB_MEX_PAYLOAD_ONLY');cleanup_payload=onCleanup(@() setenv('SDL_MATLAB_MEX_PAYLOAD_ONLY',previous_payload)); %#ok<NASGU>
setenv('SDL_MATLAB_MEX_PAYLOAD_ONLY','');
setenv('SDL_MATLAB_NO_MEX','1');baseline=sdl_matlab_runtime('prepare',BenchPayload('description'),{'BenchPayload'});
setenv('SDL_MATLAB_NO_MEX','');fast=sdl_matlab_runtime('prepare',BenchPayload('description'),{'BenchPayload'});
assert(~baseline.mex&&fast.mex);
rng(137);
for n=[0,1,2,3,7,16,127,128,1000,5000]
 bits=uint32(floor(rand(2,n)*4294967296));
 if n>=3,bits(:,1:3)=reshape(uint32([hex2dec('80000000'),hex2dec('7fc01234'),hex2dec('7fa05678'),hex2dec('ff800000'),1,hex2dec('ffffffff')]),2,3);end
 re=typecast(bits(1,:),'single');im=typecast(bits(2,:),'single');values=complex(re(:),im(:));
 % Independent big-endian byte oracle, without runtime conversion helpers.
 words=bits(:)';bytes=zeros(4,numel(words),'uint8');
 for k=1:4,bytes(k,:)=uint8(bitand(bitshift(words,-8*(4-k)),uint32(255)));end
 expected=bytes(:);encoded=sdl_packed_mex('encode_c32',values);assert(isequal(encoded,expected));
 decoded=sdl_packed_mex('decode_c32',[uint8(99);expected],2,double(n));
 assert(isequal(typecast(real(decoded),'uint32'),bits(1,:)'));
 assert(isequal(typecast(imag(decoded),'uint32'),bits(2,:)'));
 m=BenchPayload('new');m.header='mex';m.samples=values;
 wire=BenchPayload('encode',baseline,m);assert(isequal(wire,BenchPayload('encode',fast,m)));
 decoded=BenchPayload('decode',fast,wire);assert(isequal(wire,BenchPayload('encode',fast,decoded)));
 decoded=BenchPayload('decode',baseline,wire);assert(isequal(wire,BenchPayload('encode',fast,decoded)));
 if n>0
  for missing=1:min(8,numel(expected)),must_fail(@() sdl_packed_mex('decode_c32',expected(1:end-missing),1,double(n)));end
 end
 if n==3
  for k=0:numel(wire)-1,must_fail(@() BenchPayload('decode',fast,wire(1:k)));end
  must_fail(@() BenchPayload('decode',fast,[wire;uint8(0)]));
 end
end
real_values=single([1;-2;0]);expected=sdl_packed_mex('encode_c32',complex(real_values,single(zeros(3,1))));assert(isequal(expected,sdl_packed_mex('encode_c32',real_values)));
for index={0,-1,1.5,Inf,NaN},must_fail(@() sdl_packed_mex('decode_c32',uint8([]),index{1},0));end
must_fail(@() sdl_packed_mex('decode_c32',uint8([]),1,4294967295));
must_fail(@() sdl_packed_mex('decode_c32',uint8([]),2,0));
must_fail(@() sdl_packed_mex('decode_c32',single([]),1,0));
must_fail(@() sdl_packed_mex('encode_c32',double(1)));
assert(~isempty(fast.native)&&fast.native_id~=0);
bytes=uint8(unicode2native(BenchPayload('description'),'UTF-8'));
bytectx=sdl_matlab_runtime('prepare',bytes,{'BenchPayload'});assert(bytectx.native_id~=0);delete(bytectx.native);clear bytectx;
% Full-message UTF-16/UTF-8 conversion, including embedded NUL and surrogate pairs.
for header={char([]),'ascii','été',char([97,0,98]),char([hex2dec('d83d'),hex2dec('de00')]),char([1,127,128,2047,2048,65535])}
 m=BenchPayload('new');m.header=header{1};m.samples=complex(single([1;-2]),single([-3;4]));
 wire=BenchPayload('encode',baseline,m);assert(isequal(wire,BenchPayload('encode',fast,m)));
 p=BenchPayload('decode',fast,wire);q=BenchPayload('decode',baseline,wire);assert(isequal(p,q));
 [p,name]=sdl_matlab_runtime('decode',fast,wire);assert(strcmp(name,'BenchPayload')&&isequal(p,q));
end
m=BenchPayload('new');m.header='double';m.samples=complex([1.25;-3.5],[4;-2]);
assert(isequal(BenchPayload('encode',baseline,m),BenchPayload('encode',fast,m)));
m.samples={complex(1,-2),complex(3,4)};
assert(isequal(BenchPayload('encode',baseline,m),BenchPayload('encode',fast,m)));
for header={char(hex2dec('d800')),char(hex2dec('dc00')),char([hex2dec('d800'),97])}
 m=BenchPayload('new');m.header=header{1};must_fail(@() BenchPayload('encode',fast,m));
end
for wire={uint8([]),uint8([0,0,0]),uint8([129,0,0,0]),uint8([1,128,0,0]),uint8([1,0,128,0]),uint8([1,0,255,255,255,255,16]),uint8([1,0,255,255,255,255,15]),uint8([1,1,255,0]),uint8([1,2,192,128,0]),uint8([1,3,237,160,128,0]),uint8([1,4,244,144,128,128,0])}
 must_fail(@() BenchPayload('decode',fast,wire{1}));
end
% Unsupported descriptions preserve the schema-evolution decoder.
renamed=strrep(BenchPayload('description'),'header;','label;');
rctx=sdl_matlab_runtime('prepare',renamed,{'BenchPayload'});assert(isempty(rctx.native)&&rctx.native_id==0);
m=BenchPayload('new');m.header='renamed';m.samples=complex(single([1;2]),single([3;4]));
wire=BenchPayload('encode',baseline,m);p=BenchPayload('decode',rctx,wire);assert(isequal(p,m));
% Context copies share ownership. Deleting the owner invalidates every copied token.
owner=sdl_matlab_runtime('prepare',BenchPayload('description'),{'BenchPayload'});copy=owner;id=owner.native_id;
clear owner;assert(isequal(wire,BenchPayload('encode',copy,m)));
delete(copy.native);must_fail(@() sdl_packed_mex('decode',id,wire));
must_fail(@() BenchPayload('decode',copy,wire));
released=sdl_packed_mex('release',id);assert(~released);
must_fail(@() sdl_packed_mex('decode',uint64(0),wire));
% Last-owner destruction unlocks the MEX; reloading cannot revive a stale token.
delete(fast.native);clear fast copy baseline rctx;
assert(~mislocked('sdl_packed_mex'));clear sdl_packed_mex;
new=sdl_matlab_runtime('prepare',BenchPayload('description'),{'BenchPayload'});
assert(new.native_id~=id);must_fail(@() sdl_packed_mex('decode',id,wire));
assert(isequal(wire,BenchPayload('encode',new,m)));delete(new.native);
fprintf('MATLAB MEX c32 wire oracle, raw bits, offsets, truncations and fallback passed.\n');
end
function must_fail(f)
failed=false;try,unused=f();catch,failed=true;end %#ok<NASGU>
assert(failed,'invalid input accepted');
end
