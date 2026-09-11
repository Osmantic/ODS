#!/usr/bin/env python3
"""Remote-provider egress service contracts."""

from __future__ import annotations

import json
import socket
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))

from remote_provider.egress import (  # noqa: E402
    EgressError,
    prepare_upstream_request,
    provider_secret_status,
    route_from_state,
    upstream_base_url_for_route,
    validate_direct_provider_resolution,
)
from remote_provider.egress_probe import (  # noqa: E402
    PROBE_RESPONSE_SCHEMA,
    probe_route_response,
)


BASE_COMPOSE = ROOT / "docker-compose.base.yml"
MANIFEST = ROOT / "extensions" / "services" / "remote-provider-egress" / "manifest.yaml"
DOCKERFILE = ROOT / "extensions" / "services" / "remote-provider-egress" / "Dockerfile"
APP_MAIN = ROOT / "extensions" / "services" / "remote-provider-egress" / "app" / "main.py"
POLICY = ROOT / "config" / "remote-provider-egress-policy.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_egress_error(func, code: str) -> EgressError:
    try:
        func()
    except EgressError as exc:
        assert_true(exc.code == code, f"expected {code}, got {exc.code}")
        return exc
    raise AssertionError(f"expected EgressError {code}")


def ssh_metadata(**overrides: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "host": "gpu.example.test",
        "user": "ods",
        "port": 22,
        "inferenceHost": "127.0.0.1",
        "inferencePort": 8000,
    }
    metadata.update(overrides)
    return metadata


def route_state(**provider_overrides: str) -> dict[str, object]:
    provider = {
        "capability": "openai-compatible",
        "baseUrl": "https://gpu.example.test/v1",
        "model": "qwen/remote:latest",
        "transport": "direct",
    }
    provider.update(provider_overrides)
    ssh = ssh_metadata() if provider["transport"] == "ssh" else None
    return {
        "schema": "ods.remote-routing-state.v1",
        "enabled": True,
        "mode": "cloud",
        "provider": provider,
        "ssh": ssh,
        "projection": {
            "publicModel": "ods/current",
            "gateway": "litellm-cloud",
            "egressBaseUrl": "http://remote-provider-egress:8091/v1",
            "consumerRoute": "gateway",
        },
        "status": {"proven": False, "reason": "pending-provider-handshake"},
    }


def resolver_for(*addresses: str):
    def _resolver(host: str, port: int, *args, **kwargs):
        results = []
        for address in addresses:
            if ":" in address:
                results.append(
                    (
                        socket.AF_INET6,
                        socket.SOCK_STREAM,
                        6,
                        "",
                        (address, port, 0, 0),
                    )
                )
            else:
                results.append(
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
                )
        return results

    return _resolver


def test_compose_service_is_internal_only_and_hardened() -> None:
    compose = read(BASE_COMPOSE)
    assert_true("  remote-provider-egress:" in compose, "base compose must define remote-provider-egress")
    block = compose.split("  remote-provider-egress:", 1)[1].split("\n  # ", 1)[0]
    assert_true("dockerfile: extensions/services/remote-provider-egress/Dockerfile" in block, "service must use its Dockerfile")
    assert_true("image: ods-remote-provider-egress:local" in block, "service image must be local-only")
    assert_true("expose:" in block and '"8091"' in block, "service must expose only the internal port")
    assert_true("ports:" not in block, "service must not bind a host port")
    assert_true("cap_drop:" in block and "- ALL" in block, "service must drop capabilities")
    assert_true(
        '"${REMOTE_PROVIDER_DATA_GID:-1000}"' in block,
        "service must receive the host data group for mode-0640 secret reads",
    )
    assert_true("read_only: true" in block, "service filesystem must be read-only")
    assert_true(
        "${ODS_DATA_DIR:-./data}/remote-provider:/state/remote-provider:ro" in block,
        "service must mount only remote-provider state, not unrelated ODS data",
    )
    assert_true("/remote-provider/secrets/provider-api-key" in block, "service must use a secret file path")
    assert_true("http://remote-provider-ssh-tunnel:18090/health" in block, "service must check internal SSH tunnel health")
    assert_true("REMOTE_LLM_API_KEY" not in block, "service must not source provider API keys from public env")


def test_manifest_and_network_policy_mark_no_lan_exposure() -> None:
    manifest = read(MANIFEST)
    exposure = json.loads(read(ROOT / "config" / "network-exposure-policy.json"))
    assert_true("id: remote-provider-egress" in manifest, "manifest must declare service id")
    assert_true("external_port_default: 0" in manifest, "manifest must prevent host URL fallback")
    assert_true("category: core" in manifest, "egress service should be a core internal service")
    assert_true("compose_file:" not in manifest, "base-stack service manifest must not add an extension overlay")
    entry = exposure["services"]["remote-provider-egress"]
    assert_true(entry["lan_exposure"] == "none", "egress service must have no LAN exposure")
    assert_true(entry["auth_required"] is True, "egress service must require private provider auth")


