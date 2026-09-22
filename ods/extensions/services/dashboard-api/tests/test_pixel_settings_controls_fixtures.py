import pytest
from pathlib import Path
import sys

dashboard_api_dir = Path(__file__).resolve().parents[1]
if str(dashboard_api_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_api_dir))

from pixel_settings_public import normalize_preferences, CONTROLS

def test_valid_preferences():
    prefs = {
        "contextTokens": 32768,
        "thinking": "high",
        "temperature": 0.7,
        "compactionNotify": True,
    }
    normalized = normalize_preferences(prefs)
    assert normalized["contextTokens"] == 32768
    assert normalized["thinking"] == "high"
    assert normalized["temperature"] == 0.7
    assert normalized["compactionNotify"] is True

def test_invalid_preference_range_rejected():
    with pytest.raises(ValueError, match="invalid-setting-temperature"):
        normalize_preferences({"temperature": 5.0})

def test_unknown_preference_key_rejected():
    with pytest.raises(ValueError, match="invalid-settings-fields"):
        normalize_preferences({"unknown_unsupported_key": 123})
