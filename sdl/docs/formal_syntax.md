# Formal syntax of SDL schemas and SDL2 catalogues

This reference describes the language accepted by the generator and the
canonical form produced for the C, Rust, Python, Java, Ada and Matlab/Octave
runtimes. It covers two texts: the `.sdl` source file and the single-line
UTF-8 catalogue `SDL2|…`. The catalogue describes how to encode messages;
message values are transmitted separately as binary data.

A grammar describes the structure of the text. The validation rules that
follow are also part of the language: a statement can match the grammar and
still be invalid, for example `int32 x wire(bits=12,q=0.1);`.

## 1. Reading conventions

The grammars use the following EBNF notation.

| Notation | Meaning |
| --- | --- |
| `rule = expression ;` | Define a rule |
| `"text"` | Literal characters to write, without the quotation marks |
| `A, B` | A followed by B |
| `A \| B` | A or B |
| `[ A ]` | Optional A, occurring at most once |
| `{ A }` | Zero or more occurrences of A |
| `( A )` | Grouping |
| `? description ?` | A character class described in words |

In the EBNF blocks, `;` ends a rule; `";"` denotes the semicolon that belongs
to the SDL text. Rule names explain the grammar and are not SDL keywords.
Keywords and names are case-sensitive.

## 2. `.sdl` source files

### 2.1. Whitespace and comments

Files are read as UTF-8 without a BOM. Source is tokenized independently of
line breaks. Whitespace, including newlines, can separate any tokens; it does
not delimit declarations. `//` starts an ignored comment ending at the next newline.
`/* ... */` is an ignored block, and `/** ... */` is retained documentation.
Blocks may span lines, do not nest and end at the first `*/`. Identifiers and numeric literals cannot be
split by whitespace or comments, including a sign or exponent in a number.
Signs on enum and wire-option literals must remain attached; signs in constant
expressions are operators and may be separated from their operands.

Braces delimit bodies; semicolons end fields, enum items, constants, aliases and the
optional package declaration. Named message/enum declarations end with `}`.
Several declarations can share one line, and fields or `wire(...)` clauses can
span several lines. Syntax errors include the unexpected token's line and column.
Whitespace and comments between tokens are implicit in the source grammar below;
they are forbidden in the compact runtime catalogue.

