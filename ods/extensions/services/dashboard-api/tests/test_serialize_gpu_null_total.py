import pytest
import sys
from pathlib import Path

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from main import _serialize_gpu

class DummyGPU:
    name = "Virtual GPU"
    memory_used_mb = 0
    memory_total_mb = None
    memory_usage_available = False
    utilization_percent = 0
    utilization_available = False
    temperature_c = 0
    temperature_available = False
    memory_type = "discrete"
    gpu_backend = "cpu"
    power_w = None

def test_serialize_gpu_null_total():
    res = _serialize_gpu(DummyGPU())
    assert res is not None
    assert res["vramTotal"] == 0.0
