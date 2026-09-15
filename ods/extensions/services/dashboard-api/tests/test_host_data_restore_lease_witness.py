"""Real host-manager custody for source-only generic Docker restore observation."""

# Imported pytest fixtures are intentionally injected by name into tests.
# ruff: noqa: F401, F811

from __future__ import annotations

import sys

import pytest

from test_extension_data_docker_quiescence import FakeDocker, quiescence
from test_extension_data_restore_journal import _ready
from test_extension_operation_lease_host_api import FakeClock, host_server


linux_effect = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux-only generic data restore witness"
)


@linux_effect
def test_host_admission_constructs_live_token_authenticated_docker_witness(
    tmp_path, host_server
):
    agent, _listener = host_server
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    agent.INSTALL_DIR = install
    for service_id in (*command.service_ids, "gamma"):
        directory = agent.EXTENSIONS_DIR / service_id
        directory.mkdir()
        (directory / "manifest.yaml").write_text(
            "service: {}\n", encoding="utf-8"
        )

    clock = FakeClock()
    manager = agent._extension_leases.ExtensionLeaseManager(
        agent._extension_lease_lock_provider, clock=clock
    )
    agent._extension_lease_manager = manager
    grant = manager.acquire(
        command.transaction_id,
        command.plan_hash,
        [*command.service_ids, "gamma"],
        ttl_seconds=1,
    )
    evidence = agent._ExtensionMutationLeaseEvidence(
        grant["leaseId"], grant["leaseToken"],
        command.transaction_id, command.plan_hash,
    )
    admission = agent._ExtensionMutationAdmission(
        None, evidence, command.service_ids
    )
    fake = FakeDocker(states={service_id: b"exited\n" for service_id in command.service_ids})

    with pytest.raises(agent._extension_leases.LeaseExpired):
        admission.active_lease_status()
    with pytest.raises(agent._extension_leases.LeaseExpired):
        admission.docker_restore_observer(command, fake)
    assert fake.calls == []

    with admission as admitted:
        status = admission.active_lease_status()
        assert status["active"] is True
        assert status["serviceIds"] == sorted([*command.service_ids, "gamma"])
        assert admitted["serviceIds"] == list(command.service_ids)
        assert "leaseToken" not in status
        observer = admission.docker_restore_observer(command, fake)
        assert observer() is True
        call_count = len(fake.calls)

        clock.advance(1)
        with pytest.raises(agent._extension_leases.LeaseExpired):
            admission.active_lease_status()
        with pytest.raises(quiescence.DockerQuiescenceError) as caught:
            observer()
        assert caught.value.code == "lifecycle-work-data-quiescence-lease-not-active"
        assert len(fake.calls) == call_count
        assert grant["leaseToken"] not in repr(admission)
        assert grant["leaseToken"] not in repr(observer._status)

    with pytest.raises(agent._extension_leases.LeaseExpired):
        admission.active_lease_status()
    with pytest.raises(quiescence.DockerQuiescenceError) as caught:
        observer()
    assert caught.value.code == "lifecycle-work-data-quiescence-lease-not-active"
    assert len(fake.calls) == call_count
    assert all(not agent._service_locks[item].locked() for item in grant["serviceIds"])


@linux_effect
def test_host_witness_uses_the_original_manager_not_a_replaced_global(
    tmp_path, host_server
):
    agent, _listener = host_server
    install, _backup, _alpha, _store, command, _root, _journal = _ready(tmp_path)
    agent.INSTALL_DIR = install
    for service_id in command.service_ids:
        directory = agent.EXTENSIONS_DIR / service_id
        directory.mkdir()
        (directory / "manifest.yaml").write_text(
            "service: {}\n", encoding="utf-8"
        )
    manager = agent._get_extension_lease_manager()
    grant = manager.acquire(command.transaction_id, command.plan_hash, command.service_ids)
    evidence = agent._ExtensionMutationLeaseEvidence(
        grant["leaseId"], grant["leaseToken"], command.transaction_id, command.plan_hash
    )
    admission = agent._ExtensionMutationAdmission(None, evidence, command.service_ids)
    with admission:
        agent._extension_lease_manager = None
        fake = FakeDocker(states={item: b"exited\n" for item in command.service_ids})
        observer = admission.docker_restore_observer(command, fake)
        assert observer() is True
        assert agent._extension_lease_manager is None
    assert manager.describe(grant["leaseId"])["active"] is False
    assert all(agent._service_locks[item].locked() for item in command.service_ids)
    manager.release(
        grant["leaseId"], grant["leaseToken"], command.transaction_id,
        command.plan_hash,
    )
    assert all(not agent._service_locks[item].locked() for item in command.service_ids)
