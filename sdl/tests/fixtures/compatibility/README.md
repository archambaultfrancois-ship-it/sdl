# Directional compatibility fixtures

All six runtimes execute `cases.tsv`. Each row has twelve tab-separated columns:

1. Case label.
2. Publisher catalogue filename.
3. Consumer catalogue filename.
4. Selected root (`-` selects all publisher messages).
5. Required-field business policy (`0` disabled, `1` enabled).
6. Expected stable diagnostic code.
7. Expected root message.
8. Expected field path.
9. Expected unsigned field ID.
10. Expected publisher logical type.
11. Expected consumer logical type.
12. Expected signed enum value (meaningful only for `ENUM_VALUE_UNKNOWN`).

`-` represents an empty diagnostic string. Catalogues use the current canonical
SDL2 rendering, except the deliberately invalid catalogue. Expected results are
specified independently of the comparison implementation.

The cases cover field renaming, primitive widths, dimensions, cardinality,
packed/repeated interoperability, unknown fields, defaulted missing fields,
required-field policy, nested paths, message/enum kind changes, directional enum
value coverage, missing roots, all-root traversal, invalid inputs, and integer
boundaries. The enum cases demonstrate why argument order matters.
