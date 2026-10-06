function run_tests()
cases = CodecCases('new');
cases.tiny = int8(-12); cases.small = int16(1234); cases.signed_value = int32(-987654);
cases.wide = int64(1234567890123); cases.ratio = single(1.25); cases.precise = 1/7;
cases.point = complex(single(1.5), single(-2.25)); cases.position = complex(3.5,-4.75);
enum = State();
cases.state = enum.READY; cases.empty_text = ''; cases.required_zero = int32(0);
cases.samples = {int16(-3),int16(4)}; cases.measurements={0.25,-2.5};
cases.labels={'','octave'}; cases.points={complex(single(1),single(2))};
cases.empty_values={}; cases.required_enabled=true; cases.optional_enabled=false;
cases.bool_flags={true,false}; cases.fixed_states=int32([0 1 -7]);
cases.packed_states={enum.READY,enum.NEGATIVE}; cases.packed_flags={true,false};
cases.high_id_value=int32(42);
wire=CodecCases('encode',cases);
decoded=CodecCases('decode',wire);
assert(decoded.tiny == cases.tiny && decoded.small == cases.small);
assert(decoded.signed_value == cases.signed_value && decoded.wide == cases.wide);
assert(decoded.ratio == cases.ratio && decoded.precise == cases.precise);
assert(decoded.point == cases.point && decoded.position == cases.position);
assert(decoded.state == cases.state && strcmp(decoded.empty_text,''));
assert(decoded.required_zero == 0 && decoded.required_enabled);
assert(numel(decoded.samples)==2 && decoded.samples{2}==4);
assert(numel(decoded.labels)==2 && strcmp(decoded.labels{2},'octave'));
assert(isequal(decoded.fixed_states(:),cases.fixed_states(:)));
assert(decoded.high_id_value==42);
% Packed complex values keep real/imaginary components paired per element.
complex_cases=CodecCases('new');
complex_cases.points=complex(single([1.25;-3.75]),single([-2.5;4.125]));
complex_out=CodecCases('decode',CodecCases('encode',complex_cases));
assert(isequal(complex_out.points(:),complex_cases.points(:)));
% An absent optional field and empty packed fields retain their SDL defaults.
defaults=CodecCases('new');
defaults_wire=CodecCases('encode',defaults);
defaults_out=CodecCases('decode',defaults_wire);
assert(isempty(defaults_out.tiny) && isempty(defaults_out.optional_enabled));
assert(isempty(defaults_out.points) && isempty(defaults_out.packed_states));
assert(isempty(defaults_out.packed_flags) && isempty(defaults_out.samples));
assert(defaults_out.required_zero==0 && ~defaults_out.required_enabled);
assert(isequal(defaults_out.fixed_states(:),int32([0;0;0])));
% Declared enum boundary values round-trip as signed int32 values.
enum_values=int32([-2147483648;2147483647]);
for k=1:numel(enum_values)
 enum_cases=CodecCases('new'); enum_cases.state=enum_values(k);
 enum_out=CodecCases('decode',CodecCases('encode',enum_cases));
 assert(enum_out.state==enum_values(k));
end
% Empty packed SoA and multidimensional terminal columns are valid.
empty_root=RootPayload('new'); empty_root.fixed_array=struct('x',single([]),'y',single([]));
empty_root_out=RootPayload('decode',RootPayload('encode',empty_root));
assert(isempty(empty_root_out.fixed_array.x) && isempty(empty_root_out.fixed_array.y));
% All fields can be omitted from the payload; decoder defaults are applied.
empty_payload=CodecCases('decode_payload',uint8([]));
assert(empty_payload.required_zero==0 && ~empty_payload.required_enabled);
% Unknown fields are skipped, while malformed headers and values are rejected.
unknown=[test_u32(1234);test_u32(3);uint8([9;8;7])];
unknown_out=CodecCases('decode_payload',unknown);
assert(unknown_out.required_zero==0);
assert_throws(@() CodecCases('decode_payload',uint8([1;2;3])));
assert_throws(@() CodecCases('decode_payload',[test_u32(1);test_u32(2);uint8([1;2])]));
assert_throws(@() CodecCases('decode_payload',[test_u32(17);test_u32(1);uint8(2)]));
assert_throws(@() CodecCases('decode_payload',[test_u32(15);test_u32(3);uint8([1;2;3])]));
% Singular fields use the last occurrence; repeated fields append occurrences.
duplicates=[test_u32(11);test_u32(4);test_i32(7); ...
 test_u32(11);test_u32(4);test_i32(-42); ...
 test_u32(12);test_u32(2);test_i16(-9); ...
 test_u32(12);test_u32(2);test_i16(1234)];
