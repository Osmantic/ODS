import pytest
from datetime import datetime, timezone
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from usage_timeline import minute_timeline

def test_minute_timeline_empty():
    now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
    res = minute_timeline([], now=now)
    assert res["source"]["status"] == "ok"
    assert res["recorded_requests"] == 0
    assert len(res["points"]) > 0

def test_minute_timeline_invalid_arg():
    with pytest.raises(ValueError, match="Invalid telemetry response"):
        minute_timeline("not a list")

def test_minute_timeline_valid_aggregation():
    now = datetime(2026, 9, 15, 12, 5, 0, tzinfo=timezone.utc)
    events = [
        {"timestamp": "2026-09-15T12:01:10Z", "input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 0},
        {"timestamp": "2026-09-15T12:01:40Z", "input_tokens": 200, "output_tokens": 25, "cache_read_tokens": 10, "cache_write_tokens": 0},
    ]
    res = minute_timeline(events, now=now)
    assert res["recorded_requests"] == 2
    point_12_01 = next(p for p in res["points"] if p["date"].startswith("2026-09-15T12:01:00"))
    assert point_12_01["input_tokens"] == 300
    assert point_12_01["output_tokens"] == 75
    assert point_12_01["requests"] == 2
