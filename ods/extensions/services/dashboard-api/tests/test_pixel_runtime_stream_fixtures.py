import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

import pixel_runtime_state

def test_stream_counter_lifecycle():
    with pixel_runtime_state._lock:
        pixel_runtime_state._active_streams = 0

    assert pixel_runtime_state._local_pixel_stream_active() is False
    pixel_runtime_state.begin_pixel_stream()
    assert pixel_runtime_state._local_pixel_stream_active() is True
    pixel_runtime_state.end_pixel_stream()
    assert pixel_runtime_state._local_pixel_stream_active() is False

def test_stream_counter_underflow_prevention():
    with pixel_runtime_state._lock:
        pixel_runtime_state._active_streams = 0

    pixel_runtime_state.end_pixel_stream()
    assert pixel_runtime_state._active_streams == 0
