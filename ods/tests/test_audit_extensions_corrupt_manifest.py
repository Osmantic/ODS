#!/usr/bin/env python3
"""Regression test: verify load_document handles corrupt JSON and YAML manifests safely."""
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if not (repo_root / "scripts").exists():
    repo_root = Path.cwd() / "ods" if (Path.cwd() / "ods").exists() else Path.cwd()
sys.path.insert(0, str(repo_root / "scripts"))

import importlib.util
spec = importlib.util.spec_from_file_location("audit_extensions", repo_root / "scripts" / "audit-extensions.py")
audit_mod = importlib.util.module_from_spec(spec)
sys.modules["audit_extensions"] = audit_mod
spec.loader.exec_module(audit_mod)

def test_load_document_corrupt_manifest():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{ unclosed json: ", encoding="utf-8")
        
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("key: [unclosed yaml", encoding="utf-8")
        
        valid_json = tmp_path / "good.json"
        valid_json.write_text('{"name": "test-ext"}', encoding="utf-8")
        
        assert audit_mod.load_document(bad_json) is None
        assert audit_mod.load_document(bad_yaml) is None
        
        good = audit_mod.load_document(valid_json)
        assert good == {"name": "test-ext"}

if __name__ == "__main__":
    test_load_document_corrupt_manifest()
    print("test_audit_extensions_corrupt_manifest: PASS")
