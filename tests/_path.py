"""Make ``scripts/`` importable for the test suite so ``import aegis_desk`` resolves.

Every test module imports this first:  ``import tests._path  # noqa: F401``
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ROOT, "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
