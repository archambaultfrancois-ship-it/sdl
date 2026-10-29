use sdl_ose_client::{Client, Options, Retry};
use std::{sync::mpsc, time::Duration};
// Generated with the ordinary SDL generator; no graph compilation is required.
#[path = "../../../../build/generated/shared/rust/measurement.rs"]
mod measurement;
sdl_ose_client::sdl_message!(measurement::Measurement);
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().collect();
    let port = args.get(1).map(|p| p.parse()).transpose()?.unwrap_or(7000);
    let udp = args.get(2).map(|s| s == "udp").unwrap_or(false);
    let client = Client::connect("127.0.0.1", port, "external-rust")?;
    let publisher = client.publisher::<measurement::Measurement>("rust.result")?;
    let (tx, rx) = mpsc::sync_channel(1);
    let sub = client.subscribe::<measurement::Measurement>(
        "external.input",
        Options {
            udp,
            ack_received: true,
            retry: Some(Retry::default()),
            ..Options::default()
        },
        move |value, info| {
            println!("MESSAGE {} {}", info.sequence, value.value);
            tx.send(value)
                .map_err(|e| sdl_ose_client::Error::new("callback_failed", e.to_string()))?;
            Ok(())
        },
    )?;
    println!("READY");
    let mut value = rx.recv_timeout(Duration::from_secs(10))?;
    value.value *= 2.;
    let result = publisher.publish(&value)?;
    if !result.accepted() {
        return Err("broker refused an admission".into());
    }
    sub.close()?;
    if !sub.wait_closed(Duration::from_secs(2)) {
        return Err("callback still active".into());
    }
    client.close();
    Ok(())
}
