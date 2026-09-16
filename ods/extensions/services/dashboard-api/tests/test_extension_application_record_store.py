"""Adversarial tests for the dormant active-application record store."""

from __future__ import annotations

import ast
import hashlib
import json
import multiprocessing
import os
import stat
import sys
import tempfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
ODS_ROOT = Path(__file__).resolve().parents[4]
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_application_identity as app_identity  # noqa: E402, RUF100
import extension_application_observation as observation  # noqa: E402, RUF100
import extension_application_record_store as store_mod  # noqa: E402, RUF100
import extension_lifecycle_plan as lifecycle_plan  # noqa: E402, RUF100
import extension_lifecycle_work as lifecycle_work  # noqa: E402, RUF100

SUPPORTED = (
    os.name == "posix"
    and store_mod.fcntl is not None
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_NONBLOCK")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.unlink in os.supports_dir_fd
)

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
DEFINITION_SHA = "sha256:" + "3" * 64
COMPOSE_SHA = "sha256:" + "4" * 64
CONFIG_SHA = "sha256:" + "e" * 64
OTHER_CONFIG_SHA = "sha256:" + "f" * 64
SERVICE_ID = "documents"
VERSION = "1.2.3"
CONTAINERS = ("documents-api", "documents-worker")


def _definition(
    service_id: str,
    *,
    version: str = VERSION,
    compose: str | None = COMPOSE_SHA,
) -> dict:
    return {
        "id": service_id,
        "serviceType": "docker",
        "manifestSchemaVersion": "ods.services.v2",
        "version": version,
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": "2.0.0", "maximum": "3.0.0"},
        "definitionSha256": DEFINITION_SHA,
        "composeSha256": compose,
        "definitionSource": "library",
        "composeFile": "compose.yaml" if compose else None,
        "dependsOn": [],
        "provides": [],
        "requires": [],
        "conflicts": [],
        "requirements": {},
        "estimates": {},
        "configuration": [],
        "artifacts": {
            "images": [
                {
                    "reference": f"example.invalid/{service_id}:{version}",
                    "digest": "sha256:" + "5" * 64,
                    "downloadBytes": 123,
                }
            ],
            "builds": [],
        },
        "resources": {},
        "lifecycle": {},
        "data": [],
        "trust": {},
        "support": {},
    }


def _transaction(
    definition: dict,
    action: str,
    *,
    transaction_id: str = TRANSACTION_ID,
    plan_hash: str = PLAN_HASH,
) -> dict:
    return {
        "transactionId": transaction_id,
        "state": "applying",
        "approval": {
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "approvedBy": "owner",
        },
        "envelope": {
            "planHash": plan_hash,
            "plan": {
                "selectedServices": [definition["id"]],
                "operations": [
                    {"serviceId": definition["id"], "action": action}
                ],
                "definitions": [definition],
            },
        },
    }


