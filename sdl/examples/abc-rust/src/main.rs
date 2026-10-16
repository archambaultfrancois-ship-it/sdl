use std::io::{self, Write};
use std::os::unix::net::UnixStream;
use std::thread;

#[path = "../build/generated/rust/abc.rs"]
mod abc;

use abc::{EquationInput, EquationKind, EquationResult};
use sdl_runtime::{decode, description, display, encode, prepare, SdlMessage};

const MAX_FRAME_SIZE: usize = 1024 * 1024;

fn send_message<T: SdlMessage>(stream: &mut UnixStream, message: &T) -> io::Result<()> {
    let text = description(&[T::type_info()])
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, format!("{:?}", e)))?;
    let ctx = prepare(&text, &[T::type_info()])
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, format!("{:?}", e)))?;
    stream.write_all(&(text.len() as u32).to_be_bytes())?;
    stream.write_all(text.as_bytes())?;
    let frame = encode(&ctx, message)
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, format!("{:?}", e)))?;
    if frame.is_empty() || frame.len() > MAX_FRAME_SIZE || frame.len() > u32::MAX as usize {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "SDL frame is too large",
        ));
    }
    stream.write_all(&(frame.len() as u32).to_be_bytes())?;
    stream.write_all(&frame)
}

fn receive_message<T: SdlMessage + Default>(stream: &mut UnixStream) -> io::Result<T> {
    use std::io::Read;
    let mut schema_header = [0; 4];
    stream.read_exact(&mut schema_header)?;
    let schema_size = u32::from_be_bytes(schema_header) as usize;
    if schema_size == 0 || schema_size > MAX_FRAME_SIZE {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid catalogue length",
        ));
    }
    let mut schema = vec![0; schema_size];
    stream.read_exact(&mut schema)?;
    let text =
        std::str::from_utf8(&schema).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;
    let ctx = prepare(text, &[T::type_info()])
        .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, format!("{:?}", e)))?;
    let mut header = [0; 4];
    stream.read_exact(&mut header)?;
    let length = u32::from_be_bytes(header) as usize;
    if length == 0 || length > MAX_FRAME_SIZE {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid SDL frame length",
        ));
    }
    let mut frame = vec![0; length];
    stream.read_exact(&mut frame)?;
    decode(&ctx, &frame).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, format!("{:?}", e)))
}

fn input_thread(mut socket: UnixStream) -> io::Result<()> {
    print!("Enter coefficients a, b, c for a*x^2 + b*x + c = 0: ");
    io::stdout().flush()?;
    let mut line = String::new();
    io::stdin().read_line(&mut line)?;
    let values: Vec<f64> = line
        .split_whitespace()
        .map(str::parse)
        .collect::<Result<_, _>>()
        .map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "Please enter three finite real numbers.",
            )
        })?;
    if values.len() != 3 || values.iter().any(|x| !x.is_finite()) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Please enter three finite real numbers.",
        ));
    }
    send_message(
        &mut socket,
        &EquationInput {
            a: values[0],
            b: values[1],
            c: values[2],
        },
    )
}

fn solver_thread(mut input_socket: UnixStream, mut result_socket: UnixStream) -> io::Result<()> {
    let input: EquationInput = receive_message(&mut input_socket)?;
    println!("Solver received:\n{}", display(&input, 3));
    let mut result = EquationResult::default();
    if input.a == 0.0 {
        if input.b == 0.0 {
            result.kind = if input.c == 0.0 {
                EquationKind::InfiniteSolutions
            } else {
                EquationKind::NoSolution
            };
        } else {
            result.kind = EquationKind::OneReal;
            result.x1 = Some(-input.c / input.b);
        }
    } else {
        let d = input.b * input.b - 4.0 * input.a * input.c;
        if d > 0.0 {
            let root = d.sqrt();
            result.kind = EquationKind::TwoReal;
            result.x1 = Some((-input.b - root) / (2.0 * input.a));
            result.x2 = Some((-input.b + root) / (2.0 * input.a));
        } else if d == 0.0 {
            result.kind = EquationKind::OneReal;
            result.x1 = Some(-input.b / (2.0 * input.a));
        } else {
            result.kind = EquationKind::Complex;
            result.real_part = Some(-input.b / (2.0 * input.a));
            result.imaginary_part = Some((-d).sqrt() / (2.0 * input.a).abs());
        }
    }
    println!("Solver sending:\n{}", display(&result, 3));
    send_message(&mut result_socket, &result)
}

fn display_thread(mut socket: UnixStream) -> io::Result<()> {
    let result: EquationResult = receive_message(&mut socket)?;
    match result.kind {
        EquationKind::TwoReal => println!(
            "Two real roots: x1 = {:.12}, x2 = {:.12}",
            result.x1.unwrap(),
            result.x2.unwrap()
        ),
        EquationKind::OneReal => println!("One real root: x = {:.12}", result.x1.unwrap()),
        EquationKind::Complex => println!(
            "Complex roots: x = {:.12} +/- {:.12}i",
            result.real_part.unwrap(),
            result.imaginary_part.unwrap()
        ),
        EquationKind::InfiniteSolutions => println!("Every real number is a solution."),
        EquationKind::NoSolution => println!("There is no solution."),
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let (input_writer, input_reader) = UnixStream::pair()?;
    let (result_writer, result_reader) = UnixStream::pair()?;
    let display = thread::spawn(|| display_thread(result_reader));
    let solver = thread::spawn(|| solver_thread(input_reader, result_writer));
    let input = thread::spawn(|| input_thread(input_writer));
    input.join().map_err(|_| "input thread panicked")??;
    solver.join().map_err(|_| "solver thread panicked")??;
    display.join().map_err(|_| "display thread panicked")??;
    Ok(())
}
