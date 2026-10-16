#!/usr/bin/env python3
"""Run the chain test suites with compact progress and full logs on failure."""

import os
import shlex
import subprocess
import sys


SUITES = (
    ("Unit", "test-unit"),
    ("Rust SDKs", "test-sdk"),
    ("Integration", "test-integration"),
    ("Simulation", "test-simulated"),
)
LANGUAGES = set(os.environ.get("TEST_LANGUAGES", "python,c,rust,java,ada").split(","))
if LANGUAGES - {"python", "c", "rust", "java", "ada"}:
    raise ValueError("unknown TEST_LANGUAGES entry")
SUITES += tuple((language.title(), "test-" + language) for language in ("java", "ada") if language in LANGUAGES)
if {"java", "ada"} <= LANGUAGES:
    SUITES += (("Five-language integration", "test-mixed"),)
BAR_WIDTH = 24


def progress(completed, label):
    filled = BAR_WIDTH * completed // len(SUITES)
    bar = "#" * filled + "-" * (BAR_WIDTH - filled)
    sys.stdout.write("\r[{}] {}/{}  {}\033[K".format(
        bar, completed, len(SUITES), label))
    sys.stdout.flush()


def main():
    make = shlex.split(sys.argv[1]) if len(sys.argv) > 1 else ["make"]
    for index, (suite, target) in enumerate(SUITES):
        progress(index, "{} running".format(suite))
        command = make + ["-s", "--no-print-directory", target]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    errors="replace")
        except OSError as error:
            sys.stdout.write("\n")
            print("Could not start {} checks: {}".format(suite, error),
                  file=sys.stderr)
            return 2
        if result.returncode != 0:
            sys.stdout.write("\n")
            print("{} checks failed (exit {}).".format(suite, result.returncode),
                  file=sys.stderr)
            sys.stdout.write(result.stdout)
            if result.stdout and not result.stdout.endswith("\n"):
                sys.stdout.write("\n")
            return result.returncode
        progress(index + 1, "{} passed".format(suite))
    sys.stdout.write("\nSelected chain checks passed.\n")
    omitted = {"java", "ada"} - LANGUAGES
    if omitted:
        print("Not verified: " + ", ".join(sorted(omitted)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
