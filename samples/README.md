# SDL examples

- [`abc-c`](abc-c/README.md), [`abc-rust`](abc-rust/README.md),
  [`abc-java`](abc-java/README.md), and [`abc-python`](abc-python/README.md):
  operationally matching three-thread
  quadratic equation pipelines. Each uses the same SDL messages and
  length-prefixed Unix-domain socket stages in its target language.
- [`abc-matlab`](abc-matlab/README.md): the same equation workflow split into
  three sequential Matlab/Octave stages that exchange encoded SDL frames.
- [`radio_capture`](radio_capture/README.md): one schema shared by generated
  C, Rust, and Python examples, including cross-language wire interoperability.
  Java and Matlab/Octave backends are supported by the generator and covered by
  their runtime tests, but are not part of this sample.
