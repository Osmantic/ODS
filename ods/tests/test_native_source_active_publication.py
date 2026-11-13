"""Real private Root publisher FDs/CAS/renames; explicit Linux Darwin metadata simulation.

No launchd mutation, account, actual ACL, Source/provider/SDK/model execution.
The actual typed bridge/RuntimeCustody/qualified bundle fixtures are retained.
"""

# ruff: noqa: E402
import copy
import json
import os
from pathlib import Path
import plistlib
import sys
import time

import pytest

if sys.platform != "linux" or not Path("/proc/self/fd").is_dir():
    pytest.skip(
        "Linux/procfs metadata simulation, not physical Darwin", allow_module_level=True
    )
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_generation import generation, SPECS  # noqa: F401
from test_native_source_custody import native, launchctl_system  # noqa: F401
from test_connection_deployment_paths import deployment  # noqa: F401
from test_native_source_runtime_publication import built, source, package  # noqa: F401
from test_native_source_publication import material, publication, helper  # noqa: F401
from pixel_connections import custody

BOOT = "01234567-89AB-4DEF-8123-0123456789AB"


@pytest.fixture
def active(generation, monkeypatch):  # noqa: F811
    n = generation
    helper.publish_disabled_generation(n.selected, SPECS[:2])
    n.initial_gate = (custody.NATIVE_STATE / "custody.json").read_bytes()
    n.initial_inode = (custody.NATIVE_STATE / "custody.json").stat().st_ino
    n.initial_manifest = (n.metadata / "native-source-jobs/manifest.json").read_bytes()
    n.initial_defs = {
        path.name: (path.read_bytes(), path.stat().st_ino) for path in n.units.iterdir()
    }
    n.now = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(custody, "_native_clock_ns", lambda: n.now)
    monkeypatch.setattr(custody, "_native_boot_uuid", lambda: BOOT)
    original_open = os.open

    def opened(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if flags & os.O_CREAT and flags & os.O_EXCL:
            location = Path(os.readlink("/proc/self/fd/" + str(fd)))
            if location.is_relative_to(n.world):
                # Actual newly-created inode; discard only stale simulated
                # Darwin metadata left by an earlier unlinked incarnation.
                info = n.real_fstat(fd)
                n.owners.pop((info.st_dev, info.st_ino), None)
                n.acl.acls.pop((info.st_dev, info.st_ino), None)
        return fd

    monkeypatch.setattr(os, "open", opened)
    return n


def journal(n):
    return json.loads((n.metadata / "source-jobs-active.json").read_bytes())


def gate():
    return json.loads((custody.NATIVE_STATE / "custody.json").read_bytes())


def test_actual_root_publisher_v2_to_v3_commits_gate_last_and_same_id_only_observes(
    active, monkeypatch
):
    n = active
    original = os.replace
    commits = []

    def replace(first, second, **kwargs):
        if second == "custody.json":
            record = journal(n)
            assert record["phase"] == "gate-ready"
            assert (
                custody.NATIVE_STATE / "custody.json"
            ).read_bytes() == n.initial_gate
            assert (
                plistlib.loads(
                    (n.units / (custody.NATIVE_LABEL + "sync.plist")).read_bytes()
                )["Disabled"]
                is False
            )
            assert (
                json.loads(
                    (n.metadata / "native-source-jobs/manifest.json").read_bytes()
                )["schemaVersion"]
                == 2
            )
            commits.append(str(first))
        return original(first, second, **kwargs)

    monkeypatch.setattr(os, "replace", replace)
    result = helper.publish_active_slot(n.selected, "sync")
    assert result["status"] == "prepared" and len(commits) == 1
    projection = gate()
    assert (
        projection["schemaVersion"] == 3 and projection["launch"]["phase"] == "active"
    )
    assert (
        projection["launch"]["invocations"][result["label"]]["invocation"]
        == result["invocation"]
    )
    assert int(result["invocation"]["admissionDeadlineNs"]) - n.now == 30 * 10**9
    assert int(result["invocation"]["workerDeadlineNs"]) - n.now == 300 * 10**9
    assert (
        custody._native_broker_projection(projection)["generation"]
        == result["invocation"]["generation"]
    )
    before = (n.metadata / "source-jobs-active.json").read_bytes()
    n.now += 31 * 10**9  # Sync worker remains valid; a new startup does not.
    observed = helper.publish_active_slot(n.selected, "sync")
    assert observed["status"] == "observed" and observed["eligible"] is False
    assert observed["invocation"] == result["invocation"]
    assert (n.metadata / "source-jobs-active.json").read_bytes() == before and len(
        commits
    ) == 1
    assert all(
        argv[1] not in ("bootstrap", "kickstart", "enable", "bootout")
        for argv in n.commands
    )


def test_additive_slot_keeps_loaded_sibling_frames_and_inputs_unchanged(
    active, monkeypatch
):
    n = active
    first = helper.publish_active_slot(n.selected, "sync")
    before = copy.deepcopy(gate())
    original = n.f.bridge.command

    def command(argv, **kwargs):
        if argv == ["/bin/launchctl", "print", "system"]:
            return launchctl_system(first["label"])
        return original(argv, **kwargs)

    monkeypatch.setattr(n.f.bridge, "command", command)
    monkeypatch.setattr(
        helper,
        "_no_actors",
        lambda *args, **kwargs: pytest.fail(
            "additive path must not reuse DATA-only no-actors"
        ),
    )
    second = helper.publish_active_slot(
        n.selected, "action", "calendar-1893456789012-12abcdef"
    )
    after = gate()
    assert second["status"] == "prepared"
    assert (
        before["launch"]["invocations"][first["label"]]
        == after["launch"]["invocations"][first["label"]]
    )
    assert (
        before["launch"]["definitions"][first["label"]]
        == after["launch"]["definitions"][first["label"]]
    )
    for key in before:
        if key != "launch":
            assert before[key] == after[key]
    invocation = custody._native_invocation(first["invocation"])
    assert custody._native_slot_matches(
        custody._native_broker_projection(after), invocation
    )
    assert (
        custody.NATIVE_ENV.read_bytes() == n.env_before
        and custody.NATIVE_ENV.stat().st_ino == n.env_inode
    )
    with pytest.raises(custody.CustodyError, match="generation-requires-lifecycle"):
        custody.native_publish_environment(n.selected, n.env_before.decode())


@pytest.mark.parametrize(
    "where",
    [
        "empty-prefix",
        "old-definition",
        "new-definition",
        "old-manifest",
        "new-manifest",
        "before-gate",
        "after-gate",
        "metadata-commit",
    ],
)
def test_interrupted_actual_publication_resumes_same_nonce_and_exact_images(
    active, monkeypatch, where
):
    n = active
    original_save, original_rename, original_replace = (
        custody._native_save_at,
        os.rename,
        os.replace,
    )
    hit = []

    def save(selection, directory, name, value, **kwargs):
        result = original_save(selection, directory, name, value, **kwargs)
        if (
            where == "empty-prefix"
            and name == "source-jobs-active.json"
            and not hit
            and value["transaction"]
            and value["transaction"]["images"]["definition"]["inode"] is not None
            and not value["transaction"]["images"]["definition"]["ready"]
        ):
            hit.append(True)
            raise OSError("invented durable prefix interruption")
        if (
            where == "metadata-commit"
            and name == "source-jobs-active.json"
            and not hit
            and value["phase"] == "committed"
            and value["transaction"] is not None
        ):
            hit.append(True)
            raise OSError("invented committed metadata acknowledgement loss")
        return result

    def rename(first, second, **kwargs):
        selected = (
            where == "old-definition"
            and str(second).endswith(".definition-old")
            or where == "new-definition"
            and str(first).endswith(".definition")
            or where == "old-manifest"
            and str(second).endswith(".manifest-old")
            or where == "new-manifest"
            and str(first).endswith(".manifest")
        )
        result = original_rename(first, second, **kwargs)
        if selected and not hit:
            hit.append(True)
            raise OSError("invented lost rename acknowledgement")
        return result

    def replace(first, second, **kwargs):
        selected = (
            second == "custody.json"
            and where in ("before-gate", "after-gate")
            and not hit
        )
        if selected and where == "before-gate":
            hit.append(True)
            raise OSError("invented before Gate replace")
        result = original_replace(first, second, **kwargs)
        if selected:
            hit.append(True)
            raise OSError("invented lost Gate acknowledgement")
        return result

    monkeypatch.setattr(custody, "_native_save_at", save)
    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(os, "replace", replace)
    with pytest.raises(OSError, match="invented"):
        helper.publish_active_slot(n.selected, "sync")
    before = journal(n)
    assert hit and (custody.NATIVE_STATE / "custody.json").exists()
    if where in ("after-gate", "metadata-commit"):
        n.now = int(before["transaction"]["invocation"]["admissionDeadlineNs"])
    monkeypatch.setattr(custody, "_native_save_at", original_save)
    monkeypatch.setattr(os, "rename", original_rename)
    monkeypatch.setattr(os, "replace", original_replace)
    result = helper.publish_active_slot(n.selected, "sync")
    assert result["invocation"] == before["transaction"]["invocation"]
    assert journal(n)["phase"] == "committed"
    for key, image in before["transaction"]["images"].items():
        if image["inode"] is not None:
            assert journal(n)["transaction"]["images"][key]["inode"] == image["inode"]


def test_expired_uncommitted_admission_holds_without_renewal_and_can_rollback(
    active, monkeypatch
):
    n = active
    original = os.replace

    def stop(first, second, **kwargs):
        if second == "custody.json":
            raise OSError("invented before Gate")
        return original(first, second, **kwargs)

    monkeypatch.setattr(os, "replace", stop)
    with pytest.raises(OSError):
        helper.publish_active_slot(n.selected, "sync")
    before = journal(n)
    monkeypatch.setattr(os, "replace", original)
    n.now += 31 * 10**9
    with pytest.raises(ValueError, match="admission-expired"):
        helper.publish_active_slot(n.selected, "sync")
    assert (
        journal(n)["transaction"]["invocation"] == before["transaction"]["invocation"]
    )
    assert (custody.NATIVE_STATE / "custody.json").read_bytes() == n.initial_gate
    assert (
        helper.publish_active_slot(n.selected, "sync", rollback=True)["status"]
        == "restored"
    )
    assert (custody.NATIVE_STATE / "custody.json").read_bytes() == n.initial_gate
    assert {
        p.name: (p.read_bytes(), p.stat().st_ino) for p in n.units.iterdir()
    } == n.initial_defs
    with pytest.raises(ValueError, match="explicit-recovery"):
        helper.publish_active_slot(n.selected, "sync")


def test_committed_rollback_restores_bytes_and_definitions_without_claiming_old_gate_inode(
    active,
):
    n = active
    helper.publish_active_slot(n.selected, "sync")
    result = helper.publish_active_slot(n.selected, "sync", rollback=True)
    assert result["status"] == "restored"
    assert (custody.NATIVE_STATE / "custody.json").read_bytes() == n.initial_gate
    assert (custody.NATIVE_STATE / "custody.json").stat().st_ino != n.initial_inode
    assert {
        p.name: (p.read_bytes(), p.stat().st_ino) for p in n.units.iterdir()
    } == n.initial_defs
    assert (
        n.metadata / "native-source-jobs/manifest.json"
    ).read_bytes() == n.initial_manifest


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "retired",
        "gate-inode",
        "gate-bytes",
        "environment",
        "definition",
        "foreign-job",
        "extra-file",
        "duck-selection",
    ],
)
def test_unknown_or_drift_is_preserved_without_new_authority(
    active, monkeypatch, change
):
    n = active
    helper.publish_active_slot(n.selected, "sync")
    if change in ("unknown", "retired"):
        value = journal(n)
        value["phase"] = change
        (n.metadata / "source-jobs-active.json").write_bytes(helper.encoded(value))
    elif change == "gate-inode":
        path = custody.NATIVE_STATE / "custody.json"
        other = path.with_name("invented")
        other.write_bytes(path.read_bytes())
        other.chmod(0o640)
        other.replace(path)
    elif change == "gate-bytes":
        value = gate()
        value["launch"]["invocations"][custody.NATIVE_LABEL + "sync"]["invocation"][
            "nonce"
        ] = "f" * 32
        (custody.NATIVE_STATE / "custody.json").write_bytes(helper.encoded(value))
    elif change == "environment":
        custody.NATIVE_ENV.write_bytes(n.env_before + b"\n")
    elif change == "definition":
        (n.units / (custody.NATIVE_LABEL + "sync.plist")).write_bytes(
            b"preserve unknown"
        )
    elif change == "foreign-job":
        original = n.f.bridge.command
        monkeypatch.setattr(
            n.f.bridge,
            "command",
            lambda argv, **kwargs: (
                launchctl_system("com.ods.pixel-native-source-revoke")
                if argv == ["/bin/launchctl", "print", "system"]
                else original(argv, **kwargs)
            ),
        )
    elif change == "extra-file":
        (n.metadata / "native-source-jobs/unmanaged").write_bytes(b"preserve")
    selected = object() if change == "duck-selection" else n.selected
    before = (custody.NATIVE_STATE / "custody.json").read_bytes()
    record = (n.metadata / "source-jobs-active.json").read_bytes()
    with pytest.raises((ValueError, custody.CustodyError)):
        helper.publish_active_slot(selected, "direct")
    assert (custody.NATIVE_STATE / "custody.json").read_bytes() == before
    assert (n.metadata / "source-jobs-active.json").read_bytes() == record


