function filter_bench()
catalogue='SDL2|telemetry.Vector{1:fl64:x;2:fl64:y;}';
v=telemetry.Vector();v.x=3;v.y=4;
bench(v,'telemetry.Vector',catalogue,'scalar','x>0',100);
bench(v,'telemetry.Vector',catalogue,'arithmetic','x*x+y*y<=25',100);
v=telemetry.Sample();v.temperature=12.34;v.position.x=3;v.position.y=4;
v.points.x=3*ones(256,1);v.points.y=4*ones(256,1);
catalogue=sdl_matlab_runtime('description',{'telemetry.Sample'});
bench(v,'telemetry.Sample',catalogue,'collection256','all(points,p=>p.x>=0&&p.y>=0)',5);
end

function bench(v,root,catalogue,label,expression,iterations)
context=sdl_matlab_runtime('prepare',catalogue,{root});
wire=feval(root,'encode',context,v);f=sdl_filter('prepare',catalogue,root,expression);
a=sdl_filter('evaluate',f,v);b=sdl_filter('wire',f,wire);assert(a.matched&&b.matched);
actions={@()feval(root,'encode',context,v),@()feval(root,'decode',context,wire),...
         @()sdl_filter('evaluate',f,v),@()sdl_filter('wire',f,wire)};
times=zeros(1,4);
for k=1:4
 action=actions{k};for j=1:3,action();end
 trials=zeros(1,5);
 for trial=1:5
  start=tic;for j=1:iterations,out=action();end %#ok<NASGU>
  trials(trial)=toc(start)*1e9/iterations;
 end
 times(k)=median(trials);
end
sdl_filter('close',f);
if exist('OCTAVE_VERSION','builtin'),backend='Octave';else,backend='MATLAB';end
fprintf('BENCH %s %s %.1f %.1f %.1f %.1f\n',backend,label,times);
end
