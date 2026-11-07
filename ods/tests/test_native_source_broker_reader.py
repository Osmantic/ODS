"""Real bounded FDs/RuntimeCustody/bundle, explicit Linux Darwin metadata simulation.

No active projection producer, Source import, provider, service, account or real
ACL. Fixture v3 files represent Root's future authority, not owner permissions.
"""

# ruff: noqa: E402
from contextlib import contextmanager
import builtins
import copy
import io
import json
import os
from pathlib import Path
import platform
import plistlib
import sys
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

if sys.platform != "linux":
    pytest.skip(
        "Linux metadata simulation only, not physical Seatbelt/Darwin",
        allow_module_level=True,
    )
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "bin")]
from test_native_source_runtime_publication import built, source, package, helper  # noqa: F401
from test_native_source_custody import AppleACL
from pixel_connections import custody as s
from pixel_provider import runtime_custody as rt
import pixel_macos_custody as mac
import pixel_macos_process as processes

BOOT = "01234567-89AB-4DEF-8123-0123456789AB"
IDENTITY = {
    "schema": 1,
    "name": s.NATIVE_BROKER,
    "id": 60001,
    "userGuid": "11234567-89AB-4DEF-8123-0123456789AB",
    "groupGuid": "21234567-89AB-4DEF-8123-0123456789AB",
}


