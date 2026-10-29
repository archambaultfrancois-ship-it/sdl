# JSON, YAML and XSD schema sources

`sdlc` accepts four source representations of the same SDL model. These files
describe message types; they are not serialized message values or runtime SDL2
catalogues. All readers use the existing cross-file resolution, wire validation
and six code generators.

| Representation | Source suffix | Export option | Additional compiler dependency |
| --- | --- | --- | --- |
| SDL text | `.sdl` | `-sdl` | None |
| JSON descriptor | `.sdl.json` | `-json` | None |
| YAML descriptor | `.sdl.yaml`, `.sdl.yml` | `-yaml` | PyYAML |
| Annotated XML Schema 1.0 | `.sdl.xsd` | `-xsd` | None |

For a runnable example with maintained mixed sources and automatic comparisons,
see [multiformat schema compilation](../examples/multiformat/README.md).

Run these commands from the `sdl` directory:

```sh
# Install only when reading or exporting YAML.
python3 -m pip install -r sdlc/requirements-yaml.txt

# Export each source unit in all three representations.
python3 sdlc/generator.py -json -yaml -xsd schemas build/schema-export

# Inverse operation: reconstruct textual SDL from alternative or mixed sources.
python3 sdlc/generator.py -sdl build/schema-export/json build/schema-import

# Compile an exported directory, or a single self-contained source unit.
python3 sdlc/generator.py -c -python build/schema-export/json build/from-json
python3 sdlc/generator.py -rust build/schema-export/yaml build/from-yaml
python3 sdlc/generator.py -java build/schema-export/xsd build/from-xsd
```

Exports go to `OUTPUT/sdl`, `OUTPUT/json`, `OUTPUT/yaml` and `OUTPUT/xsd`. Language options
and export options can be combined. PyYAML is imported only for YAML operations;
JSON, XSD, SDL text and the generated applications do not require it. XSD reading
and writing use Python's standard library (`xml.etree.ElementTree`, Python 3.9+).

## Source discovery and unit identity

A directory is searched recursively for the dedicated suffixes above. Ordinary
`.json`, `.yaml`, `.yml` and `.xsd` configuration files are ignored. A directory
can mix representations, provided each declaration and source unit occurs once.
Select one exported representation, rather than the parent containing all three.
Keep exports outside the input directory to avoid discovering duplicate schemas
on the next invocation.

The entire suffix is removed to obtain the unit's base name: both `shapes.sdl`
and `shapes.sdl.json` generate the module `shapes`. Package hierarchy is retained
in exports. Keep the base names and unit grouping when replacing a source format;
moving declarations between units can change a unit's catalogue even when the
declarations themselves are identical. Units are processed in package/base-name
order regardless of their filesystem layout.

Readers resolve all selected units together. References, aliases and dimension
constants can cross formats and packages. Missing references, duplicate types,
unit-name collisions and incompatible wire clauses are rejected by the shared
validator.

Regeneration removes stale exports bearing the compiler's generated-file marker
and preserves unrelated files. Treat the output directory as generated content;
copy a descriptor elsewhere and remove its marker before maintaining it as source.
For JSON the marker is the first-line `generated_by` property; for SDL, YAML and
XSD it is the first-line comment.

## JSON and YAML descriptor, version 1

JSON and YAML have exactly the same data model. This minimal JSON source is valid:

```json
{
  "format": "sdl-schema",
  "messages": {
    "Point": {
      "fields": [
        {"name": "x", "type": "int32"},
        {"name": "y", "type": "int32"}
      ]
    }
  }
}
```

Its YAML equivalent is:

```yaml
format: sdl-schema
messages:
  Point:
    fields:
      - {name: x, type: int32}
      - {name: y, type: int32}
```

The root object accepts these keys. Unknown keys are rejected at every level.

| Key | Value | Default |
| --- | --- | --- |
| `format` | String `sdl-schema` | Required |
| `version` | Integer `1` | Implicit `1`, omitted from exports |
| `package` | SDL package name | Empty/global package |
| `doc` | Documentation string | Empty |
| `constants` | Name → `{value: integer, doc?: string}` | Empty object |
| `aliases` | Name → `{target: type-name, doc?: string}` | Empty object |
| `enums` | Name → `{values: [...], doc?: string}` | Empty object |
| `messages` | Name → `{fields: [...], doc?: string}` | Empty object |
| `generated_by` | Export marker | Omitted in maintained sources |

Version 1 is implicit in JSON, YAML and SDL annotations in XSD. An explicit
`version: 1` is still accepted for existing files. Other versions and non-integer
version values are rejected. This describes the source representation and does
not version message definitions or change their SDL2 binary encoding.

Declaration names may be short or qualified in the current package. A declaration
cannot belong to another package. References follow SDL rules: short names resolve
in the current package, qualified names select that package, and `.Point` selects
the global `Point`. Exported references make global names explicit.

Each enum value is `{name: string, value: signed-int32, doc?: string}`. Labels and
numeric values must be unique within the enum. The list retains declaration order.

Each message field accepts:

| Key | Value | Default |
| --- | --- | --- |
| `name` | SDL field name | Required |
| `type` | Builtin, message, enum or alias reference | Required |
| `id` | Nonzero uint32 | Previous field ID + 1, initially 1 |
| `modifier` | `required`, `optional`, `repeated`, `packed` | `required` |
| `dimensions` | Array of positive uint32 dimensions or constant references | Empty array |
| `wire` | Object containing SDL wire options | Empty object |
| `doc` | Documentation string | Empty |

