import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_offline_phase_keys_are_declared_as_booleans():
    schema = json.loads((ROOT / ".env.schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]
    expected = {
        "OFFLINE_MODE": False,
        "DISABLE_TELEMETRY": False,
        "DISABLE_UPDATE_CHECK": False,
        "WEB_SEARCH_ENABLED": True,
        "LOCAL_RAG_ENABLED": False,
    }
    for key, default in expected.items():
        assert properties[key]["type"] == "boolean"
        assert properties[key]["default"] is default
