# SDL Wireshark dissectors

This directory contains two dissectors for complete SDL wire frames carried
in individual UDP datagrams. SDL embeds its schema descriptor in every frame,
so neither dissector needs generated schema files. The wire format is
documented in [`../../docs/wire_descriptor.md`](../../docs/wire_descriptor.md).

## Lua dissector

[`sdl.lua`](sdl.lua) is the generic Wireshark Lua dissector. Copy it
into Wireshark's personal Lua plugin directory, shown under **Help → About
Wireshark → Folders → Personal Plugins**, then restart Wireshark.

Right-click a UDP packet, choose **Decode As…**, and select **SDL Wire**. The
dissector is available in the UDP Decode As protocol list without a fixed port
assignment. In **Edit → Preferences → Protocols → SDL Wire**, set the wire
byte order to match the sender. Big endian is the default; byte order is not
encoded in SDL frames, while descriptor contents are always big endian.

The Lua dissector shows the root message name, schema hash, field IDs, lengths,
and decoded primitive, enum, string, nested message, and fixed array values.
Unknown field IDs are displayed and skipped. Malformed or truncated frames
are marked with an SDL decode error. It expects one complete frame per UDP
datagram and does not reassemble frames split across datagrams.

### Sample captures

[`sdl-demo.pcap`](sdl-demo.pcap) contains three complete SDL frames in
IPv4/UDP packets. [`sdl-robustness.pcapng`](sdl-robustness.pcapng) adds valid
frames for integer boundaries, special floating-point values, UTF-8 and
embedded NUL strings, enums, packed fields, nested fixed arrays, anonymous
structs, and empty messages. It also includes three deliberately malformed
frames for a bad descriptor hash, a truncated body, and an invalid boolean.
The captures use big endian and UDP destination port `47000`. Open either file
in Wireshark and select **SDL Wire** through **Decode As…** on UDP port `47000`.
The malformed frames should be marked with the SDL expert error.

Regenerate both captures with Python 3 from the repository root:

```sh
python3 tools/wireshark/generate_sample.py
```

## C dissector

[`packet-sdl.c`](packet-sdl.c) is the C dissector. It parses the embedded
schema, checks the FNV-1a fingerprint, displays primitive, enum, nested
message, fixed array, and unknown fields, and marks malformed frames with a
protocol expert error. It also expects one complete frame per UDP datagram
and does not reassemble frames split across datagrams.

### Build on Rocky Linux 9

Enable CRB and install the compiler, Make, `pkg-config`, and Wireshark
development files:

```sh
sudo dnf config-manager --set-enabled crb
sudo dnf install gcc make pkgconf-pkg-config wireshark-devel
```

Check that `pkg-config` can find Wireshark, then build from the repository
root:

```sh
pkg-config --cflags --libs wireshark
make -C tools/wireshark
```

The build produces `tools/wireshark/sdl.so`. Install it in the external
plugin directory for the matching Wireshark version. C plugins depend on the
Wireshark API and should be built for the version that will load them. In
Wireshark, decode UDP packets through **Decode As…** and choose **SDL Wire
(C)**. Set the `sdl.wire_endian` preference to match the sender; the default
is big endian.

The C Makefile accepts `PLUGIN_SUFFIX` for platform-specific module suffixes.
For example, `make -C tools/wireshark PLUGIN_SUFFIX=.dll` changes the output
name to `sdl.dll`.