@pytest.fixture
def broker(built, tmp_path, monkeypatch):  # noqa: F811
    rootdir = tmp_path / "runtimes"
    rootdir.mkdir(mode=0o755)
    root = rootdir / built.digest
    built.root.rename(root)
    program = tmp_path / "program"
    (program / "pixel_connections").mkdir(parents=True, mode=0o755)
    (program / "pixel_connections/custody.py").write_bytes(
        (ROOT / "bin/pixel_connections/custody.py").read_bytes()
    )
    (program / "pixel_connections/custody.py").chmod(0o644)
    state = tmp_path / "broker-state"
    state.mkdir(mode=0o700)
    kit = tmp_path / "kit"
    kit.mkdir(mode=0o755)
    units = tmp_path / "definitions"
    units.mkdir(mode=0o755)
    metadata = tmp_path / "public"
    metadata.mkdir(mode=0o755)
    private = tmp_path / "root-private"
    private.mkdir(mode=0o700)
    owner = tmp_path / "owner-private"
    owner.mkdir(mode=0o700)
    (owner / "onboarding.json").write_text("invented must never read")
    for name, value in [
        ("NATIVE_PROGRAM", program),
        ("NATIVE_RUNTIMES", rootdir),
        ("NATIVE_STATE", state),
        ("NATIVE_ENV", state / "source-broker.env"),
        ("NATIVE_KIT", kit),
        ("NATIVE_DEFINITIONS", units),
        ("NATIVE_META", private),
    ]:
        monkeypatch.setattr(s, name, value)
    monkeypatch.setattr(s, "__file__", str(program / "pixel_connections/custody.py"))
    monkeypatch.setattr(helper, "PROGRAM_ROOT", program)
    monkeypatch.setattr(helper, "RUNTIME_ROOT", rootdir)
    monkeypatch.setattr(helper, "ROOT_META", private)
    monkeypatch.setattr(helper, "DESCRIPTOR", metadata / "pixel-provider-runtime.json")
    monkeypatch.setattr(
        helper, "RUNTIME_MANIFEST", metadata / "pixel-provider-runtime-manifest.json"
    )
    monkeypatch.setattr(rt, "DESCRIPTOR", helper.DESCRIPTOR)
    monkeypatch.setattr(rt, "NATIVE_MANIFEST", helper.RUNTIME_MANIFEST)
    monkeypatch.setattr(rt, "ROOT_UID", 0)
    # All Root metadata is simulated only for this disposable hierarchy.
    real_stat, real_lstat, real_fstat = os.stat, os.lstat, os.fstat
    gids = {}

    def virtual(info):
        data = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
        data["st_uid"] = 0
        data["st_gid"] = gids.get((info.st_dev, info.st_ino), 0)
        return SimpleNamespace(**data)

    def stat(path, *args, **kwargs):
        return virtual(real_stat(path, *args, **kwargs))

    def lstat(path, *args, **kwargs):
        return virtual(real_lstat(path, *args, **kwargs))

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(os, "fstat", lambda fd: virtual(real_fstat(fd)))

    def no_acl(fd):
        assert not any("acl" in name.casefold() for name in os.listxattr(fd))

    monkeypatch.setattr(rt, "_acl", no_acl)
    monkeypatch.setattr(mac, "_require_no_acl", no_acl)
    monkeypatch.setattr(s, "_native_no_acl", no_acl)

    def parents(path):
        path = Path(path)
        assert path.is_relative_to(tmp_path)
        assert path.resolve() == path
        for parent in path.parents:
            if parent == tmp_path.parent:
                break
            assert (
                parent.is_dir()
                and not parent.is_symlink()
                and not parent.stat().st_mode & 0o022
            )

    monkeypatch.setattr(rt, "_parents", parents)

    @contextmanager
    def directory(path, **kwargs):
        assert not kwargs and Path(path).is_relative_to(tmp_path)
        parents(Path(path) / "entry")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            assert os.fstat(fd).st_uid == 0
            no_acl(fd)
            yield fd
        finally:
            os.close(fd)

    monkeypatch.setattr(mac, "protected_directory", directory)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(s, "_native_broker_platform", lambda: None)
    monkeypatch.setattr(s, "_native_boot_uuid", lambda: BOOT)
    monkeypatch.setattr(
        s, "_native_clock_ns", lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    )
    acl = AppleACL()
    monkeypatch.setattr(s, "_native_acl_library", lambda: acl)
    launcher = program / "openclaw-gateway-launcher"
    launcher_bytes = (
        "#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH "
        + str(root / "node")
        + " "
        + str(root / "runtime/openclaw.mjs")
        + ' "$@"\n'
    ).encode()
    launcher.write_bytes(launcher_bytes)
    launcher.chmod(0o755)
    files = helper.runtime_files(root, built.digest, launcher_bytes, built.python)
    for path, body, mode in files:
        if path.parent == private:
            continue  # Broker must not require the Root receipt.
        path.write_bytes(body)
        path.chmod(mode)
    runtime, checksum = rt.RuntimeCustody(
        SimpleNamespace(binary=str(launcher))
    ).inspect()
    component = helper.bundle_payloads(
        root, ref=built.package[1], python=str(built.python), protected=True
    )
    for name in helper.PAYLOADS:
        (kit / name).write_bytes(component[name])
        (kit / name).chmod(0o755 if name == "broker.py" else 0o644)
    kit_doc = {
        "schemaVersion": 2,
        "kind": "ods-native-source-kit",
        "deploymentSha256": "d" * 64,
        "sourceRef": built.package[1],
        "files": {name: helper.sha(component[name]) for name in helper.PAYLOADS},
    }
    (kit / "source-kit.json").write_bytes(helper.encoded(kit_doc))
    (kit / "source-kit.json").chmod(0o644)
    template = s.parse_env(component["source-broker.env"].decode())
    env = s.env_text(template, s._native_environment_paths()).encode()
    envpath = state / "source-broker.env"
    envpath.write_bytes(env)
    envpath.chmod(0o600)
    envinfo = real_stat(envpath)
    acl.acls[(envinfo.st_dev, envinfo.st_ino)] = {
        "entries": [
            {
                "tag": 1,
                "mask": 2,
                "flags": 0,
                "uuid": uuid.UUID(IDENTITY["userGuid"]).bytes,
            }
        ],
        "flags": 0,
    }
    owner_guid = "31234567-89AB-4DEF-8123-0123456789AB"
    stateinfo = real_stat(state)
    acl.acls[(stateinfo.st_dev, stateinfo.st_ino)] = {
        "entries": [
            {"tag": 1, "mask": 10, "flags": 0, "uuid": uuid.UUID(guid).bytes}
            for guid in (owner_guid, IDENTITY["userGuid"])
        ],
        "flags": 0,
    }
    now = s._native_clock_ns()
    invocation = {
        "role": "sync",
        "identifier": None,
        "nonce": "2" * 32,
        "boot": BOOT,
        "clock": "darwin-clock-monotonic-raw-ns-v1",
        "generation": "1" * 32,
        "issuedNs": str(now),
        "admissionDeadlineNs": str(now + 30 * 10**9),
        "workerDeadlineNs": str(now + 300 * 10**9),
    }
    label = s.NATIVE_LABEL + "sync"
    _path, body, _mode = s.native_disabled_definition(
        "sync", identity=IDENTITY, python=runtime["hostPython"]
    )
    document = plistlib.loads(body)
    document["Disabled"] = False
    body = plistlib.dumps(document, sort_keys=True)
    definition = units / (label + ".plist")
    definition.write_bytes(body)
    definition.chmod(0o644)
    info = real_stat(definition)
    projection = {
        "schemaVersion": 3,
        "kind": "ods-native-source-custody",
        "phase": "active",
        "deploymentSha256": "d" * 64,
        "kitSha256": helper.sha((kit / "source-kit.json").read_bytes()),
        "ownerUid": 1000,
        "ownerGid": 1000,
        "ownerGuid": owner_guid,
        "broker": copy.deepcopy(IDENTITY),
        "state": str(state),
        "env": str(envpath),
        "kit": str(kit),
        "hostPython": runtime["hostPython"],
        "runtime": {
            "custodySha256": checksum,
            "bundleSha256": built.digest,
            "sourceRef": built.package[1],
        },
        "launch": {
            "generation": "1" * 32,
            "phase": "active",
            "boot": BOOT,
            "environment": {
                "inode": [envinfo.st_dev, envinfo.st_ino],
                "sha256": helper.sha(env),
            },
            "definitions": {
                label: {
                    "inode": [info.st_dev, info.st_ino],
                    "sha256": helper.sha(body),
                    "profileSha256": helper.sha(component["source-sync.sb"]),
                }
            },
            "invocations": {label: {"phase": "prepared", "invocation": invocation}},
        },
    }
    gate = state / "custody.json"
    gate.write_bytes(helper.encoded(projection))
    gate.chmod(0o640)
    info = real_stat(gate)
    gids[(info.st_dev, info.st_ino)] = 60001
    for name in ("getuid", "geteuid", "getgid", "getegid"):
        monkeypatch.setattr(os, name, lambda: 60001)
    monkeypatch.setattr(os, "getgroups", lambda: [])
    observed = []

    def self_identity(pid, *, uid, gid, executable):
        assert (
            pid == os.getpid()
            and uid == gid == 60001
            and executable == str(built.python)
        )
        observed.append(pid)
        return (pid, 1, 0, uid, gid, uid, gid, uid, gid, executable)

    monkeypatch.setattr(processes, "process_identity", self_identity)
    original_open, original_builtin_open, original_io_open = (
        os.open,
        builtins.open,
        io.open,
    )
    reads = []

    def trace(path, directory=None):
        if isinstance(path, int):
            path = os.readlink("/proc/self/fd/" + str(path))
        absolute = (
            Path(os.readlink("/proc/self/fd/" + str(directory))) / Path(path)
            if directory is not None
            else Path(path).absolute()
        )
        assert not absolute.is_relative_to(private) and not absolute.is_relative_to(
            owner
        )
        assert not absolute.is_relative_to(state / "private")
        if absolute.is_relative_to(tmp_path):
            reads.append(str(absolute))

    def opened(path, flags, *args, **kwargs):
        trace(path, kwargs.get("dir_fd"))
        return original_open(path, flags, *args, **kwargs)

    def builtin_opened(path, *args, **kwargs):
        trace(path)
        return original_builtin_open(path, *args, **kwargs)

    def io_opened(path, *args, **kwargs):
        trace(path)
        return original_io_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(builtins, "open", builtin_opened)
    monkeypatch.setattr(io, "open", io_opened)
    return SimpleNamespace(
        root=root,
        program=program,
        state=state,
        kit=kit,
        units=units,
        metadata=metadata,
        gate=gate,
        projection=projection,
        definition=definition,
        env=envpath,
        reads=reads,
        observed=observed,
        private=private,
        owner=owner,
        acl=acl,
        audit=tmp_path / "read-audit.json",
        simulated_gids=gids,
    )


