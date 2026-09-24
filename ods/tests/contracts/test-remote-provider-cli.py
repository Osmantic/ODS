#!/usr/bin/env python3
"""Static contracts for the remote-provider ods CLI lifecycle surface."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
ODS_CLI = ROOT_DIR / "ods-cli"


def fail(message: str) -> None:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def require(pattern: str, text: str, message: str) -> None:
    if not re.search(pattern, text, flags=re.MULTILINE):
        fail(message)


def bash_path(path: Path) -> str:
    """Return a path usable by the Bash process on native or WSL hosts."""
    posix_path = path.resolve().as_posix()
    if os.name == "nt" and len(posix_path) > 2 and posix_path[1] == ":":
        return f"/mnt/{posix_path[0].lower()}{posix_path[2:]}"
    return posix_path


def check_secret_temp_cleanup() -> None:
    """A payload-construction failure must not retain streamed secrets."""
    with tempfile.TemporaryDirectory() as root:
        root_path = Path(root)
        install_dir = root_path / "ods"
        request_tmp = root_path / "request-tmp"
        stub_bin = root_path / "bin"
        install_dir.mkdir()
        request_tmp.mkdir()
        stub_bin.mkdir()
        (install_dir / "docker-compose.base.yml").write_text(
            "services: {}\n", encoding="utf-8"
        )
        (install_dir / ".env").write_text(
            "DASHBOARD_API_KEY=test-dashboard-key\n", encoding="utf-8"
        )
        secret_file = root_path / "provider-key"
        secret_file.write_text("provider-secret-marker\n", encoding="utf-8")

        jq_stub = stub_bin / "jq"
        jq_stub.write_text("#!/usr/bin/env bash\ncat\nexit 1\n", encoding="utf-8")
        jq_stub.chmod(0o755)
        curl_stub = stub_bin / "curl"
        curl_stub.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")
        curl_stub.chmod(0o755)

        stub_path = bash_path(stub_bin)
        command_path = (
            f"{stub_path}:"
            "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        provider_args = [
            "--base-url", "https://provider.example/v1",
            "--model", "remote-model",
            "--api-key-file", bash_path(secret_file),
        ]
        invocations = {
            "apply": ["remote-provider", "configure", *provider_args],
            "plan": ["remote-provider", "plan", "configure", *provider_args],
        }
        for label, cli_args in invocations.items():
            command = " ".join([
                "exec", "env",
                f"ODS_HOME={shlex.quote(bash_path(install_dir))}",
                f"TMPDIR={shlex.quote(bash_path(request_tmp))}",
                f"PATH={shlex.quote(command_path)}",
                "bash", shlex.quote(bash_path(ODS_CLI)),
                *(shlex.quote(arg) for arg in cli_args),
            ])
            result = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                check=False,
                text=True,
            )
            if result.returncode == 0:
                fail(f"remote-provider {label} must surface payload construction failure")
            output = result.stdout + result.stderr
            if "Could not build remote-provider lifecycle request" not in output:
                fail(f"remote-provider {label} did not reach payload construction: {output}")
            if "provider-secret-marker" in output:
                fail(f"remote-provider {label} failure output printed the provider API key")
            leftovers = list(request_tmp.glob("ods-remote-provider-*"))
            if leftovers:
                fail(f"remote-provider {label} retained temporary request data: {leftovers}")


def main() -> int:
    text = ODS_CLI.read_text(encoding="utf-8")

    require(
        r"^cmd_remote_provider\(\) \{",
        text,
        "ods-cli must implement remote-provider lifecycle command",
    )
    require(
        r"remote-provider \[status\|plan\|configure\|test\|enable\|disable\|remove\|peer-models\]",
        text,
        "remote-provider command must be visible in top-level help",
    )
    require(
        r"remote-provider\)\s+shift; cmd_remote_provider \"\$@\" ;;",
        text,
        "remote-provider command must be dispatched by the main CLI",
    )
    for endpoint in (
        "/api/remote-provider/status",
        "/api/remote-provider/plan",
        "/api/remote-provider/probe",
        "/api/remote-provider/apply",
        "/api/remote-provider/peer/models",
        "/api/remote-provider/peer/models/download-status",
        "/api/remote-provider/peer/models/download/cancel",
    ):
        require(re.escape(endpoint), text, f"CLI must call {endpoint}")

    require(
        r"printf 'Authorization: Bearer %s\\n' \"\$api_key\" \|[\s\S]*--header @-",
        text,
        "Dashboard API bearer token must be streamed through stdin, not argv",
    )
    require(
        r"--data-binary @\"\$payload_file\"",
        text,
        "remote-provider lifecycle payload must be sent from a private file",
    )
    require(
        r"chmod 600 \"\$payload_file\" \"\$response_file\"",
        text,
        "remote-provider request and response files must be owner-only",
    )
    require(
        r"Raw --api-key is not supported",
        text,
        "CLI must reject raw provider API keys in command arguments",
    )
    require(
        r"Raw --ssh-private-key is not supported",
        text,
        "CLI must reject raw SSH private keys in command arguments",
    )
    require(
        r"Raw --ssh-known-hosts is not supported",
        text,
        "CLI must reject raw SSH known_hosts content in command arguments",
    )
    require(
        r"--transport direct\|ssh",
        text,
        "remote-provider help must advertise SSH transport",
    )
    for option in (
        "--context-length TOKENS",
        "--max-tokens TOKENS",
        "--reasoning true|false",
        "--ssh-host HOST",
        "--ssh-user USER",
        "--ssh-inference-host HOST",
        "--ssh-inference-port PORT",
        "--ssh-private-key-file PATH",
        "--ssh-private-key-env NAME",
        "--ssh-known-hosts-file PATH",
        "--ssh-known-hosts-env NAME",
    ):
        if option not in text:
            fail(f"remote-provider help missing SSH option: {option}")
    require(
        r"remote-provider \$action does not accept provider or secret options",
        text,
        "enable/disable/remove must reject irrelevant provider or secret options",
    )
    if "--api-key VALUE" in text or "--api-key <" in text:
        fail("remote-provider help must not advertise raw --api-key values")
    if "--ssh-private-key VALUE" in text or "--ssh-known-hosts VALUE" in text:
        fail("remote-provider help must not advertise raw SSH secret values")
    if "Only direct remote-provider CLI transport is available" in text:
        fail("remote-provider CLI must not reject SSH transport")
    if "--arg ssh_private_key" in text or "--arg ssh_known_hosts" in text:
        fail("SSH secrets must not be passed to jq as process arguments")
    if '"$REMOTE_LLM_API_KEY"' in text:
        fail("top-level help must escape REMOTE_LLM_API_KEY under set -u")

    secret_reader = re.search(
        r"^_remote_provider_read_secret\(\) \{(?P<body>[\s\S]*?)^\}\s*$",
        text,
        flags=re.MULTILINE,
    )
    if secret_reader is None:
        fail("remote-provider secret reader could not be parsed")
    if "_env_get_raw" in secret_reader.group("body"):
        fail("provider API keys must not be read from public .env keys")
    require(
        r"^_remote_provider_read_multiline_secret\(\) \{",
        text,
        "remote-provider CLI must provide a multiline SSH secret reader",
    )
    require(
        r"printf '%s\\0%s\\0%s' \"\$api_key\" \"\$ssh_private_key\" \"\$ssh_known_hosts\" \| jq -Rs",
        text,
        "SSH lifecycle payload must stream secrets through stdin",
    )
    require(
        r"sshPrivateKey: \$secrets\[1\]",
        text,
        "SSH private key must come from streamed secret payload",
    )
    require(
        r"sshKnownHosts: \$secrets\[2\]",
        text,
        "SSH known_hosts must come from streamed secret payload",
    )
    require(
        r"contextLength: \(\$context_length \| tonumber\)",
        text,
        "remote-provider CLI must serialize the declared context window",
    )
    require(
        r"maxTokens: \(\$max_tokens \| tonumber\)",
        text,
        "remote-provider CLI must serialize the declared output limit",
    )
    require(
        r"reasoning: \$reasoning",
        text,
        "remote-provider CLI must serialize the declared reasoning capability",
    )

    remote_provider_function = re.search(
        r"^cmd_remote_provider\(\) \{(?P<body>[\s\S]*?)^\}\s*$",
        text,
        flags=re.MULTILINE,
    )
    if remote_provider_function is None:
        fail("remote-provider function could not be parsed")
    body = remote_provider_function.group("body")
    for subcommand in ("status", "plan", "configure", "test", "enable", "disable", "remove"):
        if subcommand not in body:
            fail(f"remote-provider CLI missing subcommand: {subcommand}")
    if "peer-models|peer-model)" not in body:
        fail("remote-provider CLI missing peer-models subcommand")
    require(
        r"^cmd_remote_provider_peer_models\(\) \{",
        text,
        "remote-provider CLI must implement peer model management",
    )
    require(
        r"jq -rn --arg value \"\$value\" '\$value \| @uri'",
        text,
        "peer model IDs must be URL-encoded with jq @uri",
    )
    require(
        r"Use --yes to confirm remote peer model deletion",
        text,
        "peer model delete must require explicit --yes confirmation",
    )
    require(
        r"_remote_provider_peer_model_path \"\$model_id\" load[\s\S]*timeout=\"2705\"",
        text,
        "peer model load must use the long Dashboard proxy timeout",
    )
    for command in (
        "peer-models list [--json]",
        "peer-models download-status [--json]",
        "peer-models download MODEL [--json]",
        "peer-models load MODEL [--json]",
        "peer-models cancel-download [--json]",
        "peer-models delete MODEL --yes [--json]",
    ):
        if command not in text:
            fail(f"remote-provider peer model help missing: {command}")
    if "--peer-token" in text or "REMOTE_ODS_PEER_TOKEN" in text:
        fail("remote-provider CLI must not accept or print peer tokens")
    require(
        r"^_remote_provider_probe_configured\(\) \{",
        text,
        "remote-provider CLI must support configured-route egress probes",
    )
    require(
        r"_remote_provider_request POST /api/remote-provider/probe",
        text,
        "configured remote-provider test must call the egress probe endpoint",
    )
    require(
        r"_remote_provider_request POST /api/remote-provider/probe \"\$response_file\" \"\" 1860",
        text,
        "configured remote-provider test must outlast downstream lifecycle activation",
    )
    require(
        r"_remote_provider_request POST /api/remote-provider/apply \"\$response_file\" \"\$payload_file\" 1860",
        text,
        "remote-provider apply must outlast the Dashboard lifecycle deadline",
    )
    require(
        r"_remote_provider_request POST /api/remote-provider/enable \"\$response_file\" \"\" 1860",
        text,
        "remote-provider enable must use the re-prove and rollback API",
    )
    require(
        r"Route proof: ",
        text,
        "configured remote-provider test output must show route proof recording status",
    )
    require(
        r"\.routeProof\.recorded",
        text,
        "configured remote-provider test output must inspect the routeProof result",
    )
    require(
        r"if \[\[ \"\$use_configured_probe\" == \"true\" \]\]; then[\s\S]*_remote_provider_probe_configured \"\$@\"",
        body,
        "remote-provider test must use configured-route probe when provider options are absent",
    )
    require(
        r"--ssh-private-key-file[\s\S]*--ssh-known-hosts-env",
        body,
        "remote-provider test must treat SSH options as one-shot lifecycle probes",
    )

    check_secret_temp_cleanup()

    print("[PASS] remote-provider CLI static contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