def test_image_copies_shared_policy_package() -> None:
    dockerfile = read(DOCKERFILE)
    assert_true("COPY bin/remote_provider ./remote_provider" in dockerfile, "image must copy shared policy helpers")
    assert_true("USER odsremote" in dockerfile, "image must run as non-root service user")
    assert_true("--no-server-header" in dockerfile, "uvicorn should suppress server banner")


def test_route_state_prepares_direct_provider_request_without_client_auth() -> None:
    route = route_from_state(route_state())
    upstream = prepare_upstream_request(
        method="POST",
        path="/v1/chat/completions",
        headers={
            "authorization": "Bearer client-token",
            "x-request-id": "abc",
            "connection": "close",
        },
        body=json.dumps({"model": "ods/current", "messages": []}).encode("utf-8"),
        route=route,
        provider_secret="unit-test-provider-token",
    )
    assert_true(upstream.url == "https://gpu.example.test/v1/chat/completions", "provider URL/path join drifted")
    assert_true(json.loads(upstream.content)["model"] == "qwen/remote:latest", "provider model must replace public alias")
    assert_true(upstream.requested_model == "ods/current", "requested alias should be retained as metadata")
    assert_true(upstream.headers["authorization"] == "Bearer unit-test-provider-token", "provider auth must be injected")
    assert_true("client-token" not in json.dumps(upstream.headers), "client auth must not be forwarded")
    assert_true("connection" not in {key.lower() for key in upstream.headers}, "hop-by-hop headers must be stripped")


