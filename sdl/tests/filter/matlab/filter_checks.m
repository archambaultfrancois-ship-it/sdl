function filter_checks()
v=telemetry.Sample();v.sequence=intmax('uint64');v.temperature=12.34;v.active=true;v.detail=telemetry.Detail();v.detail.code=int32(0);v.detail.label='ok';v.position.x=3;v.position.y=4;
v.points.x=[1;10;-1];v.points.y=[2;20;.5];v.matrix=int32([1 4;2 5;3 6]);v.label=native2unicode(uint8([99 97 102 195 169]),'UTF-8');v.counts={int16(-2);int16(0);int16(7)};v.samples=complex(single([1;-3]),single([2;.5]));v.state=telemetry.State('ON');
catalogue=sdl_matlab_runtime('description',{'telemetry.Sample'});context=sdl_matlab_runtime('prepare',catalogue,{'telemetry.Sample'});wire=telemetry.Sample('encode',context,v);
file=fopen('build/filter/golden.bin','rb');golden=fread(file,Inf,'*uint8');fclose(file);assert(isequal(wire(:),golden(:)));
cases=filter_cases();
for k=1:size(cases,1)
 f=sdl_filter('prepare',catalogue,'telemetry.Sample',cases{k,1});a=sdl_filter('evaluate',f,v);b=sdl_filter('wire',f,wire);
 assert(a.status==cases{k,2},[cases{k,1} ' object ' a.diagnostic]);assert(b.status==cases{k,3},[cases{k,1} ' wire ' b.diagnostic]);sdl_filter('close',f);
end
v.detail=[];v.points.x=[];v.points.y=[];
for expression={'!exists(detail)','all(points,p=>false)','!any(points,p=>true)'}
 f=sdl_filter('prepare',catalogue,'telemetry.Sample',expression{1});a=sdl_filter('evaluate',f,v);assert(a.matched);sdl_filter('close',f);
end
addpath('build/filter/review-generated/matlab');filter_review_checks();
disp('MATLAB/Octave filters and wire oracle passed.');
end
