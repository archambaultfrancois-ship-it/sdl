use sdl_runtime::{decode, encode, Complex32};
use sdl_schema_tests::benchmark::BenchPayload;
use std::hint::black_box;
use std::time::Instant;

fn report_rate(operation: &str, iterations: usize, bytes_per_message: usize,
   seconds: f64) {
   let messages_per_second = iterations as f64 / seconds;
   let mebibytes_per_second = messages_per_second * bytes_per_message as f64 /
      (1024.0 * 1024.0);
   println!("{:<6} {:>10.0} msg/s  {:>8.2} MiB/s  ({} bytes/message)",
      operation, messages_per_second, mebibytes_per_second, bytes_per_message);
}

fn main() {
   let iterations = std::env::var("SDL_BENCH_ITERATIONS")
      .ok()
      .and_then(|value| value.parse::<usize>().ok())
      .filter(|value| *value != 0)
      .unwrap_or(200);
   let message = BenchPayload {
      header: "H".repeat(200),
      samples: (0..5000).map(|index| Complex32 {
         real: index as f32 * 0.25,
         imag: -((index % 97) as f32) * 0.5,
      }).collect(),
   };
   let wire = encode(&message).expect("encode benchmark message");
   assert_eq!(wire.len(), 40220);

   let start = Instant::now();
   for _ in 0..iterations {
      let encoded = encode(black_box(&message)).expect("encode message");
      assert_eq!(encoded.len(), wire.len());
      black_box(encoded);
   }
   report_rate("encode", iterations, wire.len(), start.elapsed().as_secs_f64());

   let start = Instant::now();
   for _ in 0..iterations {
      let decoded: BenchPayload = decode(black_box(&wire)).expect("decode message");
      assert_eq!(decoded.samples.len(), 5000);
      black_box(decoded);
   }
   report_rate("decode", iterations, wire.len(), start.elapsed().as_secs_f64());
   println!("iterations: {}, wire endian: big", iterations);
}
