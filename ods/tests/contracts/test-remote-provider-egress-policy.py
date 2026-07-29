#!/usr/bin/env python3
"""Remote-provider egress policy contracts."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))

from remote_provider.policy import (  # noqa: E402
    ACTIVATION_RECEIPT_SCHEMA,
    FORBIDDEN_PUBLIC_SECRET_ENV,
    INTERNAL_EGRESS_BASE_URL,
    INTERNAL_SSH_CONTROL_BASE_URL,
    PUBLIC_MODEL_ALIAS,
    REDACTED,
    SCHEMA,
    PolicyError,
    load_policy,
    normalize_peer_control_url,
    normalize_provider_base_url,
    plan_route,
    public_activation_receipt,
    validate_public_env_keys,
)
from remote_provider.transport import (  # noqa: E402
    DEFAULT_SSH_CONTROL_LISTEN_PORT,
    DEFAULT_SSH_IDENTITY_PATH,
    DEFAULT_SSH_INFERENCE_LISTEN_PORT,
    DEFAULT_SSH_KNOWN_HOSTS_PATH,
    DEFAULT_SSH_LOCAL_BIND_HOST,
    TransportError,
    build_ssh_tunnel_specs,
)
from remote_provider.ssh_supervisor import (  # noqa: E402
    SSH_SUPERVISOR_PLAN_SCHEMA,
    ssh_secret_status,
    ssh_supervisor_plan,
)
from remote_provider.reconciler import (  # noqa: E402
    PHASES,
    FakeActivationAdapter,
    result,
    run_activation_transaction,
)
from remote_provider.lifecycle import (  # noqa: E402
    LIFECYCLE_OPERATION_SCHEMA,
    LifecycleError,
    plan_lifecycle_operation,
)
from remote_provider.probe import (  # noqa: E402
    PROBE_RECEIPT_SCHEMA,
    ProbeError,
    probe_direct_provider,
    probe_provider_route,
    public_probe_receipt,
)


POLICY_PATH = ROOT / "config" / "remote-provider-egress-policy.json"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_raises_policy_error(func, message: str) -> str:
    try:
        func()
    except PolicyError as exc:
        return str(exc)
    raise AssertionError(message)


def assert_raises_transport_error(func, message: str) -> str:
    try:
        func()
    except TransportError as exc:
        return str(exc)
    raise AssertionError(message)


def assert_raises_lifecycle_error(func, message: str) -> str:
    try:
        func()
    except LifecycleError as exc:
        return str(exc)
    raise AssertionError(message)


def assert_raises_probe_error(func, message: str) -> ProbeError:
    try:
        func()
    except ProbeError as exc:
        return exc
    raise AssertionError(message)


def cloud_direct_env(**overrides: str) -> dict[str, str]:
    env = {
        "ODS_MODE": "cloud",
        "REMOTE_LLM_ENABLED": "true",
        "REMOTE_LLM_TRANSPORT": "direct",
        "REMOTE_LLM_BASE_URL": "https://gpu.example.test",
        "REMOTE_LLM_MODEL": "qwen/remote:latest",
    }
    env.update(overrides)
    return env


def cloud_ssh_env(**overrides: str) -> dict[str, str]:
    env = cloud_direct_env(
        REMOTE_LLM_TRANSPORT="ssh",
        REMOTE_LLM_BASE_URL="http://127.0.0.1:8000/v1",
        REMOTE_LLM_SSH_HOST="gpu.example.test",
        REMOTE_LLM_SSH_USER="ods",
        REMOTE_LLM_SSH_PORT="22",
        REMOTE_LLM_SSH_INFERENCE_HOST="127.0.0.1",
        REMOTE_LLM_SSH_INFERENCE_PORT="8000",
    )
    env.update(overrides)
    return env


class _ProbeResponse:
    status = 200
    headers = {"content-type": "application/json"}

    def __init__(self, body: bytes = b'{"data":[{"id":"qwen"}]}') -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit: int) -> bytes:
        return self.body


def _public_resolver(*_args, **_kwargs):
    return [(None, None, None, None, ("93.184.216.34", 443))]


def _private_resolver(*_args, **_kwargs):
    return [(None, None, None, None, ("10.0.0.5", 443))]


def test_policy_document_shape() -> None:
    policy = load_policy(POLICY_PATH)
    assert_true(policy["schema"] == SCHEMA, "policy schema must be versioned")
    assert_true(policy["version"] == 1, "policy version must be 1")
    assert_true(
        policy["egress_service"]["internal_base_url"] == INTERNAL_EGRESS_BASE_URL,
        "egress service internal URL drifted",
    )
    assert_true(
        policy["egress_service"]["public_model_alias"] == PUBLIC_MODEL_ALIAS,
        "public model alias drifted",
    )
    assert_true(
        policy["activation"]["phases"] == list(PHASES),
        "activation policy must match reconciler phases",
    )
    forbidden = set(policy["secret_custody"]["public_env_forbidden"])
    assert_true(
        FORBIDDEN_PUBLIC_SECRET_ENV <= forbidden,
        "policy must list every forbidden public secret key",
    )
    schema_text = (ROOT / ".env.schema.json").read_text(encoding="utf-8")
    example_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in forbidden:
        assert_true(key not in schema_text, f"{key} must not be in public env schema")
        assert_true(key not in example_text, f"{key} must not be in public env example")
    direct_forbidden = set(
        policy["transports"]["direct"]["provider_base_url"]["forbid_ip_literal_classes"]
    )
    assert_true("non_global" in direct_forbidden, "direct policy must reject non-global IPs")
    tunnel = policy["transports"]["ssh"]["tunnel"]
    assert_true(tunnel["service_id"] == "remote-provider-ssh-tunnel", "SSH tunnel service id drifted")
    assert_true(tunnel["local_bind_host"] == DEFAULT_SSH_LOCAL_BIND_HOST, "SSH tunnel bind host drifted")
    assert_true(tunnel["inference_listen_port"] == DEFAULT_SSH_INFERENCE_LISTEN_PORT, "SSH inference port drifted")
    assert_true(tunnel["control_listen_port"] == DEFAULT_SSH_CONTROL_LISTEN_PORT, "SSH control port drifted")
    assert_true(tunnel["identity_file"] == str(DEFAULT_SSH_IDENTITY_PATH), "SSH identity path drifted")
    assert_true(tunnel["known_hosts_file"] == str(DEFAULT_SSH_KNOWN_HOSTS_PATH), "SSH known_hosts path drifted")
    assert_true(tunnel["forbid_user_ssh_config"] is True, "SSH transport must ignore user config")
    assert_true(tunnel["strict_host_key_checking"] is True, "SSH transport must enforce known_hosts")
    assert_true(tunnel["forward_agent"] is False, "SSH transport must forbid agent forwarding")


def test_direct_normalizes_public_https_roots() -> None:
    route = plan_route(cloud_direct_env())
    assert_true(route["enabled"] is True, "remote route should be enabled")
    assert_true(route["transport"] == "direct", "transport should be direct")
    assert_true(
        route["provider"]["baseUrl"] == "https://gpu.example.test/v1",
        "host root should normalize to /v1",
    )
    assert_true(
        normalize_provider_base_url(
            "https://GPU.example.test:9443/api/v1/",
            transport="direct",
        )
        == "https://gpu.example.test:9443/api/v1",
        "direct transport should accept /api/v1",
    )
    assert_true(
        route["egress"] == {
            "internalBaseUrl": INTERNAL_EGRESS_BASE_URL,
            "publicModel": PUBLIC_MODEL_ALIAS,
            "consumerRoute": "gateway",
        },
        "route must expose only the internal egress endpoint to consumers",
    )


def test_direct_peer_lifecycle_requires_safe_public_control_url() -> None:
    route = plan_route(
        cloud_direct_env(REMOTE_ODS_PEER_URL="https://Peer.example.test/")
    )
    assert_true(
        route["peer"] == {
            "controlBaseUrl": "https://peer.example.test",
            "transport": "direct",
        },
        "direct peer control URL should normalize to a public HTTPS root",
    )
    assert_true(
        normalize_peer_control_url(
            "https://Peer.example.test:9443/",
            transport="direct",
        )
        == "https://peer.example.test:9443",
        "direct peer control URL should preserve a safe explicit port",
    )
    receipt = public_activation_receipt(
        route,
        phase="validate",
        ok=True,
        detail="metadata accepted",
    )
    assert_true(
        receipt["peer"] == route["peer"],
        "activation receipts should expose safe peer metadata",
    )
    unsafe_urls = [
        "http://peer.example.test",
        "https://user:token@peer.example.test",
        "https://peer.example.test?tenant=ods",
        "https://peer.example.test#fragment",
        "https://peer.example.test/api",
        "https://peer.example.test\\api",
        "https://127.0.0.1:8091",
        "https://10.0.0.5",
        "https://localhost",
        "https://host.docker.internal",
    ]
    for url in unsafe_urls:
        assert_raises_policy_error(
            lambda url=url: plan_route(cloud_direct_env(REMOTE_ODS_PEER_URL=url)),
            f"direct transport accepted unsafe peer URL: {url}",
        )


def test_direct_rejects_unsafe_urls() -> None:
    unsafe_urls = [
        "http://gpu.example.test/v1",
        "https://user:token@gpu.example.test/v1",
        "https://gpu.example.test/v1?tenant=ods",
        "https://gpu.example.test/v1#fragment",
        "https://gpu.example.test\\v1",
        "https://gpu.example.test/proxy",
        "https://127.0.0.1:8000/v1",
        "https://[::1]:8000/v1",
        "https://10.0.0.5/v1",
        "https://169.254.1.10/v1",
        "https://224.0.0.1/v1",
        "https://255.255.255.255/v1",
        "https://localhost/v1",
        "https://host.docker.internal/v1",
    ]
    for url in unsafe_urls:
        assert_raises_policy_error(
            lambda url=url: plan_route(cloud_direct_env(REMOTE_LLM_BASE_URL=url)),
            f"direct transport accepted unsafe URL: {url}",
        )


def test_ssh_allows_remote_side_http_with_required_metadata() -> None:
    route = plan_route(cloud_ssh_env())
    assert_true(route["enabled"] is True, "SSH remote route should be enabled")
    assert_true(route["transport"] == "ssh", "transport should be ssh")
    assert_true(
        route["provider"]["baseUrl"] == "http://127.0.0.1:8000/v1",
        "SSH transport should allow remote-side loopback HTTP",
    )
    assert_true(route["ssh"]["host"] == "gpu.example.test", "SSH host missing")
    assert_true(route["ssh"]["port"] == 22, "SSH port must be numeric")
    assert_true(
        route["ssh"]["inferencePort"] == 8000,
        "SSH inference port must be numeric",
    )


def test_ssh_peer_lifecycle_uses_control_tunnel_boundary() -> None:
    route = plan_route(
        cloud_ssh_env(
            REMOTE_LLM_SSH_CONTROL_HOST="127.0.0.1",
            REMOTE_LLM_SSH_CONTROL_PORT="8091",
        )
    )
    assert_true(
        route["peer"] == {
            "controlBaseUrl": INTERNAL_SSH_CONTROL_BASE_URL,
            "transport": "ssh",
        },
        "SSH peer lifecycle should default to the owned control tunnel",
    )
    dumped = json.dumps(route, sort_keys=True)
    assert_true("REMOTE_ODS_PEER_TOKEN" not in dumped, "peer token ref leaked into route")
    assert_true(
        plan_route(cloud_ssh_env())["peer"] is None,
        "SSH routes without a control tunnel should not imply peer lifecycle",
    )
    explicit = plan_route(cloud_ssh_env(REMOTE_ODS_PEER_URL="https://peer.example.test/"))
    assert_true(
        explicit["peer"] == {
            "controlBaseUrl": "https://peer.example.test",
            "transport": "ssh",
        },
        "SSH peer lifecycle should accept explicit public HTTPS roots",
    )
    assert_raises_policy_error(
        lambda: plan_route(cloud_ssh_env(REMOTE_ODS_PEER_URL="http://127.0.0.1:8091/")),
        "SSH explicit peer URLs must not target container-local HTTP",
    )


def test_ssh_requires_transport_metadata() -> None:
    env = cloud_ssh_env()
    del env["REMOTE_LLM_SSH_INFERENCE_PORT"]
    detail = assert_raises_policy_error(
        lambda: plan_route(env),
        "SSH transport accepted missing inference port",
    )
    assert_true(
        "REMOTE_LLM_SSH_INFERENCE_PORT" in detail,
        "failure should name the missing SSH env key",
    )
    assert_raises_policy_error(
        lambda: plan_route(cloud_ssh_env(REMOTE_LLM_SSH_PORT="70000")),
        "SSH transport accepted an out-of-range SSH port",
    )


def test_ssh_transport_specs_are_structured_and_hardened() -> None:
    route = plan_route(
        cloud_ssh_env(
            REMOTE_LLM_SSH_CONTROL_HOST="127.0.0.1",
            REMOTE_LLM_SSH_CONTROL_PORT="8091",
        )
    )
    specs = build_ssh_tunnel_specs(route)
    assert_true(len(specs) == 2, "SSH route should build inference and control tunnels")
    inference, control = specs
    assert_true(inference.name == "inference", "first tunnel should be inference")
    assert_true(control.name == "control", "second tunnel should be control")
    assert_true(inference.listen_host == DEFAULT_SSH_LOCAL_BIND_HOST, "inference bind host drifted")
    assert_true(inference.listen_port == DEFAULT_SSH_INFERENCE_LISTEN_PORT, "inference bind port drifted")
    assert_true(control.listen_port == DEFAULT_SSH_CONTROL_LISTEN_PORT, "control bind port drifted")
    assert_true(
        "0.0.0.0:18091:127.0.0.1:8000" in inference.args,
        "inference forward must be explicit and internal",
    )
    assert_true(
        "0.0.0.0:18092:127.0.0.1:8091" in control.args,
        "control forward must be explicit and internal",
    )
    for spec in specs:
        assert_true(all(isinstance(arg, str) for arg in spec.args), "SSH command must be an argv list")
        assert_true("-F" in spec.args and "/dev/null" in spec.args, "SSH must ignore user config")
        assert_true("BatchMode=yes" in spec.args, "SSH must run in batch mode")
        assert_true("ExitOnForwardFailure=yes" in spec.args, "SSH must fail on forward failure")
        assert_true("ForwardAgent=no" in spec.args, "SSH must disable agent forwarding")
        assert_true("PermitLocalCommand=no" in spec.args, "SSH must disable local commands")
        assert_true("StrictHostKeyChecking=yes" in spec.args, "SSH must enforce known_hosts")
        assert_true(
            f"UserKnownHostsFile={DEFAULT_SSH_KNOWN_HOSTS_PATH}" in spec.args,
            "SSH must use the transport-owned known_hosts file",
        )
        assert_true(
            f"IdentityFile={DEFAULT_SSH_IDENTITY_PATH}" in spec.args,
            "SSH must use the transport-owned identity file",
        )
        joined = " ".join(spec.args)
        assert_true("ProxyCommand" not in joined, "SSH transport must not use ProxyCommand")
        assert_true("REMOTE_LLM_SSH_PRIVATE_KEY" not in joined, "SSH private key env must not be projected")
        assert_true(";" not in joined and "\n" not in joined, "SSH argv must not contain shell separators")


def test_ssh_forward_brackets_ipv6_targets() -> None:
    """ssh(1) parses -L by splitting on colons, so IPv6 targets need brackets."""
    route = plan_route(cloud_ssh_env(
        REMOTE_LLM_SSH_CONTROL_HOST="127.0.0.1",
        REMOTE_LLM_SSH_CONTROL_PORT="8091",
    ))
    ipv6_route = json.loads(json.dumps(route))
    ipv6_route["ssh"]["inferenceHost"] = "2001:db8::1"
    ipv6_route["ssh"]["controlHost"] = "fe80::2"
    inference, control = build_ssh_tunnel_specs(ipv6_route)

    assert_true(
        "0.0.0.0:18091:[2001:db8::1]:8000" in inference.args,
        "IPv6 inference target must be bracketed in the -L forward",
    )
    assert_true(
        "0.0.0.0:18092:[fe80::2]:8091" in control.args,
        "IPv6 control target must be bracketed in the -L forward",
    )
    # The spec metadata (and the redacted plan built from it) keeps the plain
    # host token; only the ssh argument needs the brackets.
    assert_true(inference.target_host == "2001:db8::1", "spec target host should stay unbracketed")

    # IPv4 and hostname targets must be untouched.
    v4_inference, _ = build_ssh_tunnel_specs(route)
    assert_true(
        "0.0.0.0:18091:127.0.0.1:8000" in v4_inference.args,
        "IPv4 forward must not gain brackets",
    )


def test_ssh_transport_specs_reject_unsafe_tokens() -> None:
    route = plan_route(cloud_ssh_env())
    assert_raises_transport_error(
        lambda: build_ssh_tunnel_specs({**route, "transport": "direct"}),
        "direct routes should not build SSH specs",
    )
    unsafe_user = json.loads(json.dumps(route))
    unsafe_user["ssh"]["user"] = "ods;rm"
    assert_raises_transport_error(
        lambda: build_ssh_tunnel_specs(unsafe_user),
        "unsafe SSH user was accepted",
    )
    unsafe_host = json.loads(json.dumps(route))
    unsafe_host["ssh"]["host"] = "-oProxyCommand=sh"
    assert_raises_transport_error(
        lambda: build_ssh_tunnel_specs(unsafe_host),
        "unsafe SSH host was accepted",
    )


def test_ssh_supervisor_plan_is_redacted_and_start_gated() -> None:
    route = plan_route(
        cloud_ssh_env(
            REMOTE_LLM_SSH_CONTROL_HOST="127.0.0.1",
            REMOTE_LLM_SSH_CONTROL_PORT="8091",
        )
    )
    plan = ssh_supervisor_plan(
        route,
        secrets={
            "sshIdentity": {"configured": True, "bytes": 123},
            "sshKnownHosts": {"configured": True, "bytes": 45},
        },
    )
    dumped = json.dumps(plan, sort_keys=True)
    assert_true(plan["schema"] == SSH_SUPERVISOR_PLAN_SCHEMA, "supervisor plan schema drifted")
    assert_true(plan["status"] == "planned", "ready SSH custody should produce a planned tunnel")
    assert_true(plan["ready"] is False, "inert supervisor plan must not report a live tunnel")
    assert_true(plan["readyToStart"] is True, "ready SSH custody should allow process start")
    assert_true(plan["reason"] == "tunnel_process_not_started", "planned SSH tunnel reason drifted")
    assert_true(
        plan["tunnelBaseUrl"] == "http://remote-provider-ssh-tunnel:18091/v1",
        "SSH tunnel base URL drifted",
    )
    assert_true(len(plan["tunnels"]) == 2, "SSH plan should expose inference and control tunnels")
    for tunnel in plan["tunnels"]:
        argv = tunnel["argv"]
        assert_true(argv[0] == "ssh", "SSH tunnel must be an argv list starting with ssh")
        assert_true("-F" in argv and "/dev/null" in argv, "SSH tunnel must ignore user config")
    assert_true("unit-test-key" not in dumped, "SSH identity contents leaked into supervisor plan")
    assert_true("AAAATEST" not in dumped, "known_hosts contents leaked into supervisor plan")
    assert_true("REMOTE_LLM_SSH_PRIVATE_KEY" not in dumped, "secret env name leaked into supervisor plan")


def test_ssh_supervisor_plan_blocks_missing_secret_custody() -> None:
    route = plan_route(cloud_ssh_env())
    plan = ssh_supervisor_plan(
        route,
        secrets={
            "sshIdentity": {"configured": False, "bytes": 0},
            "sshKnownHosts": {"configured": False, "bytes": 0},
        },
    )
    assert_true(plan["status"] == "blocked", "missing SSH custody must block startup")
    assert_true(plan["readyToStart"] is False, "missing SSH custody must not be startable")
    assert_true(
        set(plan["missingSecrets"]) == {"sshIdentity", "sshKnownHosts"},
        "missing SSH custody should name both required secret files",
    )


def test_ssh_secret_status_is_support_bundle_safe() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        secret_dir = Path(tmp)
        (secret_dir / "ssh-identity").write_text("unit-test-key\n", encoding="utf-8")
        (secret_dir / "known_hosts").write_text("gpu.example.test ssh-ed25519 AAAATEST\n", encoding="utf-8")
        status = ssh_secret_status(secret_dir)
    dumped = json.dumps(status, sort_keys=True)
    assert_true(status["sshIdentity"]["configured"] is True, "identity status drifted")
    assert_true(status["sshIdentity"]["bytes"] >= 14, "identity byte status drifted")
    assert_true(status["sshKnownHosts"]["configured"] is True, "known_hosts status drifted")
    assert_true("unit-test-key" not in dumped, "identity contents leaked into secret status")
    assert_true("AAAATEST" not in dumped, "known_hosts contents leaked into secret status")


def test_public_env_forbids_remote_secrets() -> None:
    validate_public_env_keys(
        {
            "REMOTE_LLM_ENABLED": "true",
            "REMOTE_LLM_TRANSPORT": "direct",
            "REMOTE_LLM_BASE_URL": "https://gpu.example.test/v1",
            "REMOTE_LLM_MODEL": "qwen-remote",
        }
    )
    detail = assert_raises_policy_error(
        lambda: validate_public_env_keys(
            {
                "REMOTE_LLM_ENABLED": "true",
                "REMOTE_LLM_API_KEY": "unit-test-provider-token",
            }
        ),
        "public env accepted a provider API key",
    )
    assert_true("REMOTE_LLM_API_KEY" in detail, "failure should name the secret key")


def test_activation_receipt_redacts_secret_references() -> None:
    route = plan_route(cloud_direct_env())
    receipt = public_activation_receipt(
        route,
        phase="validate",
        ok=True,
        detail="metadata accepted",
        secret_refs={
            "REMOTE_LLM_API_KEY": "unit-test-provider-token",
            "REMOTE_ODS_PEER_TOKEN": "unit-test-peer-token",
        },
    )
    assert_true(receipt["schema"] == ACTIVATION_RECEIPT_SCHEMA, "receipt schema drifted")
    dumped = json.dumps(receipt, sort_keys=True)
    assert_true("unit-test-provider-token" not in dumped, "provider token leaked")
    assert_true("unit-test-peer-token" not in dumped, "peer token leaked")
    assert_true(REDACTED in dumped, "receipt should carry redacted secret refs")
    assert_true(
        receipt["provider"]["baseUrl"] == "https://gpu.example.test/v1",
        "receipt should keep non-secret provider metadata",
    )


def test_activation_transaction_orders_phases() -> None:
    adapter = FakeActivationAdapter()
    outcome = run_activation_transaction(adapter, cloud_direct_env())
    assert_true(outcome["ok"] is True, "happy path should succeed")
    assert_true(outcome["phase"] == "prove", "happy path must finish at prove")
    assert_true(adapter.calls == list(PHASES), "activation phases ran out of order")


def test_activation_transaction_fails_closed_and_rolls_back_after_commit() -> None:
    before_commit = FakeActivationAdapter(
        {"validate": [result(False, "metadata rejected")]}
    )
    failed = run_activation_transaction(before_commit, cloud_direct_env())
    assert_true(failed["ok"] is False, "validate failure should fail")
    assert_true(failed["phase"] == "validate", "failure phase should be validate")
    assert_true(
        before_commit.calls == ["stage", "validate"],
        "pre-commit failure should stop before commit",
    )
    assert_true("rollback" not in failed, "pre-commit failure must not roll back")

    after_commit = FakeActivationAdapter({"prove": [result(False, "probe failed")]})
    failed = run_activation_transaction(after_commit, cloud_direct_env())
    assert_true(failed["ok"] is False, "prove failure should fail")
    assert_true(failed["phase"] == "prove", "failure phase should be prove")
    assert_true(
        after_commit.calls == ["stage", "validate", "commit", "prove", "rollback"],
        "post-commit failure must roll back once",
    )
    assert_true(failed["rollback"]["ok"] is True, "rollback result should be attached")

    commit_failure = FakeActivationAdapter({"commit": [result(False, "swap failed")]})
    failed = run_activation_transaction(commit_failure, cloud_direct_env())
    assert_true(failed["ok"] is False, "commit failure should fail")
    assert_true(failed["phase"] == "commit", "failure phase should be commit")
    assert_true(
        commit_failure.calls == ["stage", "validate", "commit", "rollback"],
        "commit failure must request rollback once",
    )


def test_lifecycle_configure_operation_is_redacted_and_typed() -> None:
    operation = plan_lifecycle_operation(
        {
            "action": "configure",
            "provider": {
                "transport": "direct",
                "baseUrl": "https://GPU.example.test",
                "model": "qwen/remote:latest",
            },
            "secrets": {"apiKey": "unit-test-provider-token"},
        }
    )
    dumped = json.dumps(operation, sort_keys=True)
    assert_true(operation["schema"] == LIFECYCLE_OPERATION_SCHEMA, "lifecycle schema drifted")
    assert_true(operation["action"] == "configure", "configure action drifted")
    assert_true(operation["route"]["provider"]["baseUrl"] == "https://gpu.example.test/v1", "base URL not normalized")
    assert_true(operation["writes"]["routingState"] is True, "configure must write routing state")
    assert_true(operation["writes"]["providerSecret"] is True, "configure must write provider secret")
    assert_true(operation["writes"]["removesSecrets"] is False, "configure must not delete secrets")
    assert_true("REMOTE_LLM_API_KEY" in operation["secretRefs"], "provider secret ref missing")
    assert_true(operation["secretRefs"]["REMOTE_LLM_API_KEY"]["value"] == REDACTED, "provider secret not redacted")
    assert_true(operation["receipt"]["secretRefs"]["REMOTE_LLM_API_KEY"]["value"] == REDACTED, "receipt secret not redacted")
    assert_true("unit-test-provider-token" not in dumped, "provider secret leaked into lifecycle operation")


def test_lifecycle_peer_operation_tracks_peer_token_without_leaks() -> None:
    operation = plan_lifecycle_operation(
        {
            "action": "configure",
            "provider": {
                "transport": "direct",
                "baseUrl": "https://gpu.example.test",
                "model": "qwen/remote:latest",
            },
            "peer": {"controlBaseUrl": "https://peer.example.test/"},
            "secrets": {
                "apiKey": "unit-test-provider-token",
                "peerToken": "unit-test-peer-token",
            },
        }
    )
    dumped = json.dumps(operation, sort_keys=True)
    assert_true(
        operation["route"]["peer"] == {
            "controlBaseUrl": "https://peer.example.test",
            "transport": "direct",
        },
        "peer route metadata missing from lifecycle operation",
    )
    assert_true(operation["writes"]["peerToken"] is True, "configure must write peer token custody")
    assert_true("REMOTE_ODS_PEER_TOKEN" in operation["secretRefs"], "peer token ref missing")
    assert_true(
        operation["secretRefs"]["REMOTE_ODS_PEER_TOKEN"]["value"] == REDACTED,
        "peer token must be redacted",
    )
    assert_true("unit-test-peer-token" not in dumped, "peer token leaked")
    assert_true("peer-token" not in dumped, "peer token file name leaked")


def test_lifecycle_test_disable_and_remove_write_intent() -> None:
    validate = plan_lifecycle_operation(
        {
            "action": "test",
            "REMOTE_LLM_TRANSPORT": "direct",
            "REMOTE_LLM_BASE_URL": "https://gpu.example.test/v1",
            "REMOTE_LLM_MODEL": "qwen/remote:latest",
            "secrets": {"apiKey": "unit-test-provider-token"},
        }
    )
    assert_true(validate["receipt"]["phase"] == "validate", "test action should map to validate phase")
    assert_true(
        validate["writes"] == {
            "routingState": False,
            "providerSecret": False,
            "peerToken": False,
            "sshIdentity": False,
            "sshKnownHosts": False,
            "removesRoutingState": False,
            "removesSecrets": False,
        },
        "test action must not persist state",
    )

    disabled = plan_lifecycle_operation({"action": "disable"})
    assert_true(disabled["route"]["enabled"] is False, "disable must produce a disabled route")
    assert_true(disabled["writes"]["routingState"] is True, "disable must write routing state")
    assert_true(disabled["writes"]["removesSecrets"] is False, "disable must preserve secrets")

    removed = plan_lifecycle_operation({"action": "remove"})
    assert_true(removed["route"]["enabled"] is False, "remove must produce a disabled public route")
    assert_true(removed["writes"]["routingState"] is False, "remove should delete, not rewrite, route state")
    assert_true(removed["writes"]["removesRoutingState"] is True, "remove must delete route state")
    assert_true(removed["writes"]["removesSecrets"] is True, "remove must delete secret custody files")


def test_lifecycle_ssh_operation_tracks_secret_custody_without_leaks() -> None:
    operation = plan_lifecycle_operation(
        {
            "action": "configure",
            "provider": {
                "transport": "ssh",
                "baseUrl": "http://127.0.0.1:8000/v1",
                "model": "qwen/remote:latest",
            },
            "ssh": {
                "host": "gpu.example.test",
                "user": "ods",
                "port": "22",
                "inferenceHost": "127.0.0.1",
                "inferencePort": "8000",
            },
            "secrets": {
                "apiKey": "unit-test-provider-token",
                "sshPrivateKey": "-----BEGIN OPENSSH PRIVATE KEY-----\nunit-test-key\n-----END OPENSSH PRIVATE KEY-----",
                "sshKnownHosts": "gpu.example.test ssh-ed25519 AAAATEST",
            },
        }
    )
    dumped = json.dumps(operation, sort_keys=True)
    assert_true(operation["route"]["transport"] == "ssh", "SSH operation transport drifted")
    assert_true(operation["route"]["ssh"]["host"] == "gpu.example.test", "SSH metadata missing")
    assert_true(operation["writes"]["sshIdentity"] is True, "SSH configure must write identity custody")
    assert_true(operation["writes"]["sshKnownHosts"] is True, "SSH configure must write known_hosts custody")
    for ref in (
        "REMOTE_LLM_API_KEY",
        "REMOTE_LLM_SSH_PRIVATE_KEY",
        "REMOTE_LLM_SSH_KNOWN_HOSTS",
    ):
        assert_true(ref in operation["secretRefs"], f"{ref} secret ref missing")
        assert_true(operation["secretRefs"][ref]["value"] == REDACTED, f"{ref} not redacted")
    assert_true("unit-test-provider-token" not in dumped, "provider token leaked")
    assert_true("unit-test-key" not in dumped, "SSH private key leaked")
    assert_true("AAAATEST" not in dumped, "SSH known_hosts leaked")


def test_lifecycle_rejects_unsafe_or_secret_public_inputs() -> None:
    detail = assert_raises_lifecycle_error(
        lambda: plan_lifecycle_operation(
            {
                "action": "configure",
                "provider": {
                    "transport": "direct",
                    "baseUrl": "https://gpu.example.test/v1",
                    "model": "qwen/remote:latest",
                },
            }
        ),
        "configure accepted a missing provider secret",
    )
    assert_true("secrets.apiKey" in detail, "missing API key failure should name secrets.apiKey")
    assert_raises_lifecycle_error(
        lambda: plan_lifecycle_operation({"action": "rotate"}),
        "unsupported lifecycle action accepted",
    )
    assert_raises_lifecycle_error(
        lambda: plan_lifecycle_operation(
            {
                "action": "configure",
                "REMOTE_LLM_API_KEY": "unit-test-provider-token",
                "provider": {
                    "transport": "direct",
                    "baseUrl": "https://gpu.example.test/v1",
                    "model": "qwen/remote:latest",
                },
                "secrets": {"apiKey": "unit-test-provider-token"},
            }
        ),
        "public secret env key accepted in lifecycle payload",
    )
    assert_raises_policy_error(
        lambda: plan_lifecycle_operation(
            {
                "action": "configure",
                "provider": {
                    "transport": "direct",
                    "baseUrl": "https://127.0.0.1:8000/v1",
                    "model": "qwen/remote:latest",
                },
                "secrets": {"apiKey": "unit-test-provider-token"},
            }
        ),
        "unsafe direct lifecycle provider URL accepted",
    )


def test_direct_provider_probe_is_bounded_and_redacted() -> None:
    route = plan_route(cloud_direct_env())
    calls = []

    def opener(request, *, timeout: float):
        calls.append((request, timeout))
        return _ProbeResponse()

    result = probe_direct_provider(
        route,
        provider_secret="unit-test-provider-token",
        resolver=_public_resolver,
        opener=opener,
    )
    dumped = json.dumps(result, sort_keys=True)
    assert_true(result["ok"] is True, "direct provider probe should pass")
    assert_true(result["endpoint"] == "/v1/models", "probe endpoint drifted")
    assert_true(result["modelCount"] == 1, "probe should count model-list payloads")
    assert_true(result["resolution"]["addressCount"] == 1, "probe should report DNS count")
    assert_true("unit-test-provider-token" not in dumped, "probe result leaked token")
    assert_true(len(calls) == 1, "probe should perform one bounded HTTP request")
    request, timeout = calls[0]
    assert_true(timeout == 10.0, "probe timeout drifted")
    assert_true(request.full_url == "https://gpu.example.test/v1/models", "probe URL drifted")
    assert_true(
        request.headers.get("Authorization") == "Bearer unit-test-provider-token",
        "probe must send provider auth at the request boundary",
    )


def test_public_probe_receipt_is_redacted_and_typed() -> None:
    receipt = public_probe_receipt(
        {
            "ok": True,
            "status": 200,
            "endpoint": "/v1/models",
            "contentType": "application/json",
            "modelCount": 1,
            "resolution": {"ok": True, "addressCount": 1, "raw": "93.184.216.34"},
            "authorization": "Bearer unit-test-provider-token",
        },
        verified_at="2026-07-26T00:00:00+00:00",
    )
    dumped = json.dumps(receipt, sort_keys=True)
    assert_true(receipt["schema"] == PROBE_RECEIPT_SCHEMA, "probe receipt schema drifted")
    assert_true(receipt["ok"] is True, "probe receipt should keep success status")
    assert_true(receipt["httpStatus"] == 200, "probe HTTP status not retained")
    assert_true(
        receipt["resolution"] == {"ok": True, "addressCount": 1},
        "probe DNS receipt not sanitized",
    )
    assert_true("unit-test-provider-token" not in dumped, "probe receipt leaked provider auth")
    assert_true("93.184.216.34" not in dumped, "probe receipt should not expose raw DNS answers")


def test_direct_provider_probe_fails_closed_before_unsafe_dns_request() -> None:
    route = plan_route(cloud_direct_env())

    def opener(*_args, **_kwargs):
        raise AssertionError("unsafe DNS result must prevent provider HTTP")

    error = assert_raises_probe_error(
        lambda: probe_direct_provider(
            route,
            provider_secret="unit-test-provider-token",
            resolver=_private_resolver,
            opener=opener,
        ),
        "unsafe DNS resolution should reject direct provider probe",
    )
    assert_true(error.code == "provider_resolution_rejected", "unsafe DNS error code drifted")
    assert_true("private" in error.message, "unsafe DNS failure should explain address class")


def test_generic_ssh_provider_probe_uses_tunnel_boundary() -> None:
    route = plan_route(cloud_ssh_env())
    calls = []

    def opener(request, *, timeout: float):
        calls.append((request, timeout))
        return _ProbeResponse()

    result = probe_provider_route(
        route,
        provider_secret="unit-test-provider-token",
        resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("SSH tunnel probes must not run direct DNS validation")
        ),
        opener=opener,
    )
    dumped = json.dumps(result, sort_keys=True)
    assert_true(result["ok"] is True, "SSH provider probe should pass through generic helper")
    assert_true(result["transport"] == "ssh", "SSH probe transport metadata drifted")
    assert_true(result["resolution"] == {"ok": True, "addressCount": 0}, "SSH probe should not expose direct DNS")
    assert_true(len(calls) == 1, "SSH probe should perform one bounded HTTP request")
    request, timeout = calls[0]
    assert_true(timeout == 10.0, "SSH probe timeout drifted")
    assert_true(
        request.full_url == "http://remote-provider-ssh-tunnel:18091/v1/models",
        "SSH probe must target the internal tunnel service",
    )
    assert_true(
        request.headers.get("Authorization") == "Bearer unit-test-provider-token",
        "SSH probe must inject provider auth at the request boundary",
    )
    assert_true("unit-test-provider-token" not in dumped, "SSH probe result leaked provider auth")


def test_direct_probe_api_still_defers_ssh_transport() -> None:
    route = plan_route(cloud_ssh_env())
    error = assert_raises_probe_error(
        lambda: probe_direct_provider(
            route,
            provider_secret="unit-test-provider-token",
            resolver=_public_resolver,
            opener=lambda *_args, **_kwargs: _ProbeResponse(),
        ),
        "direct probe API should reject SSH transport",
    )
    assert_true(error.status == 501, "SSH probe should report unavailable transport")
    assert_true(error.code == "transport_probe_unavailable", "SSH probe error code drifted")


def main() -> int:
    tests = [
        test_policy_document_shape,
        test_direct_normalizes_public_https_roots,
        test_direct_peer_lifecycle_requires_safe_public_control_url,
        test_direct_rejects_unsafe_urls,
        test_ssh_allows_remote_side_http_with_required_metadata,
        test_ssh_peer_lifecycle_uses_control_tunnel_boundary,
        test_ssh_requires_transport_metadata,
        test_ssh_transport_specs_are_structured_and_hardened,
        test_ssh_forward_brackets_ipv6_targets,
        test_ssh_transport_specs_reject_unsafe_tokens,
        test_ssh_supervisor_plan_is_redacted_and_start_gated,
        test_ssh_supervisor_plan_blocks_missing_secret_custody,
        test_ssh_secret_status_is_support_bundle_safe,
        test_public_env_forbids_remote_secrets,
        test_activation_receipt_redacts_secret_references,
        test_activation_transaction_orders_phases,
        test_activation_transaction_fails_closed_and_rolls_back_after_commit,
        test_lifecycle_configure_operation_is_redacted_and_typed,
        test_lifecycle_peer_operation_tracks_peer_token_without_leaks,
        test_lifecycle_test_disable_and_remove_write_intent,
        test_lifecycle_ssh_operation_tracks_secret_custody_without_leaks,
        test_lifecycle_rejects_unsafe_or_secret_public_inputs,
        test_direct_provider_probe_is_bounded_and_redacted,
        test_public_probe_receipt_is_redacted_and_typed,
        test_direct_provider_probe_fails_closed_before_unsafe_dns_request,
        test_generic_ssh_provider_probe_uses_tunnel_boundary,
        test_direct_probe_api_still_defers_ssh_transport,
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
