"""Source-only host Docker observation tests; no live restore is selected."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_docker_quiescence as quiescence  # noqa: E402
from extension_operation_leases import LEASE_SCHEMA  # noqa: E402
from test_extension_data_restore_journal import _ready  # noqa: E402


linux_effect = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only Docker restore observer")
_ID = "a" * 64


def _admission(command):
    return {
        "schema": LEASE_SCHEMA, "leaseId": "lease-" + "1" * 24,
        "transactionId": command.transaction_id, "planHash": command.plan_hash,
        "serviceIds": sorted(command.service_ids),
    }


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
    return install, command, quiescence.DockerQuiescenceObserver(
        command, install, _admission(command), fake,
    )


@linux_effect
def test_stopped_compose_services_and_no_running_mounts_are_observable(tmp_path: Path):
    fake = FakeDocker(states={"alpha": b"exited\n", "beta": b""})
    _install, command, observer = _observer(tmp_path, fake)
    assert observer() is True
    assert len(fake.calls) == len(command.service_ids) + 1
    assert all(argv[:2] == ["container", "ls"] for argv in fake.calls)


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
    observer = quiescence.DockerQuiescenceObserver(command, install, _admission(command), fake)
    assert observer() is False
    assert fake.calls[-1][:5] == ["inspect", "--type", "container", "--format", "{{json .Mounts}}"]


@linux_effect
def test_unrelated_running_container_mount_does_not_create_false_overlap(tmp_path: Path):
    fake = FakeDocker(ids=(_ID + "\n").encode(), mounts=[[
        {"Type": "bind", "Source": "/var/run/docker.sock", "RW": False},
    ]])
    _install, _command, observer = _observer(tmp_path, fake)
    assert observer() is True


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
        quiescence.DockerQuiescenceObserver(command, install, admission, fake)
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
        quiescence.DockerQuiescenceObserver(command, install, admission, fake)
    assert caught.value.code == "lifecycle-work-data-quiescence-lease-required"
    assert not fake.calls


@linux_effect
def test_non_linux_posix_host_cannot_select_linux_only_observer(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    fake = FakeDocker()
    monkeypatch.setattr(quiescence.sys, "platform", "darwin")
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        quiescence.DockerQuiescenceObserver(command, install, _admission(command), fake)
    assert caught.value.code == "lifecycle-work-data-quiescence-platform-unsupported"
    assert not fake.calls
