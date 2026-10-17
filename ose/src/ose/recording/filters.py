"""Lazy loading of the optional SDL predicate service at either recording endpoint."""
import importlib
from pathlib import Path
import sys


def prepare_filter(desc, expression, *, sdl_root=None):
    try:
        module = importlib.import_module("sdl_filter")
    except ImportError:
        if sdl_root is not None:
            root = Path(sdl_root)
            for relative in ("runtime/filter/python", "build/filter/release"):
                path = str(root / relative)
                if path not in sys.path:
                    sys.path.insert(0, path)
        try:
            module = importlib.import_module("sdl_filter")
        except ImportError as error:
            raise ValueError("SDL filters require `make -C <SDL_ROOT> filter-python` with this "
                             "process's Python interpreter. Install the filter service in the "
                             "recorder and the recording source (broker/worker); make its modules "
                             "importable through installation or PYTHONPATH. "
                             f"Import failed: {error}") from error
    return module.Filter(desc["catalogue"], desc["root"], expression)
