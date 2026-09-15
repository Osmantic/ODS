import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from performance_oracle import normalize_key, local_model_id, model_publisher

def test_normalize_key():
    assert normalize_key("Meta-Llama-3.1-8B-Instruct") == "meta-llama-3-1-8b-instruct"
    assert normalize_key("Qwen/Qwen2.5-Coder-7B") == "qwen-qwen2-5-coder-7b"
    assert normalize_key("") == ""

def test_local_model_id():
    assert local_model_id("mistral-7b-instruct-v0.2.Q4_K_M.gguf") == "mistral-7b-instruct-v0.2.Q4_K_M.gguf"
    assert local_model_id("") == "local-gguf"

def test_model_publisher():
    assert model_publisher({"id": "meta-llama/Llama-3-8B"}) == {"name": "Meta", "huggingFaceAuthor": "meta-llama"}
    assert model_publisher({"id": "Qwen/Qwen2.5-7B"}) == {"name": "Qwen", "huggingFaceAuthor": "Qwen"}
    assert model_publisher({"id": "google/gemma-2-9b"}) == {"name": "Google", "huggingFaceAuthor": "google"}
    assert model_publisher({"id": "unknown-org/custom-model"}) is None
