function bench()
iterations=str2double(getenv('SDL_BENCH_ITERATIONS'));
if ~isfinite(iterations)||iterations<1, iterations=200; end
message=BenchPayload('new'); message.header=repmat('H',1,200);
message.samples=complex(single((1:5000)'*0.25),single(-mod((1:5000)',97)*0.5));
wire=BenchPayload('encode',message); n=numel(wire);
tic; for k=1:iterations, encoded=BenchPayload('encode',message); end; encode_time=toc;
tic; for k=1:iterations, decoded=BenchPayload('decode',wire); end; decode_time=toc;
fprintf('encode %10.0f msg/s  %8.2f MiB/s (%d bytes/message)\n',iterations/encode_time,iterations/encode_time*n/(1024^2),n);
fprintf('decode %10.0f msg/s  %8.2f MiB/s (%d bytes/message)\n',iterations/decode_time,iterations/decode_time*n/(1024^2),n);
fprintf('iterations: %d, wire endian: big\n',iterations);
end
