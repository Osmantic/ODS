import glob
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_library_external_port_env_keys_are_schema_declared():
    schema = json.loads((ROOT / ".env.schema.json").read_text(encoding="utf-8"))
    properties = schema["properties"]
    missing = []
    for manifest_path in glob.glob(str(ROOT / "extensions/library/services/*/manifest.yaml")):
        manifest = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
        key = (manifest.get("service") or {}).get("external_port_env")
        if key and key not in properties:
            missing.append(key)
    assert missing == []
