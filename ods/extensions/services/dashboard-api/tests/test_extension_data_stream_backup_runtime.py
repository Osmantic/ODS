"""Real generic backup receipt and host reachability; restore stays disabled."""

# Imported pytest fixtures are intentionally injected by name into tests.
# ruff: noqa: F401, F811

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_stream_backup_runtime as runtime_module  # noqa: E402
import extension_data_local_docker_runner as docker_runner  # noqa: E402
from test_extension_data_scope_contract import command as generic_command  # noqa: E402
from test_extension_data_docker_quiescence import FakeDocker  # noqa: E402
from test_extension_data_stream_snapshot import _roots, _write  # noqa: E402
from test_extension_lifecycle_work_host_api import (  # noqa: E402
    acquire_lease, begin_receipt, host_request, host_server,
    lease_evidence, work_request,
)


linux_effect = pytest.mark.skipif(os.name != "posix", reason="Linux generic data backup")


@pytest.fixture(autouse=True)
def _local_docker_stub(monkeypatch):
    """Host tests observe isolated Docker evidence, never a live daemon."""
    monkeypatch.setattr(docker_runner, "PinnedLocalDockerRunner", FakeDocker)


def _fixture_plan(agent, source):
    material = source.plan_material
    agent._extension_lifecycle_plan_loader = lambda command: replace(
        command, plan_material=replace(
            material, transaction_id=command.transaction_id,
            plan_hash=command.plan_hash,
        ),
    )


def _host_ready(agent):
    for service_id in ("alpha", "beta"):
        definition = agent.EXTENSIONS_DIR / service_id
        definition.mkdir(mode=0o700)
        (definition / "manifest.yaml").write_text("service: {}\n", encoding="utf-8")
        source = agent.DATA_DIR / service_id
        source.mkdir(mode=0o700)
        source.chmod(0o700)
        _write(source / "note", service_id.encode("ascii"))
    source = generic_command(
        actions=("install", "install"),
        selected_paths=(["data/alpha"], ["data/beta"]), prior_paths=[],
    )
    _fixture_plan(agent, source)


@linux_effect
def test_generic_backup_runtime_seals_and_recovers_exact_receipt(tmp_path):
    install, data, _unused, alpha = _roots(tmp_path)
    beta = install / "data" / "beta"
    beta.mkdir(mode=0o700)
    beta.chmod(0o700)
    _write(alpha / "note", b"alpha-original")
    _write(beta / "note", b"beta-original")
    root = data / "assistant-first" / "data-backups"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    command = generic_command(
        actions=("install", "install"),
        selected_paths=(["data/alpha"], ["data/beta"]), prior_paths=[],
    )
    runtime = runtime_module.build_stream_backup_runtime(
        install_dir=install, data_dir=data, plan_loader=lambda _value: command,
    )

    assert runtime.backup_started_observer(command).state == "missing"
    evidence = runtime.backup_dispatcher(command, witness=lambda: True)
    archive = root / f"{command.transaction_id}.{command.plan_hash}.tar"
    assert archive.is_file()
    assert archive.stat().st_mode & 0o777 == 0o400
    observed = runtime.backup_started_observer(command)
    assert observed.state == "completed"
    assert observed.evidence_hash == evidence
    assert runtime.backup_dispatcher(command, witness=lambda: pytest.fail("replayed capture")) == evidence
    assert len(list(root.iterdir())) == 1


@linux_effect
def test_generic_backup_requires_witness_and_refuses_late_writer(tmp_path):
    install, data, _unused, alpha = _roots(tmp_path)
    _write(alpha / "note", b"alpha-original")
    root = data / "assistant-first" / "data-backups"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    command = generic_command(
        actions=("install", "install"),
        selected_paths=(["data/alpha"], ["data/beta"]), prior_paths=[],
    )
    runtime = runtime_module.build_stream_backup_runtime(
        install_dir=install, data_dir=data, plan_loader=lambda _value: command,
    )
    with pytest.raises(runtime_module.LifecycleWorkExecutionError) as missing:
        runtime.backup_dispatcher(command)
    assert missing.value.code == "lifecycle-work-data-quiescence-witness-required"
    assert list(root.iterdir()) == []

    observations = iter([True, False])
    with pytest.raises(runtime_module.LifecycleWorkExecutionError) as changed:
        runtime.backup_dispatcher(command, witness=lambda: next(observations))
    assert changed.value.code == "lifecycle-work-data-quiescence-active-writer"
    assert list(root.iterdir()) == []


