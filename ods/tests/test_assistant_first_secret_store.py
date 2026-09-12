"""Linux custody and real host-HTTP tests for Assistant First secrets."""

import http.client
import importlib.util
import json
import os
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Assistant First secret custody is Linux-first"
)

BIN_DIR = Path(__file__).resolve().parents[1] / "bin"
_store_spec = importlib.util.spec_from_file_location(
    "assistant_first_secret_store", BIN_DIR / "assistant_first_secret_store.py"
)
_store_module = importlib.util.module_from_spec(_store_spec)
sys.modules[_store_spec.name] = _store_module
_store_spec.loader.exec_module(_store_module)
AssistantFirstSecretStore = _store_module.AssistantFirstSecretStore
DELETE_REQUEST_SCHEMA = _store_module.DELETE_REQUEST_SCHEMA
STAGE_REQUEST_SCHEMA = _store_module.STAGE_REQUEST_SCHEMA
STATUS_REQUEST_SCHEMA = _store_module.STATUS_REQUEST_SCHEMA
SecretStoreError = _store_module.SecretStoreError


TXN = "txn-" + "1" * 24
PLAN = "2" * 64
SCHEMA_HASH = "3" * 64
IDEMPOTENCY = "4" * 64
SECRET_VALUE = "do-not-echo-super-secret"


def stage_request(**changes):
    value = {
        "schema": STAGE_REQUEST_SCHEMA,
        "transactionId": TXN,
        "planHash": PLAN,
        "schemaHash": SCHEMA_HASH,
        "idempotencyKey": IDEMPOTENCY,
        "secretValues": {"EXAMPLE_API_KEY": SECRET_VALUE},
    }
    value.update(changes)
    return value


def bound_request(schema, reference, **changes):
    value = {
        "schema": schema,
        "transactionId": TXN,
        "planHash": PLAN,
        "schemaHash": SCHEMA_HASH,
        "reference": reference,
    }
    value.update(changes)
    return value


def error_code(call):
    with pytest.raises(SecretStoreError) as raised:
        call()
    assert SECRET_VALUE not in str(raised.value)
    assert SECRET_VALUE not in repr(raised.value)
    return raised.value.code


