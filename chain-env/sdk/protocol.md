# Component protocol CH/1

stdin/stdout are binary protocol streams. Diagnostics go to stderr. Each frame
is `uint32 big-endian length | uint8 opcode | body`. Length includes the opcode;
valid lengths are 1 through 1,048,576. Readers must handle partial reads. Ports
and catalogues are UTF-8. Payloads are ordinary SDL2 bytes, including their
catalogue-relative message ID, without S2PS/2 (the adapter adds/removes it).

| Opcode | Direction | Body |
| --- | --- | --- |
| `I` INIT | Adapter → component | JSON component configuration, parameters and port catalogues |
| `R` READY | Component → adapter | Empty |
| `D` INPUT | Adapter → component | u16 port length, port, u32 catalogue length, catalogue, SDL2 payload |
| `T` TICK | Adapter → component | Empty; preceding INPUT frames form this tick's snapshot |
| `P` PUBLISH | Component → adapter | u16 port length, port, SDL2 payload |
| `F` FINISHED | Component → adapter | Empty |
| `X` STOP | Adapter → component | Empty |

All lengths are big endian. The component receives INIT, initializes its local
codec and sends READY. After zero or more INPUT frames, TICK permits processing.
It may publish zero or more outputs, then sends FINISHED. One tick is in flight
at a time. STOP requests clean exit, including while waiting for the next tick.
The adapter's 0.5-second grace period includes both writing STOP and waiting for
exit. A full stdin pipe cannot postpone this deadline. It then sends SIGTERM,
waits up to one second, and kills/reaps the component if necessary.

FINISHED removes selected FIFO inputs after processing; it is not a network ACK.
An error must terminate the component with a nonzero status. All pending inputs
are volatile and disappear on restart. Network retries can repeat transmissions;
the adapter deduplicates them within its bounded, in-memory history.

Python: `ComponentSession` supplies configuration, `ticks()` and `publish()`.
Generated Python/C/Rust containers supply typed callback dispatch and output
methods on top of the binary SDK. They defer READY until codec and handler initialization
succeed, and send FINISHED only after callbacks return successfully. Their
callbacks run at TICK, never directly on a network receive; see
[callback containers](../docs/callbacks.md).
C: `ChainEvent.data` owns the body and must be released; `chain_input()` returns
borrowed fields, with catalogues explicitly sized rather than NUL terminated.
Rust: `initialize()` returns INIT JSON bytes; `input()` borrows the decoded
fields from its event. The C and Rust stdio SDKs do not depend on a JSON parser
or SDL. The optional C callback runner and generated Rust container use the
public SDL runtime to decode and release typed snapshots.
