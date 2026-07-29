#!/usr/bin/env python3
"""List juans with suspiciously long front buckets.

This is a thin wrapper around:
    python -m bkk repair overlong-front
"""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = REPO_ROOT / "module"
sys.path.insert(0, str(MODULE_ROOT))

from bkk.repair.cli import run  # noqa: E402


raise SystemExit(run(["overlong-front", *sys.argv[1:]]))
