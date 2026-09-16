"""Host Docker observation tests; only generic backup is selected."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_docker_quiescence as quiescence  # noqa: E402
from extension_operation_leases import LEASE_SCHEMA  # noqa: E402
from test_extension_data_restore_journal import _ready  # noqa: E402
from test_extension_data_scope_contract import command as generic_command  # noqa: E402
from test_extension_data_stream_snapshot import _roots  # noqa: E402
from test_extension_operation_leases import FakeClock, make_manager  # noqa: E402


linux_effect = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only Docker restore observer")
_ID = "a" * 64


def _admission(command):
    return {
        "schema": LEASE_SCHEMA, "leaseId": "lease-" + "1" * 24,
        "transactionId": command.transaction_id, "planHash": command.plan_hash,
        "serviceIds": sorted(command.service_ids),
    }


def _status(command, admission=None, **changes):
    admission = admission or _admission(command)
    record = {**admission, "active": True}
    record.update(changes)
    return record


class FakeDocker:
    def __init__(self, *, states: dict[str, bytes] | None = None,
                 ids: bytes = b"", mounts: list[list[dict]] | None = None,
                 returncode: int = 0) -> None:
        self.states = states or {}
        self.ids = ids
        self.mounts = mounts or []
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(argv)
        if argv[:3] == ["container", "ls", "--all"]:
            service_id = argv[4].rsplit("=", 1)[-1]
            raw = self.states.get(service_id, b"")
        elif argv[:3] == ["container", "ls", "--no-trunc"]:
            raw = self.ids
        elif argv[:1] == ["inspect"]:
            raw = b"\n".join(json.dumps(item).encode() for item in self.mounts) + b"\n"
        else:
            raise AssertionError(f"unexpected Docker argv: {argv}")
        return subprocess.CompletedProcess(argv, self.returncode, raw, b"")


def _observer(tmp_path: Path, fake: FakeDocker):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    admission = _admission(command)
    return install, command, quiescence.DockerQuiescenceObserver(
        command, install, admission, fake, lambda: _status(command, admission),
    )


@linux_effect
def test_stopped_compose_services_and_no_running_mounts_are_observable(tmp_path: Path):
    fake = FakeDocker(states={"alpha": b"exited\n", "beta": b""})
    _install, command, observer = _observer(tmp_path, fake)
    assert observer() is True
    assert len(fake.calls) == len(command.service_ids) + 1
    assert all(argv[:2] == ["container", "ls"] for argv in fake.calls)


@linux_effect
def test_backup_uses_the_same_lease_bound_scoped_docker_observation(tmp_path: Path):
    install, _data, _backup, _alpha = _roots(tmp_path)
    command = generic_command(
        actions=("install", "install"),
        selected_paths=(["data/alpha"], ["data/beta"]), prior_paths=[],
    )
    fake = FakeDocker(states={"alpha": b"exited\n"})
    admission = _admission(command)
    observer = quiescence.DockerQuiescenceObserver(
        command, install, admission, fake,
        lambda: _status(command, admission),
    )
    assert observer() is True
    assert len(fake.calls) == len(command.service_ids) + 1


@linux_effect
@pytest.mark.parametrize("state", [b"created\n", b"running\n", b"paused\n",
                                        b"restarting\n", b"removing\n", b"dead\n"])
def test_any_non_exited_compose_state_refuses_even_before_mount_scan(tmp_path: Path, state: bytes):
    fake = FakeDocker(states={"alpha": state})
    _install, _command, observer = _observer(tmp_path, fake)
    assert observer() is False
    assert len(fake.calls) == 1


@linux_effect
def test_running_foreign_container_with_ancestor_data_bind_mount_refuses(tmp_path: Path):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    fake = FakeDocker(ids=(_ID + "\n").encode(), mounts=[[
        {"Type": "bind", "Source": str(install / "data"), "RW": False},
    ]])
    admission = _admission(command)
    observer = quiescence.DockerQuiescenceObserver(
        command, install, admission, fake, lambda: _status(command, admission),
    )
    assert observer() is False
    assert fake.calls[-1][:5] == ["inspect", "--type", "container", "--format", "{{json .Mounts}}"]


@linux_effect
def test_unrelated_running_container_mount_does_not_create_false_overlap(tmp_path: Path):
    foreign = tmp_path / "foreign-mount"
    foreign.mkdir()
    fake = FakeDocker(ids=(_ID + "\n").encode(), mounts=[[
        {"Type": "bind", "Source": str(foreign), "RW": False},
    ]])
    _install, _command, observer = _observer(tmp_path, fake)
    assert observer() is True


@linux_effect
def test_symlinked_mount_source_is_not_a_safe_nonoverlap(tmp_path: Path):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    alias = tmp_path / "alias-to-data"
    alias.symlink_to(install / "data", target_is_directory=True)
    fake = FakeDocker(ids=(_ID + "\n").encode(), mounts=[[
        {"Type": "bind", "Source": str(alias)},
    ]])
    observer = quiescence.DockerQuiescenceObserver(
        command, install, _admission(command), fake,
        lambda: _status(command),
    )
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        observer()
    assert caught.value.code == "lifecycle-work-data-quiescence-mount-unverifiable"


@linux_effect
def test_same_inode_bind_alias_refuses_even_when_strings_do_not_overlap(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install, _backup, alpha, _store, command, _root, _journal = _ready(tmp_path)
    alias = tmp_path / "simulated-bind-alias"
    alias.mkdir()
    target_info = alpha.stat()
    original_lstat = quiescence.os.lstat

    def same_inode(path):
        info = original_lstat(path)
        if path == str(alias):
            return SimpleNamespace(
                st_mode=info.st_mode, st_dev=target_info.st_dev,
                st_ino=target_info.st_ino, st_nlink=info.st_nlink,
            )
        return info

    monkeypatch.setattr(quiescence.os, "lstat", same_inode)
    fake = FakeDocker(ids=(_ID + "\n").encode(), mounts=[[
        {"Type": "bind", "Source": str(alias)},
    ]])
    observer = quiescence.DockerQuiescenceObserver(
        command, install, _admission(command), fake,
        lambda: _status(command),
    )
    assert observer() is False


@linux_effect
def test_missing_or_hardlinked_mount_source_refuses_as_unverifiable(tmp_path: Path):
    install, _backup, alpha, _store, command, _root, _journal = _ready(tmp_path)
    hardlink = tmp_path / "aliased-note"
    hardlink.hardlink_to(alpha / "note")
    for source in (tmp_path / "missing-source", hardlink):
        fake = FakeDocker(ids=(_ID + "\n").encode(), mounts=[[
            {"Type": "bind", "Source": str(source)},
        ]])
        observer = quiescence.DockerQuiescenceObserver(
            command, install, _admission(command), fake,
            lambda: _status(command),
        )
        with pytest.raises(quiescence.DockerQuiescenceError) as caught:
            observer()
        assert caught.value.code == "lifecycle-work-data-quiescence-mount-unverifiable"


@linux_effect
def test_target_symlink_drift_refuses_even_without_running_containers(tmp_path: Path):
    install, _backup, alpha, _store, command, _root, _journal = _ready(tmp_path)
    saved = tmp_path / "preserved-alpha"
    alpha.rename(saved)
    alpha.symlink_to(saved, target_is_directory=True)
    fake = FakeDocker()
    observer = quiescence.DockerQuiescenceObserver(
        command, install, _admission(command), fake,
        lambda: _status(command),
    )
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        observer()
    assert caught.value.code == "lifecycle-work-data-quiescence-mount-unverifiable"


@linux_effect
@pytest.mark.parametrize("mount", [
    {"Type": "bind", "Source": "relative/path"},
    {"Type": "alien", "Source": "/tmp/other"},
    {"Type": "bind", "Source": 7},
])
def test_unknown_or_nonabsolute_mount_source_is_fail_closed(tmp_path: Path, mount: dict):
    fake = FakeDocker(ids=(_ID + "\n").encode(), mounts=[[mount]])
    _install, _command, observer = _observer(tmp_path, fake)
    with pytest.raises(quiescence.DockerQuiescenceError):
        observer()


@linux_effect
def test_malformed_or_failed_docker_output_is_not_a_stopped_service_receipt(tmp_path: Path):
    malformed = FakeDocker(states={"alpha": b"Unknown\n"})
    _install, _command, observer = _observer(tmp_path, malformed)
    with pytest.raises(quiescence.DockerQuiescenceError):
        observer()
    failed = FakeDocker(returncode=1)
    _install, _command, observer = _observer(tmp_path / "second", failed)
    with pytest.raises(quiescence.DockerQuiescenceError):
        observer()


@linux_effect
def test_lease_binding_mismatch_refuses_before_any_docker_probe(tmp_path: Path):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    fake = FakeDocker()
    admission = _admission(command)
    admission["planHash"] = "0" * 64
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        quiescence.DockerQuiescenceObserver(
            command, install, admission, fake, lambda: _status(command, admission),
        )
    assert caught.value.code == "lifecycle-work-data-quiescence-lease-required"
    assert not fake.calls


@linux_effect
@pytest.mark.parametrize("service_ids", [["beta", "alpha"], [["alpha"], "beta"],
                                                ["alpha", "alpha"]])
def test_lease_service_ids_must_be_exact_ordered_binding(
        tmp_path: Path, service_ids: list[object]):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    fake = FakeDocker()
    admission = _admission(command)
    admission["serviceIds"] = service_ids
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        quiescence.DockerQuiescenceObserver(
            command, install, admission, fake, lambda: _status(command, admission),
        )
    assert caught.value.code == "lifecycle-work-data-quiescence-lease-required"
    assert not fake.calls


@linux_effect
def test_non_linux_posix_host_cannot_select_linux_only_observer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    fake = FakeDocker()
    monkeypatch.setattr(quiescence.sys, "platform", "darwin")
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        quiescence.DockerQuiescenceObserver(
            command, install, _admission(command), fake,
            lambda: _status(command),
        )
    assert caught.value.code == "lifecycle-work-data-quiescence-platform-unsupported"
    assert not fake.calls


@linux_effect
@pytest.mark.parametrize("change", [
    {"active": False}, {"planHash": "0" * 64}, {"leaseId": "lease-" + "0" * 24},
    {"serviceIds": ["alpha"]}, {"serviceIds": [["alpha"], "beta"]},
])
def test_inactive_or_unbound_host_status_refuses_before_docker_probe(
        tmp_path: Path, change: dict):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    admission = _admission(command)
    fake = FakeDocker()
    observer = quiescence.DockerQuiescenceObserver(
        command, install, admission, fake,
        lambda: _status(command, admission, **change),
    )
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        observer()
    assert caught.value.code == "lifecycle-work-data-quiescence-lease-not-active"
    assert not fake.calls


@linux_effect
def test_lease_loss_after_docker_snapshot_refuses_before_success(tmp_path: Path):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    admission = _admission(command)
    fake = FakeDocker()
    records = iter([_status(command, admission), _status(command, admission, active=False)])
    observer = quiescence.DockerQuiescenceObserver(
        command, install, admission, fake, lambda: next(records),
    )
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        observer()
    assert caught.value.code == "lifecycle-work-data-quiescence-lease-not-active"
    assert len(fake.calls) == len(command.service_ids) + 1


@linux_effect
def test_real_manager_status_proves_only_the_active_use_window(tmp_path: Path):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    manager, locks, _events = make_manager()
    grant = manager.acquire(command.transaction_id, command.plan_hash, command.service_ids)
    fake = FakeDocker()

    def status():
        return manager.status(
            grant["leaseId"], grant["leaseToken"],
            command.transaction_id, command.plan_hash,
        )

    with manager.use(
            grant["leaseId"], grant["leaseToken"], command.transaction_id,
            command.plan_hash, command.service_ids,
    ) as admission:
        observer = quiescence.DockerQuiescenceObserver(
            command, install, admission, fake, status,
        )
        assert grant["leaseToken"] not in repr(observer)
        assert observer() is True
        assert all(locks[service_id].locked() for service_id in command.service_ids)
    prior_calls = len(fake.calls)
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        observer()
    assert caught.value.code == "lifecycle-work-data-quiescence-lease-not-active"
    assert grant["leaseToken"] not in str(caught.value)
    assert len(fake.calls) == prior_calls


@linux_effect
def test_real_manager_expiry_during_observation_refuses(tmp_path: Path):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    clock = FakeClock()
    manager, _locks, _events = make_manager(clock=clock)
    grant = manager.acquire(
        command.transaction_id, command.plan_hash, command.service_ids,
        ttl_seconds=10,
    )

    class ExpiringDocker(FakeDocker):
        def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
            result = super().__call__(argv)
            if len(self.calls) == 1:
                clock.advance(11)
            return result

    fake = ExpiringDocker()
    with manager.use(
            grant["leaseId"], grant["leaseToken"], command.transaction_id,
            command.plan_hash, command.service_ids,
    ) as admission:
        observer = quiescence.DockerQuiescenceObserver(
            command, install, admission, fake,
            lambda: manager.status(
                grant["leaseId"], grant["leaseToken"],
                command.transaction_id, command.plan_hash,
            ),
        )
        with pytest.raises(quiescence.DockerQuiescenceError) as caught:
            observer()
        assert caught.value.code == "lifecycle-work-data-quiescence-lease-not-active"
