import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from agent_monitor import ThroughputMetrics

def test_throughput_add_sample_and_stats():
    tp = ThroughputMetrics(history_minutes=15)
    assert tp.get_stats()["current"] == 0
    tp.add_sample(25.5)
    tp.add_sample(30.0)
    stats = tp.get_stats()
    assert stats["current"] == 30.0
    assert stats["peak"] == 30.0
    assert stats["average"] == 27.75

def test_throughput_invalid_samples_ignored():
    tp = ThroughputMetrics()
    tp.add_sample(True)
    tp.add_sample(-10.0)
    tp.add_sample(float("nan"))
    tp.add_sample("invalid")
    assert len(tp.data_points) == 0
