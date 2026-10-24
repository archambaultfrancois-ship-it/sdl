# Shared parameter blocks

Two Python detectors read individual `DetectionParams` blocks and a common
`SafetySettings` block. They print their effective settings whenever these change.
The example needs only Python and the sibling SDL checkout.

```sh
cd ose/examples/shared_parameters
make run
```

In another terminal in the same directory:

```sh
make list
make get
make set
make watch
```

`set` sends the complete `detection.json` block to the first detector. The second
detector retains its own settings. Both subscribe to `atelier/security`.
The public token in this example is for the default loopback demo.

`make simulate` runs two virtual seconds, changes the first detector's settings
at one second and writes a deterministic trace.

See [the parameter API](../../docs/parameters.md) for component and external SDKs,
rights, distributed access and lifecycle guarantees.