def test_stage_status_delete_round_trip_is_redacted_and_restrictive(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    response_text = json.dumps(staged)
    assert staged["configured"] is True
    assert staged["presentSecretKeys"] == ["EXAMPLE_API_KEY"]
    assert staged["duplicate"] is False
    assert SECRET_VALUE not in response_text
    assert "secretValues" not in response_text

    secret_dir = tmp_path / "assistant-first" / "secrets"
    record_path = secret_dir / f"{TXN}.json"
    assert stat.S_IMODE(secret_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o600
    assert record_path.stat().st_nlink == 1

    status = store.status(bound_request(STATUS_REQUEST_SCHEMA, staged["reference"]))
    assert status == {key: value for key, value in staged.items() if key != "duplicate"}
    assert SECRET_VALUE not in json.dumps(status)

    deleted = store.delete(bound_request(DELETE_REQUEST_SCHEMA, staged["reference"]))
    assert deleted["deleted"] is True
    assert deleted["configured"] is False
    assert not record_path.exists()
    repeated = store.delete(bound_request(DELETE_REQUEST_SCHEMA, staged["reference"]))
    assert repeated["deleted"] is False


def test_idempotent_stage_reuses_reference_and_conflict_is_value_free(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    first = store.stage(stage_request())
    second = store.stage(stage_request())
    assert second["duplicate"] is True
    assert second["reference"] == first["reference"]
    code = error_code(
        lambda: store.stage(
            stage_request(secretValues={"EXAMPLE_API_KEY": "different-secret"})
        )
    )
    assert code == "secret-idempotency-conflict"


def test_new_idempotency_key_atomically_replaces_reference(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    first = store.stage(stage_request())
    second = store.stage(
        stage_request(
            idempotencyKey="5" * 64,
            secretValues={"EXAMPLE_API_KEY": "replacement-secret"},
        )
    )
    assert second["reference"] != first["reference"]
    assert (
        error_code(
            lambda: store.status(
                bound_request(STATUS_REQUEST_SCHEMA, first["reference"])
            )
        )
        == "secret-reference-mismatch"
    )
    assert store.status(bound_request(STATUS_REQUEST_SCHEMA, second["reference"]))[
        "configured"
    ]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("transactionId", "txn-" + "a" * 23 + "/", "invalid-transaction-id"),
        ("planHash", "A" * 64, "invalid-plan-hash"),
        ("schemaHash", "z" * 64, "invalid-schema-hash"),
        ("idempotencyKey", True, "invalid-idempotency-key"),
        ("secretValues", {}, "invalid-secret-values"),
        ("secretValues", {"bad-key": "secret"}, "invalid-secret-key"),
        ("secretValues", {"API_KEY": ""}, "invalid-secret-value"),
        ("secretValues", {"API_KEY": None}, "invalid-secret-value"),
        ("secretValues", {"API_KEY": 1.25}, "invalid-secret-value"),
        ("secretValues", {"API_KEY": {"nested": "secret"}}, "invalid-secret-value"),
    ],
)
def test_invalid_stage_is_rejected_before_state_creation(
    tmp_path, field, value, expected
):
    store = AssistantFirstSecretStore(tmp_path)
    assert error_code(lambda: store.stage(stage_request(**{field: value}))) == expected
    assert list(tmp_path.iterdir()) == []


def test_multiline_secret_is_supported_but_never_projected(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    secret = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n"
    result = store.stage(stage_request(secretValues={"SSH_PRIVATE_KEY": secret}))
    assert result["presentSecretKeys"] == ["SSH_PRIVATE_KEY"]
    assert secret not in json.dumps(result)


def test_binding_and_reference_substitution_cannot_read_or_delete(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    wrong_plan = bound_request(
        STATUS_REQUEST_SCHEMA, staged["reference"], planHash="6" * 64
    )
    assert error_code(lambda: store.status(wrong_plan)) == "secret-binding-mismatch"
    wrong_ref = bound_request(DELETE_REQUEST_SCHEMA, "secret-v1-" + "7" * 48)
    assert error_code(lambda: store.delete(wrong_ref)) == "secret-reference-mismatch"
    assert store.status(bound_request(STATUS_REQUEST_SCHEMA, staged["reference"]))[
        "configured"
    ]


def test_symlink_hardlink_and_broad_permissions_fail_closed(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    record = tmp_path / "assistant-first" / "secrets" / f"{TXN}.json"
    outside = tmp_path / "outside"
    outside.write_text("untouched", encoding="utf-8")

    record.unlink()
    record.symlink_to(outside)
    assert error_code(lambda: store.stage(stage_request())) == "secret-record-integrity"
    assert outside.read_text(encoding="utf-8") == "untouched"

    record.unlink()
    replaced = store.stage(stage_request(idempotencyKey="8" * 64))
    hardlink = tmp_path / "hardlink"
    os.link(record, hardlink)
    assert (
        error_code(
            lambda: store.status(
                bound_request(STATUS_REQUEST_SCHEMA, replaced["reference"])
            )
        )
        == "secret-record-integrity"
    )
    hardlink.unlink()
    record.chmod(0o644)
    assert (
        error_code(
            lambda: store.status(
                bound_request(STATUS_REQUEST_SCHEMA, replaced["reference"])
            )
        )
        == "secret-record-integrity"
    )
    assert staged["reference"] != replaced["reference"]


def test_symlinked_data_root_and_writable_root_are_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    assert (
        error_code(lambda: AssistantFirstSecretStore(linked).stage(stage_request()))
        == "secret-store-root-integrity"
    )

    real.chmod(0o777)
    assert (
        error_code(lambda: AssistantFirstSecretStore(real).stage(stage_request()))
        == "secret-store-root-permissions"
    )


def test_corrupt_record_is_not_replaced_or_echoed(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    record = tmp_path / "assistant-first" / "secrets" / f"{TXN}.json"
    corrupt = b'{"secretValues":{"API_KEY":"do-not-echo-corrupt"}}'
    record.write_bytes(corrupt)
    record.chmod(0o600)
    assert (
        error_code(
            lambda: store.status(
                bound_request(STATUS_REQUEST_SCHEMA, staged["reference"])
            )
        )
        == "secret-record-integrity"
    )
    assert record.read_bytes() == corrupt


def test_interrupted_temporary_file_is_recovered_under_lock(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    secret_dir = tmp_path / "assistant-first" / "secrets"
    orphan = secret_dir / (f".{TXN}.json." + "a" * 24 + ".tmp")
    orphan.write_text("orphan-secret", encoding="utf-8")
    orphan.chmod(0o600)
    assert store.status(bound_request(STATUS_REQUEST_SCHEMA, staged["reference"]))[
        "configured"
    ]
    assert not orphan.exists()


def test_unsafe_temporary_entry_blocks_recovery_without_following(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    secret_dir = tmp_path / "assistant-first" / "secrets"
    outside = tmp_path / "outside-temporary"
    outside.write_text("untouched", encoding="utf-8")
    orphan = secret_dir / (f".{TXN}.json." + "b" * 24 + ".tmp")
    orphan.symlink_to(outside)
    assert (
        error_code(
            lambda: store.status(
                bound_request(STATUS_REQUEST_SCHEMA, staged["reference"])
            )
        )
        == "secret-temporary-integrity"
    )
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_concurrent_same_request_has_one_record_and_one_reference(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _item: store.stage(stage_request()), range(16)))
    assert len({result["reference"] for result in results}) == 1
    assert sum(result["duplicate"] is False for result in results) == 1
    assert sum(result["duplicate"] is True for result in results) == 15


@pytest.fixture(scope="module")
def host_server():
    path = BIN_DIR / "ods-host-agent.py"
    spec = importlib.util.spec_from_file_location("_assistant_secret_host_agent", path)
    agent = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = agent
    spec.loader.exec_module(agent)
    agent.AGENT_API_KEY = "synthetic-secret-host-key"
    agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    listener = ThreadingHTTPServer(("127.0.0.1", 0), agent.AgentHandler)
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        yield agent, listener
    finally:
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
        sys.modules.pop(spec.name, None)


@pytest.fixture
def host_request(host_server, tmp_path):
    agent, listener = host_server
    agent.DATA_DIR = tmp_path

    def call(path, body=None, *, raw=None, token="synthetic-secret-host-key"):
        connection = http.client.HTTPConnection(*listener.server_address, timeout=5)
        try:
            payload = raw if raw is not None else json.dumps(body).encode("utf-8")
            connection.request(
                "POST",
                path,
                body=payload,
                headers={"Authorization": "Bearer " + token},
            )
            response = connection.getresponse()
            document = json.loads(response.read())
            if "?" not in path:
                assert "no-store" in response.getheader("Cache-Control", "")
            return response.status, document
        finally:
            connection.close()

    return call


def test_real_host_http_round_trip_never_echoes_secret(host_request):
    status, staged = host_request("/v1/assistant-first/secrets/stage", stage_request())
    assert status == 200
    assert SECRET_VALUE not in json.dumps(staged)
    reference = staged["reference"]
    status, present = host_request(
        "/v1/assistant-first/secrets/status",
        bound_request(STATUS_REQUEST_SCHEMA, reference),
    )
    assert status == 200 and present["configured"] is True
    status, deleted = host_request(
        "/v1/assistant-first/secrets/delete",
        bound_request(DELETE_REQUEST_SCHEMA, reference),
    )
    assert status == 200 and deleted["deleted"] is True
    assert SECRET_VALUE not in json.dumps([staged, present, deleted])


def test_host_auth_and_bad_json_do_not_create_secret_state(host_request, tmp_path):
    assert (
        host_request(
            "/v1/assistant-first/secrets/stage",
            stage_request(),
            token="wrong",
        )[0]
        == 403
    )
    raw = (
        b'{"schema":"' + STAGE_REQUEST_SCHEMA.encode() + b'",'
        b'"schema":"duplicate","secretValues":{"API_KEY":"do-not-echo"}}'
    )
    status, result = host_request("/v1/assistant-first/secrets/stage", raw=raw)
    assert status == 400
    assert "do-not-echo" not in json.dumps(result)
    assert list(tmp_path.iterdir()) == []


def test_host_feature_gate_fails_closed_before_secret_state(
    host_server, host_request, tmp_path
):
    agent, _listener = host_server
    agent.ASSISTANT_TRANSACTIONS_ENABLED = False
    try:
        status, result = host_request(
            "/v1/assistant-first/secrets/stage", stage_request()
        )
    finally:
        agent.ASSISTANT_TRANSACTIONS_ENABLED = True
    assert status == 404
    assert result == {"error": {"code": "not-found"}}
    assert list(tmp_path.iterdir()) == []


def test_host_rejects_float_oversize_and_query_routes(host_request, tmp_path):
    status, result = host_request(
        "/v1/assistant-first/secrets/stage", raw=b'{"secretValues":{"API_KEY":1.5}}'
    )
    assert status == 400 and "1.5" not in json.dumps(result)
    assert (
        host_request("/v1/assistant-first/secrets/stage", raw=b"x" * (64 * 1024 + 1))[0]
        == 413
    )
    assert (
        host_request(
            "/v1/assistant-first/secrets/stage?transactionId=" + TXN,
            stage_request(),
        )[0]
        == 404
    )
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "request_line",
    [
        "POST /v1/assistant-first/secrets/stage?apiKey=do-not-log-me HTTP/1.1",
        "GET /v1/assistant-first/secrets/stage?apiKey=do-not-log-me",
        "/v1/assistant-first/secrets/stage?apiKey=do-not-log-me",
    ],
)
def test_host_request_logging_redacts_query_data(host_server, request_line):
    agent, _listener = host_server

    redacted = agent._redact_http_request_target(request_line)

    assert "?[REDACTED]" in redacted
    assert "do-not-log-me" not in redacted


def test_host_request_logging_redacts_dynamic_format_text(host_server):
    agent, _listener = host_server

    redacted = agent._redact_http_request_target(
        "request /path?token=do-not-log-me failed: %s"
    )

    assert redacted == "request /path?[REDACTED] failed: %s"
