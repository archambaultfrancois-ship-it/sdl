function build_recording_mex(root)
% Build with MATLAB R2017a or later after make recording-native.
if nargin == 0, root = fileparts(fileparts(fileparts(fileparts(mfilename('fullpath'))))); end
out = fullfile(root, 'build', 'recording', 'mex');
if ~exist(out, 'dir'), mkdir(out); end
source = fullfile(root, 'runtime', 'recording', 'matlab', 'sdl_recording_mex.c');
include = ['-I' fullfile(root, 'runtime', 'recording', 'include')];
library = ['-L' fullfile(root, 'build', 'recording', 'release')];
mex('-largeArrayDims', include, library, source, '-lsdl_recording', '-outdir', out);
% Put the Rust shared library on the platform's dynamic-library search path.
end
