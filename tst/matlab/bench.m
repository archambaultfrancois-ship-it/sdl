function bench()
m=BenchSmall('new');m.active=true;m.sequence=int32(123);m.code=int16(-2);run_case('small','BenchSmall',m,7);
m=BenchOptionals('new');m.a=int32(1);run_case('optional_sparse','BenchOptionals',m,4);names={'a','b','c','d','e','f','g','h'};for k=1:8,m.(names{k})=int32(k);end;run_case('optional_dense','BenchOptionals',m,32);
m=BenchVariable('new');m.header=repmat('V',1,40);m.entries=cell(8,1);for k=1:8,e=BenchEntry('new');e.label=sprintf('entry%d',k-1);e.number=int64(k-1);m.entries{k}=e;end;run_case('variable','BenchVariable',m,152);
m=BenchPayload('new');m.header=repmat('H',1,200);m.samples=complex(single((0:4999)'*.25),single(-mod((0:4999)',97)*.5));run_case('packed','BenchPayload',m,40200);
end
function run_case(label,type,message,useful)
text=sdl_matlab_runtime('description',{type});tic;ctx=sdl_matlab_runtime('prepare',text,{type});preparation=toc*1e6;wire=feval(type,'encode',ctx,message);decoded=feval(type,'decode',ctx,wire);assert(isequal(decoded,message));
for k=1:20,feval(type,'encode',ctx,message);feval(type,'decode',ctx,wire);end
setting=getenv('SDL_BENCH_ITERATIONS');iterations=str2double(setting);if isempty(regexp(setting,'^[0-9]+$','once'))||~isfinite(iterations)||iterations<1,iterations=200;end
fprintf('%s metadata: %d description bytes, %.1f us prepare\n',label,numel(unicode2native(text,'UTF-8')),preparation);
for operation={'encode','decode'},rates=zeros(3,1);for trial=1:3,n=0;tic;while n<iterations||toc<.1
 if strcmp(operation{1},'encode'),feval(type,'encode',ctx,message);else,feval(type,'decode',ctx,wire);end;n=n+1;
end;rates(trial)=n/toc;end;rate=median(rates);fprintf('%s %s %.1f msg/s %.4f MiB/s (%d bytes/message)\n',label,operation{1},rate,rate*useful/1048576,numel(wire));end
end
