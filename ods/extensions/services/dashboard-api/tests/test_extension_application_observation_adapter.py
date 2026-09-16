"""Read-only host evidence collection before any application success claim."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_application_identity as identity_mod  # noqa: E402
import extension_application_observation_adapter as adapter_mod  # noqa: E402
import extension_application_record_store as record_mod  # noqa: E402
import extension_document_digest as digest_mod  # noqa: E402
import extension_lifecycle_plan as plan_mod  # noqa: E402
import test_extension_application_observation as fixtures  # noqa: E402


MANIFEST = b"schema_version: ods.services.v2\nservice:\n  id: documents\n"
COMPOSE = b"services:\n  documents:\n    image: example.invalid/documents@sha256:1111\n"
CONFIG = b'{"schema":"ods.extension-configuration.v1","fields":{}}\n'
OVERRIDE = b"services:\n  documents:\n    labels:\n      ods.test: true\n"
IDS = ("a" * 64, "b" * 64)
NAMES = ("documents-api", "documents-worker")


class FakeDocker:
    def __init__(
        self,
        identity,
        *,
        containers=True,
        drift=False,
        unavailable=False,
        orphan_project=False,
    ):
        self.labels = identity_mod.identity_labels(identity)
        self.containers = containers
        self.drift = drift
        self.unavailable = unavailable
        self.orphan_project = orphan_project
        self.calls: list[tuple[str, ...]] = []
        self.rounds = 0

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(tuple(argv))
        if self.unavailable:
            return subprocess.CompletedProcess(argv, 1, b"", b"no daemon")
        if argv[1:3] == ["ps", "-a"]:
            self.rounds += 1
            if self.orphan_project:
                output = f"{IDS[0]} documents-db\n".encode()
            elif not self.containers:
                output = b""
            else:
                output = "".join(
                    f"{container_id} {name}\n" for container_id, name in zip(IDS, NAMES)
                ).encode()
            if self.drift and self.rounds == 2:
                output = b""
        elif argv[1:3] == ["ps", "-aq"]:
            if self.orphan_project:
                output = (
                    (IDS[0] + "\n").encode()
                    if argv[-1] == "label=com.docker.compose.project=ods-af-documents"
                    else b""
                )
            else:
                output = (
                    b"" if not self.containers else (IDS[0] + "\n" + IDS[1] + "\n").encode()
                )
        elif argv[1] == "inspect":
            output = "".join(
                json.dumps(
                    {
                        "Id": container_id,
                        "Name": (
                            "/documents-db"
                            if self.orphan_project
                            else "/" + NAMES[IDS.index(container_id)]
                        ),
                        "State": {"Status": "running", "Health": {"Status": "healthy"}},
                        "Config": {
                            "Labels": (
                                {"com.docker.compose.project": "ods-af-documents"}
                                if self.orphan_project
                                else self.labels
                            )
                        },
                    }
                )
                + "\n"
                for container_id in argv[4:]
            ).encode()
        else:
            raise AssertionError(argv)
        return subprocess.CompletedProcess(argv, 0, output, b"")


class FakeRecords:
    def __init__(self, record):
        self.record = record

    def snapshot(self, service_id: str):
        assert service_id == fixtures.SERVICE_ID
        return self.record


class FakeReceipts:
    def __init__(self, snapshot, compensation=None, *, drift_compensation=False):
        self.value = snapshot
        self.compensation = compensation
        self.drift_compensation = drift_compensation
        self.compensation_reads = 0

    def snapshot(self, transaction_id: str, operation_key: str):
        assert transaction_id == fixtures.TRANSACTION_ID
        if operation_key == f"apply:{fixtures.SERVICE_ID}":
            return self.value
        assert operation_key == f"compensate:{fixtures.SERVICE_ID}"
        self.compensation_reads += 1
        if self.drift_compensation and self.compensation_reads == 2:
            return fixtures.receipts_mod.LifecycleSnapshot(
                transaction_id=fixtures.TRANSACTION_ID,
                operation_key=operation_key,
                state="absent",
                started_receipt=None,
                terminal_receipt=None,
            )
        return self.compensation or fixtures.receipts_mod.LifecycleSnapshot(
            transaction_id=fixtures.TRANSACTION_ID,
            operation_key=operation_key,
            state="absent",
            started_receipt=None,
            terminal_receipt=None,
        )


def _installed(tmp_path: Path):
    root = tmp_path / ".ods-assistant-first" / "applications" / fixtures.SERVICE_ID
    root.mkdir(parents=True)
    for directory in (root, root.parent, root.parent.parent):
        directory.chmod(0o700)
    for name, content in (
        ("manifest.yaml", MANIFEST),
        ("compose.yaml", COMPOSE),
        ("configuration.json", CONFIG),
        ("compose.override.yaml", OVERRIDE),
    ):
        target = root / name
        target.write_bytes(content)
        target.chmod(0o600)
    definition = fixtures._definition(
        fixtures.SERVICE_ID, digest_mod.canonical_document_sha256(COMPOSE)
    )
    definition["definitionSha256"] = digest_mod.canonical_document_sha256(MANIFEST)
    transaction = fixtures._transaction("applying", [definition])
    command = fixtures._command(
        f"apply:{fixtures.SERVICE_ID}",
        [fixtures.SERVICE_ID],
        {"operation": {"serviceId": fixtures.SERVICE_ID, "action": fixtures.ACTION}},
    )
    bound = plan_mod.bind_lifecycle_plan(command, transaction)
    identity = identity_mod.produce_application_identity(bound)
    model = fixtures._build_canonical_record(
        identity,
        config_sha=digest_mod.canonical_document_sha256(CONFIG),
        containers=list(NAMES),
        override_sha=digest_mod.canonical_document_sha256(OVERRIDE),
    )
    model["expected_containers"] = tuple(model["expected_containers"])
    record = record_mod.ApplicationRecord(**model)
    receipt = fixtures._started_snapshot(identity)
    return root, command, bound, identity, record, receipt


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_exact_current_app_is_applied_without_claiming_health(tmp_path):
    _root, command, bound, identity, record, receipt = _installed(tmp_path)
    docker = FakeDocker(identity)
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(record),
        FakeReceipts(receipt),
        lambda _command: bound,
        lambda: True,
        docker,
    )
    result = adapter(command)
    assert result.classification == "APPLIED"
    assert result.identity_sha256 == identity.identity_sha256
    assert [container.name for container in result.containers] == list(NAMES)
    assert len([call for call in docker.calls if call[1] == "inspect"]) == 2
    assert all(call[1] in {"ps", "inspect"} for call in docker.calls)


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_reconciling_plan_observes_prior_apply_without_reenabling_effect(tmp_path):
    _root, command, bound, identity, record, receipt = _installed(tmp_path)
    recovering = replace(
        bound, plan_material=replace(bound.plan_material, state="reconciling")
    )
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(record),
        FakeReceipts(receipt),
        lambda _command: recovering,
        lambda: True,
        FakeDocker(identity),
    )
    assert adapter(command).classification == "APPLIED"
    with pytest.raises(identity_mod.ApplicationIdentityError):
        identity_mod.produce_application_identity(recovering)


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_completed_compensation_and_current_absence_are_observed_together(tmp_path):
    root, command, bound, identity, _record, _receipt = _installed(tmp_path)
    for name in (
        "manifest.yaml",
        "compose.yaml",
        "configuration.json",
        "compose.override.yaml",
    ):
        (root / name).unlink()
    recovering = replace(
        bound, plan_material=replace(bound.plan_material, state="reconciling")
    )
    receipts = FakeReceipts(
        fixtures._completed_snapshot(identity),
        fixtures._compensation_snapshot(identity),
    )
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(None),
        receipts,
        lambda _command: recovering,
        lambda: True,
        FakeDocker(identity, containers=False),
    )
    assert adapter(command).classification == "ABSENT"
    assert receipts.compensation_reads == 2


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_changed_compensation_receipt_refuses_absence(tmp_path):
    root, command, bound, identity, _record, _receipt = _installed(tmp_path)
    for name in (
        "manifest.yaml",
        "compose.yaml",
        "configuration.json",
        "compose.override.yaml",
    ):
        (root / name).unlink()
    recovering = replace(
        bound, plan_material=replace(bound.plan_material, state="reconciling")
    )
    receipts = FakeReceipts(
        fixtures._completed_snapshot(identity),
        fixtures._compensation_snapshot(identity),
        drift_compensation=True,
    )
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(None),
        receipts,
        lambda _command: recovering,
        lambda: True,
        FakeDocker(identity, containers=False),
    )
    with pytest.raises(adapter_mod.ApplicationEvidenceError) as error:
        adapter(command)
    assert error.value.code == "application-evidence-current-drift"


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_orphan_dependency_in_compose_project_blocks_compensated_absence(tmp_path):
    root, command, bound, identity, _record, _receipt = _installed(tmp_path)
    for name in (
        "manifest.yaml",
        "compose.yaml",
        "configuration.json",
        "compose.override.yaml",
    ):
        (root / name).unlink()
    recovering = replace(
        bound, plan_material=replace(bound.plan_material, state="reconciling")
    )
    docker = FakeDocker(identity, containers=False, orphan_project=True)
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(None),
        FakeReceipts(
            fixtures._completed_snapshot(identity),
            fixtures._compensation_snapshot(identity),
        ),
        lambda _command: recovering,
        lambda: True,
        docker,
    )
    with pytest.raises(fixtures.obs_mod.ApplicationObservationError) as error:
        adapter(command)
    assert error.value.code == "compensated-application-reappeared"
    assert any(
        "label=com.docker.compose.project=ods-af-documents" in call
        for call in docker.calls
    )


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_no_files_record_or_containers_is_absent(tmp_path):
    _root, command, bound, identity, _record, _receipt = _installed(tmp_path)
    # Only the owner-prepared base root stays; there is no application mutation.
    service = tmp_path / ".ods-assistant-first" / "applications" / fixtures.SERVICE_ID
    for name in (
        "manifest.yaml",
        "compose.yaml",
        "configuration.json",
        "compose.override.yaml",
    ):
        (service / name).unlink()
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(None),
        FakeReceipts(fixtures._absent_snapshot()),
        lambda _command: bound,
        lambda: True,
        FakeDocker(identity, containers=False),
    )
    assert adapter(command).classification == "ABSENT"


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
@pytest.mark.parametrize(
    "failure,code",
    [
        ("missing-record", "applied-record-required"),
        ("docker-down", "application-evidence-docker-unavailable"),
        ("docker-drift", "application-evidence-docker-drift"),
        ("lease-loss", "application-evidence-current-drift"),
    ],
)
def test_partial_or_unavailable_evidence_never_reports_success(tmp_path, failure, code):
    _root, command, bound, identity, record, receipt = _installed(tmp_path)
    calls = 0

    def lease():
        nonlocal calls
        calls += 1
        return failure != "lease-loss" or calls == 1

    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(None if failure == "missing-record" else record),
        FakeReceipts(receipt),
        lambda _command: bound,
        lease,
        FakeDocker(
            identity,
            unavailable=failure == "docker-down",
            drift=failure == "docker-drift",
        ),
    )
    with pytest.raises(
        adapter_mod.ApplicationEvidenceError
        if code.startswith("application-evidence")
        else Exception
    ) as error:
        adapter(command)
    assert error.value.code == code


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_symlinked_active_file_fails_closed(tmp_path):
    root, command, bound, identity, record, receipt = _installed(tmp_path)
    (root / "manifest.yaml").unlink()
    (root / "manifest.yaml").symlink_to(root / "compose.yaml")
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(record),
        FakeReceipts(receipt),
        lambda _command: bound,
        lambda: True,
        FakeDocker(identity),
    )
    with pytest.raises(adapter_mod.ApplicationEvidenceError) as error:
        adapter(command)
    assert error.value.code == "application-evidence-file-custody"


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_writable_configuration_file_fails_custody(tmp_path):
    root, command, bound, identity, record, receipt = _installed(tmp_path)
    (root / "configuration.json").chmod(0o666)
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(record),
        FakeReceipts(receipt),
        lambda _command: bound,
        lambda: True,
        FakeDocker(identity),
    )
    with pytest.raises(adapter_mod.ApplicationEvidenceError) as error:
        adapter(command)
    assert error.value.code == "application-evidence-file-custody"


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_intermediate_install_symlink_fails_closed(tmp_path):
    _root, command, bound, identity, record, receipt = _installed(tmp_path)
    alias = tmp_path.parent / (tmp_path.name + "-alias")
    alias.symlink_to(tmp_path, target_is_directory=True)
    adapter = adapter_mod.ApplicationObservationAdapter(
        alias,
        FakeRecords(record),
        FakeReceipts(receipt),
        lambda _command: bound,
        lambda: True,
        FakeDocker(identity),
    )
    with pytest.raises(adapter_mod.ApplicationEvidenceError) as error:
        adapter(command)
    assert error.value.code == "application-evidence-root-unavailable"


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_plan_loader_cannot_broaden_command(tmp_path):
    _root, command, bound, identity, record, receipt = _installed(tmp_path)
    docker = FakeDocker(identity)
    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(record),
        FakeReceipts(receipt),
        lambda _command: replace(bound, plan_hash="f" * 64),
        lambda: True,
        docker,
    )
    with pytest.raises(adapter_mod.ApplicationEvidenceError) as error:
        adapter(command)
    assert error.value.code == "application-evidence-plan-unavailable"
    assert docker.calls == []


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX host evidence only")
def test_duplicate_docker_inspect_key_is_refused(tmp_path):
    _root, command, bound, identity, record, receipt = _installed(tmp_path)

    class DuplicateDocker(FakeDocker):
        def __call__(self, argv):
            result = super().__call__(argv)
            if argv[1] == "inspect":
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    result.stdout.replace(b'"Id":', b'"Id":"bad","Id":', 1),
                    b"",
                )
            return result

    adapter = adapter_mod.ApplicationObservationAdapter(
        tmp_path,
        FakeRecords(record),
        FakeReceipts(receipt),
        lambda _command: bound,
        lambda: True,
        DuplicateDocker(identity),
    )
    with pytest.raises(adapter_mod.ApplicationEvidenceError) as error:
        adapter(command)
    assert error.value.code == "application-evidence-docker-invalid"
