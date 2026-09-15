import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from model_memory import (
    estimated_param_billions,
    estimated_context_kv_gb,
    required_model_memory_gb,
)

@pytest.fixture
def llama3_model_dict():
    return {
        "id": "meta-llama/Meta-Llama-3-8B-Instruct",
        "total_params_b": 8.0,
        "size_mb": 4800,
        "block_count": 32,
        "head_count": 32,
        "head_count_kv": 8,
        "embedding_length": 4096,
        "head_dimension": 128,
        "context_length": 8192,
    }

def test_estimated_param_billions(llama3_model_dict):
    assert estimated_param_billions(llama3_model_dict) == 8.0

def test_estimated_context_kv_gb(llama3_model_dict):
    kv_gb = estimated_context_kv_gb(llama3_model_dict, context_length=8192)
    assert kv_gb > 0.0
    assert isinstance(kv_gb, float)

def test_required_model_memory_gb(llama3_model_dict):
    req_gb = required_model_memory_gb(llama3_model_dict, context_length=8192)
    assert req_gb >= 4.8  # At least weight size
