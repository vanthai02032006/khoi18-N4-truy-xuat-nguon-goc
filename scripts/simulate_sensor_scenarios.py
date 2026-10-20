#!/usr/bin/env python3
"""Runner và proxy cho backend/scripts/simulate_sensor_scenarios.py."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"

for p in (str(BACKEND_DIR), str(ROOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from backend.scripts.simulate_sensor_scenarios import (
        ApiClientWrapper,
        load_scenario,
        simulate_scenario,
        run_all_scenarios,
        main,
    )
except ImportError:
    from scripts.simulate_sensor_scenarios import (  # type: ignore
        ApiClientWrapper,
        load_scenario,
        simulate_scenario,
        run_all_scenarios,
        main,
    )

if __name__ == "__main__":
    sys.exit(main())