@linux_effect
def test_present_corrupt_archive_cannot_be_claimed_missing(tmp_path):
    install, data, _unused, _alpha = _roots(tmp_path)
    root = data / "assistant-first" / "data-backups"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    command = generic_command(
        actions=("install", "install"),
        selected_paths=(["data/alpha"], ["data/beta"]), prior_paths=[],
    )
    runtime = runtime_module.build_stream_backup_runtime(
        install_dir=install, data_dir=data, plan_loader=lambda _value: command,
    )
    bad = root / f"{command.transaction_id}.{command.plan_hash}.tar"
    bad.write_bytes(b"forged archive")
    bad.chmod(0o400)
    with pytest.raises(runtime_module.LifecycleWorkError):
        runtime.backup_started_observer(command)
    assert bad.read_bytes() == b"forged archive"


@linux_effect
def test_host_selects_generic_backup_and_replays_without_duplicate_snapshot(
    host_server, host_request, monkeypatch,
):
    agent, _listener = host_server
    _host_ready(agent)
    grant = acquire_lease(agent, host_request, ["alpha", "beta"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant), operation_key="backup",
        service_ids=["alpha", "beta"],
    )
    begin_receipt(agent, host_request, request)
    calls = []
    original = agent._data_stream_backup_runtime_module.StreamSnapshotStore.backup

    def counted(store, command, *, quiescence=None):
        calls.append(command.request_hash)
        return original(store, command, quiescence=quiescence)

    monkeypatch.setattr(agent._data_stream_backup_runtime_module.StreamSnapshotStore, "backup", counted)
    status, result = host_request("/v1/extension/lifecycle-work", request)
    assert status == 200
    assert result["completed"] is True
    assert result["operationKey"] == "backup"
    assert len(calls) == 1
    assert (agent.DATA_DIR / "assistant-first" / "data-backups"
            / f"{request['transactionId']}.{request['planHash']}.tar").is_file()

    replay_status, replay = host_request("/v1/extension/lifecycle-work", request)
    assert replay_status == 200
    assert replay == result
    assert len(calls) == 1


@linux_effect
@pytest.mark.parametrize("case", ["active-before", "active-after", "docker-unavailable"])
def test_host_refuses_unquiesced_generic_backup_before_archive_publication(
    host_server, host_request, monkeypatch, case,
):
    agent, _listener = host_server
    _host_ready(agent)
    grant = acquire_lease(agent, host_request, ["alpha", "beta"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant), operation_key="backup",
        service_ids=["alpha", "beta"],
    )
    begin_receipt(agent, host_request, request)

    if case == "active-after":
        class ChangedDocker(FakeDocker):
            def __call__(self, argv):
                if len(self.calls) == 3:
                    self.states["alpha"] = b"running\n"
                return super().__call__(argv)

        fake = ChangedDocker()
    elif case == "docker-unavailable":
        fake = FakeDocker(returncode=1)
    else:
        fake = FakeDocker(states={"alpha": b"running\n"})
    monkeypatch.setattr(docker_runner, "PinnedLocalDockerRunner", lambda: fake)

    status, result = host_request("/v1/extension/lifecycle-work", request)
    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-operation-failed"}}
    assert list((agent.DATA_DIR / "assistant-first" / "data-backups").iterdir()) == []
    if case == "active-before":
        assert len(fake.calls) == 1
    elif case == "active-after":
        assert len(fake.calls) == 4