@pytest.mark.parametrize("change", ["boot", "grant-live"])
def test_boot_and_grant_quiesce_are_required_before_new_intent(
    active, monkeypatch, change
):
    n = active
    first = helper.publish_active_slot(n.selected, "sync")
    role = "direct"
    if change == "boot":
        monkeypatch.setattr(
            custody, "_native_boot_uuid", lambda: "11234567-89AB-4DEF-8123-0123456789AB"
        )
    else:
        role = "revoke"
        original = n.f.bridge.command
        monkeypatch.setattr(
            n.f.bridge,
            "command",
            lambda argv, **kwargs: (
                launchctl_system(first["label"])
                if argv == ["/bin/launchctl", "print", "system"]
                else original(argv, **kwargs)
            ),
        )
    before = (n.metadata / "source-jobs-active.json").read_bytes()
    projection = (custody.NATIVE_STATE / "custody.json").read_bytes()
    with pytest.raises(ValueError, match="boot-changed|loaded-job-preserved"):
        helper.publish_active_slot(n.selected, role)
    assert (n.metadata / "source-jobs-active.json").read_bytes() == before
    assert (custody.NATIVE_STATE / "custody.json").read_bytes() == projection


def test_incomplete_durable_prefix_cannot_claim_gate_ready(active, monkeypatch):
    n = active
    original = custody._native_save_at

    def stop(selection, directory, name, value, **kwargs):
        result = original(selection, directory, name, value, **kwargs)
        if (
            name == "source-jobs-active.json"
            and value["transaction"]["images"]["definition"]["inode"] is not None
        ):
            raise OSError("invented durable partial definition")
        return result

    monkeypatch.setattr(custody, "_native_save_at", stop)
    with pytest.raises(OSError):
        helper.publish_active_slot(n.selected, "sync")
    monkeypatch.setattr(custody, "_native_save_at", original)
    value = journal(n)
    assert value["transaction"]["images"]["definition"]["ready"] is False
    value["phase"] = "gate-ready"
    path = n.metadata / "source-jobs-active.json"
    path.write_bytes(helper.encoded(value))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="record-changed"):
        helper.publish_active_slot(n.selected, "sync")
    assert (
        path.read_bytes() == before
        and (custody.NATIVE_STATE / "custody.json").read_bytes() == n.initial_gate
    )


