from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DETECTION = (ROOT / "installers" / "phases" / "02-detection.sh").read_text(encoding="utf-8")


def test_invalid_capability_tier_fails_closed_before_fallback_selection():
    marker = 'if [[ -n "$_profile_tier_raw" && -z "$PROFILE_TIER" ]]; then'
    assert marker in DETECTION
    block = DETECTION.split(marker, 1)[1].split("    fi", 1)[0]
    assert "ai_bad" in block
    assert "unsupported tier" in block
    assert "error" in block
    assert "Cannot safely select a model tier" in block

