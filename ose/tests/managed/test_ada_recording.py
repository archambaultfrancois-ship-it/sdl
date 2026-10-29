import os
import tempfile
import unittest
from pathlib import Path
from ose.codec.sdl2 import SDL2
from tests.internal_recording import build_component
from tests.integration.test_component_recording import check_component

SDL_ROOT = Path(os.environ.get("SDL_ROOT", Path(__file__).resolve().parents[3] / "sdl"))


class AdaRecordingTests(unittest.TestCase):
    def test_context_registry_and_live_activation(self):
        with tempfile.TemporaryDirectory() as root:
            check_component(self, build_component(root, "ada", SDL_ROOT), SDL2(SDL_ROOT))
