#!/usr/bin/env python3
"""Run every backend benchmark and print one comparable summary table."""

import os
import re
import shlex
import shutil
import subprocess
import sys


RATE = re.compile(
   r"^\s*(encode|decode)\s*:?\s*([0-9]+(?:\.[0-9]+)?)\s*msg/s"
   r"[\s,]+([0-9]+(?:\.[0-9]+)?)\s*MiB/s", re.MULTILINE)
WIRE_SIZE = re.compile(r"([0-9]+) bytes/message")


def main():
   make = shlex.split(sys.argv[1]) if len(sys.argv) > 1 else ["make"]
   iterations = sys.argv[2] if len(sys.argv) > 2 else "200"
   matlab_runner = sys.argv[3] if len(sys.argv) > 3 else (
      "Matlab" if shutil.which(os.environ.get("matlab", "matlab")) else "Octave")
   matlab_label = "Matlab" if matlab_runner == "Matlab" else "Octave"
   backends = (
      ("C", "bench-c"),
      ("Rust", "bench-rust"),
      ("Python", "bench-python"),
      (matlab_label, "bench-matlab"),
      ("Java", "bench-java"),
   )
   results = []
   common_iterations = None
   common_wire_size = None
   for label, target in backends:
      print("Running {} benchmark...".format(label), flush=True)
      command = make + ["--no-print-directory", "BENCH_ITERATIONS=" + iterations,
         target]
      try:
         process = subprocess.run(command, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace")
      except OSError as error:
         print("Could not start {} benchmark: {}".format(label, error),
            file=sys.stderr)
         return 2
      if process.returncode != 0:
         print("{} benchmark failed (exit {}).".format(label,
            process.returncode), file=sys.stderr)
         sys.stdout.write(process.stdout)
         return process.returncode
      rates = {name: (messages, mebibytes)
         for name, messages, mebibytes in RATE.findall(process.stdout)}
      if "encode" not in rates or "decode" not in rates:
         print("Could not parse {} benchmark output:".format(label),
            file=sys.stderr)
         sys.stdout.write(process.stdout)
         return 1
      size_match = WIRE_SIZE.search(process.stdout)
      iteration_match = re.search(r"iterations:\s*([0-9]+)", process.stdout)
      if iteration_match is None:
         iteration_match = re.search(r"([0-9]+) iterations", process.stdout)
      size = size_match.group(1) if size_match else "?"
      results.append((label, rates["encode"], rates["decode"], size))
      if iteration_match:
         common_iterations = iteration_match.group(1)
      if size_match:
         common_wire_size = size_match.group(1)

   print("\nSDL codec benchmark summary")
   if common_iterations:
      print("Iterations per operation: {}".format(common_iterations))
   if common_wire_size:
      print("Message size: {} bytes".format(common_wire_size))
   print("{:<8} {:^16} {:^16}".format("Backend", "Encode", "Decode"))
   print("{:<8} {:>7} {:>8} {:>7} {:>8}".format(
      "", "msg/s", "MiB/s", "msg/s", "MiB/s"))
   print("-" * 42)
   for label, encode, decode, size in results:
      print("{:<8} {:>7} {:>8} {:>7} {:>8}".format(
         label, encode[0], encode[1], decode[0], decode[1]))
   return 0


if __name__ == "__main__":
   sys.exit(main())
