function bench_mex(iterations)
% Diagnostic: fixed loops separate binding overhead from direct MEX calls.
if nargin<1,iterations=20000;end
assert(isscalar(iterations)&&isfinite(iterations)&&iterations>=1&&iterations==fix(iterations));
ctx=sdl_matlab_runtime('prepare',BenchPayload('description'),{'BenchPayload'});
if ctx.native_id==0,error('SDL:MEXRequired','Build with make matlab-mex and enable the full MEX path.');end
id=ctx.native_id;m=BenchPayload('new');m.header=repmat('H',1,200);
m.samples=complex(single((0:4999)'*.25),single(-mod((0:4999)',97)*.5));
wire=sdl_packed_mex('encode',id,m);copy=sdl_packed_mex('decode',id,wire);
assert(isequal(m,copy));
for k=1:100,out=BenchPayload('encode',ctx,m);out=BenchPayload('decode',ctx,wire);end %#ok<NASGU>
rates=zeros(3,4);
for trial=1:3
 tic;for k=1:iterations,out=BenchPayload('encode',ctx,m);end;rates(trial,1)=iterations/toc; %#ok<NASGU>
 tic;for k=1:iterations,out=BenchPayload('decode',ctx,wire);end;rates(trial,2)=iterations/toc; %#ok<NASGU>
 tic;for k=1:iterations,out=sdl_packed_mex('encode',id,m);end;rates(trial,3)=iterations/toc; %#ok<NASGU>
 tic;for k=1:iterations,out=sdl_packed_mex('decode',id,wire);end;rates(trial,4)=iterations/toc; %#ok<NASGU>
end
mb=median(rates)*40200/1000000;
fprintf('Packed c32, fixed-loop diagnostic, MB/s (Enc / Dec)\n');
fprintf('MATLAB binding: %.0f / %.0f\n',mb(1),mb(2));
fprintf('Direct MEX:     %.0f / %.0f\n',mb(3),mb(4));
fprintf('Fixed iteration loops; these rates are distinct from the make bench harness.\n');
end