@linux_effect
def test_generic_backup_recovers_started_receipt_from_sealed_archive(
    host_server, host_request, monkeypatch,
):
    agent, _listener = host_server
    _host_ready(agent)
    grant = acquire_lease(agent, host_request, ["alpha", "beta"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant), operation_key="backup",
        service_ids=["alpha", "beta"],
    )
    begin_receipt(agent, host_request, request)
    command = agent._extension_lifecycle_work.parse_lifecycle_work_request({
        key: request[key] for key in agent._extension_lifecycle_work.REQUEST_KEYS
    })
    runtime = agent._get_extension_data_stream_backup_runtime()
    assert runtime is not None
    expected = runtime.backup_dispatcher(
        agent._extension_lifecycle_plan_loader(command), witness=lambda: True,
    )
    monkeypatch.setattr(
        runtime.store, "backup", lambda _command: pytest.fail("duplicate snapshot"),
    )
    status, result = host_request("/v1/extension/lifecycle-work", request)
    assert status == 200
    assert result["evidenceHash"] == expected


@linux_effect
def test_generic_restore_stays_unavailable_and_never_uses_injected_dispatcher(
    host_server, host_request,
):
    agent, _listener = host_server
    _host_ready(agent)
    grant = acquire_lease(agent, host_request, ["alpha", "beta"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant), operation_key="restore",
        service_ids=["alpha", "beta"],
    )
    begin_receipt(agent, host_request, request)
    calls = []
    agent._extension_lifecycle_work_dispatcher = lambda value: calls.append(value)
    status, result = host_request("/v1/extension/lifecycle-work", request)
    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert calls == []


@linux_effect
def test_missing_generic_backup_runtime_cannot_fall_back_to_injected_dispatcher(
    host_server, host_request,
):
    agent, _listener = host_server
    _host_ready(agent)
    grant = acquire_lease(agent, host_request, ["alpha", "beta"])
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant), operation_key="backup",
        service_ids=["alpha", "beta"],
    )
    begin_receipt(agent, host_request, request)
    calls = []
    agent._data_stream_backup_runtime_module = None
    agent._extension_lifecycle_work_dispatcher = lambda value: calls.append(value)
    status, result = host_request("/v1/extension/lifecycle-work", request)
    assert status == 503
    assert result == {"error": {"code": "lifecycle-work-dispatcher-unavailable"}}
    assert calls == []
    assert list((agent.DATA_DIR / "assistant-first" / "data-backups").iterdir()) == []


@linux_effect
@pytest.mark.parametrize("case", ["unapproved", "caller-path"])
def test_host_generic_backup_refuses_missing_approval_or_caller_path_scope(
    host_server, host_request, case,
):
    agent, _listener = host_server
    _host_ready(agent)
    if case == "unapproved":
        _fixture_plan(agent, generic_command(
            actions=("install", "install"),
            selected_paths=(["data/alpha"], ["data/beta"]),
            prior_paths=[], attested=False,
        ))
    grant = acquire_lease(agent, host_request, ["alpha", "beta"])
    payload = {"serviceIds": ["alpha", "beta"]}
    if case == "caller-path":
        payload["paths"] = ["data/someone-else"]
    request = work_request(
        agent._extension_lifecycle_work.REQUEST_SCHEMA,
        lease_evidence(agent, grant), operation_key="backup",
        service_ids=["alpha", "beta"], payload=payload,
    )
    begin_receipt(agent, host_request, request)
    status, result = host_request("/v1/extension/lifecycle-work", request)
    if case == "caller-path":
        # The immutable request grammar rejects caller paths before a plan is
        # even loaded; missing approval is rejected by the generic scope.
        assert status == 422
        assert result == {"error": {"code": "invalid-lifecycle-work-request"}}
    else:
        assert status == 409
        assert result == {"error": {"code": "lifecycle-work-receipt-mismatch"}}
    assert list((agent.DATA_DIR / "assistant-first" / "data-backups").iterdir()) == []
