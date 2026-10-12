#!/usr/bin/env python3
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as tmp:
    subprocess.run([str(ROOT / 'build/recording/ada/test'), str(Path(tmp) / 'ada.mcap')], check=True)
