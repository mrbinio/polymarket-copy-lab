#!/usr/bin/env python3
"""Print go-live checklist for PolyCop from active genome."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.print_polycop_checklist import main as print_checklist  # noqa: E402

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="roster-top8.json")
    args = parser.parse_args()
    sys.argv = ["print_polycop_checklist.py", "--config", args.config]
    print("=" * 60)
    print("GO-LIVE — PolyCop (8 portfeli, wszystkie PAUSED)")
    print("STOP sesji −$10 → Stop All Copy natychmiast")
    print("=" * 60)
    print()
    print_checklist()
