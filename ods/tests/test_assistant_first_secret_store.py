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
LEGACY_STAGE_REQUEST_SCHEMA = _store_module.LEGACY_STAGE_REQUEST_SCHEMA
STAGE_REQUEST_SCHEMA = _store_module.STAGE_REQUEST_SCHEMA
STATUS_REQUEST_SCHEMA = _store_module.STATUS_REQUEST_SCHEMA
USE_REQUEST_SCHEMA = _store_module.USE_REQUEST_SCHEMA
USE_STATUS_SCHEMA = _store_module.USE_STATUS_SCHEMA
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
        "generatedSecretKeys": [],
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


def use_request(reference_value, **changes):
    value = bound_request(
        USE_REQUEST_SCHEMA,
        reference_value,
        expectedSecretKeys=["EXAMPLE_API_KEY"],
    )
    value.update(changes)
    return value


def error_code(call):
    with pytest.raises(SecretStoreError) as raised:
        call()
    assert SECRET_VALUE not in str(raised.value)
    assert SECRET_VALUE not in repr(raised.value)
    return raised.value.code


def traceback_locals_text(error):
    frames = []
    traceback = error.__traceback__
    while traceback is not None:
        if Path(traceback.tb_frame.f_code.co_filename).resolve() == (
            BIN_DIR / "assistant_first_secret_store.py"
        ).resolve():
            frames.append(repr(traceback.tb_frame.f_locals))
        traceback = traceback.tb_next
    return "\n".join(frames)


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


def test_generated_secret_is_host_owned_redacted_and_stable_on_replay(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    request = stage_request(
        secretValues={}, generatedSecretKeys=["SEARXNG_SECRET"]
    )
    first = store.stage(request)
    record_path = tmp_path / "assistant-first" / "secrets" / f"{TXN}.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    generated = record["secretValues"]["SEARXNG_SECRET"]

    assert record["schema"] == _store_module.RECORD_SCHEMA
    assert record["generatedSecretKeys"] == ["SEARXNG_SECRET"]
    assert len(generated) == 64
    assert all(character in "0123456789abcdef" for character in generated)
    assert generated not in json.dumps(first)
    assert first["presentSecretKeys"] == ["SEARXNG_SECRET"]

    second = store.stage(request)
    replayed = json.loads(record_path.read_text(encoding="utf-8"))
    assert second["duplicate"] is True
    assert second["reference"] == first["reference"]
    assert replayed["secretValues"]["SEARXNG_SECRET"] == generated


def test_generated_secret_request_rejects_overlap_or_rebinding(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    overlap = stage_request(
        secretValues={"SEARXNG_SECRET": SECRET_VALUE},
        generatedSecretKeys=["SEARXNG_SECRET"],
    )
    assert error_code(lambda: store.stage(overlap)) == "generated-secret-key-overlap"

    original = stage_request(secretValues={}, generatedSecretKeys=["SEARXNG_SECRET"])
    store.stage(original)
    changed = stage_request(secretValues={}, generatedSecretKeys=["OTHER_SECRET"])
    assert error_code(lambda: store.stage(changed)) == "secret-idempotency-conflict"


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


def test_invoke_with_secrets_is_bound_immutable_and_value_free(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    observed = []
    retained = []

    def consume(values):
        observed.append(values["EXAMPLE_API_KEY"])
        retained.append(values)
        with pytest.raises(TypeError):
            values["EXAMPLE_API_KEY"] = "replacement"
        return {"doNotProject": SECRET_VALUE}

    result = store.invoke_with_secrets(use_request(staged["reference"]), consume)

    assert observed == [SECRET_VALUE]
    assert result == {
        "schema": USE_STATUS_SCHEMA,
        "transactionId": TXN,
        "planHash": PLAN,
        "schemaHash": SCHEMA_HASH,
        "reference": staged["reference"],
        "configured": True,
        "presentSecretKeys": ["EXAMPLE_API_KEY"],
        "invoked": True,
    }
    assert SECRET_VALUE not in json.dumps(result)
    assert retained[0]["EXAMPLE_API_KEY"] is None


@pytest.mark.parametrize(
    "raised_error",
    [RuntimeError(f"failed: {SECRET_VALUE}"), KeyboardInterrupt(SECRET_VALUE)],
)
def test_invoke_with_secrets_contains_callback_exception_chain(
    tmp_path, raised_error
):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())

    def consume(_values):
        raise raised_error

    with pytest.raises(SecretStoreError) as raised:
        store.invoke_with_secrets(use_request(staged["reference"]), consume)

    assert raised.value.code == "secret-consumer-failed"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert SECRET_VALUE not in str(raised.value)
    assert SECRET_VALUE not in repr(raised.value)
    assert SECRET_VALUE not in traceback_locals_text(raised.value)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"planHash": "6" * 64}, "secret-binding-mismatch"),
        ({"reference": "secret-v1-" + "7" * 48}, "secret-reference-mismatch"),
        ({"expectedSecretKeys": ["OTHER_KEY"]}, "secret-key-set-mismatch"),
        ({"expectedSecretKeys": []}, "invalid-expected-secret-keys"),
        (
            {"expectedSecretKeys": ["EXAMPLE_API_KEY", "EXAMPLE_API_KEY"]},
            "invalid-expected-secret-keys",
        ),
        ({"expectedSecretKeys": ["bad-key"]}, "invalid-expected-secret-key"),
    ],
)
def test_invoke_with_secrets_rejects_binding_reference_and_keys(
    tmp_path, changes, expected
):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    called = []

    assert (
        error_code(
            lambda: store.invoke_with_secrets(
                use_request(staged["reference"], **changes),
                lambda _values: called.append(True),
            )
        )
        == expected
    )
    assert called == []