def test_actual_public_reader_never_reads_owner_or_root_private_and_closes_env(
    broker, monkeypatch
):
    n = broker
    from pixel_connections import planner

    def private_reader(*args, **kwargs):
        pytest.fail("Broker must not call the owner/private deployment reader")

    monkeypatch.setattr(planner, "require_native_deployment", private_reader)
    monkeypatch.setattr(planner, "native_deployment", private_reader)
    monkeypatch.setattr(rt, "_read", private_reader)
    with s._native_broker_read("sync") as view:
        assert (
            s._native_public_json(view.public)["invocations"][s.NATIVE_LABEL + "sync"][
                "phase"
            ]
            == "prepared"
        )
        fd = view.environment_fd
        prefix = s._native_broker_sandbox_prefix(view)
        assert prefix[:3] == (
            "/usr/bin/sandbox-exec",
            "-D",
            "ODS_SOURCE_JOB_PLIST=" + str(n.definition),
        )
        assert prefix[3:5] == ("-f", str(n.kit / "source-sync.sb"))
        assert "PIXEL_SOURCE_GMAIL_QUERY" not in repr(view)
    with pytest.raises(OSError):
        os.fstat(fd)
    assert n.observed and any(
        name.endswith("pixel-provider-runtime.json") for name in n.reads
    )
    assert any(
        name.endswith("pixel-provider-runtime-manifest.json") for name in n.reads
    )
    assert n.acl.membership_queries == []  # No post-Seatbelt Directory Services lookup.
    n.audit.write_text(
        json.dumps(
            {
                "opened": sorted(set(n.reads)),
                "rootPrivateAttempts": 0,
                "ownerPrivateAttempts": 0,
                "privateStateBodiesOpened": 0,
                "runtimeInspectActual": True,
                "metadataAndCredentialsExplicitlySimulated": True,
            },
            sort_keys=True,
        )
        + "\n"
    )


