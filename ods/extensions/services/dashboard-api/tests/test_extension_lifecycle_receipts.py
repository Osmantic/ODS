"""Tests for the inert host-owned lifecycle receipt store."""

from __future__ import annotations

import ast
import hashlib
import json
import multiprocessing
import os
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from queue import Empty
from types import SimpleNamespace

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_lifecycle_receipts as receipts  # noqa: E402


TRANSACTION_ID = "txn-" + "1" * 24
OTHER_TRANSACTION_ID = "txn-" + "2" * 24
PLAN_HASH = "3" * 64
OTHER_PLAN_HASH = "4" * 64
REQUEST_HASH = "5" * 64
EVIDENCE_HASH = "6" * 64
SERVICE_IDS = ["documents", "voice"]


def _store(tmp_path: Path):
    return receipts.LifecycleReceiptStore(tmp_path / "receipts")


def _paths(store, transaction_id=TRANSACTION_ID, operation_key="stage"):
    started_name, terminal_name = receipts._derive_receipt_file_names(
        transaction_id, operation_key
    )
    return Path(store.root) / started_name, Path(store.root) / terminal_name


def _assert_code(code: str, call, *args, **kwargs):
    with pytest.raises(receipts.LifecycleReceiptError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code


def _begin_process(root: str, plan_hash: str, gate, output) -> None:
    try:
        gate.wait(10)
        store = receipts.LifecycleReceiptStore(root)
        result = store.begin(
            TRANSACTION_ID, plan_hash, "stage", REQUEST_HASH, SERVICE_IDS
        )
        output.put(("ok", result.event_hash))
    except receipts.LifecycleReceiptError as exc:
        output.put(("error", exc.code))
    except BaseException as exc:  # pragma: no cover - diagnostic boundary
        output.put(("unexpected", type(exc).__name__, str(exc)))


def _finish_process(root: str, gate, output) -> None:
    try:
        gate.wait(10)
        store = receipts.LifecycleReceiptStore(root)
        result = store.finish(
            TRANSACTION_ID,
            PLAN_HASH,
            "stage",
            REQUEST_HASH,
            SERVICE_IDS,
            "completed",
            EVIDENCE_HASH,
        )
        output.put(("ok", result.event_hash))
    except receipts.LifecycleReceiptError as exc:
        output.put(("error", exc.code))
    except BaseException as exc:  # pragma: no cover - diagnostic boundary
        output.put(("unexpected", type(exc).__name__, str(exc)))


def _begin_same_process(root: str, gate, output) -> None:
    _begin_process(root, PLAN_HASH, gate, output)


def _run_processes(target, args_list):
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    output = context.Queue()
    processes = [
        context.Process(target=target, args=(*args, gate, output))
        for args in args_list
    ]
    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(15)
    try:
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        values = []
        for _ in processes:
            try:
                values.append(output.get(timeout=5))
            except Empty:
                pytest.fail("receipt worker exited without a result")
        return values
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        output.close()
        output.join_thread()


def test_lifecycle_is_canonical_immutable_idempotent_and_chained(tmp_path):
    store = _store(tmp_path)
    assert store.snapshot(TRANSACTION_ID, "stage").state == "absent"

    started = store.begin(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS
    )
    assert store.begin(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS
    ) == started
    assert store.snapshot(TRANSACTION_ID, "stage").state == "started"
    with pytest.raises(FrozenInstanceError):
        started.event_hash = "0" * 64

    terminal = store.finish(
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "completed",
        EVIDENCE_HASH,
    )
    assert terminal.started_event_hash == started.event_hash
    assert store.finish(
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "completed",
        EVIDENCE_HASH,
    ) == terminal
    assert store.begin(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS
    ) == terminal
    snapshot = store.snapshot(TRANSACTION_ID, "stage")
    assert snapshot.state == "completed"
    assert snapshot.started_receipt == started
    assert snapshot.terminal_receipt == terminal

    started_path, terminal_path = _paths(store)
    started_raw = started_path.read_bytes()
    terminal_raw = terminal_path.read_bytes()
    assert started_raw.endswith(b"\n") and not started_raw.endswith(b"\n\n")
    assert terminal_raw.endswith(b"\n") and not terminal_raw.endswith(b"\n\n")
    started_json = json.loads(started_raw)
    terminal_json = json.loads(terminal_raw)
    assert set(started_json) == receipts._STARTED_KEYS
    assert set(terminal_json) == receipts._TERMINAL_KEYS
    for payload in (started_json, terminal_json):
        event_hash = payload.pop("eventHash")
        assert event_hash == hashlib.sha256(
            receipts._canonical_bytes(payload)
        ).hexdigest()
    assert terminal_json["startedEventHash"] == started.event_hash


@pytest.mark.parametrize(
    "operation_key",
    [
        "download-and-verify",
        "stage",
        "backup",
        "configure",
        "verify",
        "restore",
        "release",
        "observe",
        "reserve:documents",
        "apply:documents",
        "compensate:documents",
    ],
)
def test_closed_operation_keys_are_accepted(tmp_path, operation_key):
    result = _store(tmp_path).begin(
        TRANSACTION_ID, PLAN_HASH, operation_key, REQUEST_HASH, SERVICE_IDS
    )
    assert result.operation_key == operation_key


@pytest.mark.parametrize(
    ("argument", "value", "code"),
    [
        ("transaction_id", "bad", "invalid-transaction-id"),
        ("transaction_id", True, "invalid-transaction-id"),
        ("plan_hash", "bad", "invalid-plan-hash"),
        ("request_hash", "A" * 64, "invalid-request-hash"),
        ("operation_key", "install", "invalid-operation-key"),
        ("operation_key", "apply:missing", "invalid-operation-key"),
        ("service_ids", [], "invalid-service-ids"),
        ("service_ids", "documents", "invalid-service-ids"),
        ("service_ids", {"documents"}, "invalid-service-ids"),
        ("service_ids", (item for item in ["documents"]), "invalid-service-ids"),
        ("service_ids", [True], "invalid-service-id"),
        ("service_ids", ["../documents"], "invalid-service-id"),
    ],
)
def test_invalid_begin_inputs_have_stable_codes(tmp_path, argument, value, code):
    values = {
        "transaction_id": TRANSACTION_ID,
        "plan_hash": PLAN_HASH,
        "operation_key": "stage",
        "request_hash": REQUEST_HASH,
        "service_ids": SERVICE_IDS,
    }
    values[argument] = value
    _assert_code(code, _store(tmp_path).begin, **values)


def test_invalid_finish_inputs_precede_durable_access(tmp_path):
    store = _store(tmp_path)
    _assert_code(
        "invalid-outcome",
        store.finish,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "unknown",
        EVIDENCE_HASH,
    )
    _assert_code(
        "invalid-evidence-hash",
        store.finish,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "completed",
        "bad",
    )


def test_service_order_is_preserved_and_reordering_conflicts(tmp_path):
    store = _store(tmp_path)
    first = store.begin(
        TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, ["voice", "documents"]
    )
    assert first.service_ids == ("voice", "documents")
    _assert_code(
        "conflict",
        store.begin,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        ["documents", "voice"],
    )


def test_failed_state_and_finish_without_started_are_explicit(tmp_path):
    store = _store(tmp_path)
    _assert_code(
        "started-receipt-required",
        store.finish,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "failed",
        EVIDENCE_HASH,
    )
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    terminal = store.finish(
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "failed",
        EVIDENCE_HASH,
    )
    assert terminal.outcome == "failed"
    assert store.snapshot(TRANSACTION_ID, "stage").state == "failed"


def test_orphan_terminal_is_rejected_by_snapshot_and_begin(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    store.finish(
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "completed",
        EVIDENCE_HASH,
    )
    started_path, _ = _paths(store)
    started_path.unlink()

    _assert_code("integrity", store.snapshot, TRANSACTION_ID, "stage")
    _assert_code(
        "integrity",
        store.begin,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
    )


def test_durable_receipt_contradiction_is_integrity_not_caller_conflict(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    store.finish(
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "completed",
        EVIDENCE_HASH,
    )
    _, terminal_path = _paths(store)
    payload = json.loads(terminal_path.read_bytes())
    payload["planHash"] = OTHER_PLAN_HASH
    payload_without_hash = {
        key: value for key, value in payload.items() if key != "eventHash"
    }
    payload["eventHash"] = hashlib.sha256(
        receipts._canonical_bytes(payload_without_hash)
    ).hexdigest()
    if os.name == "posix":
        terminal_path.chmod(0o600)
    terminal_path.write_bytes(receipts._canonical_bytes(payload))
    if os.name == "posix":
        terminal_path.chmod(0o444)

    _assert_code("integrity", store.snapshot, TRANSACTION_ID, "stage")
    _assert_code(
        "integrity",
        store.begin,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
    )


def test_consistent_terminal_with_divergent_caller_is_conflict(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    store.finish(
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
        "completed",
        EVIDENCE_HASH,
    )
    _assert_code(
        "conflict",
        store.begin,
        TRANSACTION_ID,
        OTHER_PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
    )


def test_path_content_rebinding_is_rejected_and_reported(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    source, _ = _paths(store)
    rebound, _ = _paths(store, OTHER_TRANSACTION_ID)
    rebound.write_bytes(source.read_bytes())
    if os.name == "posix":
        rebound.chmod(0o444)

    _assert_code("integrity", store.snapshot, OTHER_TRANSACTION_ID, "stage")
    health = store.health()
    assert health["receiptCount"] == 2
    assert health["corruptCount"] == 1


def test_temp_leftover_is_observed_but_never_trusted_or_deleted(tmp_path):
    store = _store(tmp_path)
    temp_path = Path(store.root) / ("tmp-" + "a" * 32 + ".receipt.json")
    temp_path.write_bytes(b"untrusted")

    assert store.snapshot(TRANSACTION_ID, "stage").state == "absent"
    health = store.health()
    assert set(health) == {
        "status",
        "receiptCount",
        "tempCount",
        "corruptCount",
        "scannedEntryCount",
        "truncated",
    }
    assert health["tempCount"] == 1
    assert temp_path.read_bytes() == b"untrusted"


def test_health_scan_is_bounded(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(receipts, "_MAX_HEALTH_ENTRIES", 2)
    for name in ("one", "two", "three"):
        (Path(store.root) / name).write_text(name, encoding="utf-8")

    health = store.health()
    assert health["scannedEntryCount"] == 2
    assert health["truncated"] is True


def test_noncanonical_receipt_and_temp_names_count_as_corrupt(tmp_path):
    store = _store(tmp_path)
    root = Path(store.root)
    (root / "NOTHEX.started.json").write_bytes(b"{}\n")
    (root / "tmp-NOTHEX.receipt.json").write_bytes(b"temp")

    health = store.health()
    assert health["receiptCount"] == 0
    assert health["tempCount"] == 0
    assert health["corruptCount"] == 2


def test_root_symlink_and_wrong_type_are_rejected(tmp_path):
    wrong_type = tmp_path / "file"
    wrong_type.write_text("not a directory", encoding="utf-8")
    _assert_code("invalid-receipt-root", receipts.LifecycleReceiptStore, wrong_type)

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable to this user")
    _assert_code("invalid-receipt-root", receipts.LifecycleReceiptStore, link)


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_wrong_root_mode_is_rejected(tmp_path):
    root = tmp_path / "receipts"
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    _assert_code("invalid-receipt-root", receipts.LifecycleReceiptStore, root)


def test_root_inode_replacement_is_detected(tmp_path):
    store = _store(tmp_path)
    root = Path(store.root)
    retired = tmp_path / "retired"
    root.rename(retired)
    root.mkdir(mode=0o700)
    if os.name == "posix":
        root.chmod(0o700)
    _assert_code("integrity", store.snapshot, TRANSACTION_ID, "stage")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schemaVersion":1,"schemaVersion":1}\n',
        b'{"schemaVersion":1.0}\n',
        b'{"schemaVersion":NaN}\n',
        b"{}\ntrailing",
        b" {}\n",
        b"{}\n\n",
        b"\xff\n",
        b"x" * (receipts.MAX_RECEIPT_BYTES + 1),
    ],
)
def test_strict_parser_rejects_malformed_bytes(raw):
    _assert_code("integrity", receipts.parse_started_and_verify, raw)


def test_strict_parser_rejects_a_valid_envelope_with_wrong_event_hash(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    started_path, _ = _paths(store)
    payload = json.loads(started_path.read_bytes())
    payload["eventHash"] = "f" * 64
    _assert_code(
        "integrity",
        receipts.parse_started_and_verify,
        receipts._canonical_bytes(payload),
    )


def test_corrupt_existing_receipt_is_never_overwritten(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    started_path, _ = _paths(store)
    if os.name == "posix":
        started_path.chmod(0o600)
    started_path.write_bytes(b"{}\n")
    if os.name == "posix":
        started_path.chmod(0o444)
    before = started_path.read_bytes()

    _assert_code(
        "integrity",
        store.begin,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
    )
    assert started_path.read_bytes() == before


def test_symlink_directory_and_extra_hard_link_receipts_are_rejected(
    tmp_path, monkeypatch
):
    store = _store(tmp_path)
    started_name, _ = receipts._derive_receipt_file_names(TRANSACTION_ID, "stage")
    receipt_path = Path(store.root) / started_name
    receipt_path.mkdir()
    _assert_code("integrity", store.snapshot, TRANSACTION_ID, "stage")
    receipt_path.rmdir()

    decoy = tmp_path / "decoy"
    decoy.write_bytes(b"{}\n")
    try:
        receipt_path.symlink_to(decoy)
    except OSError:
        pass
    else:
        _assert_code("integrity", store.snapshot, TRANSACTION_ID, "stage")
        receipt_path.unlink()

    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    extra = tmp_path / "extra-link"
    try:
        os.link(receipt_path, extra)
    except OSError:
        pytest.skip("hard-link metadata test is unsupported on this filesystem")
    monkeypatch.setattr(receipts.time, "sleep", lambda _seconds: None)
    if os.name == "posix":
        _assert_code("integrity", store.snapshot, TRANSACTION_ID, "stage")


def test_link_count_retry_rejects_inode_replacement(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    started_path, _ = _paths(store)
    original_lstat = os.lstat
    actual = original_lstat(started_path)
    observations = iter(
        [
            SimpleNamespace(
                st_mode=actual.st_mode,
                st_size=actual.st_size,
                st_nlink=2,
                st_dev=actual.st_dev,
                st_ino=actual.st_ino,
                st_uid=getattr(actual, "st_uid", 0),
            ),
            SimpleNamespace(
                st_mode=actual.st_mode,
                st_size=actual.st_size,
                st_nlink=1,
                st_dev=actual.st_dev,
                st_ino=actual.st_ino + 1,
                st_uid=getattr(actual, "st_uid", 0),
            ),
        ]
    )

    def changing_lstat(path):
        if os.fspath(path) == os.fspath(started_path):
            return next(observations)
        return original_lstat(path)

    monkeypatch.setattr(receipts.os, "lstat", changing_lstat)
    monkeypatch.setattr(receipts.time, "sleep", lambda _seconds: None)
    _assert_code("integrity", receipts._read_published_receipt, str(started_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode contract")
def test_wrong_published_mode_is_rejected(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    started_path, _ = _paths(store)
    started_path.chmod(0o644)
    _assert_code("integrity", store.snapshot, TRANSACTION_ID, "stage")


def test_hard_link_failure_is_closed_and_leaves_no_receipt(tmp_path, monkeypatch):
    store = _store(tmp_path)

    def fail_link(*_args, **_kwargs):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(receipts.os, "link", fail_link)
    _assert_code(
        "link-unsupported",
        store.begin,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
    )
    assert list(Path(store.root).iterdir()) == []


def test_zero_byte_write_is_closed_and_cleans_temp(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(receipts.os, "write", lambda *_args: 0)
    _assert_code(
        "receipt-io-error",
        store.begin,
        TRANSACTION_ID,
        PLAN_HASH,
        "stage",
        REQUEST_HASH,
        SERVICE_IDS,
    )
    assert list(Path(store.root).iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX publication contract")
def test_cleanup_error_is_not_masked_by_close_error(tmp_path, monkeypatch):
    store = _store(tmp_path)
    real_close = receipts.os.close

    def fail_cleanup(*_args, **_kwargs):
        raise receipts.LifecycleReceiptError(
            "receipt-io-error", "primary cleanup failure"
        )

    def close_then_fail(fd):
        real_close(fd)
        raise OSError("secondary close failure")

    monkeypatch.setattr(receipts, "_unlink_temp_if_ours", fail_cleanup)
    monkeypatch.setattr(receipts.os, "close", close_then_fail)

    with pytest.raises(receipts.LifecycleReceiptError) as caught:
        store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    assert caught.value.code == "receipt-io-error"
    assert "primary cleanup failure" in str(caught.value)


@pytest.mark.skipif(os.name != "posix", reason="POSIX publication contract")
def test_read_error_is_not_masked_by_close_error(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    started_path, _ = _paths(store)
    real_close = receipts.os.close

    def fail_read(*_args, **_kwargs):
        raise receipts.LifecycleIntegrityError("primary read failure")

    def close_then_fail(fd):
        real_close(fd)
        raise OSError("secondary close failure")

    monkeypatch.setattr(receipts.os, "read", fail_read)
    monkeypatch.setattr(receipts.os, "close", close_then_fail)

    with pytest.raises(receipts.LifecycleIntegrityError, match="primary read failure"):
        receipts._read_published_receipt(str(started_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX publication contract")
def test_successful_read_close_error_is_typed(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    started_path, _ = _paths(store)
    real_close = receipts.os.close

    def close_then_fail(fd):
        real_close(fd)
        raise OSError("close failure")

    monkeypatch.setattr(receipts.os, "close", close_then_fail)
    _assert_code(
        "receipt-io-error", receipts._read_published_receipt, str(started_path)
    )


def test_identical_begin_converges_across_processes(tmp_path):
    store = _store(tmp_path)
    root = str(store.root)
    results = _run_processes(
        _begin_same_process,
        [(root,) for _ in range(4)],
    )
    assert {item[0] for item in results} == {"ok"}
    assert len({item[1] for item in results}) == 1
    assert store.snapshot(TRANSACTION_ID, "stage").state == "started"
    assert store.health()["tempCount"] == 0


def test_identical_finish_converges_across_processes(tmp_path):
    store = _store(tmp_path)
    store.begin(TRANSACTION_ID, PLAN_HASH, "stage", REQUEST_HASH, SERVICE_IDS)
    root = str(store.root)
    results = _run_processes(_finish_process, [(root,) for _ in range(4)])
    assert {item[0] for item in results} == {"ok"}
    assert len({item[1] for item in results}) == 1
    assert store.snapshot(TRANSACTION_ID, "stage").state == "completed"
    assert store.health()["tempCount"] == 0


def test_divergent_begin_has_one_process_winner(tmp_path):
    store = _store(tmp_path)
    root = str(store.root)
    results = _run_processes(
        _begin_process,
        [(root, PLAN_HASH), (root, OTHER_PLAN_HASH)],
    )
    assert sorted(item[0] for item in results) == ["error", "ok"]
    assert next(item[1] for item in results if item[0] == "error") == "conflict"
    assert store.health()["tempCount"] == 0


def test_mixed_begin_finish_race_is_safe_and_retryable(tmp_path):
    store = _store(tmp_path)
    root = str(store.root)
    context = multiprocessing.get_context("spawn")
    gate = context.Event()
    begin_output = context.Queue()
    finish_output = context.Queue()
    processes = [
        context.Process(
            target=_begin_same_process,
            args=(root, gate, begin_output),
        ),
        context.Process(
            target=_finish_process,
            args=(root, gate, finish_output),
        ),
    ]
    for process in processes:
        process.start()
    gate.set()
    for process in processes:
        process.join(15)
    try:
        assert all(not process.is_alive() for process in processes)
        assert all(process.exitcode == 0 for process in processes)
        try:
            begin_result = begin_output.get(timeout=5)
            finish_result = finish_output.get(timeout=5)
        except Empty:
            pytest.fail("mixed-race worker exited without a result")
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        for output in (begin_output, finish_output):
            output.close()
            output.join_thread()

    assert begin_result[0] == "ok"
    if finish_result[0] == "ok":
        assert len(finish_result) == 2
        assert len(finish_result[1]) == 64
    else:
        assert finish_result == ("error", "started-receipt-required")
    if finish_result[0] == "error":
        store.finish(
            TRANSACTION_ID,
            PLAN_HASH,
            "stage",
            REQUEST_HASH,
            SERVICE_IDS,
            "completed",
            EVIDENCE_HASH,
        )
    assert store.snapshot(TRANSACTION_ID, "stage").state == "completed"
    assert store.health()["tempCount"] == 0


def test_module_is_stdlib_only_and_has_only_the_guarded_host_importer():
    module_path = BIN_DIR / "extension_lifecycle_receipts.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    assert imports <= {
        "__future__",
        "dataclasses",
        "hashlib",
        "json",
        "os",
        "re",
        "stat",
        "time",
        "typing",
        "uuid",
    }

    repo_root = Path(__file__).resolve().parents[5]
    this_test = Path(__file__).resolve()
    importers = []
    for path in (repo_root / "ods").rglob("*.py"):
        if path.resolve() in {this_test, module_path.resolve()}:
            continue
        if "extension_lifecycle_receipts" in path.read_text(
            encoding="utf-8", errors="ignore"
        ):
            importers.append(path.relative_to(repo_root).as_posix())
    assert sorted(importers) == [
        "ods/bin/ods-host-agent.py",
        (
            "ods/extensions/services/dashboard-api/tests/"
            "test_extension_lifecycle_receipt_host_api.py"
        ),
    ]