def test_invoke_with_secrets_rejects_shape_schema_and_consumer_before_state(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    reference = "secret-v1-" + "0" * 48
    invalid_shape = use_request(reference, extra=True)
    invalid_schema = use_request(reference, schema=STATUS_REQUEST_SCHEMA)

    assert (
        error_code(lambda: store.invoke_with_secrets(invalid_shape, lambda _v: None))
        == "invalid-secret-use-request"
    )
    assert (
        error_code(lambda: store.invoke_with_secrets(invalid_schema, lambda _v: None))
        == "invalid-secret-use-schema"
    )
    assert (
        error_code(lambda: store.invoke_with_secrets(use_request(reference), None))
        == "invalid-secret-consumer"
    )
    assert list(tmp_path.iterdir()) == []


def test_invoke_with_secrets_holds_store_lock_for_consumer(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    entered = threading.Event()
    release = threading.Event()
    status_done = threading.Event()

    def consume(_values):
        entered.set()
        assert release.wait(timeout=5)

    def invoke():
        store.invoke_with_secrets(use_request(staged["reference"]), consume)

    def read_status():
        store.status(bound_request(STATUS_REQUEST_SCHEMA, staged["reference"]))
        status_done.set()

    invoke_thread = threading.Thread(target=invoke)
    invoke_thread.start()
    assert entered.wait(timeout=5)
    status_thread = threading.Thread(target=read_status)
    status_thread.start()
    assert not status_done.wait(timeout=0.2)
    release.set()
    invoke_thread.join(timeout=5)
    status_thread.join(timeout=5)
    assert not invoke_thread.is_alive()
    assert not status_thread.is_alive()
    assert status_done.is_set()


def test_invoke_with_secrets_reentrant_store_use_fails_without_deadlock(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())

    def consume(_values):
        store.status(bound_request(STATUS_REQUEST_SCHEMA, staged["reference"]))

    with pytest.raises(SecretStoreError) as raised:
        store.invoke_with_secrets(use_request(staged["reference"]), consume)

    assert raised.value.code == "secret-consumer-failed"
    assert SECRET_VALUE not in traceback_locals_text(raised.value)


def test_store_lock_contention_times_out_fail_closed(tmp_path, monkeypatch):
    store = AssistantFirstSecretStore(tmp_path)
    staged = store.stage(stage_request())
    original_flock = _store_module.fcntl.flock

    def blocked_flock(descriptor, operation):
        if operation & _store_module.fcntl.LOCK_NB:
            raise BlockingIOError()
        return original_flock(descriptor, operation)

    monkeypatch.setattr(_store_module, "_LOCK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(_store_module, "_LOCK_POLL_SECONDS", 0.0)
    monkeypatch.setattr(_store_module.fcntl, "flock", blocked_flock)

    assert (
        error_code(
            lambda: store.status(
                bound_request(STATUS_REQUEST_SCHEMA, staged["reference"])
            )
        )
        == "secret-store-lock-timeout"
    )


def test_invoke_with_secrets_supports_validated_legacy_record(tmp_path):
    store = AssistantFirstSecretStore(tmp_path)
    request = stage_request(schema=LEGACY_STAGE_REQUEST_SCHEMA)
    request.pop("generatedSecretKeys")
    staged = store.stage(request)
    observed = []

    result = store.invoke_with_secrets(
        use_request(staged["reference"]),
        lambda values: observed.append(values["EXAMPLE_API_KEY"]),
    )

    assert result["invoked"] is True
    assert observed == [SECRET_VALUE]


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