def test_direct_resolution_allows_only_global_provider_addresses() -> None:
    route = route_from_state(route_state())
    addresses = validate_direct_provider_resolution(
        route,
        resolver=resolver_for("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
    )
    assert_true(addresses == ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"], "global addresses should be accepted")


def test_direct_resolution_rejects_unsafe_dns_answers() -> None:
    route = route_from_state(route_state())
    for address in ("10.0.0.5", "127.0.0.1", "169.254.1.10", "224.0.0.1"):
        assert_egress_error(
            lambda address=address: validate_direct_provider_resolution(
                route,
                resolver=resolver_for(address),
            ),
            "provider_resolution_rejected",
        )
    assert_egress_error(
        lambda: validate_direct_provider_resolution(
            route,
            resolver=lambda *_args, **_kwargs: [],
        ),
        "provider_resolution_empty",
    )


def test_ssh_route_uses_internal_tunnel_without_direct_dns() -> None:
    route = route_from_state(
        route_state(transport="ssh", baseUrl="http://127.0.0.1:8000/v1")
    )
    assert_true(route["transport"] == "ssh", "SSH route should now be accepted")
    assert_true(
        route["provider"]["baseUrl"] == "http://127.0.0.1:8000/v1",
        "public route should preserve remote-side provider metadata",
    )
    assert_true(
        upstream_base_url_for_route(route) == "http://remote-provider-ssh-tunnel:18091/v1",
        "SSH egress must target the internal tunnel service",
    )
    addresses = validate_direct_provider_resolution(
        route,
        resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("SSH routes must not run direct DNS validation")
        ),
    )
    assert_true(addresses == [], "SSH routes should skip direct DNS checks")
    upstream = prepare_upstream_request(
        method="POST",
        path="/v1/chat/completions",
        headers={"authorization": "Bearer client-token"},
        body=b'{"model":"ods/current","messages":[]}',
        route=route,
        provider_secret="unit-test-provider-token",
    )
    assert_true(
        upstream.url == "http://remote-provider-ssh-tunnel:18091/v1/chat/completions",
        "SSH forward must use the tunnel service URL",
    )
    assert_true(upstream.headers["authorization"] == "Bearer unit-test-provider-token", "provider auth must still be injected")


def test_egress_fails_closed_without_secret_or_supported_transport() -> None:
    route = route_from_state(route_state())
    assert_egress_error(
        lambda: prepare_upstream_request(
            method="POST",
            path="/v1/chat/completions",
            headers={},
            body=b'{"model":"ods/current"}',
            route=route,
            provider_secret="",
        ),
        "missing_provider_secret",
    )
    unsupported = route_state(transport="unix", baseUrl="http://127.0.0.1:8000/v1")
    assert_egress_error(
        lambda: route_from_state(unsupported),
        "route_policy_rejected",
    )
    missing_ssh = route_state(transport="ssh", baseUrl="http://127.0.0.1:8000/v1")
    missing_ssh["ssh"] = None
    assert_egress_error(
        lambda: route_from_state(missing_ssh),
        "invalid_route_state",
    )


def test_secret_file_status_is_support_bundle_safe() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "provider-api-key"
        assert_true(provider_secret_status(missing)["configured"] is False, "missing secret should be unconfigured")
        missing.write_text("unit-test-provider-token", encoding="utf-8")
        status = provider_secret_status(missing)
        dumped = json.dumps(status)
        assert_true(status["configured"] is True, "non-empty secret file should be configured")
        assert_true("unit-test-provider-token" not in dumped, "secret status must not include secret value")


def test_probe_response_returns_redacted_ssh_receipt_through_tunnel_boundary() -> None:
    route = route_from_state(
        route_state(transport="ssh", baseUrl="http://127.0.0.1:8000/v1")
    )
    calls: list[dict[str, object]] = []

    def fake_probe(route, *, provider_secret, timeout):
        calls.append(
            {
                "route": route,
                "provider_secret": provider_secret,
                "timeout": timeout,
            }
        )
        return {
            "ok": True,
            "status": 200,
            "endpoint": "/v1/models",
            "transport": "ssh",
            "contentType": "application/json",
            "modelCount": 1,
            "resolution": {"ok": True, "addressCount": 0, "raw": "127.0.0.1"},
            "value": "unit-test-provider-token",
        }

    body = probe_route_response(
        route,
        provider_secret="unit-test-provider-token",
        verified_at="2026-07-26T00:00:00+00:00",
        tunnel={
            "ok": True,
            "ready": True,
            "status": "running",
            "reason": "ready",
            "process": {"status": "running", "pid": 4242},
        },
        timeout=7.0,
        probe=fake_probe,
    )

    dumped = json.dumps(body, sort_keys=True)
    assert_true(body["schema"] == PROBE_RESPONSE_SCHEMA, "probe schema drifted")
    assert_true(body["transport"] == "ssh", "probe should report SSH transport")
    assert_true(body["probe"] == {
        "schema": "ods.remote-provider-probe-receipt.v1",
        "ok": True,
        "verifiedAt": "2026-07-26T00:00:00+00:00",
        "endpoint": "/v1/models",
        "httpStatus": 200,
        "modelCount": 1,
        "resolution": {"ok": True, "addressCount": 0},
        "contentType": "application/json",
    }, "probe receipt must be public and stable")
    assert_true(body["tunnel"]["process"] == {"status": "running", "pid": 4242}, "tunnel process summary drifted")
    assert_true(calls[0]["provider_secret"] == "unit-test-provider-token", "probe must use private secret custody")
    assert_true(calls[0]["route"]["transport"] == "ssh", "probe must run against SSH route")
    assert_true(calls[0]["timeout"] == 7.0, "probe timeout should be forwarded")
    assert_true("unit-test-provider-token" not in dumped, "probe response must not leak provider secret")
    assert_true("raw" not in dumped, "probe response must not leak raw resolver output")


def test_probe_response_fails_closed_when_ssh_tunnel_is_not_ready() -> None:
    route = route_from_state(
        route_state(transport="ssh", baseUrl="http://127.0.0.1:8000/v1")
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("probe must not run before SSH tunnel is ready")

    exc = assert_egress_error(
        lambda: probe_route_response(
            route,
            provider_secret="unit-test-provider-token",
            verified_at="2026-07-26T00:00:00+00:00",
            tunnel={
                "ok": False,
                "ready": False,
                "status": "planned",
                "reason": "tunnel_process_not_started",
                "process": {"status": "stopped", "pid": None},
            },
            probe=fail_if_called,
        ),
        "ssh_tunnel_not_ready",
    )
    assert_true(exc.status == 503, "tunnel readiness failure should be a 503")


def test_service_source_avoids_public_env_secret_names() -> None:
    text = read(APP_MAIN)
    assert_true("REMOTE_LLM_API_KEY" not in text, "app must not read provider key from public env")
    assert_true("ODS_REMOTE_PROVIDER_API_KEY_FILE" in text, "app must read only the provider key file path")
    assert_true("read_provider_secret" in text, "app must use shared secret-file helper")
    assert_true("validate_direct_provider_resolution" in text, "app must enforce runtime DNS/address policy")
    assert_true("ODS_REMOTE_PROVIDER_SSH_TUNNEL_HEALTH_URL" in text, "app must check SSH tunnel readiness")
    assert_true('@app.post("/probe")' in text, "app must expose an internal probe endpoint")
    assert_true("probe_route_response" in text, "probe endpoint must use the shared egress probe helper")
    assert_true("ssh_tunnel_not_ready" in text, "app must fail closed when the SSH tunnel is down")
    assert_true("trust_env=False" in text, "egress requests must not delegate pinned connections or private headers to environment proxies")
    assert_true(POLICY.exists(), "policy document must exist for mounted service config")


def test_prepare_upstream_request_with_resolved_addresses() -> None:
    route = route_from_state(route_state())
    upstream = prepare_upstream_request(
        method="POST",
        path="/v1/chat/completions",
        headers={
            "authorization": "Bearer client-token",
        },
        body=json.dumps({"model": "ods/current", "messages": []}).encode("utf-8"),
        route=route,
        provider_secret="unit-test-provider-token",
        resolved_addresses=["93.184.216.34"],
    )
    assert_true(upstream.url == "https://93.184.216.34/v1/chat/completions", f"expected url to be rewritten to IP, got {upstream.url}")
    assert_true(upstream.tls_server_name == "gpu.example.test", f"expected TLS server name to be original host, got {upstream.tls_server_name}")
    assert_true(upstream.host_header == "gpu.example.test", f"expected Host header to preserve original authority, got {upstream.host_header}")
    assert_true(upstream.connection_key == "https://gpu.example.test:443", f"unexpected connection key: {upstream.connection_key}")


def test_prepare_upstream_request_with_resolved_ipv6_addresses() -> None:
    route = route_from_state(route_state())
    upstream = prepare_upstream_request(
        method="POST",
        path="/v1/chat/completions",
        headers={
            "authorization": "Bearer client-token",
        },
        body=json.dumps({"model": "ods/current", "messages": []}).encode("utf-8"),
        route=route,
        provider_secret="unit-test-provider-token",
        resolved_addresses=["2606:2800:220:1:248:1893:25c8:1946"],
    )
    assert_true(upstream.url == "https://[2606:2800:220:1:248:1893:25c8:1946]/v1/chat/completions", f"expected IPv6 url formatting, got {upstream.url}")
    assert_true(upstream.tls_server_name == "gpu.example.test", f"expected TLS server name to be original host, got {upstream.tls_server_name}")
    assert_true(upstream.host_header == "gpu.example.test", f"expected Host header to preserve original authority, got {upstream.host_header}")
    assert_true(
        upstream.connection_key == "https://gpu.example.test:443",
        f"unexpected IPv6 connection key: {upstream.connection_key}",
    )


def test_pinned_request_preserves_non_default_port_and_separates_tls_origins() -> None:
    first_route = route_from_state(
        route_state(baseUrl="https://gpu-a.example.test:8443/v1")
    )
    second_route = route_from_state(
        route_state(baseUrl="https://gpu-b.example.test:8443/v1")
    )
    first = prepare_upstream_request(
        method="POST",
        path="/v1/chat/completions",
        headers={},
        body=b'{"model":"ods/current","messages":[]}',
        route=first_route,
        provider_secret="unit-test-provider-token",
        resolved_addresses=["93.184.216.34"],
    )
    second = prepare_upstream_request(
        method="POST",
        path="/v1/chat/completions",
        headers={},
        body=b'{"model":"ods/current","messages":[]}',
        route=second_route,
        provider_secret="unit-test-provider-token",
        resolved_addresses=["93.184.216.34"],
    )
    rotated_address = prepare_upstream_request(
        method="POST",
        path="/v1/chat/completions",
        headers={},
        body=b'{"model":"ods/current","messages":[]}',
        route=first_route,
        provider_secret="unit-test-provider-token",
        resolved_addresses=["93.184.216.35"],
    )
    assert_true(first.url == "https://93.184.216.34:8443/v1/chat/completions", f"unexpected pinned URL: {first.url}")
    assert_true(first.host_header == "gpu-a.example.test:8443", f"non-default provider port was not preserved: {first.host_header}")
    assert_true(first.tls_server_name == "gpu-a.example.test", f"SNI must not include the provider port: {first.tls_server_name}")
    assert_true(first.connection_key != second.connection_key, "different TLS hostnames on the same IP must not share a connection pool")
    assert_true(first.connection_key == rotated_address.connection_key, "DNS address rotation for one TLS identity must use a bounded client pool")


def main() -> int:
    tests = [
        test_compose_service_is_internal_only_and_hardened,
        test_manifest_and_network_policy_mark_no_lan_exposure,
        test_image_copies_shared_policy_package,
        test_route_state_prepares_direct_provider_request_without_client_auth,
        test_direct_resolution_allows_only_global_provider_addresses,
        test_direct_resolution_rejects_unsafe_dns_answers,
        test_ssh_route_uses_internal_tunnel_without_direct_dns,
        test_egress_fails_closed_without_secret_or_supported_transport,
        test_secret_file_status_is_support_bundle_safe,
        test_probe_response_returns_redacted_ssh_receipt_through_tunnel_boundary,
        test_probe_response_fails_closed_when_ssh_tunnel_is_not_ready,
        test_service_source_avoids_public_env_secret_names,
        test_prepare_upstream_request_with_resolved_addresses,
        test_prepare_upstream_request_with_resolved_ipv6_addresses,
        test_pinned_request_preserves_non_default_port_and_separates_tls_origins,
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
