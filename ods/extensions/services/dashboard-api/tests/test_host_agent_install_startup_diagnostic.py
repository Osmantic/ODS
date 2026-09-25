"""A container that does not stay running says why in the install error.

On tower1 (2026-09-25) the library Shlink install ended with only
"Container did not reach running state within 90s (state=restarting)"; the
reason (its start script rejecting settings that were not 64-hex) was only in
`docker logs`. The install and enable-retry errors now carry the exit code,
the last failed health check and the last container log lines, with
configured credentials and the extension's declared secret settings
redacted before the output is bounded.
"""

import json
import types

import pytest

from test_host_agent_install_rollback import _mod, host  # noqa: F401  (fixture)

DB_PASSWORD = "dbsecretvalue123"
SETUP_CODE = "setupcode987654"  # Named like no credential: redacted as a declared secret.
SIGNING_VALUE = "signingvalue-4711"  # Not declared secret, not credential-named: interpolated by Compose.
PEPPER_VALUE = "pepper-value-0815"  # Another extension's ..._KEY setting: redacted by its name.
SHLINK_COMPOSE = ("services:\n  shlink:\n    image: ods/shlink:5.1.6-local-v1\n    environment:\n"
                  "      SIGNING: ${SHLINK_SIGNING}\n      PORT: ${SHLINK_PORT:-11121}\n")
MANIFEST = {"schema_version": "ods.services.v1", "service": {
    "id": "shlink", "port": 8080, "container_name": "ods-shlink", "startup_timeout": 3,
    "env_vars": [{"key": "SHLINK_DB_PASSWORD", "required": True, "secret": True},
                 {"key": "SHLINK_SETUP_CODE", "required": True, "secret": True},
                 {"key": "SHLINK_PUBLIC_NAME", "required": False, "secret": False}]}}
LOG = (
    "Starting Shlink\n"
    f"connecting as shlink with {DB_PASSWORD}\n"
    f"DB_URL=postgres://shlink:{DB_PASSWORD}@shlink-db/shlink\n"
    f"setup code {SETUP_CODE} accepted\n"
    f"signing with {SIGNING_VALUE}; pepper {PEPPER_VALUE}; listening on 11121; debug=true\n"
    "Shlink requires 64-hex database and API secrets\n")


@pytest.fixture
def failing(host, monkeypatch):  # noqa: F811
    (host.install_dir / ".env").write_text(
        f'SHLINK_DB_PASSWORD="{DB_PASSWORD}"\nSHLINK_SETUP_CODE="{SETUP_CODE}"\nSHLINK_PUBLIC_NAME=links\n'
        f'SHLINK_SIGNING={SIGNING_VALUE}\nSHLINK_PORT=11121\nOTHER_HASH_KEY={PEPPER_VALUE}\nOTHER_FLAG_KEY=true\n',
        encoding="utf-8")
    host.extension("shlink", SHLINK_COMPOSE, manifest=MANIFEST)
    host.docker()
    compose = _mod.subprocess.run
    clock = [0.0]
    monkeypatch.setattr(_mod.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(_mod.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    state = {"Status": "restarting", "ExitCode": 1, "Error": "",
             "Health": {"Status": "unhealthy", "Log": [
                 {"ExitCode": 1, "Output": "wget: can't connect to remote host"}]}}
    calls = []

    def run(command, **kwargs):
        calls.append((list(command), kwargs))
        if command[:3] == ["docker", "inspect", "--format"]:
            if command[3] == "{{json .State}}":
                return types.SimpleNamespace(returncode=0, stdout=json.dumps(host.state), stderr="")
            return types.SimpleNamespace(returncode=0, stdout="restarting|", stderr="")
        if command[:2] == ["docker", "logs"]:
            return types.SimpleNamespace(returncode=0, stdout=host.log, stderr=None)
        if command[:2] == ["docker", "compose"] and command[-3:] == ["config", "--format", "json"]:
            return types.SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                {"services": {"shlink": {"image": "ods/shlink:5.1.6-local-v1"}}}))
        return compose(command, **kwargs)
    monkeypatch.setattr(_mod.subprocess, "run", run)
    host.state, host.log, host.calls = state, LOG, calls
    return host


