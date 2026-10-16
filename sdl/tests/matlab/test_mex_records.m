function test_mex_records()
if exist('sdl_packed_mex','file')~=3,return;end
old=getenv('SDL_MATLAB_NO_MEX');cleanup=onCleanup(@() setenv('SDL_MATLAB_NO_MEX',old)); %#ok<NASGU>
old_payload=getenv('SDL_MATLAB_MEX_PAYLOAD_ONLY');cleanup_payload=onCleanup(@() setenv('SDL_MATLAB_MEX_PAYLOAD_ONLY',old_payload)); %#ok<NASGU>
setenv('SDL_MATLAB_MEX_PAYLOAD_ONLY','');
text=BenchRecordBatch('description');
setenv('SDL_MATLAB_NO_MEX','1');base=sdl_matlab_runtime('prepare',text,{'BenchRecordBatch'});
setenv('SDL_MATLAB_NO_MEX','');fast=sdl_matlab_runtime('prepare',text,{'BenchRecordBatch'});
assert(fast.native_id~=0&&strcmp(fast.native_name,'BenchRecordBatch'));
rng(731);
for n=[0 1 2 3 127 128 1000]
 bits=uint32(floor(rand(10,n)*4294967296));
 if n>0,bits(2:5,1)=uint32([hex2dec('80000000');hex2dec('7fc01234');hex2dec('7fa05678');hex2dec('ff800000')]);end
 m=BenchRecordBatch('new');
 m.records.id=reshape(typecast(reshape(bits(1,:),[],1),'int32'),n,1);
 m.records.pose.position.values=reshape(typecast(reshape(bits(2:4,:),[],1),'single'),3,n);
 m.records.pose.rotation=reshape(typecast(reshape(bits(5:8,:),[],1),'single'),4,n);
 m.records.measures=reshape(typecast(reshape(bits(9:10,:),[],1),'single'),2,n);
 words=bits(:)';bytes=zeros(4,numel(words),'uint8');
 for k=1:4,bytes(k,:)=uint8(bitand(bitshift(words,-8*(4-k)),uint32(255)));end
 count=uint32(n);counter=uint8([]);
 while true,b=uint8(bitand(count,uint32(127)));count=bitshift(count,-7);if count,b=bitor(b,uint8(128));end;counter(end+1,1)=b;if ~count,break;end;end %#ok<AGROW>
 oracle=[uint8(5);counter;bytes(:)];
 wire=BenchRecordBatch('encode',fast,m);assert(isequal(wire,oracle));assert(isequal(wire,BenchRecordBatch('encode',base,m)));
 decoded=BenchRecordBatch('decode',fast,wire);assert(isequal(wire,BenchRecordBatch('encode',fast,decoded)));
 assert(isequal(wire,BenchRecordBatch('encode',base,decoded)));
 if n==3
  for k=0:numel(wire)-1,must_fail(@() BenchRecordBatch('decode',fast,wire(1:k)));end
  must_fail(@() BenchRecordBatch('decode',fast,[wire;uint8(0)]));
  broken=m;broken.records.measures=single(0);must_fail(@() BenchRecordBatch('encode',fast,broken));
  broken=m;broken.records.id=sparse(double(broken.records.id));must_fail(@() sdl_packed_mex('encode',fast.native_id,broken));
 end
end
for wire={uint8([5;128;0]),uint8([5;255;255;255;255;15]),uint8([5;255;255;255;255;16])}
 must_fail(@() BenchRecordBatch('decode',fast,wire{1}));
end
% Other roots in the shared catalogue still use their regular MATLAB decoder.
small=BenchSmall('new');small.active=true;
wire=BenchSmall('encode',fast,small);[decoded,name]=sdl_matlab_runtime('decode',fast,wire);
assert(isequal(decoded,small)&&strcmp(name,'BenchSmall'));
% A changed schema must not select the fixed native adapter.
changed=strrep(text,'fl32[3] values','fl32[2] values');
ctx=sdl_matlab_runtime('prepare',changed,{});assert(ctx.native_id==0);
end
function must_fail(f)
failed=false;try,f();catch,failed=true;end;assert(failed);
end
