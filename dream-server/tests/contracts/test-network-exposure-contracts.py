#!/usr/bin/env python3
"""Network exposure contract checks for bundled services."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "extensions" / "services"
POLICY = ROOT / "config" / "network-exposure-policy.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def manifest_value(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s+{re.escape(key)}:\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else None


def manifest_bool(text: str, key: str) -> bool:
    value = manifest_value(text, key)
    return bool(value and value.lower() == "true")


def exposed_service_ids() -> set[str]:
    exposed: set[str] = set()
    for manifest in SERVICES.glob("*/manifest.yaml"):
        text = read(manifest)
        service_id = manifest_value(text, "id") or manifest.parent.name
        has_external_port = manifest_value(text, "external_port_default") is not None
        if has_external_port or manifest_bool(text, "host_network"):
            exposed.add(service_id)
    return exposed


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_exposed_services_are_policy_labeled() -> None:
    policy = json.loads(read(POLICY))
    policy_services = set(policy["services"])
    exposed = exposed_service_ids()
    assert_true(exposed <= policy_services, f"missing exposure policy entries: {sorted(exposed - policy_services)}")
    assert_true(policy_services <= exposed, f"stale exposure policy entries: {sorted(policy_services - exposed)}")
    for service_id, entry in policy["services"].items():
        assert_true(entry.get("risk"), f"{service_id} missing risk label")
        assert_true(entry.get("lan_exposure"), f"{service_id} missing lan_exposure label")
        assert_true(isinstance(entry.get("auth_required"), bool), f"{service_id} auth_required must be boolean")
        assert_true(entry.get("notes"), f"{service_id} missing notes")


def test_hermes_is_internal_only_and_proxy_gated() -> None:
    hermes_compose = read(SERVICES / "hermes" / "compose.yaml")
    hermes_manifest = read(SERVICES / "hermes" / "manifest.yaml")
    proxy_caddyfile = read(SERVICES / "hermes-proxy" / "Caddyfile")
    policy = json.loads(read(POLICY))["services"]

    assert_true(not re.search(r"(?m)^\s{4}ports:\s*$", hermes_compose), "hermes compose must not bind host ports")
    assert_true(re.search(r"(?m)^\s{4}expose:\s*$", hermes_compose) is not None, "hermes compose should expose only internally")
    assert_true(manifest_value(hermes_manifest, "external_port_default") == "0", "hermes manifest external port must be 0")
    assert_true(policy["hermes"]["lan_exposure"] == "none", "hermes policy must mark no LAN exposure")
    assert_true("forward_auth" in proxy_caddyfile, "hermes-proxy must verify sessions with forward_auth")
    assert_true("/api/auth/verify-session" in proxy_caddyfile, "hermes-proxy must call dashboard auth verification")
    assert_true("reverse_proxy {$HERMES_PROXY_UPSTREAM:dream-hermes:9119}" in proxy_caddyfile, "hermes-proxy must forward to internal Hermes")


def test_hermes_whatsapp_bridge_avoids_open_webui_port() -> None:
    hermes_compose = read(SERVICES / "hermes" / "compose.yaml")
    hermes_config = read(SERVICES / "hermes" / "cli-config.yaml.template")

    assert_true("whatsapp:" in hermes_config, "Hermes config should pre-seed WhatsApp settings")
    assert_true("enabled: false" in hermes_config, "WhatsApp must remain disabled by default")
    assert_true(
        re.search(r"(?m)^\s+bridge_port:\s*3010\s*$", hermes_config) is not None,
        "WhatsApp bridge must default away from Open WebUI port 3000",
    )
    assert_true(
        re.search(r"(?m)^\s+bridge_port:\s*3000\s*$", hermes_config) is None,
        "WhatsApp bridge must not use upstream's port 3000 default",
    )
    assert_true(
        re.search(r"(?m)^\s+-\s+WHATSAPP_ENABLED\s*$", hermes_compose) is not None,
        "Hermes compose should pass intentional WhatsApp enables without blank defaults",
    )
    assert_true("3010:3010" not in hermes_compose, "WhatsApp bridge must not be host-bound")


def test_dream_proxy_routes_talk_portal() -> None:
    caddyfile = read(SERVICES / "dream-proxy" / "Caddyfile")

    assert_true("talk.{$DREAM_DEVICE_NAME:dream}.local" in caddyfile, "dream-proxy must route talk.<device>.local")
    assert_true("reverse_proxy dashboard:3001" in caddyfile, "Dream Talk should be served by the dashboard container")


def test_dashboard_csp_allows_dream_talk_tts_blob_audio() -> None:
    nginx_conf = read(SERVICES / "dashboard" / "nginx.conf")

    assert_true("Content-Security-Policy" in nginx_conf, "dashboard must keep a CSP header")
    assert_true("media-src 'self' blob:" in nginx_conf, "Dream Talk TTS playback uses blob: audio URLs")


def test_openclaw_stays_deprecated_optional_and_token_gated() -> None:
    manifest = read(SERVICES / "openclaw" / "manifest.yaml")
    docs = read(ROOT / "docs" / "OPENCLAW-INTEGRATION.md")
    policy = json.loads(read(POLICY))["services"]["openclaw"]

    assert_true(manifest_bool(manifest, "deprecated"), "OpenClaw must remain deprecated")
    assert_true(manifest_value(manifest, "category") == "optional", "OpenClaw must stay optional")
    assert_true("OPENCLAW_TOKEN" in manifest, "OpenClaw manifest must require a token")
    assert_true(policy["lan_exposure"] == "opt-in-only", "OpenClaw policy must stay opt-in-only")
    assert_true("DEPRECATED" in docs, "OpenClaw docs must keep the deprecation notice")


def test_litellm_gateway_auth_is_enforced() -> None:
    compose = read(SERVICES / "litellm" / "compose.yaml")
    amd_compose = read(ROOT / "docker-compose.amd.yml")
    policy = json.loads(read(POLICY))["services"]["litellm"]

    assert_true("LITELLM_MASTER_KEY=${LITELLM_KEY:-}" in compose, "LiteLLM must keep master-key auth")
    assert_true("OPENAI_API_KEY=${LITELLM_KEY}" in amd_compose, "AMD clients must present LITELLM_KEY")
    assert_true(policy["auth_required"] is True, "LiteLLM policy must require auth")


def main() -> int:
    tests = [
        test_exposed_services_are_policy_labeled,
        test_hermes_is_internal_only_and_proxy_gated,
        test_hermes_whatsapp_bridge_avoids_open_webui_port,
        test_dream_proxy_routes_talk_portal,
        test_dashboard_csp_allows_dream_talk_tts_blob_audio,
        test_openclaw_stays_deprecated_optional_and_token_gated,
        test_litellm_gateway_auth_is_enforced,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
