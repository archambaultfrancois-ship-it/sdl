# SDL Wireshark dissector

`sdl.lua` is a generic Wireshark Lua dissector for complete SDL wire frames
carried by individual UDP datagrams. SDL embeds its schema descriptor in each
frame, so the dissector does not need generated schema files.

## Install

Copy `sdl.lua` into the personal Wireshark Lua plugin directory. Wireshark
shows the active directory under **Help → About Wireshark → Folders → Personal
Plugins**. Restart Wireshark after installing the script.

In packet details, right-click a UDP packet and select **Decode As…**, then
choose **SDL Wire** for the UDP port. The dissector is also available in the
UDP Decode As protocol list without a fixed port assignment.

In **Edit → Preferences → Protocols → SDL Wire**, set the wire byte order to
match the sender. Big endian is the default. The byte order is not encoded in
SDL frames; descriptor contents themselves always use big endian.

The dissector shows the root message name, schema hash, field IDs, lengths,
and decoded primitive, enum, string, nested message, and fixed array values.
Unknown field IDs are displayed and skipped. Malformed or truncated frames
are marked with an SDL decode error.

This first version expects one complete SDL frame per UDP datagram. It does not
reassemble frames split across datagrams.

## Sample capture

[`samples/sdl-demo.pcap`](samples/sdl-demo.pcap) contains three complete SDL
frames in IPv4/UDP packets. The messages cover repeated and optional fields,
primitive and complex values, enums, and a packed complex-float array. The
capture uses big endian and destination UDP port `47000`.

Open the capture in Wireshark, then use **Decode As…** on UDP port `47000` to
select **SDL Wire**. The file can be regenerated with Python 3:

```sh
python3 tools/sdl-lua/generate_sample.py
```
