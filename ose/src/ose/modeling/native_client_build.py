"""Stage the dependency-free Rust engine in a writable application build directory."""
from pathlib import Path


def writable_manifest(sdk, destination):
    source = Path(sdk) / 'client/rust'
    staged = Path(destination) / 'native-sdk'
    # Cargo writes a lockfile beside the root manifest. Installed SDK assets may
    # be read-only, so only the application build directory is ever mutated.
    for path in [source / 'Cargo.toml', *sorted((source / 'src').rglob('*.rs'))]:
        target = staged / path.relative_to(source)
        content = path.read_bytes()
        if not target.is_file() or target.read_bytes() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return staged / 'Cargo.toml'
