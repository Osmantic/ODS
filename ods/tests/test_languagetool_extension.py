"""Real offline proofreading through the installed LanguageTool recipe."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
from urllib.parse import urlencode

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "extensions/library/services/languagetool"


def test_languagetool_catalog_exposes_api_setup(tmp_path):
    output = tmp_path / "catalog.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/generate-extensions-catalog.py"),
                    "--output", str(output)], check=True)
    entry = next(item for item in json.loads(output.read_text())["extensions"]
                 if item["id"] == "languagetool")
    assert (entry["port"], entry["external_port_default"]) == (8081, 7826)
    assert entry["health_endpoint"] == "/v2/languages"
    checked = json.loads((ROOT / "config/extensions-catalog.json").read_text())["extensions"]
    assert entry == next(item for item in checked if item["id"] == "languagetool")


@pytest.mark.skipif(os.environ.get("ODS_TEST_LANGUAGETOOL_DOCKER") != "1",
                    reason="Opt-in exact-image offline spelling and grammar test")
def test_proofreading_without_external_network(tmp_path):
    name = "ods-language-test-" + uuid.uuid4().hex[:10]
    installed = tmp_path / "extensions/services/languagetool"
    shutil.copytree(SERVICE, installed)
    overlay = tmp_path / "isolation.yaml"
    overlay.write_text(f'''services:
  languagetool:
    container_name: {name}
    ports: !override ["127.0.0.1:0:8081"]
networks:
  ods-network:
    name: {name}
''')
    command = ["docker", "compose", "--project-name", name, "--project-directory", str(tmp_path),
               "-f", str(installed / "compose.yaml"), "-f", str(overlay)]

    def run(*args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=420)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    run("docker", "network", "create", name)
    run("docker", "network", "create", "--internal", name + "-offline")
    try:
        assert json.loads(run("docker", "network", "inspect", name + "-offline"))[0]["Internal"] is True
        plan = json.loads(run(*command, "config", "--format", "json"))["services"]["languagetool"]
        assert plan["ports"][0]["host_ip"] == "127.0.0.1"
        assert plan["user"] == "783:783"
        assert plan["read_only"] is True
        for _ in range(2):
            run(*command, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "300")
            origin = "http://" + run(*command, "port", "languagetool", "8081")
            with httpx.Client(base_url=origin, timeout=60) as client:
                languages = client.get("/v2/languages")
                assert languages.status_code == 200
                assert {"en-US", "de-DE", "fr"} <= {item["longCode"] for item in languages.json()}
                sentence = "This is a sentnce."
                spelling = client.post("/v2/check", data={"language": "en-US", "text": sentence})
                assert spelling.status_code == 200, spelling.text[:200]
                assert any(sentence[match["offset"]:match["offset"] + match["length"]] == "sentnce"
                           and "sentence" in [item["value"] for item in match["replacements"]]
                           for match in spelling.json()["matches"])
                grammar = client.post("/v2/check", data={"language": "en-US", "text": "This are a sentence."})
                assert grammar.status_code == 200
                assert any("is" in [item["value"] for item in match["replacements"]]
                           for match in grammar.json()["matches"])
                assert client.post("/v2/check", data={"language": "not-a-language", "text": sentence}).status_code == 400
                too_long = client.post("/v2/check", data={"language": "en-US", "text": "x" * 20001})
                assert too_long.status_code in (400, 413)
            # Docker does not publish ports on an internal-only bridge. Exercise
            # the same HTTP boundary inside the container after removing egress.
            run("docker", "network", "disconnect", name, name)
            run("docker", "network", "connect", name + "-offline", name)
            networks = json.loads(run("docker", "inspect", name))[0]["NetworkSettings"]["Networks"]
            assert set(networks) == {name + "-offline"}
            for text, replacement in [("This is a sentnce.", "sentence"), ("This are a sentence.", "is")]:
                response = json.loads(run("docker", "exec", name, "wget", "-q", "-O", "-",
                                          "--post-data", urlencode({"language": "en-US", "text": text}),
                                          "http://127.0.0.1:8081/v2/check"))
                assert any(replacement in [item["value"] for item in match["replacements"]]
                           for match in response["matches"])
    finally:
        run(*command, "down", "--timeout", "10")
        run("docker", "network", "rm", name)
        run("docker", "network", "rm", name + "-offline")