def test_operation_cache_reuses_only_current_files_and_still_rejects_drift(broker):
    scope = s._native_broker_scope("sync", None)
    with s._native_broker_read("sync", _scope=scope):
        pass
    cache = scope.qualifier._cache
    assert len(cache) > 107
    with s._native_broker_read("sync", _scope=scope):
        pass
    assert scope.qualifier._cache is cache
    target = broker.root / "runtime" / "openclaw.mjs"
    target.write_bytes(
        target.read_bytes() + b"\n// changed after first qualification\n"
    )
    with pytest.raises(s.CustodyError, match="runtime-changed"):
        with s._native_broker_read("sync", _scope=scope):
            pass


@pytest.mark.parametrize(
    "change",
    ["inactive-v2", "foreign-id", "definition", "profile", "env", "boot", "runtime"],
)
def test_fixed_reader_refuses_drift_before_bootstrap(broker, monkeypatch, change):
    n = broker
    if change == "inactive-v2":
        value = copy.deepcopy(n.projection)
        value["schemaVersion"] = 2
        value["phase"] = "inactive"
        n.gate.write_bytes(helper.encoded(value))
    elif change == "foreign-id":
        with pytest.raises(s.CustodyError):
            with s._native_broker_read("action", "calendar-1893456789012-12abcdef"):
                pass
        assert (
            str(
                n.units
                / "com.ods.pixel-native-source-action.calendar-1893456789012-12abcdef.plist"
            )
            not in n.reads
        )
        return
    elif change == "definition":
        n.definition.write_bytes(n.definition.read_bytes() + b"\n")
    elif change == "profile":
        (n.kit / "source-sync.sb").write_bytes(b"(allow default)\n")
    elif change == "env":
        n.env.write_bytes(n.env.read_bytes() + b"PRIVATE=unadmitted\n")
    elif change == "boot":
        monkeypatch.setattr(
            s, "_native_boot_uuid", lambda: "41234567-89AB-4DEF-8123-0123456789AB"
        )
    else:
        (n.root / "runtime/openclaw.mjs").write_bytes(b"changed")
    with pytest.raises((s.CustodyError, ValueError)):
        with s._native_broker_read("sync"):
            pass


