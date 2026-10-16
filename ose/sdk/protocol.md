# Component protocol CH/1

stdin/stdout are binary protocol streams. Diagnostics go to stderr. Each frame
is `uint32 big-endian length | uint8 opcode | body`. Length includes the opcode;
valid lengths are 1 through 1,048,576. Readers must handle partial reads. Ports
and catalogues are UTF-8. Payloads are ordinary SDL2 bytes, including their
catalogue-relative message ID, without S2PS/2 (the adapter adds/removes it).

| Opcode | Direction | Body |
| --- | --- | --- |
| `I` INIT | Adapter → component | JSON component configuration, parameters and port catalogues |
| `R` READY | Component → engine | Empty (legacy), or byte `0x01` to support timestamped TICKs |
| `D` INPUT | Adapter → component | u16 port length, port, u32 catalogue length, catalogue, SDL2 payload |
| `T` TICK | Engine → component | Empty (legacy), or u64 time_ns then u64 tick_index; preceding INPUTs form the snapshot |
| `P` PUBLISH | Component → adapter | u16 port length, port, SDL2 payload |
| `F` FINISHED | Component → adapter | Empty |
| `X` STOP | Adapter → component | Empty |

Timestamp fields are unsigned and big endian. An extended TICK has exactly 16
body bytes and a positive component-local tick index, starting at one. A component
advertises support with READY body `0x01`. The real-time adapter sends extended
ticks only to capable components and retains empty ticks for legacy READYs.
Simulation requires the capability and rejects legacy components before ticking.
Other READY bodies and malformed TICK contexts are rejected.

Updated generated containers advertise the capability automatically and accept
empty historical ticks too, using time zero and the next local index. Raw Python
components use `ComponentSession(timed_ticks=True)`; C uses `chain_ready()` and
`chain_tick()`; Rust uses `send(b'R', TICK_CONTEXT)` and `tick()`. The raw Rust
`initialize()` convenience function retains the legacy empty READY. Lifecycle
ordering and callback signatures are unchanged. The [simulation guide](../docs/simulation.md)
documents the clock context in all five languages. Java uses unsigned `BigInteger`
clock values; Ada uses `Interfaces.Unsigned_64`.

An INPUT with both catalogue length zero and an empty SDL2 payload represents
an explicit null on a required input with a configured timeout. It has the same
`D` opcode and port encoding. Other INPUT frames contain a nonempty catalogue
and valid SDL2 payload. Generated containers reject explicit nulls for untimed
or optional inputs, duplicate INPUTs and missing required INPUT frames.
Application callbacks receive only `None`/`NULL`, without timeout event metadata.

All lengths are big endian. The component receives INIT, initializes its local
codec and sends READY. After zero or more INPUT frames, TICK permits processing.
It may publish zero or more outputs, then sends FINISHED. One tick is in flight
at a time. STOP requests clean exit, including while waiting for the next tick.
The adapter's 0.5-second grace period includes both writing STOP and waiting for
exit. A full stdin pipe cannot postpone this deadline. It then sends SIGTERM,
waits up to one second, and kills/reaps the component if necessary.

FINISHED removes selected FIFO inputs after processing; it is not a network ACK.
Null inputs consume no FIFO record. Required-input deadlines rearm after the
adapter completes the tick. Timeouts do not create network messages or ACKs.
An error must terminate the component with a nonzero status. All pending inputs
are volatile and disappear on restart. Network retries can repeat transmissions;
the adapter deduplicates them within its bounded, in-memory history.

Python: `ComponentSession` supplies configuration, `ticks()` and `publish()`.
Generated Python/C/Rust/Java/Ada containers supply typed callback dispatch and output
methods on top of the binary SDK. They defer READY until codec and handler initialization
succeed, and send FINISHED only after callbacks return successfully. Their
callbacks run at TICK, never directly on a network receive; see
[callback containers](../docs/callbacks.md).
C: `ChainEvent.data` owns the body and must be released; `chain_input()` returns
borrowed fields, with catalogues explicitly sized rather than NUL terminated.
Rust: `initialize()` returns INIT JSON bytes; `input()` borrows the decoded
fields from its event and `Input::is_null()` recognizes an explicit null frame.
Python `ComponentSession.ticks()` maps that frame to a `None` entry; normal entries
remain `(catalogue, payload)` pairs. In C, `chain_input()` returns zero catalogue
and payload sizes for an explicit null. Raw SDK users must handle this value when
enabling input timeouts. The C and Rust stdio SDKs do not depend on a JSON parser
or SDL. The optional C callback runner and generated Rust container use the
public SDL runtime to decode and release typed snapshots.

Java and Ada use the same frames and capability byte. Java owns decoded objects
through the JVM; Ada containers own selected values and expose borrowed constant
references during callbacks. See [Java and Ada SDKs](../docs/java-ada.md) for
public APIs, timeout nulls and lifetime rules.
