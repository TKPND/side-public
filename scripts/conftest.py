"""Pytest rootdir fix: make scripts/ directly importable."""

import sys
from pathlib import Path

_THIS_DIR = Path(__file__).parent.resolve()
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))