def test_inert_bootstrap_uses_real_reader_and_pipes_but_never_imports_source(broker):
    n = broker
    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    result = []

    def child():
        result.append(
            s._native_post_seatbelt_bootstrap(
                "sync", None, ready_fd=ready_w, go_fd=go_r
            )
        )

    thread = threading.Thread(target=child)
    thread.start()
    invocation = s._native_invocation(
        n.projection["launch"]["invocations"][s.NATIVE_LABEL + "sync"]["invocation"]
    )
    binding = s._native_pipe_binding(ready_r, writing=False)
    deadline = int(invocation.document["admissionDeadlineNs"])
    try:
        assert s._native_pipe_transfer(
            ready_r, binding, deadline=deadline
        ) == s._native_barrier_frame(invocation, "ready")
        assert result == []
        os.write(go_w, s._native_barrier_frame(invocation, "go"))
        thread.join(timeout=15)
        assert (
            not thread.is_alive()
            and result[0]["kind"] == "ods-native-source-bootstrap-inert"
        )
    finally:
        os.close(ready_r)
        os.close(go_w)
        thread.join(timeout=15)


def test_profile_additions_are_only_two_public_metadata_and_bound_parameter():
    for body in helper.profiles("/usr/bin/python3").values():
        assert b'(literal "/private/etc/ods/pixel-provider-runtime.json")' in body
        assert (
            b'(literal "/private/etc/ods/pixel-provider-runtime-manifest.json")' in body
        )
        assert b'(literal (param "ODS_SOURCE_JOB_PLIST"))' in body
        assert b'(subpath "/Library/LaunchDaemons")' not in body
        assert b'(subpath "/private/etc/ods")' not in body


def test_gate_directory_swap_during_actual_runtime_read_never_returns_retired_view(
    broker, monkeypatch
):
    n = broker
    original = s._native_broker_runtime
    retired = n.state.with_name("retired-gate")
    swapped = []

    def runtime(projection, qualifier):
        result = original(
            projection, qualifier
        )  # Actual inspect/full tree remains exercised.
        if not swapped:
            n.state.rename(retired)
            n.state.mkdir(mode=0o700)
            (n.state / "unknown").write_text("preserve, not this generation")
            swapped.append(True)
        return result

    monkeypatch.setattr(s, "_native_broker_runtime", runtime)
    with pytest.raises(s.CustodyError, match="gate-changed"):
        with s._native_broker_read("sync"):
            pytest.fail("retired FD must not be returned")
    assert (
        swapped and (n.state / "unknown").read_text() == "preserve, not this generation"
    )
    assert (retired / "custody.json").is_file()


def test_coherent_foreign_plist_args_still_refuse_independent_renderer(broker):
    n = broker
    value = plistlib.loads(n.definition.read_bytes())
    value["ProgramArguments"].append(
        "ODS_SOURCE_JOB_PLIST=/Library/LaunchDaemons/com.foreign.plist"
    )
    body = plistlib.dumps(value, sort_keys=True)
    n.definition.write_bytes(body)
    projection = copy.deepcopy(n.projection)
    projection["launch"]["definitions"][s.NATIVE_LABEL + "sync"]["sha256"] = helper.sha(
        body
    )
    n.gate.write_bytes(helper.encoded(projection))
    with pytest.raises(s.CustodyError, match="definition-changed"):
        with s._native_broker_read("sync"):
            pass
    assert not any(name.endswith("com.foreign.plist") for name in n.reads)


