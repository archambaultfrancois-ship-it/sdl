"""Compile Java SDKs/containers with the Java 7 API and bytecode baseline."""
import os
from pathlib import Path
import subprocess
import sys
from ose.modeling.managed_containers import tool


def main():
    classes, *sources = sys.argv[1:]
    javac = tool('JAVAC', 'javac')
    version = subprocess.check_output(javac + ['-version'], stderr=subprocess.STDOUT, text=True)
    flags = ['-source', '7', '-target', '7'] if 'javac 1.7.' in version else ['--release', '7']
    # Explicit opt-in is useful for local smoke tests on JDKs that removed release 7.
    # Such a build is not a Java 7 compatibility check.
    if os.environ.get('CHAIN_JAVA_LOCAL_RELEASE'):
        flags = ['--release', os.environ['CHAIN_JAVA_LOCAL_RELEASE']]
        print('LOCAL ONLY: Java 7 compatibility is not being verified', file=sys.stderr)
    Path(classes).mkdir(parents=True, exist_ok=True)
    subprocess.run(javac + flags + ['-encoding', 'UTF-8', '-d', classes] + sources, check=True)


if __name__ == '__main__':
    main()
