"""Shared pytest path setup for the add-on package tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPERS = Path(__file__).resolve().parent / "helpers"

for path in (str(HELPERS), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)
