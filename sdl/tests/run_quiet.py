#!/usr/bin/env python3
"""Run one test command quietly, printing its complete output on failure."""

import subprocess
import sys


def main():
   arguments = sys.argv[1:]
   if arguments and arguments[0] == "--":
      arguments = arguments[1:]
   if not arguments:
      print("run_quiet.py: missing command", file=sys.stderr)
      return 2
   result = subprocess.run(arguments, stdout=subprocess.PIPE,
      stderr=subprocess.STDOUT, text=True, errors="replace")
   if result.returncode != 0:
      print("Test command failed (exit {}): {}".format(
         result.returncode, " ".join(arguments)), file=sys.stderr)
      sys.stdout.write(result.stdout)
      if result.stdout and not result.stdout.endswith("\n"):
         sys.stdout.write("\n")
   return result.returncode


if __name__ == "__main__":
   sys.exit(main())
