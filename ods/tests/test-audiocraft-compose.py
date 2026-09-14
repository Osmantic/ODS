#!/usr/bin/env python3
"""Check the resolved AudioCraft application paths against its image contract."""

import json
import subprocess
import tempfile
from pathlib import Path

import yaml

ODS = Path(__file__).resolve().parents[1]
SERVICE = ODS / "extensions/library/services/audiocraft"


def main():
    with tempfile.TemporaryDirectory(prefix="ods audiocraft ") as directory:
        root = Path(directory)
        env = root / ".env"
        env.write_text("")
        result = subprocess.run([
            "docker", "compose", "--project-directory", str(root), "--env-file", str(env),
            "-f", str(SERVICE / "compose.yaml"), "config", "--format", "json",
        ], capture_output=True, text=True, check=True)
        app = json.loads(result.stdout)["services"]["audiocraft"]
        mounts = {item["target"]: item["source"] for item in app["volumes"]}
        assert mounts["/app/outputs"] == str(root / "data/audiocraft")
        assert mounts[app["environment"]["HF_HOME"]] == str(root / "data/audiocraft/models")
        assert app["healthcheck"]["test"][:3] == ["CMD", "python", "-c"]
        manifest = yaml.safe_load((SERVICE / "manifest.yaml").read_text())
        assert manifest["service"]["container_uid"] == 1000
    print("PASS: AudioCraft health and writable cache/output configuration")


if __name__ == "__main__":
    main()
