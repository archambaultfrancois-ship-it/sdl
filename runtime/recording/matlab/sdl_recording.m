function varargout = sdl_recording(command, varargin)
% Optional MCAP services. Payloads are complete encoded SDL2 uint8 arrays.
% Handles/timestamps: uint64; channels: uint16; sequence: uint32.
% close is explicit; next returns an owning struct, or [] at complete EOF.
switch command
    case 'open_writer'
        path = utf8(varargin{1});
        overwrite = false; chunk = uint64(1024 * 1024);
        if numel(varargin) > 1, overwrite = varargin{2}; end
        if numel(varargin) > 2, chunk = varargin{3}; end
        varargout{1} = sdl_recording_mex(command, path, overwrite, chunk);
    case 'open_reader'
        varargout{1} = sdl_recording_mex(command, utf8(varargin{1}));
    case 'channel'
        metadata = cell(0, 2);
        if numel(varargin) > 4, metadata = varargin{5}; end
        varargout{1} = sdl_recording_mex(command, varargin{1}, utf8(varargin{2}), ...
            utf8(varargin{3}), utf8(varargin{4}), encode_metadata(metadata));
    case 'metadata'
        sdl_recording_mex(command, varargin{1}, utf8(varargin{2}), encode_metadata(varargin{3}));
    case 'write_encoded'
        log = sdl_recording_mex('now'); sequence = uint32(0);
        if numel(varargin) > 3, log = varargin{4}; end
        publish = log;
        if numel(varargin) > 4, publish = varargin{5}; end
        if numel(varargin) > 5, sequence = varargin{6}; end
        sdl_recording_mex(command, varargin{1}, varargin{2}, varargin{3}, log, publish, sequence);
    case 'flush'
        sync = false;
        if numel(varargin) > 1, sync = varargin{2}; end
        sdl_recording_mex(command, varargin{1}, sync);
    case {'close', 'validate_payload'}
        sdl_recording_mex(command, varargin{:});
    case 'filter'
        topic = ''; start = uint64(0); finish = uint64(0); flags = uint8(0);
        if numel(varargin) > 1, topic = varargin{2}; end
        if numel(varargin) > 2 && ~isempty(varargin{3}), start = varargin{3}; flags = bitor(flags, uint8(1)); end
        if numel(varargin) > 3 && ~isempty(varargin{4}), finish = varargin{4}; flags = bitor(flags, uint8(2)); end
        sdl_recording_mex(command, varargin{1}, utf8(topic), flags, start, finish);
    case 'next'
        varargout{1} = normalize(sdl_recording_mex(command, varargin{1}));
    case 'replay'
        consumer = varargin{3};
        if ~isa(consumer, 'function_handle'), error('SDL:Recording', 'Expected consumer function handle'); end
        [count, failure] = sdl_recording_mex(command, varargin{1}, varargin{2}, @(raw) consumer(normalize(raw)));
        if ~isempty(failure), rethrow(failure); end
        varargout{1} = count;
    case 'validate'
        varargout{1} = sdl_recording_mex(command, utf8(varargin{1}));
    case 'now'
        varargout{1} = sdl_recording_mex(command);
    otherwise
        error('SDL:Recording', 'Unknown recording command: %s', command);
end
end
function value = utf8(text)
if ~ischar(text) || (~isrow(text) && ~isempty(text))
    error('SDL:Recording', 'Expected a character row vector');
end
value = uint8(unicode2native(text, 'UTF-8'));
end
function result = encode_metadata(value)
if ~iscell(value) || size(value, 2) ~= 2 || ndims(value) ~= 2
    error('SDL:Recording', 'Metadata must be an N-by-2 cell array of text');
end
result = cell(size(value));
for i = 1:numel(value), result{i} = utf8(value{i}); end
end
function value = normalize(value)
if isempty(value), return; end
value.topic = native2unicode(value.topic, 'UTF-8');
value.root_type = native2unicode(value.root_type, 'UTF-8');
value.catalogue = native2unicode(value.catalogue, 'UTF-8');
for i = 1:numel(value.metadata), value.metadata{i} = native2unicode(value.metadata{i}, 'UTF-8'); end
end
