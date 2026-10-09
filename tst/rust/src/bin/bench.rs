use sdl_runtime::{decode, description, encode, prepare, Complex32, SdlMessage};
use sdl_schema_tests::bench_cases::{BenchEntry, BenchOptionals, BenchSmall, BenchVariable, BenchVector, BenchPose, BenchRecord, BenchRecordBatch};
use sdl_schema_tests::benchmark::BenchPayload;
use std::time::Instant;
fn bench<T: SdlMessage>(case: &str, message: T, useful: usize) {
    let text = description(&[T::type_info()]).unwrap();
    let start = Instant::now();
    let ctx = prepare(&text, &[T::type_info()]).unwrap();
    let preparation = start.elapsed().as_secs_f64() * 1e6;
    let bytes = encode(&ctx, &message).unwrap();
    let copy: T = decode(&ctx, &bytes).unwrap();
    assert_eq!(encode(&ctx, &copy).unwrap(), bytes);
    for _ in 0..100 {
        encode(&ctx, &message).unwrap();
        decode::<T>(&ctx, &bytes).unwrap();
    }
    let min_iterations = std::env::var("SDL_BENCH_ITERATIONS")
        .ok()
        .filter(|v| !v.is_empty() && v.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|v| v.parse::<usize>().ok())
        .filter(|v| *v > 0)
        .unwrap_or(200);
    println!(
        "{} metadata: {} description bytes, {:.1} us prepare",
        case,
        text.len(),
        preparation
    );
    for operation in ["encode", "decode"] {
        let mut rates = Vec::new();
        for _ in 0..3 {
            let start = Instant::now();
            let mut n = 0usize;
            while n < min_iterations || start.elapsed().as_secs_f64() < 0.1 {
                if operation == "encode" {
                    std::hint::black_box(encode(std::hint::black_box(&ctx), std::hint::black_box(&message)).unwrap());
                } else {
                    std::hint::black_box(decode::<T>(std::hint::black_box(&ctx), std::hint::black_box(&bytes)).unwrap());
                }
                n += 1;
            }
            rates.push(n as f64 / start.elapsed().as_secs_f64());
        }
        rates.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let rate = rates[1];
        println!(
            "{} {} {:.1} msg/s {:.4} MiB/s ({} bytes/message)",
            case,
            operation,
            rate,
            rate * useful as f64 / 1048576.,
            bytes.len()
        );
    }
}
fn main() {
    bench(
        "small",
        BenchSmall {
            active: true,
            sequence: 123,
            code: -2,
        },
        7,
    );
    bench(
        "optional_sparse",
        BenchOptionals {
            a: Some(1),
            ..Default::default()
        },
        4,
    );
    bench(
        "optional_dense",
        BenchOptionals {
            a: Some(1),
            b: Some(2),
            c: Some(3),
            d: Some(4),
            e: Some(5),
            f: Some(6),
            g: Some(7),
            h: Some(8),
        },
        32,
    );
    bench(
        "variable",
        BenchVariable {
            header: "V".repeat(40),
            entries: (0..8)
                .map(|i| BenchEntry {
                    label: Some(format!("entry{}", i)),
                    number: i,
                })
                .collect(),
        },
        152,
    );
    bench(
        "packed",
        BenchPayload {
            header: "H".repeat(200),
            samples: (0..5000)
                .map(|i| Complex32 {
                    real: i as f32 * 0.25,
                    imag: -(i % 97) as f32 * 0.5,
                })
                .collect(),
        },
        40200,
    );
    bench(
        "packed_struct",
        BenchRecordBatch {
            records: (0..1000).map(|i| BenchRecord {
                id: i,
                pose: BenchPose {
                    position: BenchVector { values: [i as f32 * 0.25, -(i as f32) * 0.5, (i % 97) as f32] },
                    rotation: [0.0, 0.0, 0.0, 1.0],
                },
                measures: [i as f32 * 0.125, -((i % 31) as f32) * 0.5],
            }).collect(),
        },
        40000,
    );
}
