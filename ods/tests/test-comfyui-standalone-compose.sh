#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose="$root/extensions/services/comfyui/compose.standalone.nvidia.yaml"
tmp="$(mktemp -d)"
trap 'rm -rf -- "$tmp"' EXIT

export ODS_COMFYUI_DATA_ROOT="$tmp/isolated data"
export ODS_COMFYUI_PORT=8190
docker compose -p ods-comfyui-standalone -f "$compose" config --format json > "$tmp/config.json"

python3 - "$tmp/config.json" "$ODS_COMFYUI_DATA_ROOT" <<'PY'
import json
import pathlib
import sys

config = json.loads(pathlib.Path(sys.argv[1]).read_text())
data_root = sys.argv[2]
assert config["name"] == "ods-comfyui-standalone"
assert list(config["services"]) == ["comfyui"]
service = config["services"]["comfyui"]
assert service["container_name"] == "ods-comfyui-standalone"
assert service["image"] == "ods-comfyui-standalone:0.16.4-cu128"
assert service["build"]["dockerfile"] == "Dockerfile.standalone"
assert service["labels"]["org.osmantic.ods.comfyui.data-root"] == data_root
assert service["labels"]["org.osmantic.ods.comfyui.port"] == "8190"
assert len(service["ports"]) == 1
assert service["ports"][0]["host_ip"] == "127.0.0.1"
assert service["ports"][0]["published"] == "8190"
assert service["ports"][0]["target"] == 8188
mounts = {item["target"]: item for item in service["volumes"]}
assert set(mounts) == {
    "/models", "/output", "/input", "/workflows", "/user",
    "/opt/comfyui/custom_nodes",
}
for target, name in {
    "/models": "models", "/output": "output", "/input": "input",
    "/workflows": "workflows", "/user": "user",
    "/opt/comfyui/custom_nodes": "custom_nodes",
}.items():
    assert mounts[target]["source"] == str(pathlib.Path(data_root) / name)
assert mounts["/workflows"]["read_only"] is True
assert all(item["type"] == "bind" for item in mounts.values())
print("PASS: standalone ComfyUI resolves one isolated service with private data mounts")
PY