`bits`, `align` and `pad_after` are integer wire options; `encoding` is `varint`
or `zigzag`. `q` accepts a decimal string or a finite number. Prefer strings for
exact decimal input: `wire: {bits: 12, q: "0.025"}`. The same type/option and
fixed-array restrictions apply as in textual SDL. Boolean values are not integers.

Exports omit empty documentation, empty declaration tables, an empty package,
`modifier: required`, empty `dimensions` and empty `wire`. Field IDs are omitted
when they equal the previous ID plus one (initially 1); gaps and other explicit
numbering are retained. Readers restore these defaults. Required structural lists
such as an empty message's `fields: []` remain present. Nonempty wire options,
including explicit `align: 1` and `pad_after: 0`, remain to preserve exact catalogue
identity. Enum value zero and nonempty documentation are also retained.

Exports normalize wire values and dimensions. Constant expressions are exported
as their resolved integer values. Alias declarations and
documentation are retained, while fields use resolved target types. Anonymous
structures become named message entries such as `Envelope$1`; their references
and identities are preserved. Ordinary comments and original SDL formatting are
not retained. `-sdl` reconstructs equivalent textual SDL rather than recovering
the original formatting or constant expressions. Anonymous types are restored as
inline structures. Each reconstructed unit is parsed again and checked against
the resolved model before any output is replaced. Descriptors that cannot be
represented losslessly as textual SDL are rejected, for example orphan anonymous
types, documentation containing `*/`, or global module documentation without a
package declaration.

JSON rejects duplicate keys, non-finite numbers and unsupported format versions.
YAML uses a safe loader with JSON-style implicit scalar rules: `true`, `false`,
`null` and JSON numbers have their usual types; `ON`, `OFF` and dates remain text.
Use explicit strings for numeric-looking names. Duplicate keys, custom Python
objects, merge keys, recursive aliases and non-JSON values are rejected. Readers
limit descriptor container nesting to 64; the existing SDL catalogue size and
message nesting limits also apply.

## Annotated XSD profile

The XSD export is an XML Schema 1.0 document that an independent XSD validator can
use to validate XML message values. It also carries SDL metadata in `xs:appinfo`
using namespace `urn:sdl:schema:1`, making the supported representation reversible.
Documentation is stored in `xs:documentation`.

| SDL construct | XML Schema representation |
| --- | --- |
| Message | Named `xs:complexType` with ordered `xs:sequence`, plus a global message element |
| Required field | One element, with implicit `minOccurs="1"`, `maxOccurs="1"` |
| Optional field | Element with `minOccurs="0"`, implicit `maxOccurs="1"` |
| Signed integers | `xs:byte`, `xs:short`, `xs:int`, `xs:long` |
| Unsigned integers | Corresponding `xs:unsigned*` types |
| `bool`, `string`, `fl32`, `fl64` | `xs:boolean`, `xs:string`, `xs:float`, `xs:double` |
| Enum | String restriction to SDL labels; numeric mapping in annotations |
| `c32`, `c64` | Complex type with required `real` and `imag` float/double elements |
| Fixed array | Required wrapper; nested `item` elements with exact counts for every dimension |
| Repeated or packed sequence | Required wrapper containing zero or more `item` elements |

For example, a repeated string field named `tags` is `<tags/>` when empty, or
`<tags><item>first</item><item>second</item></tags>`. Its required wrapper preserves
the distinction between an empty sequence and an absent optional value. Packed
and repeated fields share the XML structure; the modifier remains in metadata.

Full SDL type names are used as XML names when they are ASCII NCNames. Other
names, including anonymous types and names beginning with the reserved `_sdl_`
prefix, are encoded as `_sdl_` followed by their UTF-8 hexadecimal bytes. Original
names remain in annotations; this mapping has no name collisions.

Each exported XSD contains the unit's local types and the transitively referenced
types, so it is autonomous for **XML validation**. Ownership annotations distinguish
local declarations from dependency copies. For **SDL compilation**, supply the
defining source units together, as with textual SDL. Dependency copies must agree
with the resolved input graph; they do not silently create new source units.

The reader accepts this annotated profile, rather than arbitrary XSD. It checks
the XML structure against the metadata, rejects unsupported constructs and stale
dependency copies, and then applies common SDL validation. Editing field types,
cardinalities or names requires updating both the XSD structure and its SDL
annotations. Exported annotations omit the same defaults as JSON/YAML, and local
element cardinalities equal to 1 are implicit. The reader also accepts previous
exports with those defaults written explicitly. Whitespace indentation and XML
comments may change freely; the
profile uses the `xs` prefix bound to the XML Schema namespace, no default namespace
and no `targetNamespace`. Imports, includes, choices, inheritance, attributes,
mixed content, DTDs and entity declarations are outside this profile.

Field IDs, binary wire options, constants, aliases and unit ownership are SDL
metadata. XSD validates XML structure and primitive ranges; it does not enforce
every binary wire restriction such as reduced bit widths or quantization. XML is
not the SDL binary transport. XML 1.0 also cannot represent every string accepted
by SDL; XSD export rejects documentation containing forbidden XML characters.

## Validation

Core Python tests cover JSON/XSD round trips, mixed source graphs, all six
generators, binary encoding, rejection cases and operation without third-party
dependencies. YAML and independent XSD validation tests run when their optional
packages are installed:

```sh
make schema-deps test-schema-formats
```

This installs PyYAML and the test-only `xmlschema` validator (with its dependency
`elementpath`) under `build/schema-deps`. To run the compiler with that local YAML
installation, use `PYTHONPATH=build/schema-deps python3 sdlc/generator.py ...`.
Generated applications require none of these packages.