duplicate_out=CodecCases('decode_payload',duplicates);
assert(duplicate_out.required_zero==int32(-42));
assert(numel(duplicate_out.samples)==2 && duplicate_out.samples{1}==int16(-9));
assert(duplicate_out.samples{2}==int16(1234));
assert_throws(@() CodecCases('decode',wire(1:end-1)));
bad_hash=wire; descriptor_size=test_read_u32(wire(1:4));
bad_hash(5+descriptor_size)=bitxor(bad_hash(5+descriptor_size),uint8(1));
assert_throws(@() CodecCases('decode',bad_hash));
assert_throws(@() EnumRecordBatch('decode',wire));
% Malformed fixed-array lengths must be rejected.
assert_throws(@() CodecCases('decode_payload',[test_u32(20);test_u32(8); ...
 test_i32(1);test_i32(2)]));
% TODO: decide whether MATLAB should reject undeclared enum values on encode/decode.
% SoA encoding rejects terminal columns that describe different record counts.
bad_root=RootPayload('new');
bad_root.fixed_array=struct('x',single([1;2]),'y',single(3));
assert_throws(@() RootPayload('encode',bad_root));
batch=EnumRecordBatch('new');
batch.records=struct('state',int32([enum.READY;enum.NEGATIVE]),'code',int32([11;22]));
batch_wire=EnumRecordBatch('encode',batch);
batch_out=EnumRecordBatch('decode',batch_wire);
assert(isequal(batch_out.records.state,int32([1;-7])));
assert(isequal(batch_out.records.code,int32([11;22])));
root=RootPayload('new');root.header='soa';
root.fixed_array=struct('x',single([1;3]),'y',single([2;4]));
root.var_array={};root_wire=RootPayload('encode',root);root_out=RootPayload('decode',root_wire);
assert(isequal(root_out.fixed_array.x,single([1;3])));
assert(isequal(root_out.fixed_array.y,single([2;4])));
rows=FixedRowBatch('new');
rows.rows.vectors.coords=reshape(single(1:8),[2,2,2]);
rows.rows.vectors.grid=reshape(int16(1:24),[3,2,2,2]);
rows_wire=FixedRowBatch('encode',rows);rows_out=FixedRowBatch('decode',rows_wire);
assert(isequal(rows_out.rows.vectors.coords,rows.rows.vectors.coords));
assert(isequal(rows_out.rows.vectors.grid,rows.rows.vectors.grid));
fprintf('MATLAB/Octave codec tests passed (wire endian: %s)\n', getenv('SDL_WIRE_ENDIAN'));
end

function assert_throws(callback)
failed=false;
try
 callback();
catch
 failed=true;
end
assert(failed);
end

function bytes=test_u32(value)
bytes=typecast(uint32(value),'uint8');
if strcmpi(getenv('SDL_WIRE_ENDIAN'),'little'),bytes=flipud(bytes(:));else,bytes=bytes(:);end
end

function value=test_read_u32(bytes)
bytes=uint8(bytes(:));
if strcmpi(getenv('SDL_WIRE_ENDIAN'),'little'),bytes=flipud(bytes);end
value=typecast(bytes,'uint32');
end

function bytes=test_i32(value)
bytes=typecast(int32(value),'uint8');
if strcmpi(getenv('SDL_WIRE_ENDIAN'),'little'),bytes=flipud(bytes(:));else,bytes=bytes(:);end
end

function bytes=test_i16(value)
bytes=typecast(int16(value),'uint8');
if strcmpi(getenv('SDL_WIRE_ENDIAN'),'little'),bytes=flipud(bytes(:));else,bytes=bytes(:);end
end