def _command(
    service_id: str,
    action: str,
    *,
    version: str = VERSION,
    transaction_id: str = TRANSACTION_ID,
    plan_hash: str = PLAN_HASH,
) -> lifecycle_work.LifecycleWorkCommand:
    unsigned = {
        "schema": lifecycle_work.REQUEST_SCHEMA,
        "transactionId": transaction_id,
        "planHash": plan_hash,
        "operationKey": f"apply:{service_id}",
        "serviceIds": [service_id],
        "payload": {"operation": {"serviceId": service_id, "action": action}},
    }
    request = {
        **unsigned,
        "requestHash": hashlib.sha256(
            json.dumps(
                unsigned,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
    }
    parsed = lifecycle_work.parse_lifecycle_work_request(request)
    definition = _definition(service_id, version=version)
    return lifecycle_plan.bind_lifecycle_plan(
        parsed,
        _transaction(
            definition,
            action,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
        ),
    )


def _record_dict(
    service_id: str,
    *,
    action: str = "install",
    version: str = VERSION,
    config_sha256: str = CONFIG_SHA,
    containers: tuple[str, ...] | None = None,
) -> dict:
    command = _command(service_id, action, version=version)
    identity = app_identity.produce_application_identity(command)
    raw = observation.produce_active_record(
        identity,
        config_sha256,
        containers or (f"{service_id}-api",),
    )
    return observation.parse_active_record(raw)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _publish_process(root: str, service_id: str, config: str, gate, output) -> None:
    try:
        target = store_mod.ApplicationRecordStore(root)
        command = _command(service_id, "install")
        gate.wait(10)
        result = target.publish(command, config, (f"{service_id}-api",))
        output.put(("ok", result.outcome, result.record.record_sha256))
    except store_mod.ApplicationRecordStoreError as error:
        output.put(("error", error.code))
    except BaseException as error:  # noqa: BLE001, RUF100  # pragma: no cover
        output.put(("unexpected", type(error).__name__))


def _assert_code(code: str, call) -> None:
    with pytest.raises(store_mod.ApplicationRecordStoreError) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == code


def test_platform_failure_is_controlled_and_precedes_filesystem_access():
    with (
        mock.patch.object(store_mod.os, "name", "nt"),
        mock.patch.object(store_mod, "_open_root") as opened,
    ):
        _assert_code(
            "application-record-store-platform-unsupported",
            lambda: store_mod.ApplicationRecordStore("C:/not-opened"),
        )
    opened.assert_not_called()


@pytest.mark.skipif(not SUPPORTED, reason="requires POSIX descriptor APIs")
class TestApplicationRecordStore:
    @pytest.fixture(autouse=True)
    def _store(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.root = Path(temporary) / "application-state"
            self.root.mkdir(mode=0o700)
            self.root.chmod(0o700)
            self.store = store_mod.ApplicationRecordStore(self.root)
            self.snapshot_path = self.root / store_mod.SNAPSHOT_NAME
            yield

    def _publish(
        self,
        *,
        service_id: str = SERVICE_ID,
        action: str = "install",
        version: str = VERSION,
        config: str = CONFIG_SHA,
        containers: tuple[str, ...] = CONTAINERS,
        previous: str | None = None,
    ) -> store_mod.PublishResult:
        return self.store.publish(
            _command(service_id, action, version=version),
            config,
            containers,
            previous,
        )

    def test_create_persists_exact_canonical_full_record(self):
        result = self._publish()
        assert result.outcome == "created"
        assert isinstance(result.record, store_mod.ApplicationRecord)
        assert result.record.service_id == SERVICE_ID
        assert result.record.expected_containers == CONTAINERS
        assert result.record.config_sha256 == CONFIG_SHA
        assert stat.S_IMODE(self.snapshot_path.stat().st_mode) == 0o600
        assert self.snapshot_path.stat().st_nlink == 1

        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        assert payload["schema"] == store_mod.STORE_SCHEMA
        assert payload["records"] == [
            observation.parse_active_record(
                observation.produce_active_record(
                    app_identity.produce_application_identity(
                        _command(SERVICE_ID, "install")
                    ),
                    CONFIG_SHA,
                    CONTAINERS,
                )
            )
        ]
        assert self.store.snapshot(SERVICE_ID) == result.record

    def test_public_records_are_frozen_and_nested_values_are_immutable(self):
        record = self._publish().record
        with pytest.raises(FrozenInstanceError):
            record.service_id = "changed"  # type: ignore[misc]
        assert isinstance(record.expected_containers, tuple)
        with pytest.raises(TypeError):
            record.expected_containers[0] = "changed"  # type: ignore[index]
        assert self.store.snapshot(SERVICE_ID) == record

    def test_active_records_are_sorted_and_absent_snapshot_is_empty(self):
        assert self.store.active() == ()
        assert self.store.snapshot("missing") is None
        self._publish(service_id="zeta", containers=("zeta-api",))
        self._publish(service_id="alpha", containers=("alpha-api",))
        assert [record.service_id for record in self.store.active()] == [
            "alpha",
            "zeta",
        ]

    @pytest.mark.parametrize("action", ["install", "enable", "repair", "update"])
    def test_every_apply_action_can_be_persisted(self, action: str):
        result = self._publish(action=action)
        assert result.record.action == action

    def test_exact_replay_with_and_without_matching_previous_does_not_rewrite(self):
        created = self._publish()
        before = self.snapshot_path.read_bytes()
        before_stat = self.snapshot_path.stat()
        replay = self._publish()
        matching = self._publish(previous=created.record.record_sha256)
        after_stat = self.snapshot_path.stat()
        assert replay.outcome == matching.outcome == "replayed"
        assert self.snapshot_path.read_bytes() == before
        assert (after_stat.st_ino, after_stat.st_mtime_ns) == (
            before_stat.st_ino,
            before_stat.st_mtime_ns,
        )

    def test_exact_replay_with_wrong_previous_conflicts_without_write(self):
        self._publish()
        before = self.snapshot_path.read_bytes()
        _assert_code(
            "application-record-store-conflict",
            lambda: self._publish(previous="0" * 64),
        )
        assert self.snapshot_path.read_bytes() == before

    def test_absent_with_previous_conflicts_before_snapshot_creation(self):
        _assert_code(
            "application-record-store-conflict",
            lambda: self._publish(previous="0" * 64),
        )
        assert not self.snapshot_path.exists()

    def test_divergent_without_or_with_stale_previous_conflicts(self):
        created = self._publish()
        before = self.snapshot_path.read_bytes()
        _assert_code(
            "application-record-store-conflict",
            lambda: self._publish(
                action="update", version="2.0.0", config=OTHER_CONFIG_SHA
            ),
        )
        _assert_code(
            "application-record-store-conflict",
            lambda: self._publish(
                action="update",
                version="2.0.0",
                config=OTHER_CONFIG_SHA,
                previous="0" * 64,
            ),
        )
        assert self.snapshot_path.read_bytes() == before
        assert self.store.snapshot(SERVICE_ID) == created.record

    def test_matching_previous_replaces_exactly_once(self):
        created = self._publish()
        replaced = self._publish(
            action="update",
            version="2.0.0",
            config=OTHER_CONFIG_SHA,
            previous=created.record.record_sha256,
        )
        assert replaced.outcome == "replaced"
        assert replaced.record.action == "update"
        assert replaced.record.version == "2.0.0"
        assert self.store.snapshot(SERVICE_ID) == replaced.record
        assert self._publish(
            action="update",
            version="2.0.0",
            config=OTHER_CONFIG_SHA,
            previous=replaced.record.record_sha256,
        ).outcome == "replayed"

    def test_remove_absent_never_claims_deletion_or_creates_state(self):
        result = self.store.remove(SERVICE_ID, "0" * 64)
        assert result == store_mod.RemoveResult(outcome="absent", prior=None)
        assert not self.snapshot_path.exists()

    def test_remove_is_exact_cas_and_replay_observes_absence(self):
        created = self._publish()
        before = self.snapshot_path.read_bytes()
        _assert_code(
            "application-record-store-conflict",
            lambda: self.store.remove(SERVICE_ID, "0" * 64),
        )
        assert self.snapshot_path.read_bytes() == before
        removed = self.store.remove(SERVICE_ID, created.record.record_sha256)
        assert removed.outcome == "removed"
        assert removed.prior == created.record
        assert self.store.snapshot(SERVICE_ID) is None
        replay = self.store.remove(SERVICE_ID, created.record.record_sha256)
        assert replay.outcome == "absent"
        assert replay.prior is None
        assert json.loads(self.snapshot_path.read_text())["records"] == []

    @pytest.mark.parametrize("ambient", [0o002, 0o077])
    def test_snapshot_mode_is_exact_under_supported_umasks(self, ambient: int):
        previous = os.umask(ambient)
        try:
            self._publish()
        finally:
            os.umask(previous)
        assert stat.S_IMODE(self.snapshot_path.stat().st_mode) == 0o600

    @pytest.mark.parametrize(
        "bad",
        [None, True, "", "A", "unsafe/path", "a" * 129],
    )
    def test_service_id_validation_is_bounded_and_value_free(self, bad):
        _assert_code(
            "application-record-store-binding-invalid",
            lambda: self.store.snapshot(bad),
        )
        if str(bad):
            assert str(bad) not in str(
                store_mod.ApplicationRecordStoreError(
                    "application-record-store-binding-invalid"
                )
            )

    def test_record_inputs_are_validated_before_root_reopen_or_temp_allocation(self):
        with mock.patch.object(store_mod, "_open_root") as opened:
            _assert_code(
                "application-record-store-record-invalid",
                lambda: self.store.publish(
                    _command(SERVICE_ID, "install"), "secret-invalid", CONTAINERS
                ),
            )
        opened.assert_not_called()
        assert list(self.root.iterdir()) == []

    def test_previous_hash_is_validated_before_root_reopen(self):
        with mock.patch.object(store_mod, "_open_root") as opened:
            _assert_code(
                "application-record-store-binding-invalid",
                lambda: self._publish(previous="invalid-secret"),
            )
        opened.assert_not_called()

    def test_lock_failure_is_stable_and_value_free(self):
        with mock.patch.object(
            store_mod.fcntl, "flock", side_effect=OSError("injected-secret")
        ):
            _assert_code("application-record-store-lock", self.store.active)

    def test_invalid_candidate_is_rejected_before_temp_allocation(self):
        beta = _record_dict("beta")
        alpha = _record_dict("alpha")
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with mock.patch.object(store_mod.os, "open") as opened:
                _assert_code(
                    "application-record-store-order",
                    lambda: store_mod._write_snapshot(root_fd, [beta, alpha]),
                )
            opened.assert_not_called()
        finally:
            os.close(root_fd)

    @pytest.mark.parametrize(
        "root_value",
        ["relative", "/", "/tmp/../tmp/application-state", "//tmp/state"],
    )
    def test_root_value_is_strict(self, root_value: str):
        _assert_code(
            "application-record-store-root-invalid",
            lambda: store_mod.ApplicationRecordStore(root_value),
        )

    def test_missing_wrong_mode_and_symlinked_roots_fail_closed(self):
        missing = self.root.parent / "missing"
        _assert_code(
            "application-record-store-root-missing",
            lambda: store_mod.ApplicationRecordStore(missing),
        )
        self.root.chmod(0o755)
        _assert_code(
            "application-record-store-root-custody",
            lambda: store_mod.ApplicationRecordStore(self.root),
        )
        self.root.chmod(0o700)
        self.root.rmdir()
        target = self.root.parent / "target"
        target.mkdir(mode=0o700)
        self.root.symlink_to(target, target_is_directory=True)
        _assert_code(
            "application-record-store-root-invalid",
            lambda: store_mod.ApplicationRecordStore(self.root),
        )

    def test_root_swap_after_construction_fails_closed(self):
        self._publish()
        self.snapshot_path.unlink()
        self.root.rmdir()
        target = self.root.parent / "replacement"
        target.mkdir(mode=0o700)
        self.root.symlink_to(target, target_is_directory=True)
        _assert_code(
            "application-record-store-root-invalid", self.store.active
        )

    @pytest.mark.parametrize("kind", ["symlink", "fifo", "hardlink", "mode"])
    def test_snapshot_custody_attacks_fail_closed(self, kind: str):
        self._publish()
        if kind == "symlink":
            content = self.snapshot_path.read_bytes()
            self.snapshot_path.unlink()
            target = self.root / "target"
            target.write_bytes(content)
            self.snapshot_path.symlink_to(target)
        elif kind == "fifo":
            self.snapshot_path.unlink()
            os.mkfifo(self.snapshot_path)
        elif kind == "hardlink":
            os.link(self.snapshot_path, self.root / "extra")
        else:
            self.snapshot_path.chmod(0o644)
        _assert_code("application-record-store-custody", self.store.active)

    def test_snapshot_uid_mismatch_is_rejected_with_a_stable_error(self):
        self._publish()
        snapshot = self.snapshot_path.stat()
        real_fstat = store_mod.os.fstat

        def mismatched_snapshot_uid(descriptor: int):
            result = real_fstat(descriptor)
            if (result.st_dev, result.st_ino) != (snapshot.st_dev, snapshot.st_ino):
                return result
            return SimpleNamespace(
                st_dev=result.st_dev,
                st_ino=result.st_ino,
                st_mode=result.st_mode,
                st_nlink=result.st_nlink,
                st_uid=result.st_uid + 1,
                st_size=result.st_size,
                st_mtime_ns=result.st_mtime_ns,
                st_ctime_ns=result.st_ctime_ns,
            )

        with mock.patch.object(
            store_mod.os, "fstat", side_effect=mismatched_snapshot_uid
        ):
            _assert_code("application-record-store-custody", self.store.active)

    def test_device_snapshot_is_rejected_when_creation_is_permitted(self):
        self._publish()
        self.snapshot_path.unlink()
        try:
            os.mknod(self.snapshot_path, stat.S_IFCHR | 0o600, os.makedev(1, 3))
        except (OSError, PermissionError):
            pytest.skip("device creation is not permitted")
        _assert_code("application-record-store-custody", self.store.active)

    def test_noncanonical_duplicate_key_order_and_tampered_record_fail_closed(self):
        created = self._publish()
        valid = json.loads(self.snapshot_path.read_text())
        cases = [
            _canonical(valid).rstrip(b"\n"),
            (
                b'{"records":[],"schema":"'
                + store_mod.STORE_SCHEMA.encode()
                + b'","schema":"duplicate"}\n'
            ),
            _canonical(
                {
                    "schema": store_mod.STORE_SCHEMA,
                    "records": [_record_dict("zeta"), _record_dict("alpha")],
                }
            ),
            _canonical(
                {
                    "schema": store_mod.STORE_SCHEMA,
                    "records": [valid["records"][0], valid["records"][0]],
                }
            ),
        ]
        tampered = dict(valid["records"][0])
        tampered["record_sha256"] = "0" * 64
        cases.append(
            _canonical({"schema": store_mod.STORE_SCHEMA, "records": [tampered]})
        )
        for raw in cases:
            self.snapshot_path.write_bytes(raw)
            with pytest.raises(store_mod.ApplicationRecordStoreError):
                self.store.active()
        assert created.record.service_id == SERVICE_ID

    def test_oversize_snapshot_fails_before_json_parse(self):
        self.snapshot_path.write_bytes(b"x" * (store_mod.MAX_FILE_BYTES + 1))
        self.snapshot_path.chmod(0o600)
        _assert_code("application-record-store-size", self.store.active)

    def test_bounded_read_accepts_exact_file_limit_without_blocking(self):
        limit_path = self.root / "limit"
        limit_path.write_bytes(b"x" * store_mod.MAX_FILE_BYTES)
        descriptor = os.open(limit_path, store_mod._file_read_flags())
        try:
            raw = store_mod._read_all(descriptor, store_mod.MAX_FILE_BYTES)
        finally:
            os.close(descriptor)
        assert len(raw) == store_mod.MAX_FILE_BYTES

    def test_maximum_record_count_parses_and_next_create_is_nonmutating(self):
        maximum_version = "\U0010ffff" * 128

        def maximum_service_id(index: int) -> str:
            prefix = f"s{index:03d}-"
            return prefix + "s" * (64 - len(prefix))

        def maximum_containers(service_id: str) -> tuple[str, ...]:
            names = []
            for index in range(observation.MAX_CONTAINERS):
                prefix = f"{service_id}-{index:02d}-"
                names.append(prefix + "x" * (128 - len(prefix)))
            return tuple(names)

        records = [
            _record_dict(
                maximum_service_id(index),
                version=maximum_version,
                containers=maximum_containers(maximum_service_id(index)),
            )
            for index in range(store_mod.MAX_RECORDS)
        ]
        raw = _canonical({"schema": store_mod.STORE_SCHEMA, "records": records})
        # Every producer-variable field is at its maximum: 64-character identity
        # service IDs, 128 four-byte Unicode scalars in version, and 32 unique
        # 128-character container names per record. The canonical schema therefore
        # cannot produce a valid snapshot at the larger file-size boundary.
        assert len(raw) < store_mod.MAX_FILE_BYTES
        self.snapshot_path.write_bytes(raw)
        self.snapshot_path.chmod(0o600)

        assert len(self.store.active()) == store_mod.MAX_RECORDS
        before = self.snapshot_path.read_bytes()
        before_stat = self.snapshot_path.stat()
        _assert_code(
            "application-record-store-size",
            lambda: self._publish(
                service_id="overflow", containers=("overflow-api",)
            ),
        )
        after_stat = self.snapshot_path.stat()
        assert self.snapshot_path.read_bytes() == before
        assert (after_stat.st_ino, after_stat.st_mtime_ns) == (
            before_stat.st_ino,
            before_stat.st_mtime_ns,
        )

    def test_partial_write_and_file_fsync_failure_preserve_prior_snapshot(self):
        self._publish()
        before = self.snapshot_path.read_bytes()
        real_write = store_mod.os.write
        calls = 0

        def partial_then_zero(descriptor: int, content: bytes) -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(descriptor, content[: max(1, len(content) // 2)])
            return 0

        with mock.patch.object(store_mod.os, "write", side_effect=partial_then_zero):
            _assert_code(
                "application-record-store-write-failed",
                lambda: self._publish(
                    service_id="voice", containers=("voice-api",)
                ),
            )
        assert self.snapshot_path.read_bytes() == before

        real_fsync = store_mod.os.fsync

        def fail_regular_file(descriptor: int) -> None:
            if stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise OSError("injected")
            real_fsync(descriptor)

        with mock.patch.object(store_mod.os, "fsync", side_effect=fail_regular_file):
            _assert_code(
                "application-record-store-write-failed",
                lambda: self._publish(
                    service_id="voice", containers=("voice-api",)
                ),
            )
        assert self.snapshot_path.read_bytes() == before

    def test_definite_replace_failure_preserves_prior_snapshot(self):
        self._publish()
        before = self.snapshot_path.read_bytes()
        with mock.patch.object(
            store_mod.os, "replace", side_effect=OSError("injected")
        ):
            _assert_code(
                "application-record-store-write-failed",
                lambda: self._publish(
                    service_id="voice", containers=("voice-api",)
                ),
            )
        assert self.snapshot_path.read_bytes() == before

    def test_replace_that_completes_then_raises_is_reconciled(self):
        self._publish()
        real_replace = store_mod.os.replace

        def replace_then_raise(*args, **kwargs):
            real_replace(*args, **kwargs)
            raise OSError("lost response")

        with mock.patch.object(
            store_mod.os, "replace", side_effect=replace_then_raise
        ):
            result = self._publish(
                service_id="voice", containers=("voice-api",)
            )
        assert result.outcome == "created"
        assert self.store.snapshot("voice") == result.record

    def test_persistent_directory_fsync_failure_is_ambiguous_not_success(self):
        self._publish()
        real_fsync = store_mod.os.fsync

        def fail_directory(descriptor: int) -> None:
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError("injected")
            real_fsync(descriptor)

        with mock.patch.object(store_mod.os, "fsync", side_effect=fail_directory):
            _assert_code(
                "application-record-store-write-ambiguous",
                lambda: self._publish(
                    service_id="voice", containers=("voice-api",)
                ),
            )
        # Current state may contain the complete intended snapshot, but the
        # failed call never claimed a durable success.
        assert self.store.snapshot("voice") is not None

    def test_temp_name_identity_swap_never_replaces_prior_snapshot(self):
        self._publish()
        before = self.snapshot_path.read_bytes()
        real_stat = store_mod.os.stat

        def mismatched_temp(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if isinstance(path, str) and path.startswith(store_mod._TEMP_PREFIX):
                return SimpleNamespace(
                    st_dev=result.st_dev,
                    st_ino=result.st_ino + 1,
                    st_mode=result.st_mode,
                    st_nlink=result.st_nlink,
                    st_uid=result.st_uid,
                    st_size=result.st_size,
                    st_mtime_ns=result.st_mtime_ns,
                    st_ctime_ns=result.st_ctime_ns,
                )
            return result

        with mock.patch.object(store_mod.os, "stat", side_effect=mismatched_temp):
            _assert_code(
                "application-record-store-write-failed",
                lambda: self._publish(
                    service_id="voice", containers=("voice-api",)
                ),
            )
        assert self.snapshot_path.read_bytes() == before

    @pytest.mark.parametrize("same_service", [False, True])
    def test_cross_process_publish_serialization(self, same_service: bool):
        context = multiprocessing.get_context("fork")
        gate = context.Event()
        output = context.Queue()
        service_ids = ("shared", "shared") if same_service else ("alpha", "beta")
        configs = (CONFIG_SHA, OTHER_CONFIG_SHA)
        processes = [
            context.Process(
                target=_publish_process,
                args=(str(self.root), service_id, config, gate, output),
            )
            for service_id, config in zip(service_ids, configs, strict=True)
        ]
        for process in processes:
            process.start()
        gate.set()
        for process in processes:
            process.join(15)
        try:
            assert all(not process.is_alive() for process in processes)
            assert all(process.exitcode == 0 for process in processes)
            results = [output.get(timeout=5) for _ in processes]
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            output.close()
            output.join_thread()
        if same_service:
            assert sorted(result[0] for result in results) == ["error", "ok"]
            assert any(
                result == ("error", "application-record-store-conflict")
                for result in results
            )
            assert len(self.store.active()) == 1
        else:
            assert all(result[:2] == ("ok", "created") for result in results)
            assert [record.service_id for record in self.store.active()] == [
                "alpha",
                "beta",
            ]

    def test_tampered_frozen_command_is_reproved_before_lock(self):
        command = _command(SERVICE_ID, "install")
        object.__setattr__(command, "operation_key", "apply:other")
        with mock.patch.object(store_mod, "_open_root") as opened:
            _assert_code(
                "application-record-store-record-invalid",
                lambda: self.store.publish(command, CONFIG_SHA, CONTAINERS),
            )
        opened.assert_not_called()

    def test_public_surface_is_narrow_and_store_remains_dormant(self):
        assert set(store_mod.__all__) == {
            "ApplicationRecord",
            "ApplicationRecordStore",
            "ApplicationRecordStoreError",
            "MAX_FILE_BYTES",
            "MAX_RECORDS",
            "PublishResult",
            "RemoveResult",
            "SNAPSHOT_NAME",
            "STORE_SCHEMA",
        }
        module = ODS_ROOT / "bin" / "extension_application_record_store.py"
        importers = []
        for path in ODS_ROOT.rglob("*.py"):
            if path.resolve() == module.resolve() or "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and any(
                    alias.name == "extension_application_record_store"
                    for alias in node.names
                ) or (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "extension_application_record_store"
                ):
                    importers.append(path.relative_to(ODS_ROOT).as_posix())
        # The read-only collector, bounded application runtime, and Compose
        # effect may consume the store. The host imports only the runtime; the
        # transaction executor still has no direct store authority.
        assert sorted(importers) == [
            "bin/extension_application_observation_adapter.py",
            "bin/extension_library_application_runtime.py",
            "bin/extension_library_compose_apply_effect.py",
        ]
        source = module.read_text(encoding="utf-8")
        assert "subprocess" not in source
        assert "getenv" not in source
