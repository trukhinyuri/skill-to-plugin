#!/usr/bin/env python3
"""Checkout/cache relocatable entrypoint; no install or external dependencies."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))
from skill_to_plugin.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