Documentation is allowed at declaration boundaries. A block before a declaration
documents that declaration; a block opening on the same line after a completed
declaration instead documents the preceding one. These completions are `;` for
fields, enum items, constants, aliases and packages, and `}` for named messages/enums.
Anonymous fields complete at their final semicolon. Documentation before a closing
brace or at EOF without a declaration, or inside a declaration's tokens, is rejected.
Package documentation describes the generated schema/module, and anonymous field
documentation is also attached to its generated message type. Continuation-line
`*` decorations are optional. See
[comments and generated documentation](schema.md#comments-and-generated-documentation).

```ebnf
DIGIT     = "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" ;
INTEGER   = DIGIT, { DIGIT } ;
SIGN      = "+" | "-" ;
NAME      = ? one or more characters matched by Python 3 \w ? ;
CONSTNAME = ? NAME beginning with a word character other than a digit ? ;
```

`NAME` includes Unicode letters and digits, and `_`. For names portable across
all six target languages, start with an ASCII letter, continue with ASCII
letters, digits or `_`, and avoid their reserved words. Backend-specific
restrictions also apply to runtime names and collisions after name conversion.
`$` is reserved for anonymous message names produced by the generator; it
cannot be written in a source name.

For field names shared across backends, use lowercase snake_case or camelCase.
Snake_case names match `[a-z][a-z0-9]*(?:_[a-z0-9]+)*`, for example `device_id`,
`temperature_c` or `sample_count`. CamelCase names start with a lowercase ASCII
letter and match `[a-z][A-Za-z0-9]*`, for example `deviceId`, `temperatureC` or
`sampleCount`. With snake_case, use single underscores between nonempty
segments, with no leading or trailing underscore. Avoid language keywords,
C macros such as `I` and `NULL`, and runtime members such as `display` or
`SDL_FIELDS`. Names within a message should remain distinct after case folding
and backend name conversion; for example, `deviceId` and `deviceID` collide
in Ada. See the
[portable naming recommendation](schema.md#portable-naming-recommendation)
for examples and generation checks. These recommendations supplement the
general source grammar, which still permits `_` and Unicode word characters.

`INTEGER` is always decimal: `0012` means 12. Signs are forbidden on field
numbers, dimensions and integer options. An enum value may start with `-`,
but not with `+`.

### 2.2. Declaration grammar

```ebnf
schema          = [ package ], { declaration } ;
package         = "package", package_name, ";" ;
package_name    = package_segment, { ".", package_segment } ;
package_segment = ? ASCII letter followed by ASCII letters, digits or underscore ? ;
declaration     = message | enumeration | constant | type_alias ;
constant        = "const", CONSTNAME, "=", expression, ";" ;
expression      = product, { ( "+" | "-" ), product } ;
product         = unary, { ( "*" | "/" ), unary } ;
unary           = { "+" | "-" }, primary ;
primary         = INTEGER | constant_ref | "(", expression, ")" ;
type_alias      = "alias", CONSTNAME, "=", type, ";" ;
message_keyword = "message" | "structure" | "struct" ;

message         = message_keyword, NAME, "{",
                  { field | anonymous_field }, "}" ;

enumeration     = "enum", NAME, "{", enum_item, { enum_item }, "}" ;
enum_item       = NAME, "=", enum_value, ";" ;
enum_value      = [ "-" ], INTEGER ;

prefix          = [ INTEGER, ":" ], [ cardinality ] ;
cardinality     = "required" | "optional" | "repeated" | "packed" ;
type            = [ "." ], NAME, { ".", NAME } ;
dimension       = "[", dimension_bound, "]" ;
dimension_bound = INTEGER | constant_ref ;
constant_ref    = [ "." ], NAME, { ".", NAME } ;

field           = prefix, type, { dimension }, NAME, [ wire_clause ], ";" ;

anonymous_field = prefix, message_keyword, "{", { field | anonymous_field },
                  "}", NAME, [ wire_clause ], ";" ;
```

The aliases `message`, `structure` and `struct` have exactly the same meaning.
`fl32[3][4]` and `fl32 [ 3 ] [ 4 ]` are equivalent, as are `7:int32 x;` and
`7 : int32 x;`. Distinct word tokens still require a separator:
`message Point` is valid, while `messagePoint` is a different identifier.

Dimension constants are integer expressions declared at top level.
They follow the same package lookup rules as types, can be referenced before
being declared, and are substituted before validation and generation. Constant
names cannot reuse a built-in type name or the complete logical name of another
type/alias/constant in the input set. Expressions support decimal literals,
constant references, unary signs, `+`, `-`, `*`, `/` and parentheses. Binary
operators associate left to right, with standard arithmetic precedence. Division
must be exact and its divisor nonzero. Each literal and intermediate result has
magnitude at most uint32's maximum, and the final value is a positive uint32.
Parentheses nest at most 64 levels. Missing references and cycles are rejected
even when unused. Hexadecimal literals, digit separators and floats are forbidden.
Constants are absent from generated bindings and catalogues; dimensions there
remain numeric. See [dimension constants](schema.md#dimension-constants).

A field type is a built-in type, message/enum or alias from the input schema set.
Aliases resolve transitively to built-ins, messages or enums before validation
and generation. They have no distinct runtime identity or generated native type;
catalogues contain only their ultimate targets. Missing targets and cycles are
rejected even in unused aliases. Aliases share the logical namespace and package
lookup rules of types and constants. See [type aliases](schema.md#type-aliases).
Unqualified references select the current package without global fallback;
qualified references are absolute, and a leading dot selects the global package.
Forward references and references between files are allowed. There are no import
directives, inheritance or default values written in the schema. See
[packages and type references](schema.md#packages-and-type-references).

| Family | Built-in types |
| --- | --- |
| Boolean | `bool` |
| Signed integers | `int8`, `int16`, `int32`, `int64` |
| Unsigned integers | `uint8`, `uint16`, `uint32`, `uint64` |
| IEEE-754 floating point | `fl32`, `fl64` |
| Complex numbers | `c32`: two `fl32` components; `c64`: two `fl64` components |
| UTF-8 text | `string` |

### 2.3. Numbering, cardinalities and arrays

A field without a number receives 1 if it is the first field; otherwise it
receives the previous field's number **in source order**, plus 1. An explicit
number replaces that value and becomes the starting point for the next field.
Each message, including an anonymous message, has its own sequence. Duplicate
numbers are not skipped: they cause rejection. A number must be in
`[1, 4294967295]`.

Omitting the cardinality means `required`, independently of whether an explicit
number is present.

| Cardinality | Logical value | Ordinary representation |
| --- | --- | --- |
| `required` | One value | The value directly |
| `optional` | Zero or one value | Count 0 or 1, then the value if present |
| `repeated` | A sequence of values | Element count, then the elements |
| `packed` | A sequence of elements with a fixed logical size | Same layout as `repeated` |

Counts use ULEB128. An array `T[d1][d2]…` has positive dimensions fixed by
the schema and carries no count. Its elements are transmitted in row-major
order: the last index varies fastest. An array field must be `required`,
explicitly or by default.

A fixed logical size is calculated from the ordinary representation, before
applying `wire` clauses. Primitives other than `string`, and enums, have a
fixed size. A nonempty message has a fixed size if all its fields are
`required` and have fixed sizes, possibly with fixed dimensions. Empty
messages, strings and messages containing optional fields or sequences cannot
be elements of fixed arrays or `packed` fields. However,
`wire(encoding=varint)` can make the actual transmitted size variable for an
element whose logical size is fixed.

### 2.4. Grammar and rules of `wire(...)`

```ebnf
wire_clause     = "wire", "(", wire_option, { ",", wire_option }, ")" ;
wire_option     = ( "bits", "=", INTEGER
                  | "q", "=", quantum
                  | "encoding", "=", encoding
                  | "align", "=", INTEGER
                  | "pad_after", "=", INTEGER ) ;
encoding        = "varint" | "zigzag" ;
quantum         = [ SIGN ], mantissa, [ exponent ] ;
mantissa        = INTEGER, [ ".", [ INTEGER ] ] | ".", INTEGER ;
exponent        = ( "e" | "E" ), [ SIGN ], INTEGER ;
```

Whitespace can separate `wire`, `(`, keys, `=`, values and commas, including
newlines. Keys may appear in any order, but each key can appear only once. An
empty clause, a trailing comma or an unknown key is invalid. The quantum is a
single numeric literal without internal whitespace and must convert to a finite
positive binary64 number. Dimension constants are used in array brackets and other constants;
wire options still require their literal values.

| Option | Validity condition |
| --- | --- |
| `bits=N` on an integer | `1 ≤ N ≤` the type's width |
| `bits=N` on an enum | `1 ≤ N ≤ 32` |
| `bits=N` on `bool` | `N = 1` |
| `bits=N` on a float | `q` required; `1 ≤ N ≤ 32` for `fl32`, `1 ≤ N ≤ 64` for `fl64` |
| `q=Q` | Only `fl32` or `fl64`, with `bits`, without `encoding` |
| `encoding=varint` | Integer or enum, without `bits`; the value to encode must be nonnegative |
| `encoding=zigzag` | Signed integer or enum, without `bits` |
| `align=N` | Power of two, from 1 through 256 |
| `pad_after=N` | Integer from 0 through 65535 |

`bits` and `encoding` are mutually exclusive. Complex numbers, strings and
messages may receive `align` and `pad_after`, but not `bits`, `q` or
`encoding`. These rules apply to element types for sequences and arrays.
On an anonymous message, the clause follows its field name on the
`} name …;` line.

With `bits=N,q=Q`, the transmitted integer is
`k = round_ties_even(x / Q)` and the reconstructed value is `k × Q`.
Rounding chooses the nearest integer; an exact tie chooses the even integer.
The result must satisfy `−2^(N−1) ≤ k < 2^(N−1)`. NaN, infinities,
nonfinite quotients and results outside this interval are rejected, without
saturation.

Division and multiplication use binary64; `fl32` inputs and outputs have
binary32 precision. A decimal half step is therefore not necessarily an exact
binary tie. Code zero reconstructs as positive zero. The logical type's
precision may make several codes indistinguishable. Validation requires
`Q × 2^(N−1) ≤ max(type)` and, for `fl32`, `Q > 2^−150`, so the quantum
does not round to zero.

Bits are written most significant first. Signed codes use two's complement.
Elements with `bits` remain contiguous; other operations may complete a byte
with zero bits. `align` is relative to the beginning of the enclosing message
body and precedes any count; `pad_after` follows the entire field. A nested
message has its own alignment origin. Details appear in
[field wire representation](schema.md#field-wire-representation).

### 2.5. Other validation constraints

- Type names are unique and cannot reuse a built-in type name. Field names
  and numbers are unique within each message.
- An enum has at least one item. Its item names and values are unique;
  each value is in `[-2147483648, 2147483647]`.
- Message dependencies are acyclic. A path has at most 64 nested references,
  or 65 messages including the root.
- A file contains at most 65535 messages and 65535 enums; each message has
  at most 65535 fields and each enum at most 65535 items.
- An identifier occupies at most 65535 UTF-8 bytes. A field has at most
  255 dimensions, each in `[1, 4294967295]`. The total logical size of an
  array field must fit in an unsigned 32-bit integer.
- The generated catalogue occupies at most 1048576 bytes. Preparing a
  catalogue requires at least one message declaration.
- When generating from a directory, each `.sdl` file is a generation unit;
  references can resolve to declarations in other input files.
  Complete logical type names must be unique across files; emitted identifiers
  must also remain distinct after backend conversion. Filenames and identifiers must meet the
  requirements of the selected backends.

## 3. Single-line compact catalogue

### 3.1. Grammar

The catalogue is a UTF-8 string without whitespace, comments, a BOM, a
trailing newline or a terminating NUL. The strings below show its exact
contents, without host-language quotation marks. The numeric and type
constraints of section 2 also apply to the catalogue.

```ebnf
catalogue       = "SDL2|", compact_message,
                  { "|", compact_message },
                  { "|", compact_enum } ;
compact_name    = ? one or more NAME characters or "$" ? ;
compact_type    = compact_name, { ".", compact_name } ;
compact_message = compact_type, "{", { compact_field }, "}" ;
compact_field   = INTEGER, [ marker ], ":", compact_type,
                  { dimension }, ":", compact_name,
                  [ wire_suffix ], ";" ;
marker          = "?" | "*" | "+" ;
compact_enum    = "#", compact_type, "{",
                  compact_item, { compact_item }, "}" ;
compact_item    = compact_name, "=", enum_value, ";" ;
wire_suffix     = "@", compact_option, { ",", compact_option } ;
compact_option  = "b", INTEGER
                | "q", plain_decimal
                | "v" | "z"
                | "a", INTEGER
                | "p", INTEGER ;
plain_decimal   = INTEGER, [ ".", INTEGER ] ;
```

`INTEGER`, `dimension` and `enum_value` have the same lexical definitions as
in the source grammar. No whitespace or comments are allowed here. The compact quantum
is a positive decimal without a sign or exponent: `q0.1` is valid;
`q.1`, `q1.`, `q1e-1` and `q+0.1` are not.

| Source element | Compact spelling |
| --- | --- |
| `message X { … }`, `structure X { … }`, `struct X { … }` | `X{…}` |
| `enum E { … }` | `#E{…}` |
| `required` or omitted cardinality | No marker after the number |
| `optional` | `?` after the number |
| `repeated` | `*` after the number |
| `packed` | `+` after the number |
| `bits=N` | `bN` after `@` |
| `q=Q` | `qQ` after `@` |
| `encoding=varint` / `encoding=zigzag` | `v` / `z` after `@` |
| `align=N` / `pad_after=N` | `aN` / `pN` after `@` |

### 3.2. Canonical form

The grammar and validation rules are supplemented by the following rendering
rules.

1. All messages precede the enums. Each group is sorted by the names' UTF-8
   bytes, in ascending order, without locale-dependent sorting. Fields are
   sorted by increasing number; their numbers are always explicit. Enum
   items retain their source order.
2. Integers are rendered in decimal without leading zeros, a `+` sign or
   negative zero. Names keep their exact spelling.
3. Options are rendered in the order `b`, `q`, `v` or `z`, `a`, `p`.
   Absent options are not added; explicitly present options remain present,
   even `align=1` or `pad_after=0`.
4. The source quantum is expanded into a decimal without an exponent, `+`,
   redundant leading zeros or trailing fractional zeros. A zero always
   precedes the decimal point for values below 1. Thus `.1000`, `1e-1` and
   `+000.100` all produce `q0.1`. This normalization preserves the literal's
   decimal value; the runtime subsequently converts it to binary64.
5. An anonymous message becomes a named declaration `Root$k`, where `k`
   counts anonymous openings in source order, starting at 1, for each named
   root message. Its field uses that name as its type. A declared package
   qualifies both the root and its anonymous message names.
6. All declarations in the file appear, including those referenced by no
   field, plus transitive declarations referenced in other input files.
   There is no `|` after the final declaration.

Runtimes may accept some noncanonical spellings, such as leading zeros or a
different option order. For publication or identity calculation, use the
generator's canonical output: the hash covers the exact bytes and does not
normalize received text.

### 3.3. Complete example: source and corresponding string

```sdl
enum Etat {
   PRET = 0;
   ERREUR = -1;
}

message Point {
   fl64 x;
   fl64 y;
}

message Paquet {
   bool actif wire(bits=1);
   optional Etat etat;
   packed int16 echantillons wire(bits=12);
   fl32 temperature wire(q=1e-1,bits=12);
   Point position;
   uint8[2] flags;
}
```

The exact generated string is:

```text
SDL2|Paquet{1:bool:actif@b1;2?:Etat:etat;3+:int16:echantillons@b12;4:fl32:temperature@b12,q0.1;5:Point:position;6:uint8[2]:flags;}|Point{1:fl64:x;2:fl64:y;}|#Etat{PRET=0;ERREUR=-1;}
```

For example, `4:fl32:temperature@b12,q0.1;` denotes required field number 4,
with logical type `fl32`, named `temperature`, transmitted as a signed 12-bit
integer with quantum 0.1. The decimal separator in the string is always `.`.
`Paquet` receives message identifier 1 and `Point` identifier 2; `Etat`
receives none. These identifiers come from message sorting and are distinct
from field numbers.

### 3.4. Anonymous message example

```sdl
message Enveloppe {
   struct {
      int32 code wire(bits=12);
      string detail;
   } resultat wire(align=4,pad_after=1);
}
```

```text
SDL2|Enveloppe{1:Enveloppe$1:resultat@a4,p1;}|Enveloppe$1{1:int32:code@b12;2:string:detail;}
```

The catalogue no longer contains an inline declaration. It contains a
reference to type `Enveloppe$1`, followed by the definition of that type.

### 3.5. Rejection examples

| Source text | Reason |
| --- | --- |
| `int32 x wire(bits=12,q=0.1);` | `q` is reserved for floats |
| `fl32 x wire(bits=12);` | A float with `bits` requires `q` |
| `fl64 x wire(q=0.1);` | `q` requires `bits` |
| `fl64 x wire(bits=12,q=0);` | A strictly positive quantum is required |
| `int16 x wire(bits=17);` | Width exceeds the type's width |
| `optional int32[2] x;` | A fixed array must be required |
| `packed string x;` | Variable logical size |
| `int32 x wire(align=3);` | Alignment is not a power of two |
| `1:int32 x;` | Missing whitespace after `:` in source |
| `int32 x wire (bits=12);` | Whitespace between `wire` and `(` |

These lines are to be read inside a message. The same type and option
restrictions apply to a catalogue constructed directly.

## 4. Relationship to binary data

After catalogue preparation, a data buffer contains
`ULEB128(message_identifier)` followed by field values in field-number order.
Nested messages carry only their bodies, without an additional message
identifier. Field numbers and names are not repeated in each buffer.

Without `wire` options, integers and floats have fixed widths and are written
in big-endian order. Strings carry their UTF-8 byte length in ULEB128.
The `bits`, `q`, `encoding`, `align` and `pad_after` options modify this
representation according to the catalogue. The receiver uses the sender's
catalogue, including its quantum, to read the values.

The `SDL2|…` string is therefore the encoding description, while the buffer
is a message instance. The transport protocol must delimit both objects.
See [the binary format and its byte-by-byte example](wire_descriptor.md)
and [the preparation, encoding and decoding APIs](runtime.md).
