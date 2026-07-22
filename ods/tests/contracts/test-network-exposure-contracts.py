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


def caddy_block_body(text: str, opener: str) -> str:
    """Return the body of the Caddy block whose opening line equals `opener`.

    `opener` is the literal site line ending in the block's opening brace, e.g.
    ``http://chat.{$ODS_DEVICE_NAME:ods}.local {``. Depth counting starts at that
    brace; inline placeholders like ``{scheme}`` or ``{$ENV}`` are brace-balanced,
    so they net to zero and do not disturb the match. This keeps assertions scoped
    to the intended host block rather than the file as a whole.
    """
    idx = text.index(opener)
    brace = idx + len(opener) - 1  # opener ends with the block's opening "{"
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[brace + 1 : i]
    raise AssertionError(f"unbalanced braces after Caddy block opener {opener!r}")


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
    assert_true("reverse_proxy {$HERMES_PROXY_UPSTREAM:ods-hermes:9119}" in proxy_caddyfile, "hermes-proxy must forward to internal Hermes")


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


def test_hermes_local_provider_has_generous_timeouts() -> None:
    base_compose = read(ROOT / "docker-compose.base.yml")
    hermes_compose = read(SERVICES / "hermes" / "compose.yaml")
    hermes_config = read(SERVICES / "hermes" / "cli-config.yaml.template")

    assert_true("providers:" in hermes_config, "Hermes config should declare provider overrides")
    assert_true(
        re.search(
            r"(?ms)^providers:\s*\n\s+custom:\s*\n(?:\s{4}.+\n)*?\s{4}request_timeout_seconds:\s*180\s*$",
            hermes_config,
        )
        is not None,
        "Hermes custom provider must allow slow local-model first-token latency",
    )
    assert_true(
        re.search(r"(?m)^\s+-\s+HERMES_STREAM_STALE_TIMEOUT=900\s*$", hermes_compose) is not None,
        "Hermes streaming paths must allow slow local-model first-token latency",
    )
    assert_true(
        "ODS_TALK_HERMES_TIMEOUT=${ODS_TALK_HERMES_TIMEOUT:-900}" in base_compose,
        "dashboard-api must give ODS Talk the same long local-model timeout on the base stack",
    )


def test_ods_proxy_routes_talk_portal() -> None:
    caddyfile = read(SERVICES / "ods-proxy" / "Caddyfile")

    assert_true("talk.{$ODS_DEVICE_NAME:ods}.local" in caddyfile, "ods-proxy must route talk.<device>.local")
    assert_true("reverse_proxy dashboard:3001" in caddyfile, "ODS Talk should be served by the dashboard container")


def test_dashboard_csp_allows_ods_talk_tts_blob_audio() -> None:
    nginx_conf = read(SERVICES / "dashboard" / "nginx.conf")

    assert_true("Content-Security-Policy" in nginx_conf, "dashboard must keep a CSP header")
    assert_true("media-src 'self' blob:" in nginx_conf, "ODS Talk TTS playback uses blob: audio URLs")


def test_dashboard_csp_allows_huggingface_author_avatars_only_as_images() -> None:
    nginx_conf = read(SERVICES / "dashboard" / "nginx.conf")

    assert_true(
        "img-src 'self' data: https://huggingface.co https://cdn-avatars.huggingface.co;" in nginx_conf,
        "the Models library renders Hugging Face author avatars",
    )
    assert_true(
        "connect-src 'self';" in nginx_conf,
        "Hub API access must remain server-side instead of exposing HF_TOKEN to the browser",
    )


def test_ods_proxy_caps_request_body_sizes() -> None:
    caddyfile = read(SERVICES / "ods-proxy" / "Caddyfile")

    chat_block = caddy_block_body(caddyfile, "http://chat.{$ODS_DEVICE_NAME:ods}.local {")
    assert_true(
        re.search(r"request_body\s*\{\s*max_size\s+200MB\s*\}", chat_block) is not None,
        "ods-proxy chat host must cap request body at 200MB (Open WebUI document uploads)",
    )

    api_block = caddy_block_body(caddyfile, "http://api.{$ODS_DEVICE_NAME:ods}.local {")
    assert_true(
        re.search(r"request_body\s*\{\s*max_size\s+50MB\s*\}", api_block) is not None,
        "ods-proxy api host must cap request body at 50MB (admin surface)",
    )


def test_hermes_proxy_caps_request_body() -> None:
    caddyfile = read(SERVICES / "hermes-proxy" / "Caddyfile")

    site_block = caddy_block_body(caddyfile, ":9120 {")
    assert_true(
        re.search(r"request_body\s*\{\s*max_size\s+50MB\s*\}", site_block) is not None,
        "hermes-proxy must cap request body at 50MB (agent prompt attachments)",
    )


def test_dashboard_pre_stages_hsts() -> None:
    nginx_conf = read(SERVICES / "dashboard" / "nginx.conf")

    # HSTS is pre-staged so it activates once TLS lands via Caddy/Tailscale.
    # Browsers ignore the header over plain HTTP, so it is inert until then.
    assert_true(
        re.search(
            r'add_header\s+Strict-Transport-Security\s+"max-age=\d+;[^"]*includeSubDomains',
            nginx_conf,
        )
        is not None,
        "dashboard nginx must pre-stage a Strict-Transport-Security header with includeSubDomains",
    )


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
        test_hermes_local_provider_has_generous_timeouts,
        test_ods_proxy_routes_talk_portal,
        test_dashboard_csp_allows_ods_talk_tts_blob_audio,
        test_ods_proxy_caps_request_body_sizes,
        test_hermes_proxy_caps_request_body,
        test_dashboard_pre_stages_hsts,
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