def test_wrong_env_principal_refuses_even_with_unchanged_content_hash(broker):
    n = broker
    info = n.env.stat()
    n.acl.acls[(info.st_dev, info.st_ino)]["entries"][0]["uuid"] = uuid.UUID(
        n.projection["ownerGuid"]
    ).bytes
    with pytest.raises(s.CustodyError, match="env-acl"):
        with s._native_broker_read("sync"):
            pass


def test_inactive_bootstrap_closes_its_pipe_ends_without_ready(broker):
    n = broker
    value = copy.deepcopy(n.projection)
    value["schemaVersion"] = 2
    value["phase"] = "inactive"
    n.gate.write_bytes(helper.encoded(value))
    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    try:
        with pytest.raises(s.CustodyError, match="inactive"):
            s._native_post_seatbelt_bootstrap(
                "sync", None, ready_fd=ready_w, go_fd=go_r
            )
        assert os.read(ready_r, 256) == b""
        with pytest.raises(BrokenPipeError):
            os.write(go_w, b"no receiver")
    finally:
        os.close(ready_r)
        os.close(go_w)


@pytest.mark.parametrize("change", ["sibling-only", "own-nonce", "common-deployment"])
def test_atomic_sibling_publication_preserves_only_the_original_own_authority(
    broker, monkeypatch, change
):
    n = broker
    original = s._native_broker_runtime
    own_label = s.NATIVE_LABEL + "sync"
    own = copy.deepcopy(n.projection["launch"]["invocations"][own_label])
    identifier = "calendar-1893456789012-12abcdef"
    sibling_label = s.NATIVE_LABEL + "action." + identifier
    published = []

    def runtime(projection, qualifier):
        result = original(projection, qualifier)
        if published:
            return result
        candidate = copy.deepcopy(n.projection)
        sibling = copy.deepcopy(own["invocation"])
        sibling.update(role="action", identifier=identifier, nonce="4" * 32)
        sibling["workerDeadlineNs"] = str(int(sibling["issuedNs"]) + 120 * 10**9)
        sibling["admissionDeadlineNs"] = sibling["workerDeadlineNs"]
        assert s._native_invocation(sibling).document == sibling
        path, body, _mode = s.native_disabled_definition(
            "action", identity=IDENTITY, python=result[0]["hostPython"], identifier=identifier
        )
        document = plistlib.loads(body)
        document["Disabled"] = False
        body = plistlib.dumps(document, sort_keys=True)
        path = n.units / path.name
        path.write_bytes(body)
        path.chmod(0o644)
        info = path.stat()
        candidate["launch"]["definitions"][sibling_label] = {
            "inode": [info.st_dev, info.st_ino],
            "sha256": helper.sha(body),
            "profileSha256": helper.sha((n.kit / "source-action.sb").read_bytes()),
        }
        candidate["launch"]["invocations"][sibling_label] = {
            "phase": "prepared", "invocation": sibling,
        }
        if change == "own-nonce":
            candidate["launch"]["invocations"][own_label]["invocation"]["nonce"] = "5" * 32
        elif change == "common-deployment":
            candidate["deploymentSha256"] = "e" * 64
        pending = n.state / ".custody-next"
        pending.write_bytes(helper.encoded(candidate))
        pending.chmod(0o640)
        info = pending.stat()
        n.simulated_gids[(info.st_dev, info.st_ino)] = IDENTITY["id"]
        os.replace(pending, n.gate)
        published.append(candidate)
        n.reads.clear()  # Record subsequent reader I/O, excluding this Root fixture writer.
        return result

    monkeypatch.setattr(s, "_native_broker_runtime", runtime)
    if change == "sibling-only":
        with s._native_broker_read("sync") as view:
            snapshot = s._native_public_json(view.public)
            assert snapshot["invocations"][own_label] == own
            assert sibling_label in snapshot["invocations"]
            assert view.invocation.document == own["invocation"]
    else:
        with pytest.raises(s.CustodyError, match="gate-changed"):
            with s._native_broker_read("sync"):
                pytest.fail("a changed own nonce/common binding must not survive")
    assert published
    assert not any(name.endswith(sibling_label + ".plist") for name in n.reads)
