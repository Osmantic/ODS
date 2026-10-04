"""Credentials recognizable by their format, and this installation's own
secrets, must not survive a shareable support bundle."""

import json
import os
import random
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
HEX = "0123456789abcdef"
WINDOW = 12


def filler(seed, length, alphabet=ALNUM):
    return "".join(random.Random(seed).choices(alphabet, k=length))


# Built at run time from parts, so secret scanners do not flag this file.
TOKENS = {
    "openai": "sk" + "-proj-" + filler("openai", 48),
    "anthropic": "sk" + "-ant-api03-" + filler("anthropic", 93),
    "langfuse": "pk" + "-lf-" + filler("langfuse", 36),
    "stripe": "rk" + "_live_" + filler("stripe", 24),
    "github": "gh" + "p_" + filler("github", 36),
    "github-fine-grained": "github" + "_pat_" + filler("gh-fg", 82),
    "gitlab": "glpat" + "-" + filler("gitlab", 20),
    "slack": "xo" + "xb-" + filler("slack-id", 11, "0123456789") + "-" + filler("slack", 24),
    "aws": "AK" + "IA" + filler("aws", 16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"),
    "google": "AI" + "za" + filler("google", 35),
    "huggingface": "hf" + "_" + filler("huggingface", 34),
    "jwt": "ey" + "JhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxIn0." + filler("jwt", 43),
    "groq": "gsk" + "_" + filler("groq", 52),
    "npm": "npm" + "_" + filler("npm", 36),
    "replicate": "r8" + "_" + filler("replicate", 37),
    "perplexity": "pplx" + "-" + filler("perplexity", 48),
    "xai": "xai" + "-" + filler("xai", 80),
    "telegram": filler("telegram-id", 10, "123456789") + ":" + "AA" + filler("telegram", 33),
    "sendgrid": "SG" + "." + filler("sendgrid-a", 22) + "." + filler("sendgrid-b", 43),
    "pypi": "pypi" + "-AgE" + filler("pypi", 50),
    "digitalocean": "dop" + "_v1_" + filler("digitalocean", 64, HEX),
    "tailscale": "tskey" + "-auth-" + filler("tailscale", 30),
    "brave": "BS" + "A" + filler("brave", 28),
    "private-key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----\n" + filler("pem", 70) + "\n"
                   + "-----END " + "OPENSSH PRIVATE KEY-----",
}
CONTEXTS = [
    "INFO upstream credential {} accepted",
    '{{"provider": "fixture", "value": "{}"}}',
    'curl -H "X-Provider: {}" https://api.example',
]
OWN_SECRETS = {
    "DASHBOARD_API_KEY": filler("dashboard", 64, HEX),
    "N8N_PASS": filler("n8n", 24),
    "SEARXNG_SECRET": filler("searxng", 48, HEX),
}
ORDINARY = [
    "ghcr.io/osmantic/ods@sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "source 9f3b6ecd25db3ab51bef4091473d88ee5824bc3b",
    "request 550e8400-e29b-41d4-a716-446655440000",
    "model qwen3.5-9b-q4_k_m.gguf loaded in 4.2s",
    "task-scheduler started; disk-usage-monitor idle",
]


def leaked(secret, text):
    """True when any WINDOW-character run of the secret survives."""
    return any(secret[i:i + WINDOW] in text for i in range(len(secret) - WINDOW + 1))


def secret_part(value):
    return value.splitlines()[1] if "\n" in value else value[8:]


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("support-bundle")
    scripts = tmp_path / "install/scripts"
    scripts.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1] / "scripts/ods-support-bundle.sh"
    script = scripts / source.name
    shutil.copyfile(source, script)
    env_lines = [f"{key}={value}" for key, value in OWN_SECRETS.items()] + ["WEBUI_AUTH=true"]
    (scripts.parent / ".env").write_text("\n".join(env_lines) + "\n")
    log_lines = [context.format(value) for value in TOKENS.values() for context in CONTEXTS]
    log_lines += [f"connected with {value}" for value in OWN_SECRETS.values()]
    log_lines += ORDINARY
    log = tmp_path / "log"
    log.write_text("\n".join(log_lines) + "\n")
    docker = tmp_path / "docker-fixture"
    docker.write_text('''#!/usr/bin/env bash
set -eu
case "$1" in
  version|info|compose) printf 'fixture docker\\n' ;;
  ps) printf 'ods-fixture\\n' ;;
  logs) cat "$ODS_TEST_TOKEN_LOG" ;;
  *) exit 2 ;;
esac
''')
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(script), "--output", str(tmp_path / "bundles"), "--json"],
        env=dict(os.environ, ODS_SUPPORT_BUNDLE_DOCKER=str(docker), ODS_TEST_TOKEN_LOG=str(log)),
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    with tarfile.open(receipt["archive"]) as archive:
        files = {member.name: archive.extractfile(member).read().decode()
                 for member in archive.getmembers() if member.isfile()}
    return receipt, files


@pytest.mark.parametrize("name", sorted(TOKENS))
def test_recognizable_credentials_are_redacted_everywhere(bundle, name):
    _receipt, files = bundle
    for path, text in files.items():
        assert not leaked(secret_part(TOKENS[name]), text), path


def test_installation_secrets_are_redacted_wherever_echoed(bundle):
    _receipt, files = bundle
    for path, text in files.items():
        for value in OWN_SECRETS.values():
            assert not leaked(value, text), path


def test_ordinary_diagnostics_survive(bundle):
    _receipt, files = bundle
    log = next(text for path, text in files.items() if path.endswith("/logs/ods-fixture.log"))
    for line in ORDINARY:
        assert line in log


def test_bundle_and_archive_are_owner_only(bundle):
    receipt, files = bundle
    manifest = next(text for path, text in files.items() if path.endswith("/manifest.json"))
    assert json.loads(manifest)["redaction_version"] == "2"
    directory = Path(receipt["bundle_dir"])
    for path in [Path(receipt["archive"]), directory, *directory.rglob("*")]:
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, path
