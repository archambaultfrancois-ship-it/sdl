#!/usr/bin/env python3
"""Run all backend checks with a compact progress bar."""

import shlex
import subprocess
import sys


BACKENDS = (
   ("C", "test-c"),
   ("Rust", "test-rust"),
   ("Python", "test-python"),
   ("Matlab", "test-matlab"),
   ("Java", "test-java"),
   ("Ada", "test-ada"),
)
BAR_WIDTH = 24


def progress(completed, label):
   filled = BAR_WIDTH * completed // len(BACKENDS)
   bar = "#" * filled + "-" * (BAR_WIDTH - filled)
   sys.stdout.write("\r[{}] {}/{}  {}\033[K".format(
      bar, completed, len(BACKENDS), label))
   sys.stdout.flush()


def main():
   make = shlex.split(sys.argv[1]) if len(sys.argv) > 1 else ["make"]
   for index, (backend, target) in enumerate(BACKENDS):
      progress(index, "{} running".format(backend))
      command = make + ["-s", "--no-print-directory", "CHECK_SILENT=1", target]
      try:
         result = subprocess.run(command, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, errors="replace")
      except OSError as error:
         sys.stdout.write("\n")
         print("Could not start {} checks: {}".format(backend, error), file=sys.stderr)
         return 2
      if result.returncode != 0:
         sys.stdout.write("\n")
         print("{} checks failed (exit {}).".format(backend,
            result.returncode), file=sys.stderr)
         sys.stdout.write(result.stdout)
         if result.stdout and not result.stdout.endswith("\n"):
            sys.stdout.write("\n")
         return result.returncode
      progress(index + 1, "{} passed".format(backend))
   sys.stdout.write("\nAll backend checks passed.\n")
   return 0


if __name__ == "__main__":
   sys.exit(main())
