function filter_review_checks()
v=telemetry.review.Regression();
v.points(1).x=1;v.points(2).x=2;
v.grid(1,1).x=10;v.grid(2,1).x=20;v.grid(1,2).x=30;v.grid(2,2).x=40;
v.blocks(1).points(1).x=11;v.blocks(1).points(2).x=12;
v.blocks(2).points(1).x=21;v.blocks(2).points(2).x=22;
v.value32=single(3);v.value64=4;
v.fixed32=single([5;6]);v.packed32=single([7;8]);v.repeated64={9;10};
root='telemetry.review.Regression';
catalogue=sdl_matlab_runtime('description',{root});
context=sdl_matlab_runtime('prepare',catalogue,{root});
wire=telemetry.review.Regression('encode',context,v);
decoded=telemetry.review.Regression('decode',context,wire);
queries={'points[1].x==2','all(points,p=>p.x>0)',...
 'grid[0][1].x==20','grid[1][0].x==30','grid[1][1].x==40',...
 'blocks[1].points[0].x==21','blocks[1].points[1].x==22',...
 'real(value32)==3&&imag(value32)==0&&value32.real==3&&value32.imag==0',...
 'real(value64)==4&&imag(value64)==0&&value64.real==4&&value64.imag==0',...
 'fixed32[1].real==6&&fixed32[1].imag==0',...
 'all(packed32,p=>real(p)>0&&p.imag==0)',...
 'repeated64[1].real==10&&all(repeated64,p=>imag(p)==0)'};
for k=1:numel(queries)
 f=sdl_filter('prepare',catalogue,root,queries{k});
 a=sdl_filter('evaluate',f,v);b=sdl_filter('wire',f,wire);c=sdl_filter('evaluate',f,decoded);
 assert(a.matched,[queries{k} ' object ' a.diagnostic]);
 assert(b.matched,[queries{k} ' wire ' b.diagnostic]);
 assert(c.matched,[queries{k} ' decoded ' c.diagnostic]);
 sdl_filter('close',f);
end
disp('MATLAB/Octave fixed struct arrays and real complex storage passed.');
end
