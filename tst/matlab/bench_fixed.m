function bench_fixed()
% Compare automatic fixed codecs with ordinary MATLAB field traversal.
m=BenchSmall('new');m.active=true;m.sequence=int32(123);m.code=int16(-2);measure('BenchSmall',m);
r=BenchRecord('new');r.id=int32(7);r.pose.position.values=single([1;2;3]);
r.pose.rotation=single([0;0;0;1]);r.measures=single([4;5]);measure('BenchRecord',r);
r=AutoRecord('new');r.id=uint32(7);r.point.coordinates=single([1;2]);r.point.code=int16(-3);
m=AutoBatch('new');m.label='samples';m.records=repmat({r},32,1);m.samples=repmat({int16(1)},32,1);measure('AutoBatch',m);
end
function measure(name,m)
ctx=sdl_matlab_runtime('prepare',feval(name,'description'),{name});
baseline=ctx;baseline.fixed_enabled=false;names=keys(ctx.fixed_codecs);
baseline.fixed_codecs=containers.Map(names,num2cell(false(size(names))));
wire=feval(name,'encode',baseline,m);
assert(isequal(feval(name,'encode',ctx,m),wire));assert(isequal(feval(name,'decode',ctx,wire),m));
fprintf('%s\n',name);
for op={'encode','decode'}
 rates=zeros(1,2);contexts={baseline,ctx};
 for mode=1:2
  samples=zeros(1,3);c=contexts{mode};
  for trial=1:3
   n=0;tic;while toc<.2
    if strcmp(op{1},'encode'),feval(name,'encode',c,m);else,feval(name,'decode',c,wire);end;n=n+1;
   end;samples(trial)=n/toc;
  end;rates(mode)=median(samples);
 end
 fprintf('  %s: %.0f -> %.0f msg/s (%.2fx)\n',op{1},rates(1),rates(2),rates(2)/rates(1));
end
end
