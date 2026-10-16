# SDL2 Wireshark dissectors

The C dissector reads a UTF-8 `SDL2|` catalogue announcement followed by
SDL2 data on the same directional UDP flow (source/destination addresses and
ports). Each datagram contains one complete catalogue or one complete data
buffer. It caches catalogues for the capture lifetime and resets on capture
initialization. Values follow the recorded SDL wire descriptors.

Data packets need a preceding catalogue announcement. Capture from connection
opening; isolated data is not self describing. A new announcement replaces the
flow's context, representing a new connection. Stream transports would require
an additional framing/reassembly adapter. See the [wire specification](../../docs/wire_descriptor.md).

The UDP dissector is a native C plugin and works in Wireshark builds without Lua.

SDL PCAPNG recordings use a separate USER0 transport, described below. Its
application envelope is little-endian; the contained SDL payload keeps its normal
SDL wire representation.

## USER0 PCAPNG recordings

Build and install the C plugin described below, then open a file produced with
`format='pcapng'` or convert an SDL MCAP recording with
`sdl-recording convert input.mcap output.pcapng --format pcapng`.
The plugin handles both directional UDP flows and SDL USER0 recording packets,
with no Lua or Rust recording library dependency. USER0 registers the `sdlrec`
protocol under `WTAP_ENCAP_USER0` in `wtap_encap`; 147 is the file link-type value,
not the Wireshark internal encapsulation key. See
[Wireshark's explanation](https://lists.wireshark.org/archives/wireshark-users/202109/msg00012.html).
USER0 is a local convention. If another plugin claims USER0, disable it or set
the User DLT payload protocol to `sdlrec`. Remove any previously installed
`sdl_recording.lua` before installing this C plugin to avoid duplicate protocols.

The inspector displays HEADER, CATALOGUE, CHANNEL, MESSAGE, METADATA and END,
including topics, root names, metadata, exact unsigned 64-bit nanosecond timestamps,
sequence and raw payload bytes. MESSAGE fields use the C runtime's dynamic decoder,
including unsigned integers, wire representations and quantized floats.
Catalogues are resolved by local schema/channel ID; UDP flow catalogues are kept
separately. Redissection respects declaration frame order and never counts a
message twice. Both caches reset when the capture is initialized or closed.

The adapter checks packet CRC, bounded fields, UTF-8, duplicate metadata keys,
conflicting declarations, channel roots and message bodies. END counters and stream
CRC are checked during the initial sequential pass and retained for redissection.
Capture timestamps must equal log time for messages and zero for control records.
Filter `sdlrec.kind == 5` to display only messages; other useful filters are
`sdlrec.id == 1`, `sdlrec.topic == "radio/iq"` and `sdlrec.topic contains "radio"`.
Control bytes in strings are escaped; ordinary topic values have no surrounding quotes.

Packet dissection cannot certify all PCAPNG block/options constraints, physical EOF
or missing END. Use `sdl-recording validate` for full-file validation. Saving selected
packets does not regenerate END or preserve all required declarations. The
[binary profile](../../docs/recording_pcapng.md) defines the supported single-section,
single-interface arrangement. TCP framing/reassembly remains outside this plugin.

The independent C adapter and runtime are compiled with C99, warnings as errors,
and exercised by `make test-wireshark-adapter` without Wireshark or Lua. Its seven
tests cover decoding, declaration revisions, redissection, malformed packets and
END integrity. The plugin translation unit also compiles with warnings as errors
against the actual Wireshark 4.0.17 and 4.4.19 SDK headers. Complete compilation and linking with `-Werror` and `-Wl,-z,defs`, native
plugin loading, UDP heuristic/Decode As dissection, USER0 dissection and two-pass
redissection have also been validated with the installed TShark 3.4.10, built
without Lua. Runtime loading with 4.0/4.4 and GUI execution remain untested;
the SDK checks for those releases are compilation checks only.

Run the opt-in integration suite after building and installing the matching plugin:

```sh
make -C tools/wireshark CFLAGS='-std=c11 -O2 -Wall -Wextra -Werror' LDFLAGS='-Wl,-z,defs'
make test-wireshark-tshark
```

The linker flag above is for ELF platforms. The three integration tests compare
one-pass and `tshark -2` output, check valid and malformed UDP samples, exact
uint64 times and payloads, quantized values, display filters, declaration frame
order and persistent END errors. `TSHARK` may select another installed executable.
The suite fails if the native plugin is unavailable; ordinary `test-recording`
runs skip these installation-dependent tests.

## C

The C plugin uses the SDL C runtime's prepared catalogue and dynamic decoder.
Install Wireshark development headers, a C11 compiler, Make, and pkg-config,
then build for the same Wireshark version that will load it:

```sh
pkg-config --cflags --libs wireshark
make -C tools/wireshark
```

Install `sdl.so` in that version's external plugin directory. Select
**Decode As… → SDL2 Wire (C)** (`tshark -d udp.port==47000,sdl`). The Makefile supports `PLUGIN_SUFFIX=.dll`
where the platform/toolchain supports shared modules. Development headers are
required; this plugin is not compiled by the main runtime suite.
The standalone adapter/runtime remain C99; the plugin uses C11 to accommodate
Wireshark SDK headers. The source includes the plugin description entry point
and heuristic callback types expected by newer Wireshark releases.

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
