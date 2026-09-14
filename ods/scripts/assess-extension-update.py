#!/usr/bin/env python3
"""Run the read-only Assistant First extension/core update preflight."""

from __future__ import annotations

import sys
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1] / "extensions/services/dashboard-api"
sys.path.insert(0, str(MODULE_ROOT))

from extension_update_preflight import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
