#!/usr/bin/env python3
"""Release contracts for model-service healthcheck startup budgets."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[2]
_DURATION_RE = re.compile(r"^(\d+)([smh])$")
_SECONDS_PER_UNIT = {"s": 1, "m": 60, "h": 3600}


def duration_seconds(value: object) -> int:
    """Parse the single-unit durations used by bundled Compose files."""
    match = _DURATION_RE.fullmatch(str(value))
    if match is None:
        raise AssertionError(f"unsupported Compose duration: {value!r}")
    amount, unit = match.groups()
    return int(amount) * _SECONDS_PER_UNIT[unit]


def test_tts_start_period_covers_measured_cpu_cold_start() -> None:
    compose_path = ROOT / "extensions" / "services" / "tts" / "compose.yaml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    healthcheck = compose["services"]["tts"]["healthcheck"]

    # Kokoro has been measured taking ~255s to load and warm on a CPU host.
    # Keep a practical margin so Docker does not advertise a false failure.
    assert duration_seconds(healthcheck["start_period"]) >= 300


if __name__ == "__main__":
    test_tts_start_period_covers_measured_cpu_cold_start()
    print("service startup budget contracts passed")
