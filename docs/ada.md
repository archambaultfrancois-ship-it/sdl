# Ada bindings

Generate Ada 2012 bindings with:

```sh
python3 gen/generator.py -ada sdl build/generated
make test-ada bench-ada
```

Each schema file becomes an `SDL_<file>` package, with a specification `.ads`
and body `.adb`. The runtime is `runtime/ada/sdl_runtime.ads/.adb` plus
`runtime/ada/sdl_ada_bridge.c` and the C runtime sources in `runtime/c`.
No generated C bindings are needed for Ada applications.

The Makefile uses `GNAT_HOME=~/opt/gnat-21.1` and `ADA_RTS=sjlj` by default.
The native runtime in that installation lacks generic container bodies, while
its sjlj runtime is complete. For another installation, for example:

```sh
make test-ada GNAT_HOME=/opt/gnat ADA_RTS=native
```

`ADA_FLAGS` defaults to `--RTS=$(ADA_RTS) -gnat2012 -gnata -gnatn -O3`. Range and
overflow checks stay enabled. Linking requires the C runtime objects, the bridge
object, and `-lm`; the Makefile demonstrates the complete `gnatmake` command.
Use the same runtime when compiling and linking every Ada unit.

The [ABC equation sample](../samples/abc-ada/README.md) demonstrates three Ada
tasks exchanging catalogues and typed messages over Unix-domain sockets.
Run it with `make -C samples/abc-ada run`.

## Typed API

For `sdl/wire_example.sdl`:

```ada
with Ada.Strings.Unbounded; use Ada.Strings.Unbounded;
with SDL_Runtime; use SDL_Runtime;
with SDL_Wire_Example; use SDL_Wire_Example;

procedure Example is
   C : constant Context := Prepare;
   M : T_Packet;
begin
   M.F_active := True;
   M.H_code := True;
   M.F_code := -2;
   M.F_label := To_Unbounded_String ("hello");
   M.F_samples.Append (300);
   declare
      Wire : constant Bytes := Encode (C, M);
      Received : constant T_Packet := Decode (C, Wire);
   begin
      pragma Assert (Received = M);
   end;
end Example;
```

The return type selects the overloaded `Decode`. `Bytes` is an unconstrained
array of `Interfaces.Unsigned_8`, indexed by `Positive`. Decoding accepts any
positive lower bound. Data contains the catalogue's message ID and values;
the catalogue travels separately. `Description` contains the complete UTF-8
SDL2 catalogue for the schema file.

Records use `T_<type>` and `F_<field>` names. Optional fields add an `H_<field>`
boolean; the stored value is ignored when absent and defaults on decode.
Repeated and Packed fields both use typed vectors. Fixed dimensions use nested
fixed arrays indexed from one, including arrays of records. Nested and anonymous
structures have their own generated record types. Empty messages are null records.
Names are checked case-insensitively to reject Ada declaration collisions.

Integers and floats use `Interfaces` fixed-width types. Complex values have
`Re` and `Im` components. Strings are `Unbounded_String` containing UTF-8 bytes,
including embedded NUL bytes. Enum types are `Int32` subtypes with constants
named `E_<enum>_<item>`; codecs validate membership, including negative values.
Fixed array bounds and vector lengths must fit Ada's `Positive`/`Natural` range.

## Contexts and validation

`Prepare` creates an opaque reusable context. Ada controlled copying retains the
native context; finalization releases it automatically. Prepare outside codec
loops. Shared contexts require synchronization when copied/finalized across tasks.

`Prepare (Remote_Catalogue)` compares the received catalogue against the local
`Description`. Fields map by SDL IDs, allowing renames, added/removed fields and
repeated/Packed interchange. Missing local fields retain defaults. Common field
types, cardinality and fixed dimensions must agree. Decoding rejects unknown local
enum values. Encoding requires matching fields for each visited message. The generic
encoder validates the resulting bytes against the remote catalogue.

Malformed catalogues or data raise `SDL_Runtime.Codec_Error`. Wire validation
checks bool values, enums, UTF-8, canonical counters, truncation, lengths and
trailing bytes during direct decoding or before generic typed materialization. Every temporary C dynamic tree is
released on success or exception.

Messages containing Packed fields, including through nested records, use generated
Ada positional codecs when all reachable field IDs, types, cardinalities,
dimensions and enum value sets match the remote catalogue. Field and enum labels
may change. Eligibility is cached during context preparation; a change inside a
nested type disables the direct path for its parent messages.

The encoder computes the exact wire size, allocates one byte array, and writes
big-endian values into it. Fixed record size is constant and sequence size uses
count times record size. The decoder reads directly into Ada records and reserves
vector storage once, avoiding the C dynamic tree. Fixed subrecord codecs and
scalar word conversions are inlined with `-gnatn`; Ada range checks remain enabled.
Floating-point bits, including negative zero and NaN payloads, are preserved.
The direct path validates enum membership, bool values, UTF-8, canonical counters,
sequence limits, truncation and trailing bytes itself. Changed layouts or enum
sets retain the C dynamic decoding and scalar encoding fallback.

To compare against the generic implementation, prepare with
`Prepare (Use_Direct => False)` (or `SDL_Runtime.Prepare (Remote, Local,
Use_Direct => False)`). For the Ada benchmark:

```sh
make bench-ada
SDL_ADA_NO_DIRECT=1 make bench-ada
```

The flag changes the implementation, keeping the typed vector API, messages and
wire bytes identical. Tests compare direct encoders/decoders against the generic
wire oracle and cover malformed inputs and nested schema evolution.
