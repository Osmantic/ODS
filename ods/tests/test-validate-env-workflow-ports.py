import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_validate_env_workflow_uses_canonical_port_defaults():
    workflow = (ROOT.parent / ".github/workflows/validate-env.yml").read_text(encoding="utf-8")
    ports = json.loads((ROOT / "config/ports.json").read_text(encoding="utf-8"))
    defaults = {entry["env_var"]: entry["external_default"] for entry in ports["ports"]}
    for key in ("SEARXNG_PORT", "TOKEN_SPY_PORT"):
        match = re.search(rf"^\s+{key}=(\d+)\s*$", workflow, re.MULTILINE)
        assert match, f"{key} missing from validation workflow"
        assert int(match.group(1)) == defaults[key]
