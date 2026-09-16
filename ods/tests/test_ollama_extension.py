"""Pinned Ollama library metadata without claiming model weights are installed."""

import json
from pathlib import Path
import subprocess
import sys

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/ollama"


def test_ollama_catalog_is_v2_and_preserves_one_click_metadata(tmp_path):
    manifest = yaml.safe_load((SERVICE / "manifest.yaml").read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "extensions/library/schema/service-manifest.v2.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.validate(manifest, schema)

    output = tmp_path / "catalog.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
         "--output", str(output)],
        check=True,
    )
    generated = json.loads(output.read_text(encoding="utf-8"))
    checked_in = json.loads(
        (ROOT / "config/extensions-catalog.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in generated["extensions"] if item["id"] == "ollama")
    assert entry == next(
        item for item in checked_in["extensions"] if item["id"] == "ollama"
    )
    assert entry["port"] == 11434
    assert entry["external_port_default"] == 7804
    assert entry["health_endpoint"] == "/api/tags"
    assert entry["manifest_schema_version"] == "ods.services.v2"
    assert entry["planning"]["legacy"] is False
    assert entry["planning"]["provides"] == ["ollama-api@1"]

    planning = entry["planning"]
    assert planning["resources"]["hostPorts"] == [
        {"port": 7804, "protocol": "tcp", "configurationKey": "EXT_OLLAMA_PORT"}
    ]
    assert planning["data"] == [
        {"path": "data/ollama", "backupClass": "required", "owner": "user",
         "uninstall": "preserve", "purge": "separate-approval"}
    ]
    assert planning["estimates"]["downloadBytes"] == sum(
        image["downloadBytes"] for image in planning["artifacts"]["images"]
    )
    assert planning["estimates"]["downloadBytes"] == 2517783838
    assert planning["artifacts"]["builds"] == []
    assert planning["configuration"] and not any(
        item["secret"] or item["required"] for item in planning["configuration"]
    )
    assert planning["lifecycle"]["setupHook"] is None
    assert "separately" in entry["description"]

    compose = yaml.safe_load((SERVICE / "compose.yaml").read_text(encoding="utf-8"))
    assert compose["services"]["ollama"]["image"] == "@".join(
        (planning["artifacts"]["images"][0]["reference"],
         planning["artifacts"]["images"][0]["digest"])
    )
    assert compose["services"]["ollama"]["ports"] == [
        "${BIND_ADDRESS:-127.0.0.1}:${EXT_OLLAMA_PORT:-7804}:11434"
    ]


def test_ollama_manifest_does_not_claim_an_automatic_model_download():
    manifest = yaml.safe_load((SERVICE / "manifest.yaml").read_text(encoding="utf-8"))
    planning = manifest["service"]["planning"]
    assert planning["artifacts"]["images"] and planning["artifacts"]["builds"] == []
    assert planning["lifecycle"]["health_checks"] == ["/api/tags"]
    assert planning["lifecycle"]["setup_hook"] is None
    model = next(item for item in planning["configuration"]
                 if item["key"] == "OLLAMA_MODEL")
    assert model["default"] == "llama3"
    assert model["required"] is False
    assert "does not download" in next(
        item["description"] for item in manifest["service"]["env_vars"]
        if item["key"] == "OLLAMA_MODEL"
    )
