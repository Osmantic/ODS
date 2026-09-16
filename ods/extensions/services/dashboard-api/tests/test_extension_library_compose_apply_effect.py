"""Adversarial tests for the dormant generic library Compose effect."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
import yaml


ODS = Path(__file__).resolve().parents[4]
BIN = ODS / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import extension_application_identity as application_identity  # noqa: E402
import extension_application_record_store as record_store  # noqa: E402
import extension_document_digest as document_digest  # noqa: E402
import extension_library_compose_apply_effect as effect  # noqa: E402
import extension_library_install_materializer as materializer  # noqa: E402
from extension_library_configuration_binding import (  # noqa: E402
    BoundLibraryConfiguration,
)
from extension_library_effect_input import VerifiedLibraryEffectInput  # noqa: E402
from extension_library_tree_digest import snapshot_extension_tree  # noqa: E402
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedHostPort,
    PlannedImage,
    PlannedOperation,
)
from extension_lifecycle_work import (  # noqa: E402
    LifecycleWorkCommand,
    LifecycleWorkValidationError,
)


SUPPORTED = (
    os.name == "posix"
    and effect.fcntl is not None
    and all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW"))
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.stat, os.mkdir, os.link, os.unlink)
    )
    and os.stat in os.supports_follow_symlinks
)
pytestmark = pytest.mark.skipif(not SUPPORTED, reason="requires Linux dirfd custody")

TXN = "txn-" + "1" * 24
PLAN = "2" * 64
REQUEST = "3" * 64
SCHEMA = "4" * 64
REFERENCE = "secret-v1-" + "5" * 48
ADMIN_SECRET = "miniflux-admin-sensitive-value"
DB_SECRET = "miniflux-database-sensitive-value"
LIBRARY = ODS / "extensions" / "library" / "services"

VALUES = {
    "gitea": (
        ("GITEA_APP_NAME", "ODS Git"),
        ("GITEA_HOST", "localhost"),
        ("GITEA_PORT", 7830),
        ("GITEA_SSH_PORT", 2222),
    ),
    "miniflux": (
        ("MINIFLUX_BASE_URL", "http://localhost:8098"),
        ("MINIFLUX_PORT", 8098),
    ),
    "ntfy": (
        ("NTFY_BASE_URL", "http://localhost:8097"),
        ("NTFY_PORT", 8097),
    ),
    "ollama": (("EXT_OLLAMA_PORT", 7804), ("OLLAMA_MODEL", "llama3")),
}
SERVICES = {
    "gitea": ("gitea",),
    "miniflux": ("miniflux", "miniflux-db"),
    "ntfy": ("ntfy",),
    "ollama": ("ollama",),
}
HOST_PORTS = {
    "gitea": (2222, 7830),
    "miniflux": (8098,),
    "ntfy": (8097,),
    "ollama": (7804,),
}


@pytest.fixture(autouse=True)
def private_umask():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    install = tmp_path / "install"
    users = tmp_path / "user-extensions"
    install.mkdir(mode=0o700)
    users.mkdir(mode=0o700)
    install.chmod(0o700)
    users.chmod(0o700)
    return install, users


def _inputs(tmp_path: Path, service_id: str = "miniflux") -> SimpleNamespace:
    source = tmp_path / ("approved-" + service_id)
    shutil.copytree(LIBRARY / service_id, source)
    source.chmod(0o700)
    for item in source.rglob("*"):
        if item.is_dir():
            item.chmod(0o755)
        elif item.is_file():
            executable = bool(item.stat().st_mode & 0o111)
            item.chmod(0o755 if executable else 0o644)
    payload = snapshot_extension_tree(source)
    manifest = (source / "manifest.yaml").read_bytes()
    compose = (source / "compose.yaml").read_bytes()
    definition = PlannedDefinition(
        service_id=service_id,
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256=document_digest.canonical_document_sha256(manifest),
        compose_sha256=document_digest.canonical_document_sha256(compose),
        definition_source="library",
        compose_file="compose.yaml",
        images=(
            PlannedImage(
                reference=f"example.invalid/{service_id}:1.0.0",
                digest="sha256:" + "6" * 64,
                download_bytes=123,
            ),
        ),
        builds=(),
        canonical_document=manifest,
        host_ports=tuple(PlannedHostPort("tcp", port) for port in HOST_PORTS[service_id]),
        source_tree_sha256=payload.digest,
    )
    material = LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN,
        plan_hash=PLAN,
        state="applying",
        operations=(PlannedOperation(service_id, "install"),),
        definitions=(definition,),
        attested_approval=True,
    )
    command = LifecycleWorkCommand(
        transaction_id=TXN,
        plan_hash=PLAN,
        operation_key=f"apply:{service_id}",
        request_hash=REQUEST,
        service_ids=(service_id,),
        payload={"operation": {"serviceId": service_id, "action": "install"}},
        timeout_seconds=900,
        plan_material=material,
    )
    secret_keys = (
        ("MINIFLUX_ADMIN_PASSWORD", "MINIFLUX_DB_PASSWORD")
        if service_id == "miniflux"
        else ()
    )
    configuration = BoundLibraryConfiguration(
        transaction_id=TXN,
        plan_hash=PLAN,
        service_id=service_id,
        schema_hash=SCHEMA,
        values=VALUES[service_id],
        secret_keys=secret_keys,
        expected_secret_keys=secret_keys,
        secret_reference=REFERENCE if secret_keys else None,
        configured=bool(secret_keys),
    )
    verified = VerifiedLibraryEffectInput(
        transaction_id=TXN,
        plan_hash=PLAN,
        service_id=service_id,
        action="install",
        payload=payload,
    )
    identity = application_identity.produce_application_identity(command)
    return SimpleNamespace(
        command=command,
        configuration=configuration,
        effect_input=verified,
        identity=identity,
        manifest=manifest,
        compose=compose,
        services=SERVICES[service_id],
    )


class FakeSecrets:
    def __init__(self):
        self.requests = []

    def invoke_with_secrets(self, request, consumer):
        self.requests.append(request)
        consumer(
            MappingProxyType(
                {
                    "MINIFLUX_ADMIN_PASSWORD": ADMIN_SECRET,
                    "MINIFLUX_DB_PASSWORD": DB_SECRET,
                }
            )
        )
        return {"configured": True}


class ComposeHarness:
    def __init__(self, service_id: str, *, failure: str | None = None):
        self.service_id = service_id
        self.failure = failure
        self.calls = []
        self.environments = []

    def _remember(self, kind, argv, environment, timeout, maximum=None):
        self.environments.append(environment)
        self.calls.append(
            (kind, argv, dict(environment), timeout, maximum)
        )

    def runner(self, argv, environment, timeout):
        self._remember("run", argv, environment, timeout)
        return self.failure != "runner"

    def capture(self, argv, environment, timeout, maximum):
        self._remember("capture", argv, environment, timeout, maximum)
        if argv[-2:] == ("config", "--services"):
            if self.failure == "services":
                return b"duplicate\nduplicate\n"
            return ("\n".join(SERVICES[self.service_id]) + "\n").encode()
        assert argv[-4:] == ("ps", "--all", "--format", "json")
        if self.failure == "containers":
            return b'{"Service":"wrong"}\n'
        project = "ods-af-" + self.service_id
        rows = [
            {
                "Service": service,
                "Name": "ods-" + service,
                "Project": project,
            }
            for service in SERVICES[self.service_id]
        ]
        return ("\n".join(json.dumps(row, separators=(",", ":")) for row in rows)).encode()


def _active(install: Path, service_id: str) -> Path:
    return install / ".ods-assistant-first" / "applications" / service_id


def _all_file_bytes(root: Path) -> bytes:
    return b"".join(
        path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )


def test_miniflux_full_graph_secret_boundary_and_record_last(tmp_path):
    install, users = _roots(tmp_path)
    inputs = _inputs(tmp_path)
    harness = ComposeHarness("miniflux")
    secrets = FakeSecrets()

    result = effect.LibraryComposeApplyEffect(
        install, users, harness.runner, harness.capture
    ).apply(
        inputs.command,
        inputs.effect_input,
        inputs.configuration,
        inputs.identity,
        secrets,
    )

    active = _active(install, "miniflux")
    target = users / "miniflux"
    assert result.materialization_outcome == "published"
    assert result.active_files_outcome == "materialized"
    assert result.record_outcome == "created"
    assert result.compose_services == ("miniflux", "miniflux-db")
    assert result.expected_containers == ("ods-miniflux", "ods-miniflux-db")
    assert (target / "manifest.yaml").read_bytes() == inputs.manifest
    assert (target / "compose.yaml").read_bytes() == inputs.compose
    assert (target / materializer.RECEIPT_NAME).is_file()
    assert (active / "manifest.yaml").read_bytes() == inputs.manifest
    assert (active / "compose.yaml").read_bytes() == inputs.compose
    metadata = json.loads((active / "configuration.json").read_bytes())
    assert metadata == {
        "configured": True,
        "identitySha256": inputs.identity.identity_sha256,
        "planHash": PLAN,
        "schema": "ods.assistant-first.active-library-configuration.v1",
        "schemaHash": SCHEMA,
        "secretKeys": ["MINIFLUX_ADMIN_PASSWORD", "MINIFLUX_DB_PASSWORD"],
        "secretReference": REFERENCE,
        "serviceId": "miniflux",
        "transactionId": TXN,
        "values": {
            "MINIFLUX_BASE_URL": "http://localhost:8098",
            "MINIFLUX_PORT": 8098,
        },
    }
    override = yaml.safe_load((active / "compose.override.yaml").read_bytes())
    assert set(override["services"]) == {"miniflux", "miniflux-db"}
    assert all(
        item["labels"] == application_identity.identity_labels(inputs.identity)
        for item in override["services"].values()
    )
    assert all(
        stat.S_IMODE((active / name).stat().st_mode) == 0o600
        for name in (
            "manifest.yaml",
            "compose.yaml",
            "configuration.json",
            "compose.override.yaml",
        )
    )
    assert secrets.requests == [
        {
            "schema": "ods.assistant-first.secret-use-request.v1",
            "transactionId": TXN,
            "planHash": PLAN,
            "schemaHash": SCHEMA,
            "reference": REFERENCE,
            "expectedSecretKeys": [
                "MINIFLUX_ADMIN_PASSWORD",
                "MINIFLUX_DB_PASSWORD",
            ],
        }
    ]
    assert [item[0] for item in harness.calls] == ["capture", "run", "capture"]
    discovery, apply_call, observation = harness.calls
    assert discovery[1][-2:] == ("config", "--services")
    assert apply_call[1][-5:] == ("up", "-d", "--no-build", "--pull", "never")
    assert "--no-deps" not in apply_call[1]
    assert apply_call[1][apply_call[1].index("--project-directory") + 1] == str(
        target
    )
    assert apply_call[1].count("-f") == 2
    assert observation[1][-4:] == ("ps", "--all", "--format", "json")
    assert all(reference == {} for reference in harness.environments)
    assert all(
        set(call[2])
        == {
            "PATH",
            "BIND_ADDRESS",
            "MINIFLUX_BASE_URL",
            "MINIFLUX_PORT",
            "MINIFLUX_ADMIN_PASSWORD",
            "MINIFLUX_DB_PASSWORD",
        }
        for call in harness.calls
    )
    record = record_store.ApplicationRecordStore(active.parent).snapshot("miniflux")
    assert record is not None
    assert record.identity_sha256 == inputs.identity.identity_sha256
    assert record.expected_containers == result.expected_containers
    assert record.config_sha256 == result.config_sha256
    stored = _all_file_bytes(install) + _all_file_bytes(users)
    assert ADMIN_SECRET.encode() not in stored
    assert DB_SECRET.encode() not in stored
    assert ADMIN_SECRET not in repr(result)
    assert DB_SECRET not in repr(result)


@pytest.mark.parametrize("service_id", ["gitea", "ntfy", "ollama"])
def test_default_only_apps_apply_without_opening_secret_store(tmp_path, service_id):
    install, users = _roots(tmp_path)
    inputs = _inputs(tmp_path, service_id)
    harness = ComposeHarness(service_id)

    class NoSecrets:
        def invoke_with_secrets(self, *_args):  # pragma: no cover - must not run
            raise AssertionError("secret store must remain closed")

    result = effect.LibraryComposeApplyEffect(
        install, users, harness.runner, harness.capture
    ).apply(
        inputs.command,
        inputs.effect_input,
        inputs.configuration,
        inputs.identity,
        NoSecrets(),
    )

    assert result.compose_services == SERVICES[service_id]
    assert result.expected_containers == tuple(
        sorted("ods-" + item for item in SERVICES[service_id])
    )
    assert all(reference == {} for reference in harness.environments)


def test_unapproved_or_misaligned_inputs_fail_before_any_effect(tmp_path):
    install, users = _roots(tmp_path)
    inputs = _inputs(tmp_path)
    calls = []
    runtime = effect.LibraryComposeApplyEffect(
        install,
        users,
        lambda *args: calls.append(args) or True,
        lambda *args: calls.append(args) or b"",
    )
    unapproved = replace(
        inputs.command,
        plan_material=replace(
            inputs.command.plan_material, attested_approval=False
        ),
    )
    invalid = (
        (unapproved, inputs.effect_input, inputs.configuration, inputs.identity),
        (
            inputs.command,
            inputs.effect_input,
            replace(
                inputs.configuration,
                values=inputs.configuration.values + (("COMPOSE_PROJECT_NAME", "bad"),),
            ),
            inputs.identity,
        ),
        (
            inputs.command,
            inputs.effect_input,
            replace(
                inputs.configuration,
                values=tuple(
                    item
                    for item in inputs.configuration.values
                    if item[0] != "MINIFLUX_PORT"
                ),
            ),
            inputs.identity,
        ),
        (
            inputs.command,
            inputs.effect_input,
            replace(
                inputs.configuration,
                values=tuple(
                    (key, 9001 if key == "MINIFLUX_PORT" else value)
                    for key, value in inputs.configuration.values
                ),
            ),
            inputs.identity,
        ),
        (
            inputs.command,
            replace(
                inputs.effect_input,
                payload=inputs.effect_input.payload._replace(
                    digest="sha256:" + "0" * 64
                ),
            ),
            inputs.configuration,
            inputs.identity,
        ),
    )
    for arguments in invalid:
        with pytest.raises(LifecycleWorkValidationError):
            runtime.apply(*arguments, FakeSecrets())
    assert calls == []
    assert list(install.iterdir()) == []
    assert list(users.iterdir()) == []


@pytest.mark.parametrize(
    "service_id,port_key",
    [
        ("gitea", "GITEA_PORT"),
        ("gitea", "GITEA_SSH_PORT"),
        ("miniflux", "MINIFLUX_PORT"),
        ("ntfy", "NTFY_PORT"),
        ("ollama", "EXT_OLLAMA_PORT"),
    ],
)
def test_pilot_app_port_must_be_reserved_by_approved_plan(
    tmp_path, service_id, port_key
):
    install, users = _roots(tmp_path)
    inputs = _inputs(tmp_path, service_id)
    calls = []
    runtime = effect.LibraryComposeApplyEffect(
        install,
        users,
        lambda *args: calls.append(args) or True,
        lambda *args: calls.append(args) or b"",
    )
    values = tuple(
        (key, 9001 if key == port_key else value)
        for key, value in inputs.configuration.values
    )
    with pytest.raises(LifecycleWorkValidationError) as caught:
        runtime.apply(
            inputs.command,
            inputs.effect_input,
            replace(inputs.configuration, values=values),
            inputs.identity,
            FakeSecrets(),
        )
    assert caught.value.code == "library-compose-port-plan-mismatch"
    assert calls == []
    assert list(install.iterdir()) == []
    assert list(users.iterdir()) == []


@pytest.mark.parametrize("failure", ["services", "runner", "containers"])
def test_any_post_materialization_failure_is_uncertain_without_active_record(
    tmp_path, failure
):
    install, users = _roots(tmp_path)
    inputs = _inputs(tmp_path)
    harness = ComposeHarness("miniflux", failure=failure)

    with pytest.raises(effect.LibraryComposeApplyUncertain) as caught:
        effect.LibraryComposeApplyEffect(
            install, users, harness.runner, harness.capture
        ).apply(
            inputs.command,
            inputs.effect_input,
            inputs.configuration,
            inputs.identity,
            FakeSecrets(),
        )

    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert (users / "miniflux" / materializer.RECEIPT_NAME).is_file()
    assert not (
        _active(install, "miniflux").parent / record_store.SNAPSHOT_NAME
    ).exists()
    assert ADMIN_SECRET not in str(caught.value)
    assert DB_SECRET not in str(caught.value)


def test_materialization_conflict_is_uncertain_and_preserves_owner_files(tmp_path):
    install, users = _roots(tmp_path)
    inputs = _inputs(tmp_path)
    target = users / "miniflux"
    target.mkdir(mode=0o700)
    marker = target / "owner.txt"
    marker.write_bytes(b"keep\n")
    calls = []

    with pytest.raises(effect.LibraryComposeApplyUncertain):
        effect.LibraryComposeApplyEffect(
            install,
            users,
            lambda *args: calls.append(args) or True,
            lambda *args: calls.append(args) or b"",
        ).apply(
            inputs.command,
            inputs.effect_input,
            inputs.configuration,
            inputs.identity,
            FakeSecrets(),
        )

    assert marker.read_bytes() == b"keep\n"
    assert calls == []
    assert not (
        _active(install, "miniflux").parent / record_store.SNAPSHOT_NAME
    ).exists()


def test_exact_replay_preserves_files_and_repeats_observation(tmp_path):
    install, users = _roots(tmp_path)
    inputs = _inputs(tmp_path)
    harness = ComposeHarness("miniflux")
    runtime = effect.LibraryComposeApplyEffect(
        install, users, harness.runner, harness.capture
    )
    first = runtime.apply(
        inputs.command,
        inputs.effect_input,
        inputs.configuration,
        inputs.identity,
        FakeSecrets(),
    )
    active = _active(install, "miniflux")
    names = ("manifest.yaml", "compose.yaml", "configuration.json", "compose.override.yaml")
    active_inodes = tuple((active / name).stat().st_ino for name in names)
    user_inode = (users / "miniflux").stat().st_ino

    second = runtime.apply(
        inputs.command,
        inputs.effect_input,
        inputs.configuration,
        inputs.identity,
        FakeSecrets(),
    )

    assert first.materialization_outcome == "published"
    assert second.materialization_outcome == "replayed"
    assert second.active_files_outcome == "replayed"
    assert second.record_outcome == "replayed"
    assert tuple((active / name).stat().st_ino for name in names) == active_inodes
    assert (users / "miniflux").stat().st_ino == user_inode
    assert [item[0] for item in harness.calls] == [
        "capture",
        "run",
        "capture",
        "capture",
        "run",
        "capture",
    ]


def test_production_host_agent_registers_only_through_the_bounded_runtime():
    host_source = (ODS / "bin" / "ods-host-agent.py").read_text(encoding="utf-8")
    runtime_source = (
        ODS / "bin" / "extension_library_application_runtime.py"
    ).read_text(encoding="utf-8")
    assert "extension_library_application_runtime" in host_source
    assert "extension_library_compose_apply_effect" not in host_source
    assert "LibraryComposeApplyEffect" not in host_source
    assert "from extension_library_compose_apply_effect import" in runtime_source
    assert "build_library_application_runtime" in runtime_source
