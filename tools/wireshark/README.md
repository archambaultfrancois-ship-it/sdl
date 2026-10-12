# SDL2 Wireshark dissectors

The Lua and C dissectors read a UTF-8 `SDL2` catalogue announcement followed by
SDL2 data on the same directional UDP flow (source/destination addresses and
ports). Each datagram contains one complete catalogue or one complete data
buffer. They cache catalogues for the capture lifetime and reset on capture
initialization. All numeric values use big endian.

Data packets need a preceding catalogue announcement. Capture from connection
opening; isolated data is not self describing. A new announcement replaces the
flow's context, representing a new connection. Stream transports would require
an additional framing/reassembly adapter. See the [wire specification](../../docs/wire_descriptor.md).

## Lua

Copy [`sdl.lua`](sdl.lua) into Wireshark's personal Lua plugin directory, then
restart Wireshark. Choose **Decode As… → SDL Wire** for UDP. The heuristic
recognizes catalogue announcements and subsequent packets on known flows.
Malformed data is marked with a protocol expert error.

## C

The C plugin uses the SDL C runtime's prepared catalogue and dynamic decoder.
Install Wireshark development headers, a C99 compiler, Make, and pkg-config,
then build for the same Wireshark version that will load it:

```sh
pkg-config --cflags --libs wireshark
make -C tools/wireshark
```

Install `sdl.so` in that version's external plugin directory. Select
**Decode As… → SDL Wire (C)**. The Makefile supports `PLUGIN_SUFFIX=.dll`
where the platform/toolchain supports shared modules. Development headers are
required; this plugin is not compiled by the main runtime suite.

## Sample captures

[`sdl-demo.pcap`](sdl-demo.pcap) contains one opening catalogue and five valid
data packets. [`sdl-robustness.pcapng`](sdl-robustness.pcapng) adds three invalid
data packets (boolean, invalid optional count, truncation). They use UDP destination
port 47000. Valid values include enums, complex samples, nested arrays, Unicode,
NUL text, floating-point boundaries, and an empty message.

Regenerate both from the repository root:

```sh
python3 tools/wireshark/generate_sample.py
```
