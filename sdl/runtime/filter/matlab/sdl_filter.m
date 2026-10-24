function varargout=sdl_filter(action,varargin)
% Catalogue-bound filtering. Status: 1 match, 0 no match, 2 indeterminate, -1 error.
switch action
case 'prepare'
 catalogue=varargin{1};root=varargin{2};expression=varargin{3};bindings={};
 names=regexp(catalogue,'(?:^SDL2\||\|)([^#|{}]+)\{','tokens');
 for k=1:numel(names)
  owner=names{k}{1};fn=regexprep(owner,'[^\w.]','_');
  % Octave 6 cannot resolve dotted package names with exist/which.
  parts=strsplit(fn,'.');for p=1:numel(parts)-1,parts{p}=['+' parts{p}];end
  if exist(fn,'file')||~isempty(which(fn))||exist([fullfile(parts{:}) '.m'],'file')
   fields=feval(fn,'fields');bindings{end+1}=struct('owner',owner,'fields',{fields});
  end
 end
 varargout{1}=sdl_filter_mex('prepare',unicode2native(catalogue,'UTF-8'),unicode2native(root,'UTF-8'),unicode2native(expression,'UTF-8'),bindings);
case {'evaluate','wire'}
 [status,diagnostic]=sdl_filter_mex(action,varargin{:});
 varargout{1}=struct('status',status,'diagnostic',diagnostic,'matched',status==1);
case 'close'
 sdl_filter_mex('close',varargin{1});
otherwise
 error('SDL:Filter','unknown filter action');
end
end
