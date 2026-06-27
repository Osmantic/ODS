#!/usr/bin/env python3
"""Static contract tests for Perplexica's ODS entrypoint patch."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_SCHEMA = ROOT / ".env.schema.json"
SERVICE_DIR = ROOT / "extensions" / "services" / "perplexica"
COMPOSE = SERVICE_DIR / "compose.yaml"
ENTRYPOINT = SERVICE_DIR / "docker-entrypoint.sh"
WHISPER_COMPOSE = ROOT / "extensions" / "services" / "whisper" / "compose.yaml"


def test_compose_uses_ods_entrypoint() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "PERPLEXICA_SCRAPE_URL_MAX_CHARS=${PERPLEXICA_SCRAPE_URL_MAX_CHARS:-30000}" in compose
    assert "/app/ods-entrypoint.sh" in compose
    assert "./extensions/services/perplexica/docker-entrypoint.sh:/app/ods-entrypoint.sh:ro" in compose
    assert 'exec /bin/sh /app/ods-entrypoint.sh \\"$@\\"' in compose


def test_bind_mounted_entrypoints_do_not_require_executable_bit() -> None:
    service_entrypoints = (
        (COMPOSE, "/app/ods-entrypoint.sh", 'exec /bin/sh /app/ods-entrypoint.sh \\"$@\\"'),
        (WHISPER_COMPOSE, "/app/docker-entrypoint.sh", "exec /bin/sh /app/docker-entrypoint.sh"),
    )
    for compose_path, mounted_script, shell_exec in service_entrypoints:
        compose = compose_path.read_text(encoding="utf-8")
        assert f"until [ -f {mounted_script} ]" in compose
        assert f"until [ -x {mounted_script} ]" not in compose
        assert shell_exec in compose
        assert f"exec {mounted_script}" not in compose


def test_entrypoint_patches_scrape_url_result_content() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert "name:\"scrape_url\"" in script
    assert "PERPLEXICA_SCRAPE_URL_MAX_CHARS" in script
    assert "content:k.slice(0,${max})" in script

    sample = 'g.push({content:k,metadata:{url:a,title:j}})'
    pattern = re.compile(
        r"([A-Za-z_$][\w$]*\.push\(\{content:)"
        r"([A-Za-z_$][\w$]*)"
        r"(,metadata:\{url:[A-Za-z_$][\w$]*,title:[A-Za-z_$][\w$]*\}\}\))"
    )
    patched = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}.slice(0,30000){m.group(3)}", sample)
    assert patched == 'g.push({content:k.slice(0,30000),metadata:{url:a,title:j}})'


def test_env_schema_allows_scrape_cap_override() -> None:
    schema = json.loads(ENV_SCHEMA.read_text(encoding="utf-8"))
    property_schema = schema["properties"]["PERPLEXICA_SCRAPE_URL_MAX_CHARS"]
    assert property_schema["type"] == "integer"
    assert property_schema["default"] == 30000
    assert property_schema["minimum"] == 1000


def test_compose_restores_image_command() -> None:
    # Setting `entrypoint:` in compose drops the upstream image's CMD
    # (`node server.js`). The override must restate it or the patched
    # entrypoint exits 0 with no app process, restart-looping.
    compose = COMPOSE.read_text(encoding="utf-8")
    assert 'command: ["node", "server.js"]' in compose


def test_entrypoint_falls_back_to_node_server_when_no_args() -> None:
    # Belt-and-suspenders: even if a future compose change drops `command:`,
    # the entrypoint should still launch the app instead of exiting 0.
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'if [ "$#" -eq 0 ]' in script
    assert "set -- node server.js" in script


if __name__ == "__main__":
    test_compose_uses_ods_entrypoint()
    test_bind_mounted_entrypoints_do_not_require_executable_bit()
    test_entrypoint_patches_scrape_url_result_content()
    test_env_schema_allows_scrape_cap_override()
    test_compose_restores_image_command()
    test_entrypoint_falls_back_to_node_server_when_no_args()
