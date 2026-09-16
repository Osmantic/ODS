"""Current-state checks must not equate an apply receipt with working health."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
ODS_DIR = Path(__file__).resolve().parents[4]
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_library_verify_runtime as verify  # noqa: E402
from extension_application_identity import (  # noqa: E402
    ApplicationIdentity,
    _identity_digest,
    identity_labels,
)
from extension_application_observation import ContainerObservation, RECORD_SCHEMA  # noqa: E402
from extension_application_record_store import ApplicationRecord  # noqa: E402
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedHostPort,
    PlannedOperation,
)
from extension_lifecycle_work import LifecycleWorkCommand  # noqa: E402

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64
DEFINITION_HASH = "sha256:" + "3" * 64
COMPOSE_HASH = "sha256:" + "4" * 64
CONFIG_HASH = "sha256:" + "5" * 64


def test_pilot_probe_contract_matches_generated_catalog() -> None:
    catalog = json.loads(
        (ODS_DIR / "config" / "extensions-catalog.json").read_text(encoding="utf-8")
    )
    by_id = {item["id"]: item["planning"] for item in catalog["extensions"]}
    base = _command().plan_material.definitions[0]
    for service_id, expected in verify._PILOT_HTTP.items():
        definition = replace(
            base,
            service_id=service_id,
            canonical_document=json.dumps(by_id[service_id]).encode(),
        )
        selection = verify.LibraryVerifySelection(service_id, "install", definition)
        assert verify._declared_probe(selection) == expected


def _command(action: str = "install") -> LifecycleWorkCommand:
    definition = PlannedDefinition(
        service_id="gitea",
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256=DEFINITION_HASH,
        compose_sha256=COMPOSE_HASH,
        definition_source="library",
        compose_file="compose.yaml",
        images=(),
        builds=(),
        canonical_document=json.dumps(
            {
                "lifecycle": {
                    "healthChecks": ["/api/healthz"],
                    "readiness": ["healthy"],
                },
                "resources": {"containerPorts": [2222, 3000]},
            },
            sort_keys=True,
        ).encode(),
        host_ports=(PlannedHostPort("tcp", 2222), PlannedHostPort("tcp", 7830)),
        source_tree_sha256="sha256:" + "6" * 64,
    )
    return LifecycleWorkCommand(
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        operation_key="verify",
        request_hash="7" * 64,
        service_ids=("gitea",),
        payload={"serviceIds": ["gitea"]},
        timeout_seconds=600,
        plan_material=LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TRANSACTION_ID,
            plan_hash=PLAN_HASH,
            state="verifying",
            operations=(PlannedOperation("gitea", action),),
            definitions=(definition,),
            attested_approval=True,
        ),
    )


def _record(command: LifecycleWorkCommand, *, prior: bool = False) -> ApplicationRecord:
    selected = verify.bind_library_verify(command)[0]
    base = ApplicationIdentity(
        service_id="gitea",
        version="1.0.0",
        action="install",
        transaction_id=("txn-" + "8" * 24) if prior else TRANSACTION_ID,
        plan_sha256=("9" * 64) if prior else PLAN_HASH,
        request_sha256=("a" * 64)
        if prior
        else verify._apply_request_hash(command, selected),
        definition_sha256=DEFINITION_HASH,
        compose_sha256=COMPOSE_HASH,
        identity_sha256="0" * 64,
    )
    identity = replace(base, identity_sha256=_identity_digest(base))
    return ApplicationRecord(
        schema=RECORD_SCHEMA,
        service_id=identity.service_id,
        version=identity.version,
        action=identity.action,
        transaction_id=identity.transaction_id,
        plan_sha256=identity.plan_sha256,
        request_sha256=identity.request_sha256,
        definition_sha256=identity.definition_sha256,
        compose_sha256=identity.compose_sha256,
        identity_sha256=identity.identity_sha256,
        config_sha256=CONFIG_HASH,
        override_sha256=verify.canonical_document_sha256(
            verify._override_bytes(
                identity, verify._PILOT_COMPOSE_SERVICES[identity.service_id]
            )
        ),
        expected_containers=("ods-gitea-1",),
        record_sha256="b" * 64,
    )


class Records:
    def __init__(self, record: ApplicationRecord) -> None:
        self.record = record

    def snapshot(self, _service_id: str) -> ApplicationRecord:
        return self.record


def _dispatcher(monkeypatch, *, action: str = "install", prior: bool = False):
    monkeypatch.setattr(verify.sys, "platform", "linux")
    command = _command(action)
    record = _record(command, prior=prior)
    identity = verify._identity(record)
    labels = identity_labels(identity)
    labels["com.docker.compose.service"] = "gitea"
    containers = [
        ContainerObservation(
            name="ods-gitea-1", state="running", health="healthy", labels=labels
        )
    ]
    monkeypatch.setattr(
        verify,
        "_current_files",
        lambda _root, _service: (DEFINITION_HASH, COMPOSE_HASH, CONFIG_HASH),
    )
    monkeypatch.setattr(
        verify,
        "_current_containers",
        lambda _service, _names, _runner: tuple(containers),
    )
    override_digest = verify.canonical_document_sha256(
        verify._override_bytes(identity, ("gitea",))
    )
    monkeypatch.setattr(
        verify, "_current_override", lambda _root, _service: override_digest
    )
    monkeypatch.setattr(
        verify,
        "_inspect_current_container",
        lambda *_args: (
            {2222: 2222, 3000: 7830},
            "c" * 64,
            "sha256:" + "d" * 64,
        ),
    )
    monkeypatch.setattr(verify, "_project_ids", lambda _service, _runner: {"c" * 64})
    clock = [0.0]
    probes: list[tuple[int, str]] = []

    def probe(port: int, path: str) -> bool:
        probes.append((port, path))
        return True

    dispatcher = verify.LibraryVerifyDispatcher(
        install_dir=Path("/ods"),
        record_store=Records(record),
        active_lease=lambda: True,
        docker_runner=lambda _argv: None,
        probe=probe,
        clock=lambda: clock[0],
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    return command, dispatcher, containers, probes, clock


def test_fresh_replay_is_stable_and_probes_the_bound_http_port(monkeypatch) -> None:
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    first = dispatcher(command)
    replay = dispatcher(command)
    assert len(first) == 64
    assert replay == first
    assert probes == [(7830, "/api/healthz"), (7830, "/api/healthz")]


def test_composite_verify_checks_every_selected_service(monkeypatch) -> None:
    monkeypatch.setattr(verify.sys, "platform", "linux")
    base = _command()
    material = replace(
        base.plan_material,
        operations=(
            PlannedOperation("gitea", "install"),
            PlannedOperation("ntfy", "noop"),
        ),
        definitions=(
            base.plan_material.definitions[0],
            replace(base.plan_material.definitions[0], service_id="ntfy"),
        ),
    )
    command = replace(
        base,
        service_ids=("gitea", "ntfy"),
        payload={"serviceIds": ["gitea", "ntfy"]},
        plan_material=material,
    )
    sampled = []
    probes = []

    def sample(_command, selected):
        sampled.append(selected.service_id)
        port = 7830 if selected.service_id == "gitea" else 8097
        path = "/api/healthz" if selected.service_id == "gitea" else "/v1/health"
        return verify._CurrentService(
            service_id=selected.service_id,
            action=selected.action,
            record_sha256="b" * 64,
            definition_sha256=DEFINITION_HASH,
            compose_sha256=COMPOSE_HASH,
            config_sha256=CONFIG_HASH,
            override_sha256="sha256:" + "e" * 64,
            containers=(
                (
                    selected.service_id,
                    "c" * 64,
                    "sha256:" + "d" * 64,
                    "running",
                    "healthy",
                ),
            ),
            host_port=port,
            path=path,
            ready=True,
        )

    dispatcher = verify.LibraryVerifyDispatcher(
        install_dir=Path("/ods"),
        record_store=Records(_record(base)),
        active_lease=lambda: True,
        docker_runner=lambda _argv: None,
        probe=lambda port, path: probes.append((port, path)) or True,
        clock=lambda: 0.0,
        sleep=lambda _seconds: None,
    )
    dispatcher._sample = sample
    assert len(dispatcher(command)) == 64
    assert sampled == ["gitea", "ntfy", "gitea", "ntfy"]
    assert probes == [(7830, "/api/healthz"), (8097, "/v1/health")]


def test_noop_may_use_prior_transaction_record_but_still_checks_current_state(
    monkeypatch,
) -> None:
    command, dispatcher, containers, _probes, _clock = _dispatcher(
        monkeypatch, action="noop", prior=True
    )
    assert len(dispatcher(command)) == 64
    containers[0] = replace(containers[0], state="exited")
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-readiness-failed"


def test_mutable_requires_record_from_this_exact_apply(monkeypatch) -> None:
    command, dispatcher, _containers, _probes, _clock = _dispatcher(
        monkeypatch, prior=True
    )
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-record-mismatch"


def test_missing_or_wrong_bound_host_port_is_refused(monkeypatch) -> None:
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    monkeypatch.setattr(
        verify,
        "_inspect_current_container",
        lambda *_args: ({2222: 2222, 3000: 9000}, "c" * 64, "sha256:" + "d" * 64),
    )
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-port-unbound"
    assert probes == []


def test_extra_published_port_is_refused(monkeypatch) -> None:
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    monkeypatch.setattr(
        verify,
        "_inspect_current_container",
        lambda *_args: (
            {2222: 2222, 3000: 7830, 9000: 9000},
            "c" * 64,
            "sha256:" + "d" * 64,
        ),
    )
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-port-unbound"
    assert probes == []


def test_unexpected_container_in_compose_project_is_refused(monkeypatch) -> None:
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    monkeypatch.setattr(
        verify, "_project_ids", lambda _service, _runner: {"c" * 64, "e" * 64}
    )
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-container-set-mismatch"
    assert probes == []


@pytest.mark.parametrize("project_ids", [set(), {"e" * 64}])
def test_missing_or_wrong_compose_project_container_is_refused(
    monkeypatch, project_ids: set[str]
) -> None:
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    monkeypatch.setattr(verify, "_project_ids", lambda _service, _runner: project_ids)
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-container-set-mismatch"
    assert probes == []


def test_project_query_includes_stopped_containers_and_uses_exact_project() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, ("c" * 64 + "\n").encode(), b"")

    assert verify._project_ids("gitea", runner) == {"c" * 64}
    assert calls == [
        [
            "docker",
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            "label=com.docker.compose.project=ods-af-gitea",
        ]
    ]


def test_dispatcher_uses_real_project_query_on_both_samples(monkeypatch) -> None:
    project_ids = verify._project_ids
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    monkeypatch.setattr(verify, "_project_ids", project_ids)
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, ("c" * 64 + "\n").encode(), b"")

    dispatcher._docker = runner
    assert len(dispatcher(command)) == 64
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert calls[0][-1] == "label=com.docker.compose.project=ods-af-gitea"
    assert probes == [(7830, "/api/healthz")]


def test_changed_container_state_between_probe_and_second_sample_fails(
    monkeypatch,
) -> None:
    command, dispatcher, containers, _probes, _clock = _dispatcher(monkeypatch)

    def drift(_port: int, _path: str) -> bool:
        containers[0] = replace(containers[0], health="unhealthy")
        return True

    dispatcher._probe = drift
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-current-drift"


def test_replaced_container_between_samples_fails(monkeypatch) -> None:
    command, dispatcher, _containers, _probes, _clock = _dispatcher(monkeypatch)
    current_id = ["c" * 64]
    monkeypatch.setattr(
        verify, "_project_ids", lambda _service, _runner: {current_id[0]}
    )
    monkeypatch.setattr(
        verify,
        "_inspect_current_container",
        lambda *_args: (
            {2222: 2222, 3000: 7830},
            current_id[0],
            "sha256:" + "d" * 64,
        ),
    )

    def replaced(_port: int, _path: str) -> bool:
        current_id[0] = "e" * 64
        return True

    dispatcher._probe = replaced
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-current-drift"


def test_changed_active_override_fails_before_probe(monkeypatch) -> None:
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    monkeypatch.setattr(verify, "_current_override", lambda _root, _service: None)
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-override-mismatch"
    assert probes == []


def test_record_override_digest_must_match_generated_override(monkeypatch) -> None:
    command, dispatcher, _containers, probes, _clock = _dispatcher(monkeypatch)
    dispatcher._records.record = replace(
        dispatcher._records.record,
        override_sha256="sha256:" + "0" * 64,
    )
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-override-mismatch"
    assert probes == []


def test_unavailable_record_is_value_free(monkeypatch) -> None:
    command, dispatcher, _containers, _probes, _clock = _dispatcher(monkeypatch)

    def broken(_service_id: str):
        raise OSError("sensitive-host-path")

    dispatcher._records.snapshot = broken
    with pytest.raises(verify.LibraryVerifyError) as caught:
        dispatcher(command)
    assert caught.value.code == "library-verify-unavailable"
    assert "sensitive-host-path" not in str(caught.value)


def test_port_observation_refuses_non_loopback_or_extra_port() -> None:
    identity = verify._identity(_record(_command()))
    labels = identity_labels(identity)
    labels["com.docker.compose.service"] = "gitea"
    observed = ContainerObservation(
        name="ods-gitea-1", state="running", health="healthy", labels=labels
    )

    def inspect(host_ip: str, extra: bool = False):
        ports = {
            "3000/tcp": [{"HostIp": host_ip, "HostPort": "7830"}],
            "2222/tcp": [{"HostIp": "127.0.0.1", "HostPort": "2222"}],
        }
        if extra:
            ports["9000/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "9000"}]
        document = {
            "Name": "/ods-gitea-1",
            "Id": "c" * 64,
            "Image": "sha256:" + "d" * 64,
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {"Labels": labels},
            "NetworkSettings": {"Ports": ports},
        }
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(document).encode(), stderr=b""
        )

    assert verify._inspect_current_container(
        identity, observed, lambda _argv: inspect("127.0.0.1")
    ) == ({3000: 7830, 2222: 2222}, "c" * 64, "sha256:" + "d" * 64)
    with pytest.raises(verify.LibraryVerifyError) as caught:
        verify._inspect_current_container(
            identity, observed, lambda _argv: inspect("0.0.0.0")
        )
    assert caught.value.code == "library-verify-port-unbound"
    published, _id, _image = verify._inspect_current_container(
        identity, observed, lambda _argv: inspect("127.0.0.1", extra=True)
    )
    assert 9000 in published.values()
