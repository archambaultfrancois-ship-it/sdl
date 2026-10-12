function run_recording_mex_tests(output_path, python_path, format)
if nargin < 3, format = 'mcap'; end
topic = native2unicode(uint8([114 97 100 105 111 47 195 169 240 159 147 187 0 105 113]), 'UTF-8');
metadata = {'origin', topic};
w = sdl_recording('open_writer', output_path, false, uint64(1048576), format);
try
    c = sdl_recording('channel', w, topic, 'SDL2|Packet{}', 'Packet', metadata);
    sdl_recording('metadata', w, 'run', metadata);
    sdl_recording('write_encoded', w, c, uint8(1), intmax('uint64'), intmax('uint64'), intmax('uint32'));
    sdl_recording('write_encoded', w, c, uint8(1), intmax('uint64'), intmax('uint64'), uint32(1));
    sdl_recording('flush', w, true);
    assert_throws(@() sdl_recording('write_encoded', w, c, uint8(2), uint64(0)));
    sdl_recording('close', w);
catch err
    try, sdl_recording('close', w); catch, end
    rethrow(err);
end
assert(sdl_recording('validate', output_path) == uint64(2));
assert_throws(@() sdl_recording('close', w));
r = sdl_recording('open_reader', output_path);
try
    sdl_recording('filter', r, topic, intmax('uint64'), []);
    first = sdl_recording('next', r); second = sdl_recording('next', r);
    assert(strcmp(first.topic, topic)); assert(isequal(first.payload, uint8(1)));
    assert(first.log_time_ns == intmax('uint64')); assert(first.sequence == intmax('uint32'));
    assert(second.sequence == uint32(1)); assert(isempty(sdl_recording('next', r)));
    assert(strcmp(first.catalogue, 'SDL2|Packet{}')); % Record owns copied buffers.
    sdl_recording('close', r);
catch err
    try, sdl_recording('close', r); catch, end
    rethrow(err);
end
r = sdl_recording('open_reader', output_path);
seen = 0;
try
    count = sdl_recording('replay', r, 0, @consume);
    assert(count == uint64(2)); assert(seen == 2);
    sdl_recording('close', r);
catch err
    try, sdl_recording('close', r); catch, end
    rethrow(err);
end
r = sdl_recording('open_reader', output_path);
try
    try
        count = sdl_recording('replay', r, 0, @consumer_error); %#ok<NASGU>
        error('SDL:Test', 'Consumer exception was lost');
    catch err
        assert(strcmp(err.identifier, 'SDL:ConsumerMarker'));
    end
    second = sdl_recording('next', r); assert(second.sequence == uint32(1));
    sdl_recording('close', r);
catch err
    try, sdl_recording('close', r); catch, end
    rethrow(err);
end
r = sdl_recording('open_reader', python_path);
try
    record = sdl_recording('next', r);
    assert(strcmp(record.root_type, 'Sample')); assert(~isempty(strfind(record.catalogue, 'q0.25')));
    assert(strcmp(record.topic, topic)); assert(numel(record.payload) > 1);
    sdl_recording('close', r);
catch err
    try, sdl_recording('close', r); catch, end
    rethrow(err);
end
fprintf('Octave C MEX recording tests passed\n');
    function consume(record)
        seen = seen + 1;
        assert(record.log_time_ns == intmax('uint64'));
        assert_throws(@() sdl_recording('close', r));
    end
end
function consumer_error(record) %#ok<INUSD>
error('SDL:ConsumerMarker', 'consumer-marker');
end
function assert_throws(action)
failed = false;
try, action(); catch, failed = true; end
assert(failed);
end