def _error(host):  # noqa: F811
    record = host.progress("shlink")
    assert record["status"] == "error"
    return record["error"]


def test_install_error_names_the_containers_own_reason_without_secrets(failing):
    failing.install("shlink")

    error = _error(failing)
    assert error.splitlines()[0] == "Container did not reach running state within 3s (state=restarting)"
    assert "Untrusted container output, credentials redacted:" in error
    assert "Last exit code: 1." in error
    assert "Last health check (unhealthy):\nwget: can't connect to remote host" in error
    assert error.rstrip().endswith("Shlink requires 64-hex database and API secrets")
    assert DB_PASSWORD not in error and SETUP_CODE not in error
    # Every value the extension's Compose interpolates, and ..._KEY settings
    # elsewhere, are redacted too; port-sized numbers and flags stay readable.
    assert SIGNING_VALUE not in error and PEPPER_VALUE not in error
    assert "listening on 11121; debug=true" in error
    assert "postgres://[REDACTED]@shlink-db" in error or "DB_URL=[REDACTED]" in error
    logs = [command for command, _ in failing.calls if command[:2] == ["docker", "logs"]]
    assert logs == [["docker", "logs", "--tail", "12", "ods-shlink"]]
    # The container's environment is never read.
    assert not any("Config" in " ".join(command) or "Env" in " ".join(command) for command, _ in failing.calls)


def test_enable_retry_error_carries_the_same_diagnostic(failing, monkeypatch):
    monkeypatch.setattr(_mod, "docker_compose_action", lambda sid, action: (True, ""))

    _mod._enable_retry_work("shlink")

    error = _error(failing)
    assert error.startswith("Container did not reach running state within 3s (state=restarting)")
    assert "Shlink requires 64-hex database and API secrets" in error
    assert DB_PASSWORD not in error and SETUP_CODE not in error


def test_long_output_is_bounded_to_the_most_recent_whole_lines(failing):
    failing.log = "".join(f"line {index:03d} " + "x" * 400 + "\n" for index in range(40)) + "final reason\n"
    failing.state = {"Status": "restarting", "ExitCode": 3}

    failing.install("shlink")

    error = _error(failing)
    diagnostic = error.split("Last container log lines:\n", 1)[1]
    assert len(diagnostic) <= _mod.STARTUP_DIAGNOSTIC_LIMIT
    assert diagnostic.endswith("final reason")
    assert all(len(line) <= _mod.BUILD_ERROR_LINE_LIMIT for line in diagnostic.splitlines())
    assert "line 000" not in diagnostic
    assert "Last health check" not in error


def test_nothing_is_disclosed_when_credentials_cannot_be_checked(failing, monkeypatch):
    def unreadable(service_def, ext_dir=None):
        raise OSError("unreadable .env")
    monkeypatch.setattr(_mod, "_declared_secret_values", unreadable)

    failing.install("shlink")

    error = _error(failing)
    assert "Container output withheld: credential redaction could not be completed." in error
    assert "Shlink requires" not in error and DB_PASSWORD not in error


def test_no_output_keeps_the_existing_message(failing):
    failing.log = ""
    failing.state = {"Status": "restarting", "ExitCode": 0}

    failing.install("shlink")

    assert _error(failing) == "Container did not reach running state within 3s (state=restarting)"


def test_a_diagnostic_failure_never_leaves_the_install_without_a_terminal_state(failing, monkeypatch):
    def broken(service_def, ext_dir=None):
        raise TypeError("unexpected declaration shape")
    monkeypatch.setattr(_mod, "_declared_secret_values", broken)

    failing.install("shlink")

    assert _error(failing) == ("Container did not reach running state within 3s (state=restarting)\n"
                               "Container diagnostics unavailable.")


def test_malformed_env_vars_do_not_break_the_diagnostic(failing):
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["service"]["env_vars"] = 42
    (failing.users / "shlink" / "manifest.yaml").write_text(json.dumps(manifest), encoding="utf-8")

    failing.install("shlink")

    error = _error(failing)
    assert "Shlink requires 64-hex database and API secrets" in error
    # Still redacted: by credential name and as values the Compose file interpolates.
    assert DB_PASSWORD not in error and SIGNING_VALUE not in error
