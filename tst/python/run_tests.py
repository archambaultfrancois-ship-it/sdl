#!/usr/bin/env python3
"""Run Python unit tests by SDL component and operational priority."""

import sys
import unittest

import test_codec
import test_generator
import test_native
import test_buffers


PHASES = (
   ("Python runtime: ordinary codec behavior", (
      (test_codec.CodecTests, ("test_01_codec_",)),
      (test_native.NativeTests, ("test_01_codec_",)),
      (test_buffers.BufferTests, ("test_01_codec_",)),
   )),
   ("SDL schema parser and backend validation", (
      (test_generator.GeneratorValidationTests,
         ("test_01_schema_", "test_02_backend_")),
   )),
   ("Python runtime: malformed and rejected input", (
      (test_codec.CodecTests, ("test_02_invalid_",)),
      (test_native.NativeTests, ("test_02_invalid_",)),
      (test_buffers.BufferTests, ("test_02_invalid_",)),
   )),
   ("Boundary and size-limit cases (final phase)", (
      (test_codec.CodecTests, ("test_03_limits_",)),
      (test_generator.GeneratorValidationTests, ("test_03_limits_",)),
   )),
)


def selected_suite(test_class, prefixes):
   names = unittest.defaultTestLoader.getTestCaseNames(test_class)
   selected = [test_class(name) for name in names
      if any(name.startswith(prefix) for prefix in prefixes)]
   return unittest.TestSuite(selected)


def main():
   runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
   success = True
   for title, groups in PHASES:
      print("\n=== {} ===".format(title), flush=True)
      suite = unittest.TestSuite(selected_suite(test_class, prefixes)
         for test_class, prefixes in groups)
      result = runner.run(suite)
      success = result.wasSuccessful() and success
   return 0 if success else 1


if __name__ == "__main__":
   sys.exit(main())