def test_gate_directory_swap_after_intent_preserves_unknown_namespace(
    active, monkeypatch
):
    n = active
    original = custody._native_save_at
    moved = []

    def save(selection, directory, name, value, **kwargs):
        result = original(selection, directory, name, value, **kwargs)
        if name == "source-jobs-active.json" and not moved:
            moved.append(True)
            custody.NATIVE_STATE.rename(n.world / "retained-original-gate")
            custody.NATIVE_STATE.mkdir(mode=0o700)
            (custody.NATIVE_STATE / "unknown").write_text(
                "preserve invented unknown namespace"
            )
        return result

    monkeypatch.setattr(custody, "_native_save_at", save)
    with pytest.raises((ValueError, custody.CustodyError)):
        helper.publish_active_slot(n.selected, "sync")
    assert moved
    assert (
        n.world / "retained-original-gate/custody.json"
    ).read_bytes() == n.initial_gate
    assert set(custody.NATIVE_STATE.iterdir()) == {custody.NATIVE_STATE / "unknown"}
    assert (
        custody.NATIVE_STATE / "unknown"
    ).read_text() == "preserve invented unknown namespace"


def test_deadline_crossed_inside_last_real_guard_never_replaces_gate(
    active, monkeypatch
):
    n = active
    original_binding, original_replace = custody._native_environment_binding, os.replace
    state = {"armed": False, "advanced": False, "replaces": 0, "deadline": None}

    def clock():
        path = n.metadata / "source-jobs-active.json"
        if path.exists():
            value = journal(n)
            if value["phase"] == "gate-ready" and not state["armed"]:
                state["deadline"] = int(
                    value["transaction"]["invocation"]["admissionDeadlineNs"]
                )
                n.now = state["deadline"] - 1
                state["armed"] = True
        return n.now

    def binding(*args, **kwargs):
        value = original_binding(
            *args, **kwargs
        )  # Actual qualification, not a guard stub.
        if state["armed"] and not state["advanced"]:
            assert journal(n)["phase"] == "gate-ready"
            assert (
                custody.NATIVE_STATE / "custody.json"
            ).read_bytes() == n.initial_gate
            assert n.now == state["deadline"] - 1
            n.now = state["deadline"]
            state["advanced"] = True
        return value

    def replace(first, second, **kwargs):
        if second == "custody.json":
            state["replaces"] += 1
        return original_replace(first, second, **kwargs)

    monkeypatch.setattr(custody, "_native_clock_ns", clock)
    monkeypatch.setattr(custody, "_native_environment_binding", binding)
    monkeypatch.setattr(os, "replace", replace)
    failure = None
    try:
        helper.publish_active_slot(n.selected, "sync")
    except ValueError as error:
        failure = str(error)
    state.update(
        failure=failure,
        gateBefore=(custody.NATIVE_STATE / "custody.json").read_bytes()
        == n.initial_gate,
        phase=journal(n)["phase"],
        clockAtFinish=n.now,
    )
    (n.world / "deadline-control.json").write_text(
        json.dumps(state, sort_keys=True) + "\n"
    )
    assert state["armed"] and state["advanced"] and n.now == state["deadline"]
    assert failure == "native-source-active-admission-expired"
    assert state["gateBefore"] and state["replaces"] == 0
