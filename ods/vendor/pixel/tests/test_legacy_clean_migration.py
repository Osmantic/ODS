import hashlib
import importlib.util
import io
import json
import tarfile
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_legacy_clean_migration", ROOT / "scripts/migrate-legacy-clean.py",
)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)

NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
TREE = "b" * 40
CONTRACT_SHA = "e" * 64
LEGACY_AUDIT = {
    "status": "pass", "schemaVersion": 1, "members": 3, "roots": 2,
    "uncompressedBytes": 100, "pixelVersion": "3.2.2", "rootsSha256": CONTRACT_SHA,
}

RUNTIME_BOUNDARY = (
    "Content-free verification receipt for one observed local deployment. It binds source, "
    "installed manifests, configuration, profiles, and connector checks. Model capability "
    "remains unproven until a separate exact real-backend qualification receipt exists."
)


def write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def make_install(tmp, version, name="install", mode=0o700):
    install = tmp / name
    release = install / "releases" / version
    release.mkdir(parents=True)
    (install / "releases").chmod(mode)
    release.chmod(mode)
    install.chmod(mode)
    write(release / "VERSION", version + "\n")
    (release / "VERSION").chmod(0o600)
    if (install / "current").is_symlink() or (install / "current").exists():
        (install / "current").unlink()
    os.symlink("releases/" + version, install / "current")
    return install


def make_signed_encrypted_backup(tmp, paths, name="backup.tar.gz.age"):
    """Create a real age-encrypted, ssh-signed 3.2 backup declaring ``paths``."""
    identity = tmp / "identity.agekey"
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(
        ["age-keygen", "-y", str(identity)], stdout=subprocess.PIPE, check=True,
    ).stdout.decode().strip()
    signing_key = tmp / "backup-signing-key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "pixel-private-backup", "-f", str(signing_key)],
        check=True, capture_output=True,
    )
    public_key = subprocess.run(
        ["ssh-keygen", "-y", "-f", str(signing_key)], stdout=subprocess.PIPE, check=True,
    ).stdout.decode().strip()
    signers = tmp / "allowed-signers"
    signers.write_text("pixel-backup " + public_key + "\n", encoding="utf-8")
    signers.chmod(0o600)

    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for path in paths:
            info = tarfile.TarInfo(path)
            info.type = tarfile.DIRTYPE
            info.mode = 0o700
            tar.addfile(info)
            member = tarfile.TarInfo(path + "/state.json")
            member.mode = 0o600
            member.size = 2
            tar.addfile(member, io.BytesIO(b"{}\n"))
        manifest = json.dumps({"schemaVersion": 1, "pixelVersion": "3.2.2", "paths": paths}).encode()
        info = tarfile.TarInfo(".pixel-backup-manifest.json")
        info.mode = 0o600
        info.size = len(manifest)
        tar.addfile(info, io.BytesIO(manifest))
    payload = archive.getvalue()

    backup = tmp / name
    subprocess.run(
        ["age", "--encrypt", "--recipient", recipient, "--output", str(backup)],
        input=payload, check=True,
    )
    subprocess.run(
        ["ssh-keygen", "-q", "-Y", "sign", "-f", str(signing_key), "-n", "pixel-private-backup", str(backup)],
        check=True, capture_output=True,
    )
    checksum = subprocess.run(
        ["sha256sum", str(backup)], stdout=subprocess.PIPE, check=True, text=True,
    ).stdout
    checksum_path = tmp / (name + ".sha256")
    checksum_path.write_text(checksum, encoding="utf-8")
    checksum_path.chmod(0o600)
    return backup, identity, signers


def make_repo(tmp):
    repo = tmp / "repo"
    repo.mkdir()
    write(repo / "VERSION", "4.3.27\n")
    write(
        repo / "RELEASE-MANIFEST.json",
        json.dumps(
            {
                "pixel": "4.3.27",
                "releaseUpdate": {
                    "qualificationMode": "forward",
                    "minimumUpgradablePixel": "4.0.0",
                },
                "legacyCleanMigration": {
                    "schemaVersion": 1,
                    "cli": "./scripts/migrate-legacy-clean.py",
                    "evidenceSchema": "./schemas/legacy-clean-migration-v1.schema.json",
                    "boundary": "terminal-only-clean-migration-no-in-place-update",
                    "v1Contract": {"sourcePixel": "3.2.2", "targetPixel": "4.3.27"},
                    "activation": "self-contained-migrate-legacy-clean-activate-transaction",
                    "privacy": "content-free-receipts-no-paths-no-identity-no-user-content",
                },
            },
            indent=2,
        )
        + "\n",
    )
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, stdout=subprocess.PIPE, check=True).stdout.decode().strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, stdout=subprocess.PIPE, check=True).stdout.decode().strip()
    return repo, commit, tree


def build_receipt(backup_sha, **overrides):
    receipt = {
        "schemaVersion": 1, "kind": "pixel-restore-receipt", "status": "pass", "mode": "restore",
        "verified": True, "automaticRollbackArmed": True,
        "knowledgeDeletionReconciled": False, "historicalKeyWrappingRemoved": False,
        "backupSha256": backup_sha, "sourcePixel": "3.2.2", "targetPixel": "4.3.27",
        "receiptSha256": None, "generatedAt": "2026-08-22T12:00:00Z",
        "privacy": dict(migration.PRIVACY), "boundary": migration.BOUNDARIES["restore"],
    }
    hash_override = overrides.pop("receiptSha256", None)
    receipt.update(overrides)
    receipt["receiptSha256"] = hash_override if hash_override is not None else migration.self_hash(receipt, "receiptSha256")
    return receipt


def build_attestation(commit, tree, verified_at=None, **overrides):
    attestation = {
        "schemaVersion": 1, "kind": "pixel-runtime-attestation", "status": "verified",
        "verifiedAt": verified_at or "2026-08-22T12:00:00Z", "pixel": "4.3.27",
        "source": {"state": "git-clean", "commit": commit, "tree": tree},
        "qualification": {"recordStatus": "supported", "sourceCommit": commit, "qualifiedAt": "2026-08-22", "relationship": "same-source"},
        "release": {
            "sourceIdentitySha256": "1" * 64, "deploymentInputsSha256": "2" * 64,
            "sourceRuntimeSha256": "3" * 64, "installManifestSha256": "4" * 64,
            "releaseManifestSha256": "5" * 64, "compatibilityManifestSha256": "6" * 64,
            "qualificationMatrixSha256": "7" * 64,
        },
        "configuration": {"generatedDeploymentSha256": "8" * 64, "activeOpenClawSha256": "9" * 64},
        "runtime": {
            "state": "gateway-verified-model-unproven", "openclaw": "2026.7.1-2", "routeClass": "local",
            "providerIdSha256": "a" * 64, "modelIdSha256": "b" * 64,
            "contextWindow": 8192, "maxOutputTokens": 2048, "reasoning": True, "endpointChecks": "verified",
        },
        "profiles": {"deployment": "prepared", "capability": "minimal"},
        "connectors": [
            {"id": cid, "state": "enabled-verified"}
            for cid in ("email", "calendar", "social", "web", "operations", "frontier")
        ],
        "boundary": RUNTIME_BOUNDARY,
    }
    attestation.update(overrides)
    return attestation


class MigrationBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.backup = write(self.directory / "backup.tar.gz.age", "legacy-encrypted")
        self.install = make_install(self.directory, "3.2.2")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, audit=None, install=None):
        return migration.build_plan(
            ROOT, install or self.install,
            audit or LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(COMMIT, TREE), backup_roots_sha256=CONTRACT_SHA,
        )

    def test_plan_rehearsal_completion_succeed_with_stdlib_validation(self):
        plan = self.plan()
        migration.validate_plan_doc(plan)
        self.assertEqual(plan["sourcePixel"], "3.2.2")
        self.assertEqual(plan["targetPixel"], "4.3.27")
        self.assertEqual(plan["installedPixel"], "3.2.2")
        self.assertEqual(plan["releasePolicy"]["qualificationMode"], "forward")
        self.assertEqual(plan["planSha256"], migration.self_hash(plan, "planSha256"))

        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(COMMIT, TREE),
        )
        migration.validate_rehearsal_doc(rehearsal)
        self.assertEqual(rehearsal["planSha256"], plan["planSha256"])
        self.assertIs(rehearsal["liveStateChanged"], False)

        install42 = make_install(self.directory, "4.3.27", name="install42")
        receipt = build_receipt(plan["backupSha256"])
        receipt_bytes = json.dumps(receipt).encode("utf-8")
        attestation = json.dumps(build_attestation(COMMIT, TREE)).encode("utf-8")
        completion = migration.build_completion(
            ROOT, install42, plan, rehearsal, receipt, receipt_bytes, attestation,
            now=lambda: NOW, source_identity=(COMMIT, TREE),
        )
        migration.validate_completion_doc(completion)
        self.assertEqual(completion["activeRelease"], "4.3.27")
        self.assertEqual(completion["runtimeAttestationSha256"], hashlib.sha256(attestation).hexdigest())
        self.assertEqual(completion["restoreReceiptSha256"], hashlib.sha256(receipt_bytes).hexdigest())
        self.assertEqual(completion["completionSha256"], migration.self_hash(completion, "completionSha256"))

    def test_wrong_source_and_installed_versions_are_rejected(self):
        with self.assertRaises(migration.MigrationError):
            self.plan(audit={"status": "pass", "pixelVersion": "4.0.0"})
        with self.assertRaises(migration.MigrationError):
            self.plan(install=make_install(self.directory, "4.3.27"))

    def test_completion_rejects_wrong_active_release(self):
        plan = self.plan()
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(COMMIT, TREE),
        )
        with self.assertRaises(migration.MigrationError):
            migration.build_completion(
                ROOT, self.install, plan, rehearsal, build_receipt(plan["backupSha256"]),
                b"{}", b"{}", now=lambda: NOW, source_identity=(COMMIT, TREE),
            )

    def test_completion_rejects_wrong_receipt_and_wrong_source(self):
        plan = self.plan()
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(COMMIT, TREE),
        )
        install42 = make_install(self.directory, "4.3.27", name="install42")
        wrong = build_receipt("f" * 64)
        with self.assertRaises(migration.MigrationError):
            migration.build_completion(
                ROOT, install42, plan, rehearsal, wrong, b"{}", b"{}",
                now=lambda: NOW, source_identity=(COMMIT, TREE),
            )
        with self.assertRaises(migration.MigrationError):
            migration.build_completion(
                ROOT, install42, plan, rehearsal, build_receipt(plan["backupSha256"]),
                b"{}", b"{}", now=lambda: NOW, source_identity=("c" * 40, "d" * 40),
            )

    def test_rehearsal_rejects_plan_from_other_source(self):
        plan = self.plan()
        with self.assertRaises(migration.MigrationError):
            migration.build_rehearsal(
                ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
                {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
                now=lambda: NOW, source_identity=("c" * 40, "d" * 40),
            )

    def test_restore_receipt_validation(self):
        good = build_receipt("c" * 64)
        migration.validate_restore_receipt(good)
        self.assertEqual(good["receiptSha256"], migration.self_hash(good, "receiptSha256"))
        for overrides in (
            {"sourcePixel": "4.0.0"},
            {"targetPixel": "4.1.0"},
            {"verified": False},
            {"automaticRollbackArmed": False},
            {"status": "fail"},
            {"mode": "rehearse"},
            {"receiptSha256": "1" * 64},
        ):
            bad = build_receipt("c" * 64, **overrides)
            with self.assertRaises(migration.MigrationError):
                migration.validate_restore_receipt(bad)

    def test_restore_receipt_rejects_extra_and_missing_fields(self):
        good = build_receipt("c" * 64)
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_receipt({**good, "extra": 1})
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_receipt({k: v for k, v in good.items() if k != "backupSha256"})

    def test_restore_receipt_rejects_minimal_artifact(self):
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_receipt({"status": "pass", "mode": "restore"})

    def test_runtime_attestation_validation(self):
        start = NOW
        end = NOW + timedelta(seconds=10)
        migration.validate_runtime_attestation(
            build_attestation(COMMIT, TREE, verified_at="2026-08-22T12:00:05Z"),
            "4.3.27", COMMIT, TREE, start, end,
        )
        stale = build_attestation(COMMIT, TREE, verified_at="2026-08-22T11:00:00Z")
        with self.assertRaises(migration.MigrationError):
            migration.validate_runtime_attestation(stale, "4.3.27", COMMIT, TREE, start, end)
        wrong_source = build_attestation("c" * 40, "d" * 40)
        with self.assertRaises(migration.MigrationError):
            migration.validate_runtime_attestation(wrong_source, "4.3.27", COMMIT, TREE, start, end)
        unavailable = build_attestation(None, None, source_state="unavailable")
        with self.assertRaises(migration.MigrationError):
            migration.validate_runtime_attestation(unavailable, "4.3.27", COMMIT, TREE, start, end)
        not_verified = build_attestation(COMMIT, TREE, status="limited")
        with self.assertRaises(migration.MigrationError):
            migration.validate_runtime_attestation(not_verified, "4.3.27", COMMIT, TREE, start, end)
        fixture = {"kind": "pixel-runtime-attestation"}
        with self.assertRaises(migration.MigrationError):
            migration.validate_runtime_attestation(fixture, "4.3.27", COMMIT, TREE, start, end)

    def test_bool_as_int_and_string_counts_are_rejected(self):
        plan = self.plan()
        plan["backupAudit"]["members"] = True
        with self.assertRaises(migration.MigrationError):
            migration.validate_plan_doc(plan)
        plan = self.plan()
        plan["backupAudit"]["members"] = "3"
        with self.assertRaises(migration.MigrationError):
            migration.validate_plan_doc(plan)
        plan = self.plan()
        plan["backupAudit"]["roots"] = -1
        with self.assertRaises(migration.MigrationError):
            migration.validate_plan_doc(plan)

    def test_plan_rejects_hostile_audit_before_writing_plan(self):
        base = {"status": "pass", "schemaVersion": 1, "members": 3, "roots": 2,
                "uncompressedBytes": 100, "pixelVersion": "3.2.2",
                "rootsSha256": CONTRACT_SHA}
        hostile = [
            {**base, "members": "3"},
            {**base, "members": True},
            {**base, "roots": -1},
            {**base, "uncompressedBytes": "100"},
            {**base, "extra": 1},
            {**base, "rootsSha256": "not-a-hash"},
            {**base, "rootsSha256": "d" * 64},
            {key: value for key, value in base.items() if key != "rootsSha256"},
            {key: value for key, value in base.items() if key != "members"},
            {key: value for key, value in base.items() if key != "schemaVersion"},
            {key: value for key, value in base.items() if key != "pixelVersion"},
            {**base, "status": "fail"},
            {**base, "pixelVersion": "4.0.0"},
        ]
        for audit in hostile:
            with self.assertRaises(migration.MigrationError):
                self.plan(audit=audit)

    def test_runtime_attestation_rejects_unpromoted_qualification(self):
        for overrides in (
            {"recordStatus": "candidate"},
            {"recordStatus": "blocked"},
            {"recordStatus": "retired"},
            {"recordStatus": None},
            {"relationship": "unverified"},
            {"relationship": "unavailable"},
            {"sourceCommit": None},
            {"qualifiedAt": None},
            {"qualifiedAt": "2026-8-22"},
            {"qualifiedAt": "2026-13-99"},
        ):
            attestation = build_attestation(COMMIT, TREE, **overrides)
            with self.assertRaises(migration.MigrationError):
                migration.validate_runtime_attestation(attestation, migration.TARGET_PIXEL, COMMIT, TREE, NOW, NOW)

    def test_runtime_attestation_rejects_duplicate_connectors(self):
        duplicates = [
            {"id": cid, "state": "enabled-verified"}
            for cid in ("email", "email", "calendar", "social", "web", "operations")
        ]
        attestation = build_attestation(COMMIT, TREE, connectors=duplicates)
        with self.assertRaises(migration.MigrationError):
            migration.validate_runtime_attestation(attestation, migration.TARGET_PIXEL, COMMIT, TREE, NOW, NOW)

    def test_restore_receipt_rejects_contradictory_knowledge_booleans(self):
        for deletion, wrapping in ((True, False), (False, True)):
            receipt = build_receipt(
                "c" * 64, knowledgeDeletionReconciled=deletion, historicalKeyWrappingRemoved=wrapping,
            )
            with self.assertRaises(migration.MigrationError):
                migration.validate_restore_receipt(receipt)

    def test_restore_receipt_time_ordering_binding(self):
        good = build_receipt("c" * 64)
        migration.validate_restore_receipt(
            good, rehearsal_generated_at=NOW, finalize_now=NOW + timedelta(seconds=5),
        )
        predated = build_receipt("c" * 64, generatedAt="2026-08-22T11:00:00Z")
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_receipt(predated, rehearsal_generated_at=NOW)
        future = build_receipt("c" * 64, generatedAt="2026-08-22T15:00:00Z")
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_receipt(future, finalize_now=NOW)

    def test_validate_schema_dispatch_rejects_unknown_operation(self):
        with self.assertRaises(migration.MigrationError):
            migration.validate_schema({"operation": "pixel-something-else"})


class MigrationStdlibOnlyTests(unittest.TestCase):
    def test_no_third_party_imports_in_migration_module(self):
        source = (ROOT / "scripts/migrate-legacy-clean.py").read_text(encoding="utf-8")
        imports = re_find_imports(source)
        third_party = imports - {
            "__future__", "argparse", "datetime", "hashlib", "json", "os", "pathlib", "re",
            "secrets", "shutil", "stat", "subprocess", "sys", "tempfile", "typing", "base64",
        }
        self.assertEqual(third_party, set(), f"third-party imports reintroduced: {third_party}")

    def test_migration_module_loads_without_jsonschema(self):
        proc = subprocess.run(
            ["python3", "-c",
             "import sys; sys.path.insert(0, %r); "
             "import importlib.util; "
             "spec=importlib.util.spec_from_file_location('m', 'scripts/migrate-legacy-clean.py'); "
             "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)" % str(ROOT)],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("jsonschema", proc.stderr)

    def test_migration_module_has_no_second_tree_walk_implementation(self):
        source = (ROOT / "scripts/migrate-legacy-clean.py").read_text(encoding="utf-8")
        # The canonical release-tree digest must come only from the shared helper
        # (scripts/lib/release-tree-sha.py); a second tree-walk implementation here is a
        # single-authority regression and must never be reintroduced.
        for forbidden in ("def walk(fd, rel):", "os.listdir(fd)", "os.lstat(name, dir_fd=fd)"):
            self.assertNotIn(forbidden, source, f"tree-walk implementation reintroduced: {forbidden!r}")
        self.assertIn("release-tree-sha.py", source)
        self.assertIn("sys.executable", source)

    def test_clean_interpreter_help_is_dependency_free(self):
        proc = subprocess.run(
            ["python3", str(ROOT / "scripts/migrate-legacy-clean.py"), "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("finalize", proc.stdout)


def re_find_imports(source):
    import re
    imports = set()
    for match in re.finditer(r"^\s*(?:from\s+([A-Za-z0-9_\.]+)\s+import|import\s+([A-Za-z0-9_\.]+))", source, re.MULTILINE):
        module = match.group(1) or match.group(2)
        imports.add(module.split(".")[0])
    return imports


class MigrationValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.backup = write(self.directory / "backup.tar.gz.age", "legacy-encrypted")

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_json_rejects_duplicates_and_nonfinite(self):
        with self.assertRaises(migration.MigrationError):
            migration.parse_json(b'{"a":1,"a":2}', "hostile")
        with self.assertRaises(migration.MigrationError):
            migration.parse_json(b'{"a":NaN}', "hostile")
        with self.assertRaises(migration.MigrationError):
            migration.parse_json(b'{"a":}', "hostile")

    def test_write_new_private_refuses_overwrite(self):
        out = self.directory / "parent" / "plan.json"
        migration.write_new_private(out, b"{}")
        with self.assertRaises(migration.MigrationError):
            migration.write_new_private(out, b"{}")
        self.assertEqual(stat.S_IMODE(out.lstat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(out.parent.lstat().st_mode), 0o700)

    def test_write_new_private_refuses_symlinked_parent(self):
        real = self.directory / "real"
        real.mkdir()
        link = self.directory / "link"
        os.symlink(real, link)
        out = link / "plan.json"
        with self.assertRaises(migration.MigrationError):
            migration.write_new_private(out, b"{}")

    def test_read_installed_version_rejects_escape_and_non_link(self):
        install = self.directory / "flat"
        release = install / "releases" / "3.2.2"
        release.mkdir(parents=True)
        write(release / "VERSION", "3.2.2\n")
        with self.assertRaises(migration.MigrationError):
            migration.read_installed_version(install)
        escape = self.directory / "escape"
        escape.mkdir()
        write(escape / "VERSION", "3.2.2\n")
        install2 = self.directory / "install2"
        install2.mkdir()
        os.symlink(str(escape), install2 / "current")
        with self.assertRaises(migration.MigrationError):
            migration.read_installed_version(install2)

    def test_check_install_pointer_requires_exact_target(self):
        install = make_install(self.directory, "4.3.27", name="install42")
        self.assertEqual(migration.check_install_pointer(install), "4.3.27")
        wrong = make_install(self.directory, "4.1.0")
        with self.assertRaises(migration.MigrationError):
            migration.check_install_pointer(wrong)

    def test_check_install_release_rejects_wrong_in_tree_path(self):
        install = self.directory / "install"
        wrong = install / "not-releases" / "3.2.2"
        wrong.mkdir(parents=True)
        (install / "not-releases").chmod(0o700)
        wrong.chmod(0o700)
        write(wrong / "VERSION", "3.2.2\n")
        (wrong / "VERSION").chmod(0o600)
        (install / "releases").mkdir(parents=True)
        (install / "releases").chmod(0o700)
        os.symlink("not-releases/3.2.2", install / "current")
        with self.assertRaises(migration.MigrationError):
            migration.check_install_release(install, "3.2.2")

    def test_check_install_release_rejects_unsafe_world_writable_component(self):
        install = make_install(self.directory, "3.2.2")
        (install / "releases").chmod(0o777)
        with self.assertRaises(migration.MigrationError):
            migration.check_install_release(install, "3.2.2")

    def test_check_install_release_accepts_root_owned_sticky_tmp_ancestor(self):
        tmp_mode = stat.S_IMODE(os.stat("/tmp").st_mode)
        root_owned_sticky_writable = (
            os.stat("/tmp").st_uid == 0
            and bool(tmp_mode & stat.S_ISVTX)
            and (tmp_mode & 0o022) != 0
        )
        if not root_owned_sticky_writable:
            self.skipTest("/tmp is not a root-owned sticky world-writable directory")
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            install = make_install(Path(raw), "3.2.2")
            self.assertEqual(migration.check_install_release(install, "3.2.2"), "3.2.2")

    def test_check_install_release_rejects_non_sticky_world_writable_ancestor(self):
        writable = self.directory / "writable"
        writable.mkdir()
        writable.chmod(0o777)
        install = make_install(writable, "3.2.2")
        with self.assertRaises(migration.MigrationError):
            migration.check_install_release(install, "3.2.2")

    def test_check_install_release_rejects_non_root_owned_sticky_writable_ancestor(self):
        sticky = self.directory / "sticky"
        sticky.mkdir()
        sticky.chmod(0o1777)
        install = make_install(sticky, "3.2.2")
        with self.assertRaises(migration.MigrationError):
            migration.check_install_release(install, "3.2.2")

    def test_check_install_release_accepts_absolute_symlink_target(self):
        install = self.directory / "install"
        release = install / "releases" / "3.2.2"
        release.mkdir(parents=True)
        install.chmod(0o700)
        (install / "releases").chmod(0o700)
        release.chmod(0o700)
        write(release / "VERSION", "3.2.2\n")
        (release / "VERSION").chmod(0o600)
        os.symlink(str(release), install / "current")
        self.assertEqual(migration.check_install_release(install, "3.2.2"), "3.2.2")

    def test_release_tree_binds_venv_symlink_targets_and_detects_tamper(self):
        # Requirement 6: a realistic enabled web-courier build contains venv-style symlinks
        # (bin/python3 -> /usr/bin/python3, bin/python -> python3, lib64 -> lib). The
        # canonical digest binds the TARGET STRING exactly and never follows it, so these
        # are accepted by text (absolute and relative-with-.. are legitimate) while a
        # tampered target changes the canonical digest.
        release = self.directory / "rel"
        release.mkdir(parents=True)
        release.chmod(0o700)
        (release / "bin").mkdir()
        (release / "bin").chmod(0o700)
        write(release / "VERSION", "4.3.27\n")
        os.symlink("/usr/bin/python3", release / "bin" / "python3")
        os.symlink("python3", release / "bin" / "python")
        os.symlink("lib", release / "lib64")
        manifest = "{}  VERSION\n".format(hashlib.sha256((release / "VERSION").read_bytes()).hexdigest())
        (release / "install-manifest.sha256").write_text(manifest)
        (release / "install-manifest.sha256").chmod(0o600)
        digest = migration._release_tree_sha(release)
        self.assertIsInstance(digest, str)
        self.assertEqual(len(digest), 64)
        # The exact bound tree is stable and accepted.
        self.assertEqual(migration._release_tree_sha(release), digest)
        # Symlink-target tampering changes the canonical digest (bound, never followed).
        os.unlink(release / "bin" / "python3")
        os.symlink("/usr/bin/evil", release / "bin" / "python3")
        self.assertNotEqual(migration._release_tree_sha(release), digest)

    def test_release_tree_sha_invokes_shared_helper(self):
        release = self.directory / "rel"
        release.mkdir(parents=True)
        digest = "d" * 64
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = digest + "\n"
        fake.stderr = ""
        with mock.patch.object(migration.subprocess, "run", return_value=fake) as run:
            self.assertEqual(migration._release_tree_sha(release), digest)
        args = run.call_args.args[0]
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(Path(args[1]), ROOT / "scripts/lib/release-tree-sha.py")
        self.assertEqual(Path(args[2]), release)
        self.assertEqual(run.call_args.kwargs["timeout"], migration.RELEASE_TREE_SHA_TIMEOUT)
        self.assertTrue(run.call_args.kwargs["check"] is False)

    def test_release_tree_sha_nonzero_helper_fails_closed(self):
        release = self.directory / "rel"
        release.mkdir(parents=True)
        fake = mock.Mock()
        fake.returncode = 1
        fake.stdout = "a" * 64
        fake.stderr = "boom"
        with mock.patch.object(migration.subprocess, "run", return_value=fake):
            with self.assertRaises(migration.MigrationError):
                migration._release_tree_sha(release)

    def test_release_tree_sha_malformed_helper_output_fails_closed(self):
        release = self.directory / "rel"
        release.mkdir(parents=True)
        for bad in (
            "", "not-hex", "A" * 64, "a" * 63,
            ("a" * 64) + "\n" + ("b" * 64), "a" * 64 + " extra",
            " " + ("a" * 64) + "\n", ("a" * 64) + " \n", ("a" * 64) + "\r\n",
            "a" * 64, "\n", ("a" * 64) + "\n\n",
        ):
            fake = mock.Mock()
            fake.returncode = 0
            fake.stdout = bad
            fake.stderr = ""
            with mock.patch.object(migration.subprocess, "run", return_value=fake):
                with self.assertRaises(migration.MigrationError):
                    migration._release_tree_sha(release)

    def test_release_tree_sha_timeout_helper_fails_closed(self):
        release = self.directory / "rel"
        release.mkdir(parents=True)
        with mock.patch.object(
            migration.subprocess, "run",
            side_effect=migration.subprocess.TimeoutExpired(cmd="x", timeout=120),
        ):
            with self.assertRaises(migration.MigrationError):
                migration._release_tree_sha(release)

    def test_check_install_release_rejects_non_owner_world_writable_version(self):
        install = make_install(self.directory, "3.2.2")
        (install / "releases" / "3.2.2" / "VERSION").chmod(0o666)
        with self.assertRaises(migration.MigrationError):
            migration.check_install_release(install, "3.2.2")

    def test_stale_plan_and_rehearsal_hashes_rejected(self):
        fresh_plan = migration.build_plan(
            ROOT, make_install(self.directory, "3.2.2"),
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(COMMIT, TREE), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_path = self.directory / "plan.json"
        migration.write_new_private(plan_path, (json.dumps(fresh_plan) + "\n").encode("utf-8"))
        self.assertEqual(migration.checked_private_plan(plan_path), fresh_plan)
        stale_plan = dict(fresh_plan)
        stale_plan["planSha256"] = "f" * 64
        migration.write_new_private(self.directory / "bad.json", (json.dumps(stale_plan) + "\n").encode("utf-8"))
        with self.assertRaises(migration.MigrationError):
            migration.checked_private_plan(self.directory / "bad.json")

        rehearsal = migration.build_rehearsal(
            ROOT, fresh_plan, fresh_plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(COMMIT, TREE),
        )
        good = self.directory / "rehearsal.json"
        migration.write_new_private(good, (json.dumps(rehearsal) + "\n").encode("utf-8"))
        self.assertEqual(migration.checked_private_rehearsal(good, fresh_plan), rehearsal)
        bad = dict(rehearsal)
        bad["rehearsalSha256"] = "e" * 64
        migration.write_new_private(self.directory / "bad-r.json", (json.dumps(bad) + "\n").encode("utf-8"))
        with self.assertRaises(migration.MigrationError):
            migration.checked_private_rehearsal(self.directory / "bad-r.json", fresh_plan)

    def test_private_evidence_mode_enforced(self):
        plan = migration.build_plan(
            ROOT, make_install(self.directory, "3.2.2"),
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(COMMIT, TREE), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_path = self.directory / "plan.json"
        migration.write_new_private(plan_path, (json.dumps(plan) + "\n").encode("utf-8"))
        plan_path.chmod(0o644)
        with self.assertRaises(migration.MigrationError):
            migration.checked_private_plan(plan_path)

    def test_live_path_rehearsal_root_rejected(self):
        live = [self.directory / "workspace", self.directory / "openclaw"]
        inside = live[0] / "nested"
        with self.assertRaises(migration.MigrationError):
            migration._rehearsal_root_valid(inside, live)
        with self.assertRaises(migration.MigrationError):
            migration._rehearsal_root_valid(live[1], live)
        with self.assertRaises(migration.MigrationError):
            migration._rehearsal_root_valid(Path("/"), live)

    def test_read_private_evidence_rejects_symlink_hardlink_and_mode(self):
        good = build_receipt("c" * 64)
        path = self.directory / "receipt.json"
        write(path, json.dumps(good))
        path.chmod(0o600)
        self.assertEqual(migration.read_bytes(path, private=True), json.dumps(good).encode("utf-8"))
        target = self.directory / "target.json"
        write(target, json.dumps(good))
        link = self.directory / "link.json"
        os.symlink(target, link)
        with self.assertRaises(migration.MigrationError):
            migration.read_bytes(link, private=True)
        hard = self.directory / "hard.json"
        os.link(path, hard)
        with self.assertRaises(migration.MigrationError):
            migration.read_bytes(path, private=True)
        path.chmod(0o644)
        with self.assertRaises(migration.MigrationError):
            migration.read_bytes(path, private=True)

    def test_tool_never_performs_in_place_update_or_update_rollback(self):
        source = (ROOT / "scripts/migrate-legacy-clean.py").read_text(encoding="utf-8")
        self.assertNotIn('"update-rollback"', source)
        self.assertNotIn('"bootstrap"', source)
        self.assertNotIn('"apply"', source)
        self.assertIn('[str(pixel), "verify", "--expected-install-dir", str(install_dir)]', source)
        self.assertIn("--validate-only", source)
        self.assertIn("--rehearse", source)
        self.assertIn('"restore"', source)
        self.assertIn('"--replace"', source)

    def test_restore_receipt_guard_only_for_actual_restore(self):
        source = (ROOT / "scripts/restore-private-state.sh").read_text(encoding="utf-8")
        self.assertIn('[[ $mode == restore || -z "$receipt" ]] || pixel_die "--receipt is valid only for an actual confirmed restore"', source)
        self.assertIn('if [[ -n "$receipt" ]]; then', source)

    def test_git_identity_requires_clean_available_source(self):
        plain = self.directory / "not-a-repo"
        plain.mkdir()
        with self.assertRaises(migration.MigrationError):
            migration.git_identity(plain)
        repo = make_repo(self.directory)[0]
        write(repo / "dirty.txt", "x")
        with self.assertRaises(migration.MigrationError):
            migration.git_identity(repo)

    def test_output_inside_repo_rejected(self):
        with self.assertRaises(migration.MigrationError):
            migration._outside_repo(ROOT / "inner" / "plan.json", ROOT, "plan output")

    def test_non_regression_plan_rehearsal_field_compatibility(self):
        plan = migration.build_plan(
            ROOT, make_install(self.directory, "3.2.2"),
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(COMMIT, TREE), backup_roots_sha256=CONTRACT_SHA,
        )
        self.assertEqual(sorted(plan.keys()), sorted(migration.PLAN_KEYS))
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(COMMIT, TREE),
        )
        self.assertEqual(sorted(rehearsal.keys()), sorted(migration.REHEARSAL_KEYS))
        self.assertEqual(rehearsal["planSha256"], plan["planSha256"])
        self.assertEqual(rehearsal["backupSha256"], plan["backupSha256"])

    def test_restore_without_receipt_stdout_unchanged(self):
        source = (ROOT / "scripts/restore-private-state.sh").read_text(encoding="utf-8")
        self.assertIn('{"status":"pass","mode":"restore","verified":true,"automaticRollbackArmed":true,"knowledgeDeletionReconciled":false,"historicalKeyWrappingRemoved":false}', source)
        self.assertIn('{"status":"pass","mode":"restore","verified":true,"automaticRollbackArmed":true,"knowledgeDeletionReconciled":true,"historicalKeyWrappingRemoved":true}', source)
        self.assertIn('if [[ -n "$receipt" ]]; then', source)

    def test_rewrite_reserved_refuses_replaced_or_non_0600(self):
        reserve = [str(ROOT / "scripts/restore-receipt.py"), "reserve"]
        good = self.directory / "completion.json"
        subprocess.run([*reserve, str(good), str(ROOT)], check=True)
        migration._rewrite_reserved(good, b'{"status":"pass"}\n')
        self.assertEqual(migration.read_bytes(good, private=True), b'{"status":"pass"}\n')
        self.assertEqual(stat.S_IMODE(good.lstat().st_mode), 0o600)

        loose = self.directory / "completion2.json"
        subprocess.run([*reserve, str(loose), str(ROOT)], check=True)
        loose.chmod(0o644)
        with self.assertRaises(migration.MigrationError):
            migration._rewrite_reserved(loose, b'{}')
        self.assertEqual(loose.read_text(encoding="utf-8").strip() != "", True)

    def test_write_failure_receipt_is_content_free_non_pass(self):
        from tests.test_legacy_clean_migration import ROOT as _ROOT  # noqa: F401
        out = self.directory / "completion.json"
        subprocess.run([str(ROOT / "scripts/restore-receipt.py"), "reserve", str(out), str(ROOT)], check=True)
        migration._write_failure_receipt(out, rolled_back=False)
        failure = migration.read_json(out, "completion", private=True)
        self.assertEqual(failure["status"], "failed")
        self.assertIs(failure["backupRetained"], True)
        self.assertIs(failure["rolledBack"], False)
        for key, value in migration.PRIVACY.items():
            self.assertIs(failure["privacy"][key], False)
        self.assertNotIn("SECRET", failure["reason"])
        self.assertNotIn("SECRET", out.read_text(encoding="utf-8"))


class MigrationCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.repo, self.commit, self.tree = make_repo(self.directory)
        self.root_contract = hashlib.sha256(
            b"etc/pixel-agent/gateway.env\nvar/lib/pixel-legacy-state\n"
        ).hexdigest()
        self.backup, self.identity, self.signers = make_signed_encrypted_backup(
            self.directory, ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"],
        )
        self.pixel = write(self.directory / "pixel", "#!/usr/bin/env bash\nset -euo pipefail\n")
        self.pixel.chmod(0o700)
        self.install = make_install(self.directory, "3.2.2")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *arguments, env=None):
        return subprocess.run(
            ["python3", str(ROOT / "scripts/migrate-legacy-clean.py"), *map(str, arguments)],
            cwd=self.directory, text=True, capture_output=True,
            env={**os.environ, **(env or {})},
        )

    def _write_fake_pixel(self, install42):
        fake = (
            "if [[ \"$1\" == restore && \"$*\" == *--validate-only* ]]; then "
            "echo '{\"status\":\"pass\",\"schemaVersion\":1,\"members\":3,\"roots\":2,\"uncompressedBytes\":100,\"pixelVersion\":\"3.2.2\",\"rootsSha256\":\"{rootContract}\"}'; exit 0; fi\n"
            "if [[ \"$1\" == restore && \"$*\" == *--rehearse* ]]; then last=\"${@: -1}\"; "
            "mkdir -p \"$last\"; echo '{\"status\":\"pass\",\"mode\":\"rehearse\",\"liveStateChanged\":false}'; exit 0; fi\n"
            "if [[ \"$1\" == verify ]]; then expected=\"${@: -1}\"; python3 - \"$expected\" \"{commit}\" \"{tree}\" <<'FAKEPY'\n"
            "import json, os, sys\n"
            "from datetime import datetime, timezone\n"
            "install, commit, tree = sys.argv[1], sys.argv[2], sys.argv[3]\n"
            "att = json.loads('{att}')\n"
            "att['verifiedAt'] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')\n"
            "att['source'] = {'state': 'git-clean', 'commit': commit, 'tree': tree}\n"
            "os.makedirs(install, exist_ok=True)\n"
            "open(os.path.join(install, 'runtime-attestation.json'), 'w').write(json.dumps(att))\n"
            "FAKEPY\n"
            "exit 0; fi\n"
            "echo \"unexpected: $*\" >&2; exit 1\n"
        )
        script = (fake.replace("{commit}", self.commit).replace("{tree}", self.tree)
                  .replace("{rootContract}", self.root_contract)
                  .replace("{att}", json.dumps(build_attestation(self.commit, self.tree))))
        write(self.pixel, self.pixel.read_text() + script)
    def test_plan_rehearse_finalize_happy_path(self):
        install42 = make_install(self.directory, "4.3.27", name="install42")
        self._write_fake_pixel(install42)
        plan_out = self.directory / "plan.json"
        plan_result = self.run_cli(
            "plan", "--root", self.repo, "--pixel", self.pixel, "--install-dir", self.install,
            "--backup", self.backup, "--identity", self.identity, "--signers", self.signers,
            "--output", plan_out,
        )
        self.assertEqual(plan_result.returncode, 0, plan_result.stderr)
        plan = migration.read_json(plan_out, "plan", private=True)
        migration.validate_plan_doc(plan)
        self.assertEqual(plan["sourceCommit"], self.commit)
        self.assertEqual(plan["sourceTree"], self.tree)

        rehearsal_root = self.directory / "rehearsal-root"
        rehearsal_out = self.directory / "rehearsal.json"
        rehearse_result = self.run_cli(
            "rehearse", "--root", self.repo, "--pixel", self.pixel,
            "--plan", plan_out, "--backup", self.backup, "--identity", self.identity,
            "--signers", self.signers, "--rehearsal-root", rehearsal_root, "--output", rehearsal_out,
        )
        self.assertEqual(rehearse_result.returncode, 0, rehearse_result.stderr)
        self.assertTrue(rehearsal_root.exists())
        rehearsal = migration.read_json(rehearsal_out, "rehearsal", private=True)
        migration.validate_rehearsal_doc(rehearsal)
        self.assertEqual(rehearsal["planSha256"], plan["planSha256"])

        receipt = build_receipt(plan["backupSha256"], generatedAt=rehearsal["generatedAt"])
        receipt_out = self.directory / "receipt.json"
        migration.write_new_private(receipt_out, (json.dumps(receipt) + "\n").encode("utf-8"))
        completion_out = self.directory / "completion.json"
        completion_result = self.run_cli(
            "finalize", "--root", self.repo, "--pixel", self.pixel, "--install-dir", install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--receipt", receipt_out, "--output", completion_out,
            env={"PIXEL_INSTALL_DIR": str(install42)},
        )
        self.assertEqual(completion_result.returncode, 0, completion_result.stderr)
        completion = migration.read_json(completion_out, "completion", private=True)
        migration.validate_completion_doc(completion)
        self.assertEqual(completion["activeRelease"], "4.3.27")
        self.assertEqual(completion["planSha256"], plan["planSha256"])
        self.assertEqual(completion["rehearsalSha256"], rehearsal["rehearsalSha256"])
        self.assertEqual(completion["backupSha256"], plan["backupSha256"])

    def test_finalize_rejects_minimal_restore_result(self):
        install42 = make_install(self.directory, "4.3.27", name="install42")
        self._write_fake_pixel(install42)
        plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self.directory / "plan.json"
        migration.write_new_private(plan_out, (json.dumps(plan) + "\n").encode("utf-8"))
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(self.commit, self.tree),
        )
        rehearsal_out = self.directory / "rehearsal.json"
        migration.write_new_private(rehearsal_out, (json.dumps(rehearsal) + "\n").encode("utf-8"))
        minimal = self.directory / "minimal.json"
        migration.write_new_private(minimal, b'{"status":"pass","mode":"restore"}\n')
        result = self.run_cli(
            "finalize", "--root", self.repo, "--pixel", self.pixel, "--install-dir", install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--receipt", minimal, "--output", self.directory / "completion.json",
            env={"PIXEL_INSTALL_DIR": str(install42)},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[pixel] ERROR", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_finalize_rejects_wrong_backup(self):
        install42 = make_install(self.directory, "4.3.27", name="install42")
        self._write_fake_pixel(install42)
        plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self.directory / "plan.json"
        migration.write_new_private(plan_out, (json.dumps(plan) + "\n").encode("utf-8"))
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(self.commit, self.tree),
        )
        rehearsal_out = self.directory / "rehearsal.json"
        migration.write_new_private(rehearsal_out, (json.dumps(rehearsal) + "\n").encode("utf-8"))
        wrong_backup = write(self.directory / "other-backup.age", "different bytes")
        receipt_out = self.directory / "receipt.json"
        migration.write_new_private(receipt_out, (json.dumps(build_receipt(plan["backupSha256"])) + "\n").encode("utf-8"))
        result = self.run_cli(
            "finalize", "--root", self.repo, "--pixel", self.pixel, "--install-dir", install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", wrong_backup,
            "--receipt", receipt_out, "--output", self.directory / "completion.json",
            env={"PIXEL_INSTALL_DIR": str(install42)},
        )
        self.assertEqual(result.returncode, 2)

    def test_finalize_rejects_stale_source(self):
        install42 = make_install(self.directory, "4.3.27", name="install42")
        self._write_fake_pixel(install42)
        plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self.directory / "plan.json"
        migration.write_new_private(plan_out, (json.dumps(plan) + "\n").encode("utf-8"))
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(self.commit, self.tree),
        )
        rehearsal_out = self.directory / "rehearsal.json"
        migration.write_new_private(rehearsal_out, (json.dumps(rehearsal) + "\n").encode("utf-8"))
        write(self.repo / "dirty.txt", "dirty\n")
        result = self.run_cli(
            "finalize", "--root", self.repo, "--pixel", self.pixel, "--install-dir", install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--receipt", self.directory / "receipt.json", "--output", self.directory / "completion.json",
            env={"PIXEL_INSTALL_DIR": str(install42)},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[pixel] ERROR", result.stderr)

    def test_finalize_rejects_wrong_receipt_source(self):
        install42 = make_install(self.directory, "4.3.27", name="install42")
        self._write_fake_pixel(install42)
        plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self.directory / "plan.json"
        migration.write_new_private(plan_out, (json.dumps(plan) + "\n").encode("utf-8"))
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(self.commit, self.tree),
        )
        rehearsal_out = self.directory / "rehearsal.json"
        migration.write_new_private(rehearsal_out, (json.dumps(rehearsal) + "\n").encode("utf-8"))
        bad_receipt = self.directory / "bad-receipt.json"
        migration.write_new_private(bad_receipt, (json.dumps(build_receipt("f" * 64)) + "\n").encode("utf-8"))
        result = self.run_cli(
            "finalize", "--root", self.repo, "--pixel", self.pixel, "--install-dir", install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--receipt", bad_receipt, "--output", self.directory / "completion.json",
            env={"PIXEL_INSTALL_DIR": str(install42)},
        )
        self.assertEqual(result.returncode, 2)

    def test_finalize_verify_failure_is_content_free_exit_2(self):
        install42 = make_install(self.directory, "4.3.27", name="install42")
        write(self.pixel, self.pixel.read_text() + (
            'if [[ "$1" == restore && "$*" == *--validate-only* ]]; then '
            'echo \'{"status":"pass","schemaVersion":1,"members":3,"roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2","rootsSha256":"'
            + self.root_contract + '"}\'; exit 0; fi\n'
            'if [[ "$1" == verify ]]; then echo "boom" >&2; exit 1; fi\n'
            'echo "unexpected: $*" >&2; exit 1\n'
        ))
        plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self.directory / "plan.json"
        migration.write_new_private(plan_out, (json.dumps(plan) + "\n").encode("utf-8"))
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(self.commit, self.tree),
        )
        rehearsal_out = self.directory / "rehearsal.json"
        migration.write_new_private(rehearsal_out, (json.dumps(rehearsal) + "\n").encode("utf-8"))
        receipt_out = self.directory / "receipt.json"
        migration.write_new_private(receipt_out, (json.dumps(build_receipt(plan["backupSha256"])) + "\n").encode("utf-8"))
        result = self.run_cli(
            "finalize", "--root", self.repo, "--pixel", self.pixel, "--install-dir", install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--receipt", receipt_out, "--output", self.directory / "completion.json",
            env={"PIXEL_INSTALL_DIR": str(install42)},
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("[pixel] ERROR", result.stderr)
        self.assertNotIn("boom", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_finalize_removes_preexisting_attestation(self):
        install42 = make_install(self.directory, "4.3.27", name="install42")
        self._write_fake_pixel(install42)
        attestation_path = install42 / "runtime-attestation.json"
        write(attestation_path, '{"kind":"pixel-runtime-attestation"}\n')
        plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self.directory / "plan.json"
        migration.write_new_private(plan_out, (json.dumps(plan) + "\n").encode("utf-8"))
        rehearsal = migration.build_rehearsal(
            ROOT, plan, plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(self.commit, self.tree),
        )
        rehearsal_out = self.directory / "rehearsal.json"
        migration.write_new_private(rehearsal_out, (json.dumps(rehearsal) + "\n").encode("utf-8"))
        receipt_out = self.directory / "receipt.json"
        migration.write_new_private(receipt_out, (json.dumps(build_receipt(plan["backupSha256"])) + "\n").encode("utf-8"))
        result = self.run_cli(
            "finalize", "--root", self.repo, "--pixel", self.pixel, "--install-dir", install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--receipt", receipt_out, "--output", self.directory / "completion.json",
            env={"PIXEL_INSTALL_DIR": str(install42)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('"kind":"pixel-runtime-attestation"'), 0)

    def test_rehearse_refuses_live_path_and_stale_plan(self):
        write(self.pixel, self.pixel.read_text() + 'echo "unexpected: $*" >&2; exit 1\n')
        plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree), backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self.directory / "plan.json"
        migration.write_new_private(plan_out, (json.dumps(plan) + "\n").encode("utf-8"))

        workspace = self.directory / "workspace"
        workspace.mkdir()
        live_root = workspace / "nested"
        result = self.run_cli(
            "rehearse", "--root", self.repo, "--pixel", self.pixel,
            "--plan", plan_out, "--backup", self.backup, "--identity", self.identity,
            "--signers", self.signers, "--rehearsal-root", live_root, "--output", self.directory / "r.json",
            "--workspace", workspace,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("outside live Pixel state", result.stderr)

    def test_plan_rejects_hostile_audit_output_before_writing_plan(self):
        write(self.pixel, self.pixel.read_text() + (
            'if [[ "$1" == restore && "$*" == *--validate-only* ]]; then '
            'echo \'{"status":"pass","schemaVersion":1,"members":"3","roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2","rootsSha256":"'
            + self.root_contract + '"}\'; exit 0; fi\n'
            'echo "unexpected: $*" >&2; exit 1\n'
        ))
        plan_out = self.directory / "plan.json"
        result = self.run_cli(
            "plan", "--root", self.repo, "--pixel", self.pixel, "--install-dir", self.install,
            "--backup", self.backup, "--identity", self.identity, "--signers", self.signers,
            "--output", plan_out,
        )
        self.assertEqual(result.returncode, 2)
        self.assertFalse(plan_out.exists())
        self.assertIn("[pixel] ERROR", result.stderr)

    def test_unsupported_argument_per_subcommand_rejected(self):
        result = self.run_cli("plan", "--rehearsal", self.directory / "x.json")
        self.assertEqual(result.returncode, 2)


def make_install42(tmp, name="install42"):
    install = tmp / name
    release = install / "releases" / "4.3.27"
    release.mkdir(parents=True)
    # Match install.sh's production runtime layout: the source-tree controller is not copied
    # into the installed release.
    for component in ("plugin", "plugin-ops", "plugin-frontier"):
        (release / component).mkdir()
    version = write(release / "VERSION", "4.3.27\n")
    version.chmod(0o600)
    manifest = write(
        release / "install-manifest.sha256",
        f"{hashlib.sha256(version.read_bytes()).hexdigest()}  ./VERSION\n",
    )
    manifest.chmod(0o600)
    (install / "releases").chmod(0o700)
    release.chmod(0o700)
    install.chmod(0o700)
    os.symlink("releases/4.3.27", install / "current")
    return install


class FaithfulMigrationTransaction:
    """Faithful in-process model of the migration-only restore transaction.

    Mirrors ``restore-private-state.sh --migration``: the swap restarts services and keeps
    the old-path rollback state armed (no commit), commit marks the state committed before it
    deletes the old state without restarting services (they already run and passed the outer
    verify), and rollback restores every pre-migration path byte and restarts services. Uses
    real filesystem moves so rollback is provable byte-for-byte.
    """

    def __init__(self, directory, destination, restored_bytes, services, backup_sha, rehearsal_at):
        self.directory = Path(directory)
        self.destination = Path(destination)
        self.old = Path(str(destination) + ".old")
        self.restored_bytes = restored_bytes
        self.services = list(services)
        self.committed = False
        self.rolled_back = False
        self.had_old = self.destination.exists()
        self.pre_state = self.destination.read_bytes() if self.had_old else None
        self.backup_sha = backup_sha
        self.rehearsal_at = rehearsal_at
        self.swap_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0
        self.restarts = 0

    def _make_receipt(self):
        receipt = {
            "schemaVersion": 1, "kind": "pixel-restore-receipt", "status": "pass", "mode": "restore",
            "verified": True, "automaticRollbackArmed": True,
            "knowledgeDeletionReconciled": False, "historicalKeyWrappingRemoved": False,
            "backupSha256": self.backup_sha, "sourcePixel": "3.2.2", "targetPixel": "4.3.27",
            "receiptSha256": None, "generatedAt": self.rehearsal_at,
            "privacy": dict(migration.PRIVACY), "boundary": migration.BOUNDARIES["restore"],
        }
        receipt["receiptSha256"] = migration.self_hash(receipt, "receiptSha256")
        return receipt

    def _journal(self):
        return {
            "schemaVersion": 1, "kind": "pixel-restore-migration-journal",
            "backupSha256": self.backup_sha, "sourcePixel": "3.2.2", "targetPixel": "4.3.27",
            "committed": self.committed, "cleanup": "armed", "rolledBack": self.rolled_back,
            "finalization": "armed",
            "contractRoots": [str(self.destination)], "destinations": [str(self.destination)],
            "oldPaths": [str(self.old)], "temporaryPaths": [str(self.destination) + ".new"],
            "hadOld": [self.had_old], "units": self.services,
        }

    def swap(self, pixel, backup, identity, signers, receipt, journal, contract_sha256):
        self.swap_calls += 1
        self.restarts += 1  # services are running (restarted) before the swap returns
        if self.had_old:
            os.rename(self.destination, self.old)
        self.destination.write_bytes(self.restored_bytes)
        receipt.write_text(json.dumps(self._make_receipt()) + "\n")
        os.chmod(receipt, 0o600)
        journal.write_text(json.dumps(self._journal()) + "\n")
        os.chmod(journal, 0o600)
        return {"status": "pass", "mode": "restore-migration", "verified": True,
                "automaticRollbackArmed": True, "committed": False}

    def commit(self, pixel, journal):
        self.commit_calls += 1
        # Terminal boundary is marked committed before any destructive cleanup and the
        # verb is idempotent: cleanup only removes the old pre-migration state. Commit never
        # restarts services (they already run and passed the outer verify).
        self.committed = True
        if self.old.exists():
            self.old.unlink()
        journal.write_text(json.dumps(self._journal()) + "\n")
        os.chmod(journal, 0o600)

    def rollback(self, pixel, journal):
        self.rollback_calls += 1
        if self.destination.exists():
            self.destination.unlink()
        if self.had_old and self.old.exists():
            os.rename(self.old, self.destination)
        self.restarts += 1
        self.rolled_back = True


class MigrationActivateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.repo, self.commit, self.tree = make_repo(self.directory)
        self.backup = write(self.directory / "backup.tar.gz.age", "legacy-encrypted")
        self.identity = write(self.directory / "identity", "age-identity")
        self.signers = write(self.directory / "allowed-signers", "pixel-backup ssh-ed25519 AAA")
        self.install = make_install(self.directory, "3.2.2")
        self.install42 = make_install42(self.directory)
        self.pixel = write(self.directory / "pixel", "#!/usr/bin/env bash\nset -euo pipefail\n")
        self.pixel.chmod(0o700)
        self._patched = []
        # The activate journal must live directly beneath the fixed custody directory; the
        # unit harness substitutes a temp custody root so no /var/lib path is touched.
        self.custody = self.directory / "custody"
        self.custody.mkdir(mode=0o700)
        self._patch("MIGRATION_JOURNAL_CUSTODY", self.custody)

    def tearDown(self):
        for target, original in reversed(self._patched):
            setattr(migration, target, original)
        self.tmp.cleanup()

    def _patch(self, target, replacement):
        self._patched.append((target, getattr(migration, target)))
        setattr(migration, target, replacement)

    def _install_transaction(self, pre_state=b"pre-migration-live-bytes\n", restored=b"restored-3.2-bytes\n"):
        destination = self.directory / "live" / "workspace" / "state.bin"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(pre_state)
        txn = FaithfulMigrationTransaction(
            self.directory, destination, restored,
            services=["openclaw-gateway.service", "pixel-web-courier.service"],
            backup_sha=self.plan["backupSha256"], rehearsal_at=self.rehearsal["generatedAt"],
        )
        self._patch("run_restore_migration_swap", txn.swap)
        self._patch("run_restore_migration_commit", txn.commit)
        self._patch("run_restore_migration_rollback", txn.rollback)
        return txn

    def _write_verify_pixel(self, fail_verify=False):
        fake = (
            'if [[ "$1" == verify ]]; then\n'
            '  if [[ {fail_verify} == 1 ]]; then echo "verify boom" >&2; exit 1; fi\n'
            '  expected="${{@: -1}}"; python3 - "$expected" "{commit}" "{tree}" <<"FAKEPY"\n'
            'import json, os, sys\n'
            'from datetime import datetime, timezone\n'
            'install, commit, tree = sys.argv[1], sys.argv[2], sys.argv[3]\n'
            'att = json.loads("""{att}""")\n'
            'att["verifiedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")\n'
            'att["source"] = {{"state": "git-clean", "commit": commit, "tree": tree}}\n'
            'os.makedirs(install, exist_ok=True)\n'
            'open(os.path.join(install, "runtime-attestation.json"), "w").write(json.dumps(att))\n'
            'FAKEPY\n'
            '  exit 0; fi\n'
            'echo "unexpected: $*" >&2; exit 1\n'
        )
        script = fake.format(fail_verify=int(fail_verify), commit=self.commit, tree=self.tree,
                             att=json.dumps(build_attestation(self.commit, self.tree)))
        write(self.pixel, self.pixel.read_text() + script)

    def _receipt_doc(self):
        receipt = {
            "schemaVersion": 1, "kind": "pixel-restore-receipt", "status": "pass", "mode": "restore",
            "verified": True, "automaticRollbackArmed": True,
            "knowledgeDeletionReconciled": False, "historicalKeyWrappingRemoved": False,
            "backupSha256": self.plan["backupSha256"], "sourcePixel": "3.2.2", "targetPixel": "4.3.27",
            "receiptSha256": None, "generatedAt": self.rehearsal["generatedAt"],
            "privacy": dict(migration.PRIVACY), "boundary": migration.BOUNDARIES["restore"],
        }
        receipt["receiptSha256"] = migration.self_hash(receipt, "receiptSha256")
        return receipt

    def _write_evidence(self, path, value):
        migration.write_new_private(path, (json.dumps(value) + "\n").encode("utf-8"))
        return path

    def _make_plan_rehearsal(self, rehearsal_at=None):
        self.plan = migration.build_plan(
            ROOT, self.install,
            LEGACY_AUDIT,
            self.backup, now=lambda: NOW, source_identity=(self.commit, self.tree),
            backup_roots_sha256=CONTRACT_SHA,
        )
        plan_out = self._write_evidence(self.directory / "plan.json", self.plan)
        self.rehearsal = migration.build_rehearsal(
            ROOT, self.plan, self.plan["backupSha256"], self.directory / "rehearsal-root",
            {"status": "pass", "mode": "rehearse", "liveStateChanged": False},
            now=lambda: NOW, source_identity=(self.commit, self.tree),
        )
        if rehearsal_at:
            self.rehearsal["generatedAt"] = rehearsal_at
        rehearsal_out = self._write_evidence(self.directory / "rehearsal.json", self.rehearsal)
        return self.plan, self.rehearsal, plan_out, rehearsal_out

    def _activate_args(self, plan_out, rehearsal_out, receipt_out, completion_out, journal_out):
        return [
            "activate", "--root", self.repo, "--pixel", self.pixel, "--install-dir", self.install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--identity", self.identity, "--signers", self.signers,
            "--restore-receipt", receipt_out, "--completion", completion_out, "--journal", journal_out,
        ]

    def run_activate(self, *arguments):
        original_argv = sys.argv
        sys.argv = ["migrate-legacy-clean.py", *map(str, arguments)]
        try:
            return migration.main()
        finally:
            sys.argv = original_argv

    def test_activate_happy_path(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal(rehearsal_at="2026-08-22T12:00:00Z")
        self._write_verify_pixel()
        txn = self._install_transaction()
        receipt_out = self.directory / "receipt.json"
        completion_out = self.directory / "completion.json"
        journal_out = self.custody / "journal.json"
        result = self.run_activate(*self._activate_args(plan_out, rehearsal_out, receipt_out, completion_out, journal_out))
        self.assertEqual(result, 0)
        self.assertTrue(receipt_out.exists())
        self.assertTrue(completion_out.exists())
        self.assertEqual(stat.S_IMODE(completion_out.lstat().st_mode), 0o600)
        completion = migration.read_json(completion_out, "completion", private=True)
        migration.validate_completion_doc(completion)
        self.assertEqual(completion["activeRelease"], "4.3.27")
        self.assertEqual(completion["planSha256"], plan["planSha256"])
        self.assertEqual(completion["rehearsalSha256"], rehearsal["rehearsalSha256"])
        self.assertEqual(completion["backupSha256"], plan["backupSha256"])
        self.assertEqual(txn.swap_calls, 1)
        self.assertEqual(txn.commit_calls, 1)
        self.assertEqual(txn.rollback_calls, 0)
        self.assertTrue(txn.committed)
        self.assertEqual(txn.restarts, 1)  # only the swap restarts services; commit never restarts
        self.assertFalse(txn.old.exists())

    def test_activate_rejects_mismatched_backup_before_mutation(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        other_backup = write(self.directory / "other.tar.gz.age", "other")
        completion_out = self.directory / "completion.json"
        args = [
            "activate", "--root", self.repo, "--pixel", self.pixel, "--install-dir", self.install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", str(other_backup),
            "--identity", self.identity, "--signers", self.signers,
            "--restore-receipt", self.directory / "receipt.json", "--completion", completion_out,
            "--journal", self.custody / "journal.json",
        ]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertFalse(completion_out.exists())

    def test_activate_rejects_dirty_source_before_mutation(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        write(self.repo / "dirty.txt", "x")
        completion_out = self.directory / "completion.json"
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)
        self.assertFalse(completion_out.exists())

    def test_activate_rejects_unprepared_42_before_mutation(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        broken = make_install42(self.directory, "broken42")
        (broken / "releases/4.3.27/plugin").rmdir()
        completion_out = self.directory / "completion.json"
        args = [
            "activate", "--root", self.repo, "--pixel", self.pixel, "--install-dir", str(broken),
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--identity", self.identity, "--signers", self.signers,
            "--restore-receipt", self.directory / "receipt.json", "--completion", completion_out,
            "--journal", self.custody / "journal.json",
        ]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertFalse(completion_out.exists())

    def test_activate_rejects_prepare_spec_without_stage_before_mutation(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()
        txn = self._install_transaction()
        spec_path = write(self.directory / "prepare-spec.json", '{"deploymentItems":[{"kind":"config","path":"/x","oldPath":"/x-a.old","newPath":"/x-a.new","hadOld":1}]}')
        args = [
            "activate", "--root", self.repo, "--pixel", self.pixel, "--install-dir", self.install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--identity", self.identity, "--signers", self.signers,
            "--restore-receipt", self.directory / "receipt.json", "--completion", self.directory / "completion.json",
            "--journal", self.custody / "journal.json",
            "--prepare-spec", spec_path,
        ]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertFalse((self.directory / "completion.json").exists())
        self.assertEqual(txn.swap_calls, 0)

    def test_activate_rejects_prepare_stage_without_spec_before_mutation(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()
        txn = self._install_transaction()
        stage = self.directory / "stage"
        stage.mkdir()
        args = [
            "activate", "--root", self.repo, "--pixel", self.pixel, "--install-dir", self.install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--identity", self.identity, "--signers", self.signers,
            "--restore-receipt", self.directory / "receipt.json", "--completion", self.directory / "completion.json",
            "--journal", self.custody / "journal.json",
            "--prepare-stage", stage,
        ]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertFalse((self.directory / "completion.json").exists())
        self.assertEqual(txn.swap_calls, 0)

    def _make_prepare_spec_and_receipt(self, spec_sha=None, candidates=None):
        item = {"kind": "config", "path": "/tmp/x/app.json",
                "oldPath": "/tmp/x/.pixel-restore-app.json-1-0.old",
                "newPath": "/tmp/x/.pixel-restore-app.json-1-0.new", "hadOld": 1,
                "sha256": "a" * 64}
        # Valid new spec/receipt schema: the spec binds exactly deploymentItems +
        # serviceDesired and the receipt carries every required field with a recomputed
        # bundle hash, so the activate tests reach the intended candidate-binding /
        # spec-mismatch error rather than passing on an earlier shape error.
        sd = {"openclaw-gateway.service": {"enabled": True, "active": True}}
        spec = {"deploymentItems": [item], "serviceDesired": sd}
        spec_bytes = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        spec_path = write(self.directory / "prep-spec.json", spec_bytes.decode("utf-8"))
        spec_path.chmod(0o600)
        actual = hashlib.sha256(spec_bytes).hexdigest()
        if candidates is None:
            candidates = {os.path.basename(item["newPath"]): {"type": "file", "sha256": "a" * 64}}
        sd_sha = hashlib.sha256(json.dumps(sd, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        chosen_spec_sha = spec_sha if spec_sha else actual
        receipt = {"mode": "migration-prepare", "targetPixel": "4.3.27",
                   "specSha256": chosen_spec_sha, "candidates": candidates,
                   "installManifestSha256": "b" * 64, "releaseIdentitySha256": "c" * 64,
                   "releaseVersion": "4.3.27", "releaseTreeSha256": "d" * 64,
                   "serviceDesiredSha256": sd_sha}
        bundle = json.dumps(
            {"specSha256": chosen_spec_sha, "candidates": candidates,
             "installManifestSha256": receipt["installManifestSha256"],
             "releaseIdentitySha256": receipt["releaseIdentitySha256"],
             "releaseVersion": receipt["releaseVersion"],
             "releaseTreeSha256": receipt["releaseTreeSha256"],
             "serviceDesiredSha256": sd_sha},
            sort_keys=True, separators=(",", ":")).encode("utf-8")
        receipt["bundleSha256"] = hashlib.sha256(bundle).hexdigest()
        receipt_path = write(self.directory / "prep-receipt.json", json.dumps(receipt))
        receipt_path.chmod(0o600)
        stage = self.directory / "stage"
        stage.mkdir(exist_ok=True)
        return spec_path, receipt_path, stage

    def test_activate_requires_prepare_receipt_with_spec(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()
        txn = self._install_transaction()
        spec_path, _, stage = self._make_prepare_spec_and_receipt()
        args = [
            "activate", "--root", self.repo, "--pixel", self.pixel, "--install-dir", self.install42,
            "--plan", plan_out, "--rehearsal", rehearsal_out, "--backup", self.backup,
            "--identity", self.identity, "--signers", self.signers,
            "--restore-receipt", self.directory / "receipt.json", "--completion", self.directory / "completion.json",
            "--journal", self.custody / "journal.json",
            "--prepare-spec", spec_path, "--prepare-stage", stage,
        ]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertFalse((self.directory / "completion.json").exists())
        self.assertEqual(txn.swap_calls, 0)

    def test_activate_rejects_prepare_receipt_spec_mismatch(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()
        txn = self._install_transaction()
        spec_path, receipt_path, stage = self._make_prepare_spec_and_receipt(spec_sha="f" * 64)
        args = self._activate_args(plan_out, rehearsal_out, self.directory / "receipt.json",
                                   self.directory / "completion.json", self.custody / "journal.json")
        args += ["--prepare-spec", spec_path, "--prepare-stage", stage, "--prepare-receipt", receipt_path]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertFalse((self.directory / "completion.json").exists())
        self.assertEqual(txn.swap_calls, 0)

    def test_activate_rejects_prepare_receipt_missing_candidate_binding(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()
        txn = self._install_transaction()
        spec_path, receipt_path, stage = self._make_prepare_spec_and_receipt(candidates={})
        args = self._activate_args(plan_out, rehearsal_out, self.directory / "receipt.json",
                                   self.directory / "completion.json", self.custody / "journal.json")
        args += ["--prepare-spec", spec_path, "--prepare-stage", stage, "--prepare-receipt", receipt_path]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertFalse((self.directory / "completion.json").exists())
        self.assertEqual(txn.swap_calls, 0)

    def test_activate_prepared_cutover_starts_from_exact_legacy_pointer(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal(
            rehearsal_at="2026-08-22T12:00:00Z"
        )
        self._write_verify_pixel()
        txn = self._install_transaction()

        # A prepared clean migration starts with the legacy release active and the target
        # installed beside it. The privileged transaction, not the preflight, performs the
        # pointer switch while rollback is armed.
        legacy_release = self.install42 / "releases" / "3.2.2"
        legacy_release.mkdir()
        legacy_release.chmod(0o700)
        write(legacy_release / "VERSION", "3.2.2\n")
        (legacy_release / "VERSION").chmod(0o600)
        current = self.install42 / "current"
        current.unlink()
        os.symlink("releases/3.2.2", current)

        original_swap = txn.swap

        def prepared_swap(*args, **kwargs):
            result = original_swap(*args, **kwargs)
            current.unlink()
            os.symlink("releases/4.3.27", current)
            return result

        self._patch("run_restore_migration_swap", prepared_swap)
        spec_path, receipt_path, stage = self._make_prepare_spec_and_receipt()
        target_release = self.install42 / "releases" / "4.3.27"
        release_identity = write(target_release / "release-identity.json", "{}\n")
        install_manifest = write(
            target_release / "install-manifest.sha256",
            "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in (target_release / "VERSION", release_identity)
            ),
        )
        prepare_receipt = json.loads(receipt_path.read_text())
        prepare_receipt["installManifestSha256"] = hashlib.sha256(
            install_manifest.read_bytes()
        ).hexdigest()
        prepare_receipt["releaseIdentitySha256"] = hashlib.sha256(
            release_identity.read_bytes()
        ).hexdigest()
        prepare_receipt["releaseTreeSha256"] = migration._release_tree_sha(target_release)
        prepare_receipt["bundleSha256"] = hashlib.sha256(json.dumps(
            {
                "specSha256": prepare_receipt["specSha256"],
                "candidates": prepare_receipt["candidates"],
                "installManifestSha256": prepare_receipt["installManifestSha256"],
                "releaseIdentitySha256": prepare_receipt["releaseIdentitySha256"],
                "releaseVersion": prepare_receipt["releaseVersion"],
                "releaseTreeSha256": prepare_receipt["releaseTreeSha256"],
                "serviceDesiredSha256": prepare_receipt["serviceDesiredSha256"],
            },
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        receipt_path.write_text(json.dumps(prepare_receipt))
        receipt_out = self.directory / "receipt.json"
        completion_out = self.directory / "completion.json"
        args = self._activate_args(
            plan_out, rehearsal_out, receipt_out, completion_out, self.custody / "journal.json",
        )
        args += [
            "--prepare-spec", spec_path,
            "--prepare-stage", stage,
            "--prepare-receipt", receipt_path,
        ]
        result = self.run_activate(*args)
        self.assertEqual(result, 0)
        self.assertEqual(os.readlink(current), "releases/4.3.27")
        self.assertEqual(txn.swap_calls, 1)
        self.assertEqual(txn.commit_calls, 1)
        migration.validate_completion_doc(
            migration.read_json(completion_out, "completion", private=True)
        )

    def test_activate_prepared_cutover_rejects_target_already_active(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        txn = self._install_transaction()
        self._patch("_verify_prepare_receipt", lambda *args, **kwargs: None)
        self._patch("_require_installed_release", lambda *args, **kwargs: None)
        spec_path = write(self.directory / "prepared-spec.json", "{}\n")
        receipt_path = write(self.directory / "prepared-receipt.json", "{}\n")
        stage = self.directory / "prepared-stage"
        stage.mkdir()
        completion_out = self.directory / "completion.json"
        args = self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json",
            completion_out, self.custody / "journal.json",
        )
        args += [
            "--prepare-spec", spec_path,
            "--prepare-stage", stage,
            "--prepare-receipt", receipt_path,
        ]
        result = self.run_activate(*args)
        self.assertEqual(result, 2)
        self.assertEqual(txn.swap_calls, 0)
        self.assertFalse(completion_out.exists())

    def test_activate_compatibility_mode_rejects_legacy_pointer(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        txn = self._install_transaction()
        legacy_release = self.install42 / "releases" / "3.2.2"
        legacy_release.mkdir()
        legacy_release.chmod(0o700)
        write(legacy_release / "VERSION", "3.2.2\n")
        (legacy_release / "VERSION").chmod(0o600)
        current = self.install42 / "current"
        current.unlink()
        os.symlink("releases/3.2.2", current)
        completion_out = self.directory / "completion.json"
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json",
            completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)
        self.assertEqual(txn.swap_calls, 0)
        self.assertFalse(completion_out.exists())

    def test_activate_restore_failure_leaves_non_pass_receipt_rolled_back_conservative(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        completion_out = self.directory / "completion.json"

        def failing_swap(pixel, backup, identity, signers, receipt, journal, contract_sha256):
            # The swap failed before the transaction was armed. Its internal rollback is
            # best-effort and unverified, so rolledBack must be conservatively False.
            raise migration.MigrationError("swap failed before arming")

        self._patch("run_restore_migration_swap", failing_swap)
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)
        self.assertTrue(completion_out.exists())
        failure = migration.read_json(completion_out, "completion", private=True)
        self.assertEqual(failure["status"], "failed")
        self.assertIs(failure["backupRetained"], True)
        self.assertIs(failure["rolledBack"], False)
        for key, value in migration.PRIVACY.items():
            self.assertIs(failure["privacy"][key], False)

    def test_activate_postswap_verify_failure_rolls_back_every_prestate_byte_and_service(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel(fail_verify=True)
        txn = self._install_transaction(pre_state=b"original-pre-state-bytes-0123456789\n")
        completion_out = self.directory / "completion.json"
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)
        self.assertTrue(completion_out.exists())
        failure = migration.read_json(completion_out, "completion", private=True)
        self.assertEqual(failure["status"], "failed")
        self.assertIs(failure["rolledBack"], True)
        self.assertEqual(txn.swap_calls, 1)
        self.assertEqual(txn.rollback_calls, 1)
        self.assertEqual(txn.commit_calls, 0)
        self.assertTrue(txn.rolled_back)
        self.assertEqual(txn.destination.read_bytes(), b"original-pre-state-bytes-0123456789\n")
        self.assertFalse(txn.old.exists())

    def test_activate_postswap_config_failure_rolls_back(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()
        txn = self._install_transaction(pre_state=b"config-pre-state\n")
        self._patch("_verify_frozen_config", lambda install_dir, snapshot: (_ for _ in ()).throw(
            migration.MigrationError("prepared target configuration changed during the legacy data swap")))
        completion_out = self.directory / "completion.json"
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)
        failure = migration.read_json(completion_out, "completion", private=True)
        self.assertEqual(failure["status"], "failed")
        self.assertIs(failure["rolledBack"], True)
        self.assertEqual(txn.rollback_calls, 1)
        self.assertEqual(txn.commit_calls, 0)
        self.assertEqual(txn.destination.read_bytes(), b"config-pre-state\n")

    def test_activate_rollback_failure_is_reported_conservatively(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel(fail_verify=True)
        txn = self._install_transaction(pre_state=b"pre-state-bytes-0123456789\n")
        self._patch("run_restore_migration_rollback", lambda *a, **k: (_ for _ in ()).throw(
            migration.MigrationError("rollback could not be completed exactly")))
        completion_out = self.directory / "completion.json"
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)
        failure = migration.read_json(completion_out, "completion", private=True)
        self.assertEqual(failure["status"], "failed")
        self.assertIs(failure["rolledBack"], False)

    def test_activate_cleanup_failure_never_rolls_back_or_overwrites_pass_receipt(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()
        txn = self._install_transaction(pre_state=b"cleanup-pre-state\n")
        # Once the terminal completion receipt is durably written and commit begins, a
        # cleanup failure must retain the verified new live state: never roll back and
        # never rewrite the pass completion.
        self._patch("run_restore_migration_commit", lambda *a, **k: (_ for _ in ()).throw(
            migration.MigrationError("migration commit cleanup failed; state is committed")))
        completion_out = self.directory / "completion.json"
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)
        self.assertTrue(completion_out.exists())
        completion = migration.read_json(completion_out, "completion", private=True)
        self.assertIs(completion["verified"], True)
        migration.validate_completion_doc(completion)
        self.assertEqual(txn.rollback_calls, 0)
        # The verified new live state is untouched.
        self.assertEqual(txn.destination.read_bytes(), b"restored-3.2-bytes\n")

    def test_activate_completion_refuses_overwrite(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        completion_out = self.directory / "completion.json"
        self._write_evidence(completion_out, {"existing": True})
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 2)

    def test_frozen_config_proves_postswap_change(self):
        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        snapshot = migration._validate_prepared_config(self.install42)
        (self.install42 / "releases/4.3.27/VERSION").write_text("4.3.27\n")
        migration._verify_frozen_config(self.install42, snapshot)
        (self.install42 / "releases/4.3.27/VERSION").write_text("4.3.2\n")
        with self.assertRaises(migration.MigrationError):
            migration._verify_frozen_config(self.install42, snapshot)

    def test_activate_preserves_legacy_calendar_shapes_and_keeps_them_non_retryable(self):
        spec = importlib.util.spec_from_file_location("pixel_broker", ROOT / "deploy/source-broker/broker.py")
        broker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(broker)
        proposal_id = "calendar-1785876500000-a1b2c3d4"
        legacy_dir = self.directory / "legacy-results"
        live_dir = self.directory / "live-results"
        legacy_dir.mkdir(parents=True)
        live_dir.mkdir(parents=True)
        proposal = {
            "schemaVersion": 1, "proposalId": proposal_id, "source": "pixel-owner-conversation",
            "action": "update", "status": "pending-operator-approval",
            "createdAt": "2026-08-05T12:00:00Z",
            "values": {"eventId": "event-1", "expectedEtag": '"etag"', "summary": "Updated summary"},
            "boundary": broker.PROPOSAL_BOUNDARY,
        }
        approved_path = legacy_dir / f"{proposal_id}.approved.json"
        approved_path.write_text(json.dumps(proposal) + "\n")
        if os.name != "nt":
            approved_path.chmod(0o600)
        _approved, proposal_hash = broker.protected_proposal(approved_path)
        (legacy_dir / f"{proposal_id}.processing.json").write_text(json.dumps({
            "schemaVersion": 1, "proposalId": proposal_id, "proposalSha256": proposal_hash,
            "status": "processing", "startedAt": "2026-08-05T12:00:01Z",
            "recovery": "Do not retry automatically; inspect Calendar and the actuator journal.",
        }) + "\n")
        (legacy_dir / f"{proposal_id}.json").write_text(json.dumps({
            "schemaVersion": 1, "proposalId": proposal_id, "status": "done", "action": "update",
            "providerId": "calendar", "eventId": "event-1",
        }) + "\n")

        plan, rehearsal, plan_out, rehearsal_out = self._make_plan_rehearsal()
        self._write_verify_pixel()

        def swap_copy(pixel, backup, identity, signers, receipt, journal, contract_sha256):
            for name in (f"{proposal_id}.approved.json", f"{proposal_id}.processing.json", f"{proposal_id}.json"):
                shutil.copy2(legacy_dir / name, live_dir / name)
                if os.name != "nt":
                    (live_dir / name).chmod(0o600)
            receipt.write_text(json.dumps(self._receipt_doc()) + "\n")
            os.chmod(receipt, 0o600)
            journal.write_text(json.dumps({
                "schemaVersion": 1, "kind": "pixel-restore-migration-journal", "committed": False,
                "destinations": [], "oldPaths": [], "temporaryPaths": [], "hadOld": [], "units": [],
            }) + "\n")
            os.chmod(journal, 0o600)
            return {"status": "pass", "mode": "restore-migration", "verified": True,
                    "automaticRollbackArmed": True, "committed": False}

        self._patch("run_restore_migration_swap", swap_copy)
        self._patch("run_restore_migration_commit", lambda *a, **k: None)
        self._patch("run_restore_migration_rollback", lambda *a, **k: None)
        completion_out = self.directory / "completion.json"
        result = self.run_activate(*self._activate_args(
            plan_out, rehearsal_out, self.directory / "receipt.json", completion_out, self.custody / "journal.json",
        ))
        self.assertEqual(result, 0)

        for name in (f"{proposal_id}.approved.json", f"{proposal_id}.processing.json", f"{proposal_id}.json"):
            self.assertTrue((live_dir / name).exists(), name)

        previous = os.environ.get("PIXEL_ACTION_RESULT_DIR")
        os.environ["PIXEL_ACTION_RESULT_DIR"] = str(live_dir)
        try:
            # The legacy result is historical evidence, not permission to synthesize a
            # 4.2 success record; the retained claim is an indeterminate outcome. Either
            # fail-closed path (legacy result not 4.2-bound, or retained processing claim)
            # must refuse to retry: no provider call, no new journal, shapes untouched.
            with self.assertRaises(RuntimeError):
                broker.approve(proposal_id)
            self.assertTrue((live_dir / f"{proposal_id}.approved.json").exists())
            self.assertTrue((live_dir / f"{proposal_id}.processing.json").exists())
            self.assertTrue((live_dir / f"{proposal_id}.json").exists())
            self.assertFalse((live_dir / ".journal").exists())
        finally:
            if previous is None:
                os.environ.pop("PIXEL_ACTION_RESULT_DIR", None)
            else:
                os.environ["PIXEL_ACTION_RESULT_DIR"] = previous

    def test_validate_prepared_config_rejects_wrong_active(self):
        legacy = make_install(self.directory, "3.2.2", name="legacy42")
        with self.assertRaises(migration.MigrationError):
            migration._validate_prepared_config(legacy)

    def test_validate_prepared_config_accepts_installed_runtime_without_controller(self):
        release = self.install42 / "releases/4.3.27"
        self.assertFalse((release / "pixel").exists())
        snapshot = migration._validate_prepared_config(self.install42)
        self.assertRegex(snapshot["releaseTreeSha256"], r"^[0-9a-f]{64}$")

    def test_validate_prepared_config_accepts_bound_runtime_symlinks(self):
        release = self.install42 / "releases/4.3.27"
        venv = release / "web-courier/.venv/bin"
        venv.mkdir(parents=True)
        os.symlink("/usr/bin/python3", venv / "python3")
        snapshot = migration._validate_prepared_config(self.install42)
        self.assertRegex(snapshot["releaseTreeSha256"], r"^[0-9a-f]{64}$")
        (venv / "python3").unlink()
        os.symlink("/usr/bin/python3.12", venv / "python3")
        with self.assertRaises(migration.MigrationError):
            migration._verify_frozen_config(self.install42, snapshot)

    def test_validate_prepared_config_rejects_missing_runtime_component(self):
        (self.install42 / "releases/4.3.27/plugin-frontier").rmdir()
        with self.assertRaisesRegex(
            migration.MigrationError,
            "prepared target deployment is missing a required component",
        ):
            migration._validate_prepared_config(self.install42)


class MigrationRestoreSafetyBackupRecipientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.identity = self.directory / "identity.agekey"
        self.identity.write_text("AGE-SECRET-KEY-TEST\n", encoding="utf-8")
        self.identity.chmod(0o600)
        self.recipient = "age1" + "q" * 58

    def tearDown(self):
        self.tmp.cleanup()

    def _arguments(self):
        return (
            self.directory / "pixel",
            self.directory / "backup.tar.gz.age",
            self.identity,
            self.directory / "allowed-signers",
            self.directory / "restore-receipt.json",
            self.directory / "journal.json",
            CONTRACT_SHA,
        )

    def test_swap_derives_mandatory_safety_backup_recipient_from_bound_identity(self):
        derived = subprocess.CompletedProcess(
            ["age-keygen", "-y", str(self.identity)], 0, self.recipient + "\n", "",
        )
        with mock.patch.object(migration.subprocess, "run", return_value=derived) as run:
            with mock.patch.object(migration, "_run_pixel") as run_pixel:
                with mock.patch.dict(
                    os.environ,
                    {"PIXEL_BACKUP_AGE_RECIPIENT": "age1" + "x" * 58},
                    clear=False,
                ):
                    migration.run_restore_migration_swap(*self._arguments())

        run.assert_called_once_with(
            ["age-keygen", "-y", str(self.identity)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        env = run_pixel.call_args.kwargs["env"]
        self.assertEqual(env["PIXEL_MIGRATION_CONTRACT_SHA256"], CONTRACT_SHA)
        self.assertEqual(env["PIXEL_BACKUP_AGE_RECIPIENT"], self.recipient)

    def test_swap_fails_before_restore_when_identity_cannot_yield_recipient(self):
        invalid = subprocess.CompletedProcess(
            ["age-keygen", "-y", str(self.identity)], 1, "", "invalid identity",
        )
        with mock.patch.object(migration.subprocess, "run", return_value=invalid):
            with mock.patch.object(migration, "_run_pixel") as run_pixel:
                with self.assertRaisesRegex(
                    migration.MigrationError,
                    "safety-backup recipient could not be derived",
                ):
                    migration.run_restore_migration_swap(*self._arguments())
        run_pixel.assert_not_called()

    def test_recipient_derivation_rejects_multiline_or_wrong_shape(self):
        for value in ("age1short", self.recipient + "\n" + self.recipient, "ssh-ed25519 test"):
            with self.subTest(value=value):
                result = subprocess.CompletedProcess(
                    ["age-keygen", "-y", str(self.identity)], 0, value, "",
                )
                with mock.patch.object(migration.subprocess, "run", return_value=result):
                    with self.assertRaises(migration.MigrationError):
                        migration._backup_recipient_from_identity(self.identity)

    def test_recipient_derivation_fails_closed_when_age_keygen_is_unavailable(self):
        with mock.patch.object(
            migration.subprocess,
            "run",
            side_effect=FileNotFoundError("age-keygen unavailable"),
        ):
            with self.assertRaisesRegex(
                migration.MigrationError,
                "safety-backup recipient could not be derived",
            ):
                migration._backup_recipient_from_identity(self.identity)


class MigrationRestoreContractTests(unittest.TestCase):
    """Prove standard 4.2 restore rejects a realistic 3.2 manifest while the
    migration-only root contract accepts it only with matching evidence."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _archive(self, paths):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for path in paths:
                info = tarfile.TarInfo(path)
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                archive.addfile(info)
                member = tarfile.TarInfo(path + "/state.json")
                member.mode = 0o600
                member.size = 2
                archive.addfile(member, io.BytesIO(b"{}\n"))
            manifest = json.dumps({"schemaVersion": 1, "pixelVersion": "3.2.2", "paths": paths}).encode()
            info = tarfile.TarInfo(".pixel-backup-manifest.json")
            info.mode = 0o600
            info.size = len(manifest)
            archive.addfile(info, io.BytesIO(manifest))
        return output.getvalue()

    def _run_audit(self, script, allowed_text, archive):
        allowed = self.directory / "allowed"
        manifest = self.directory / "manifest"
        allowed.write_text(allowed_text, encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(script), "--allowed", str(allowed), "--manifest-output", str(manifest)],
            input=archive, capture_output=True, check=False,
        )

    def test_standard_42_allowlist_rejects_realistic_32_manifest(self):
        # A realistic 3.2 manifest whose roots are outside the standard 4.2 restore
        # allowlist is rejected by ordinary restore (which keeps its strict modern
        # allowlist and never widens it).
        realistic_32_roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        standard_42_allowlist = (
            "var/lib/pixel-test/openclaw.json\n"
            "var/lib/pixel-test/workspace\n"
            "var/lib/pixel-source-broker\n"
            "var/lib/pixel-ops-broker\n"
            "var/lib/pixel-frontier-broker\n"
        )
        result = self._run_audit(
            ROOT / "scripts/audit-private-backup.py", standard_42_allowlist,
            self._archive(realistic_32_roots),
        )
        self.assertNotEqual(result.returncode, 0, result.stderr.decode())

    def test_migration_contract_reads_authentic_manifest_only_with_matching_evidence(self):
        # Truthful migration contract: every declared legacy destination must be within
        # the known 4.2 destination allowlist (a 3.2 root that 4.2 dropped is rejected by
        # the live migration destination check, never silently accepted). The manifest
        # may omit new 4.2-era control/frontier roots.
        realistic_32_roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-source-broker"]
        archive = self._archive(realistic_32_roots)
        # Migration-only contract: read the exact authenticated 3.2 root contract
        # (read-backup-manifest.py) and audit the archive against it.
        manifest_out = subprocess.run(
            [sys.executable, str(ROOT / "scripts/read-backup-manifest.py")],
            input=archive, capture_output=True, check=False,
        )
        self.assertEqual(manifest_out.returncode, 0, manifest_out.stderr.decode())
        migration_allowlist = manifest_out.stdout.decode()
        result = self._run_audit(
            ROOT / "scripts/audit-private-backup.py", migration_allowlist, archive,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        audit = json.loads(result.stdout)
        self.assertEqual(audit["status"], "pass")
        self.assertEqual(audit["pixelVersion"], "3.2.2")
        self.assertEqual(audit["roots"], len(realistic_32_roots))
        contract_observed = hashlib.sha256(migration_allowlist.encode("utf-8")).hexdigest()
        self.assertEqual(audit["rootsSha256"], contract_observed)
        # A different contract evidence (e.g. a drifted plan) must not match.
        self.assertNotEqual(contract_observed, hashlib.sha256(b"var/lib/pixel-legacy-state\n").hexdigest())

    def test_read_backup_manifest_rejects_overlapping_contract(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/read-backup-manifest.py")],
            input=self._archive(["var/lib/pixel-legacy-state", "var/lib/pixel-legacy-state/sub"]),
            capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)


class MigrationRootContractDerivationTests(unittest.TestCase):
    """Derive the authenticated root contract from a real signed/encrypted backup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _live_six_field_audit(self):
        return {
            "status": "pass", "schemaVersion": 1, "members": 3, "roots": 2,
            "uncompressedBytes": 100, "pixelVersion": "3.2.2",
        }

    def test_exact_live_six_field_legacy_audit_validates(self):
        # Real Tower2 3.2.2 restore --validate-only emits exactly six fields with no
        # rootsSha256; the legacy six-field set must be accepted as-is.
        self.assertEqual(
            migration.validate_restore_audit(self._live_six_field_audit()), "legacy",
        )

    def test_exact_modern_seven_field_audit_validates(self):
        # The modern seven-field audit includes the canonical root contract digest.
        self.assertEqual(
            migration.validate_restore_audit(dict(LEGACY_AUDIT)), "modern",
        )

    def test_legacy_audit_rejects_extra_field(self):
        audit = self._live_six_field_audit()
        audit["intruder"] = True
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_audit(audit)

    def test_legacy_audit_rejects_missing_field(self):
        audit = self._live_six_field_audit()
        del audit["roots"]
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_audit(audit)

    def test_modern_audit_rejects_malformed_root_contract(self):
        audit = dict(LEGACY_AUDIT)
        audit["rootsSha256"] = "not-a-hash"
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_audit(audit)

    def test_derive_root_contract_matches_manifest_hash(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        contract = migration.derive_backup_root_contract(backup, identity, signers)
        expected = hashlib.sha256("".join(path + "\n" for path in sorted(roots)).encode("utf-8")).hexdigest()
        self.assertEqual(contract, expected)

    def test_derive_rejects_tampered_signature(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        sig_path = Path(str(backup) + ".sig")
        sig_path.write_bytes(b"tampered\n")
        with self.assertRaises(migration.MigrationError):
            migration.derive_backup_root_contract(backup, identity, signers)

    def test_derive_rejects_unsorted_manifest_roots(self):
        # read-backup-manifest.py rejects unsorted roots; the derive path must fail closed.
        unsorted = ["var/lib/pixel-legacy-state", "etc/pixel-agent/gateway.env"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, unsorted)
        with self.assertRaises(migration.MigrationError):
            migration.derive_backup_root_contract(backup, identity, signers)

    def test_cohesive_cohort_audits_frozen_backup_and_binds_sha(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        expected = hashlib.sha256("".join(path + "\n" for path in sorted(roots)).encode("utf-8")).hexdigest()
        args_log = self.directory / "pixel-args.log"
        pixel = write(self.directory / "pixel", (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'printf "%s\\n" "$@" > ' + str(args_log) + "\n"
            'backup="$2"\n'
            'sha_file="${backup}.sha256"\n'
            '[[ -f "$sha_file" && ! -L "$sha_file" ]] || exit 90\n'
            "expected=\"$(awk 'NR == 1 {print $1}' \"$sha_file\")\"\n"
            "observed=\"$(sha256sum \"$backup\" | awk '{print $1}')\"\n"
            '[[ "$expected" == "$observed" ]] || exit 91\n'
            'echo \'{"status":"pass","schemaVersion":1,"members":3,"roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2","rootsSha256":"'
            + expected + '"}\'\n'
        ))
        pixel.chmod(0o700)
        audit, backup_sha, root_sha = migration.build_legacy_cohort(pixel, backup, identity, signers)
        self.assertEqual(audit["status"], "pass")
        recorded = args_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(recorded[0], "restore")
        frozen_backup = Path(recorded[1])
        self.assertNotEqual(frozen_backup, backup)
        self.assertNotEqual(Path(recorded[3]), identity)
        self.assertNotEqual(Path(recorded[5]), signers)
        self.assertEqual(backup_sha, hashlib.sha256(backup.read_bytes()).hexdigest())
        self.assertEqual(root_sha, expected)

    def test_six_field_cohort_normalizes_root_contract_before_plan(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        expected = hashlib.sha256("".join(path + "\n" for path in sorted(roots)).encode("utf-8")).hexdigest()
        pixel = write(self.directory / "pixel", (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'echo \'{"status":"pass","schemaVersion":1,"members":3,"roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2"}\'\n'
        ))
        pixel.chmod(0o700)
        audit, backup_sha, root_sha = migration.build_legacy_cohort(pixel, backup, identity, signers)
        self.assertEqual(root_sha, expected)
        # The legacy audit is normalized to the independently derived root contract.
        self.assertEqual(audit["rootsSha256"], root_sha)
        self.assertEqual(migration.validate_restore_audit(audit), "modern")
        plan = migration.build_plan(
            ROOT, make_install(self.directory, "3.2.2"), audit, backup,
            now=lambda: NOW, source_identity=(COMMIT, TREE),
            backup_roots_sha256=root_sha, backup_sha=backup_sha,
        )
        self.assertEqual(plan["backupRootsSha256"], root_sha)
        self.assertEqual(plan["backupAudit"]["roots"], 2)

    def test_plan_rejects_un_normalized_legacy_audit(self):
        audit = self._live_six_field_audit()
        backup, _, _ = make_signed_encrypted_backup(
            self.directory, ["etc/pixel-agent/gateway.env"],
        )
        with self.assertRaisesRegex(migration.MigrationError, "normalized"):
            migration.build_plan(
                ROOT, make_install(self.directory, "3.2.2"), audit, backup,
                now=lambda: NOW, source_identity=(COMMIT, TREE),
                backup_roots_sha256=CONTRACT_SHA,
                backup_sha=hashlib.sha256(backup.read_bytes()).hexdigest(),
            )

    def test_six_field_cohort_rejects_mutation_between_audit_and_derive(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        pixel = write(self.directory / "pixel", (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'echo \'{"status":"pass","schemaVersion":1,"members":3,"roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2"}\'\n'
        ))
        pixel.chmod(0o700)
        real_validate = migration.run_restore_validate

        def mutate_between_audit_and_derive(pixel, frozen_backup, frozen_identity, frozen_signers):
            audit = real_validate(pixel, frozen_backup, frozen_identity, frozen_signers)
            identity_path = Path(frozen_identity)
            os.chmod(identity_path, 0o600)
            identity_path.write_bytes(b"mutated-frozen-identity")
            return audit

        with mock.patch.object(
            migration, "run_restore_validate", side_effect=mutate_between_audit_and_derive,
        ):
            with self.assertRaises(migration.MigrationError):
                migration.build_legacy_cohort(pixel, backup, identity, signers)

    def test_cohort_rejects_restore_audit_root_contract_mismatch(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        pixel = write(self.directory / "pixel", (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'echo \'{"status":"pass","schemaVersion":1,"members":3,"roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2","rootsSha256":"'
            + ("d" * 64) + '"}\'\n'
        ))
        pixel.chmod(0o700)
        with self.assertRaisesRegex(migration.MigrationError, "root contract does not match"):
            migration.build_legacy_cohort(pixel, backup, identity, signers)

    def test_cohort_rejects_mutation_between_audit_and_derive(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        expected = hashlib.sha256("".join(path + "\n" for path in sorted(roots)).encode("utf-8")).hexdigest()
        pixel = write(self.directory / "pixel", (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'echo \'{"status":"pass","schemaVersion":1,"members":3,"roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2","rootsSha256":"'
            + expected + '"}\'\n'
        ))
        pixel.chmod(0o700)
        real_validate = migration.run_restore_validate

        def mutate_between_audit_and_derive(pixel, frozen_backup, frozen_identity, frozen_signers):
            audit = real_validate(pixel, frozen_backup, frozen_identity, frozen_signers)
            identity_path = Path(frozen_identity)
            os.chmod(identity_path, 0o600)
            identity_path.write_bytes(b"mutated-frozen-identity")
            return audit

        with mock.patch.object(
            migration, "run_restore_validate", side_effect=mutate_between_audit_and_derive,
        ):
            with self.assertRaises(migration.MigrationError):
                migration.build_legacy_cohort(pixel, backup, identity, signers)

    def test_cohort_rejects_checksum_drift_between_audit_and_derive(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, identity, signers = make_signed_encrypted_backup(self.directory, roots)
        expected = hashlib.sha256("".join(path + "\n" for path in sorted(roots)).encode("utf-8")).hexdigest()
        pixel = write(self.directory / "pixel", (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            'echo \'{"status":"pass","schemaVersion":1,"members":3,"roots":2,"uncompressedBytes":100,"pixelVersion":"3.2.2","rootsSha256":"'
            + expected + '"}\'\n'
        ))
        pixel.chmod(0o700)
        real_validate = migration.run_restore_validate

        def mutate_checksum_between_audit_and_derive(pixel, frozen_backup, frozen_identity, frozen_signers):
            audit = real_validate(pixel, frozen_backup, frozen_identity, frozen_signers)
            checksum_path = Path(str(frozen_backup) + ".sha256")
            os.chmod(checksum_path, 0o600)
            checksum_path.write_bytes(b"0" * 64 + b"\n")
            return audit

        with mock.patch.object(
            migration, "run_restore_validate", side_effect=mutate_checksum_between_audit_and_derive,
        ):
            with self.assertRaises(migration.MigrationError):
                migration.build_legacy_cohort(pixel, backup, identity, signers)

    def test_original_backup_drift_fails_before_plan_output(self):
        roots = ["etc/pixel-agent/gateway.env", "var/lib/pixel-legacy-state"]
        backup, _, _ = make_signed_encrypted_backup(self.directory, roots)
        frozen_sha = migration.sha256_file(backup, "backup")
        backup.write_bytes(b"replacement-after-freeze")
        with self.assertRaises(migration.MigrationError):
            migration.build_plan(
                ROOT, make_install(self.directory, "3.2.2"),
                LEGACY_AUDIT, backup, now=lambda: NOW,
                source_identity=(COMMIT, TREE),
                backup_roots_sha256=CONTRACT_SHA, backup_sha=frozen_sha,
            )

    def test_freeze_input_rejects_oversized_and_unsafe_inputs(self):
        frozen = self.directory / "frozen"
        frozen.mkdir()
        big_sig = self.directory / "big.sig"
        big_sig.write_bytes(b"x" * (migration.SIGNATURE_MAX_BYTES + 1))
        with self.assertRaises(migration.MigrationError):
            migration._freeze_input(big_sig, frozen / "s", "backup signature", maximum=migration.SIGNATURE_MAX_BYTES)
        big_checksum = self.directory / "big.sha256"
        big_checksum.write_bytes(b"x" * (migration.CHECKSUM_MAX_BYTES + 1))
        with self.assertRaises(migration.MigrationError):
            migration._freeze_input(big_checksum, frozen / "c", "backup checksum", maximum=migration.CHECKSUM_MAX_BYTES)
        big_identity = self.directory / "big.id"
        big_identity.write_bytes(b"x" * (migration.SUPPORT_MAX_BYTES + 1))
        with self.assertRaises(migration.MigrationError):
            migration._freeze_input(big_identity, frozen / "i", "age identity", maximum=migration.SUPPORT_MAX_BYTES)
        target = self.directory / "target"
        target.write_bytes(b"payload")
        link = self.directory / "link"
        os.symlink(target, link)
        with self.assertRaises(migration.MigrationError):
            migration._freeze_input(link, frozen / "l", "age identity", maximum=migration.SUPPORT_MAX_BYTES)

    def test_partial_os_write_cannot_truncate_frozen_copy(self):
        payload = bytes(range(256)) * 40
        source = self.directory / "src"
        source.write_bytes(payload)
        frozen = self.directory / "frozen"
        frozen.mkdir()
        dest = frozen / "out"
        real_write = os.write

        def partial_write(fd, data):
            return real_write(fd, data[:7])

        with mock.patch("os.write", side_effect=partial_write):
            migration._freeze_input(source, dest, "backup", maximum=migration.MAX_BACKUP_BYTES)
        self.assertEqual(dest.read_bytes(), payload)

    def test_freeze_input_rejects_zero_write(self):
        payload = bytes(range(256)) * 40
        source = self.directory / "src"
        source.write_bytes(payload)
        frozen = self.directory / "frozen"
        frozen.mkdir()
        dest = frozen / "out"

        with mock.patch("os.write", side_effect=lambda fd, data: 0):
            with self.assertRaises(migration.MigrationError):
                migration._freeze_input(source, dest, "backup", maximum=migration.MAX_BACKUP_BYTES)

    def test_freeze_input_rejects_hardlink_appearing_during_copy(self):
        payload = bytes(range(256)) * 40
        source = self.directory / "src"
        source.write_bytes(payload)
        frozen = self.directory / "frozen"
        frozen.mkdir()
        dest = frozen / "out"
        real_read = os.read

        def read_then_add_link(fd, n):
            chunk = real_read(fd, n)
            if not chunk:
                os.link(source, frozen / "extra-link")
            return chunk

        with mock.patch("os.read", side_effect=read_then_add_link):
            with self.assertRaises(migration.MigrationError):
                migration._freeze_input(source, dest, "backup", maximum=migration.MAX_BACKUP_BYTES)


class MigrationReleaseContractDerivationTests(unittest.TestCase):
    """The single release-contract derivation rule must bind the exact release target from
    the COHERENT in-tree release identity (VERSION == RELEASE-MANIFEST pixel == legacy
    target) and fail closed on incoherent field/source substitution. A coherent future
    signed release identity is accepted only as a release identity; the loader establishes
    coherence only, never authenticity (which comes from the exact clean/signed release and
    release-tree gates). No environment override, CLI override, basename inference, loose
    semver, or fallback."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_contract_repo(self, version="4.3.27", pixel="4.3.27", source="3.2.2", target="4.3.27"):
        repo = self.directory / "repo"
        repo.mkdir()
        write(repo / "VERSION", version + "\n")
        write(
            repo / "RELEASE-MANIFEST.json",
            json.dumps({
                "pixel": pixel,
                "releaseUpdate": {"qualificationMode": "forward", "minimumUpgradablePixel": "4.0.0"},
                "legacyCleanMigration": {
                    "schemaVersion": 1,
                    "v1Contract": {"sourcePixel": source, "targetPixel": target},
                },
            })
            + "\n",
        )
        return repo

    def test_derives_exact_release_target_from_in_tree_identity(self):
        repo = self.make_contract_repo()
        self.assertEqual(migration._load_release_contract(repo), "4.3.27")
        self.assertEqual(migration.SOURCE_PIXEL, "3.2.2")
        self.assertEqual(migration.TARGET_PIXEL, "4.3.27")

    def test_rejects_version_substitution(self):
        repo = self.make_contract_repo(version="4.2.0")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_manifest_version_substitution(self):
        repo = self.make_contract_repo(pixel="4.2.0")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_manifest_target_substitution(self):
        repo = self.make_contract_repo(target="4.2.0")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_manifest_source_substitution(self):
        repo = self.make_contract_repo(source="3.2.1")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_validate_release_policy_accepts_consistent_contract(self):
        repo = self.make_contract_repo()
        policy = migration.validate_release_policy(repo, migration.TARGET_PIXEL)
        self.assertEqual(policy["pixel"], "4.3.27")
        self.assertEqual(policy["qualificationMode"], "forward")

    def test_validate_release_policy_rejects_target_substitution(self):
        repo = self.make_contract_repo(target="4.2.0")
        with self.assertRaises(migration.MigrationError):
            migration.validate_release_policy(repo, migration.TARGET_PIXEL)

    def test_accepts_coherent_future_identity_as_release_identity(self):
        """A coherent future signed release (VERSION == manifest pixel == legacy target) is
        accepted as the release identity. This documents that the loader establishes
        coherence only: the agreeing raw files are not themselves trusted for authenticity,
        which comes from the exact clean/signed release and release-tree gates."""
        repo = self.make_contract_repo(version="4.4.0", pixel="4.4.0", target="4.4.0")
        self.assertEqual(migration._load_release_contract(repo), "4.4.0")

    def test_rejects_symlinked_version(self):
        repo = self.make_contract_repo()
        (repo / "VERSION").unlink()
        target = self.directory / "version-target"
        write(target, "4.3.27\n")
        os.symlink(str(target), repo / "VERSION")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_hardlinked_version(self):
        repo = self.make_contract_repo()
        (repo / "VERSION").unlink()
        target = self.directory / "version-target"
        write(target, "4.3.27\n")
        os.link(str(target), str(repo / "VERSION"))
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_directory_version(self):
        repo = self.make_contract_repo()
        (repo / "VERSION").unlink()
        (repo / "VERSION").mkdir()
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_oversized_version(self):
        repo = self.make_contract_repo()
        write(repo / "VERSION", "x" * 65 + "\n")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_symlinked_manifest(self):
        repo = self.make_contract_repo()
        (repo / "RELEASE-MANIFEST.json").unlink()
        target = self.directory / "manifest-target"
        write(
            target,
            json.dumps({
                "pixel": "4.3.27",
                "releaseUpdate": {"qualificationMode": "forward", "minimumUpgradablePixel": "4.0.0"},
                "legacyCleanMigration": {
                    "schemaVersion": 1,
                    "v1Contract": {"sourcePixel": "3.2.2", "targetPixel": "4.3.27"},
                },
            }),
        )
        os.symlink(str(target), repo / "RELEASE-MANIFEST.json")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_oversized_manifest(self):
        repo = self.make_contract_repo()
        write(repo / "RELEASE-MANIFEST.json", "{" + " " * (1024 * 1024) + "}")
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_duplicate_manifest_keys_at_top_level(self):
        repo = self.make_contract_repo()
        write(
            repo / "RELEASE-MANIFEST.json",
            '{"pixel":"4.3.27","pixel":"4.2.0",'
            '"releaseUpdate":{"qualificationMode":"forward"},'
            '"legacyCleanMigration":{"schemaVersion":1,'
            '"v1Contract":{"sourcePixel":"3.2.2","targetPixel":"4.3.27"}}}',
        )
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)

    def test_rejects_duplicate_manifest_keys_nested(self):
        repo = self.make_contract_repo()
        write(
            repo / "RELEASE-MANIFEST.json",
            '{"pixel":"4.3.27",'
            '"releaseUpdate":{"qualificationMode":"forward","qualificationMode":"sideways"},'
            '"legacyCleanMigration":{"schemaVersion":1,'
            '"v1Contract":{"sourcePixel":"3.2.2","targetPixel":"4.3.27"}}}',
        )
        with self.assertRaises(RuntimeError):
            migration._load_release_contract(repo)


class ReleaseIdentityReaderTests(unittest.TestCase):
    """The canonical migration release-identity read (_read_release_file) must fail closed
    on short-read/early-EOF and on same-inode metadata mutation, not only on pathname
    swaps/replacement. Deterministic mocks drive the races so the tests never depend on
    timing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        write(self.directory / "VERSION", "4.3.27\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _read(self):
        return migration._read_release_file(self.directory, "VERSION", maximum=64)

    def test_read_release_file_accepts_stable_identity(self):
        self.assertEqual(self._read(), b"4.3.27\n")

    def test_read_release_file_rejects_short_read_early_eof(self):
        # First os.read returns a short prefix, then EOF: the loop must keep reading until
        # exactly the pre-read st_size and must fail on the early EOF instead of accepting a
        # truncated identity.
        calls = {"n": 0}

        def fake_read(_fd, _n):
            calls["n"] += 1
            if calls["n"] == 1:
                return b"4.3"
            return b""

        with mock.patch.object(migration.os, "read", side_effect=fake_read):
            with self.assertRaises(RuntimeError) as ctx:
                self._read()
        self.assertIn("ended early", str(ctx.exception))

    def test_read_release_file_rejects_growth(self):
        # First os.read returns the full pre-read size, then an extra byte on the trailing
        # 1-byte probe: a file that grew during the read must be rejected.
        calls = {"n": 0}

        def fake_read(_fd, _n):
            calls["n"] += 1
            if calls["n"] == 1:
                return b"4.3.27\n"
            return b"X"

        with mock.patch.object(migration.os, "read", side_effect=fake_read):
            with self.assertRaises(RuntimeError) as ctx:
                self._read()
        self.assertIn("grew during read", str(ctx.exception))

    def test_read_release_file_rejects_same_inode_metadata_mutation(self):
        # The second fstat (post-read, still on the same descriptor/inode) reports mutated
        # mtime/ctime, simulating an in-place write race that never swaps the path. The read
        # must fail closed even though device/inode/regular/single-link are unchanged.
        real_fstat = migration.os.fstat
        calls = {"n": 0}

        def fake_fstat(fd):
            calls["n"] += 1
            st = real_fstat(fd)
            if calls["n"] == 1:
                return st
            return mock.Mock(
                st_dev=st.st_dev, st_ino=st.st_ino, st_mode=st.st_mode,
                st_nlink=st.st_nlink, st_size=st.st_size,
                st_mtime_ns=st.st_mtime_ns + 1, st_ctime_ns=st.st_ctime_ns + 1,
            )

        with mock.patch.object(migration.os, "fstat", side_effect=fake_fstat):
            with self.assertRaises(RuntimeError) as ctx:
                self._read()
        self.assertIn("changed during read", str(ctx.exception))

    def test_read_release_file_fails_closed_when_no_follow_guarantee_missing(self):
        # The read depends on O_NOFOLLOW/O_CLOEXEC (Pixel is Linux-only and the code
        # claims these guarantees); if either flag is unavailable the read must fail
        # closed instead of silently dropping the guarantee. Patch the module os
        # reference with a bare platform lacking the flags - locally, without mutating
        # the real os module - so the failure is deterministic.
        class FlaglessOS:  # no O_NOFOLLOW/O_CLOEXEC guarantees
            pass
        with mock.patch.object(migration, "os", FlaglessOS):
            with self.assertRaises(RuntimeError) as ctx:
                self._read()
        self.assertIn("O_NOFOLLOW/O_CLOEXEC", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()
