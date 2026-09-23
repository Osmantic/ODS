import argparse
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import select
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release-update.py"


def load_release_update(path=SCRIPT, module_name="pixel_release_update"):
    specification = importlib.util.spec_from_file_location(module_name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError("release-update module is unavailable")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def encoded(value):
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def run_in_pty(arguments, *, cwd=None, environment=None, timeout=20):
    import fcntl
    import pty
    import termios

    master, slave = pty.openpty()

    def establish_controlling_terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    process = subprocess.Popen(
        arguments,
        cwd=cwd,
        env=environment,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        close_fds=True,
        preexec_fn=establish_controlling_terminal,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)
            elif process.poll() is not None:
                break
        else:
            process.kill()
            process.wait()
            raise subprocess.TimeoutExpired(arguments, timeout, output=bytes(output))
        returncode = process.wait(timeout=1)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait()
    combined = output.decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(arguments, returncode, combined, combined)


def next_patch(version, increment=1):
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + increment}"


class ReleaseFixture:
    def __init__(
        self, root, version=None, executable=False, failure_phase=None, *,
        source_commit=None, source_tree=None, qualification_source_commit=None,
        identity="current", pre_archive_policy=False, pre_reactivation_archive_policy=False,
        release_archive_bridge=False, reactivation_archive_bridge=False,
    ):
        self.root = Path(root).resolve()
        self.executable = executable
        self.failure_phase = failure_phase
        self.identity = identity
        self.current_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
        self.version = version or self.current_version
        self.source_commit = source_commit or subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        self.source_tree = source_tree or subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True
        ).strip()
        self.qualification_source_commit = qualification_source_commit or self.source_commit
        manifest = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        manifest["pixel"] = self.version
        # Normalize the synthetic baseline to the full current policy even while the
        # repository itself is the one version-bound bridge release.
        manifest["releaseUpdate"]["reactivationArchiveReceiptSchema"] = (
            "./schemas/release-update-reactivation-archive-v1.schema.json"
        )
        manifest["releaseUpdate"]["reactivationArchive"] = (
            "exact-terminal-no-live-mutation-reactivation-preservation-outside-bounded-staging"
        )
        manifest.pop("releaseReactivationArchive", None)
        manifest["releaseUpdate"]["qualificationMode"] = (
            "bootstrap" if self.version in {"4.0.0", "4.1.0"} else "forward"
        )
        if sum((
            pre_archive_policy, pre_reactivation_archive_policy,
            release_archive_bridge, reactivation_archive_bridge,
        )) > 1:
            raise ValueError("release fixture policy modes are mutually exclusive")
        if release_archive_bridge and self.version != "4.3.23":
            raise ValueError("release archive bridge is fixed to Pixel 4.3.23")
        if reactivation_archive_bridge and self.version != "4.3.27":
            raise ValueError("release reactivation archive bridge is fixed to Pixel 4.3.27")
        if pre_archive_policy:
            manifest["releaseUpdate"].pop("reactivationArchiveReceiptSchema", None)
            manifest["releaseUpdate"].pop("reactivationArchive", None)
            manifest["releaseUpdate"].pop("archiveReceiptSchema", None)
            manifest["releaseUpdate"].pop("archive", None)
            manifest.pop("releaseArchive", None)
        elif pre_reactivation_archive_policy:
            manifest["releaseUpdate"].pop("reactivationArchiveReceiptSchema", None)
            manifest["releaseUpdate"].pop("reactivationArchive", None)
        elif release_archive_bridge:
            manifest["releaseUpdate"].pop("reactivationArchiveReceiptSchema", None)
            manifest["releaseUpdate"].pop("reactivationArchive", None)
            manifest["releaseUpdate"].pop("archiveReceiptSchema", None)
            manifest["releaseUpdate"].pop("archive", None)
            manifest["releaseArchive"] = {
                "schemaVersion": 1,
                "receiptSchema": "./schemas/release-update-archive-v1.schema.json",
                "operation": "exact-failed-rollback-preservation-outside-bounded-staging",
                "boundary": "terminal-failed-rollback-evidence-only-no-active-deployment-change",
            }
        elif reactivation_archive_bridge:
            manifest["releaseUpdate"].pop("reactivationArchiveReceiptSchema", None)
            manifest["releaseUpdate"].pop("reactivationArchive", None)
            manifest["releaseReactivationArchive"] = {
                "schemaVersion": 1,
                "receiptSchema": "./schemas/release-update-reactivation-archive-v1.schema.json",
                "operation": "exact-terminal-no-live-mutation-reactivation-preservation-outside-bounded-staging",
                "boundary": "terminal-no-live-mutation-reactivation-evidence-only-no-active-deployment-change",
            }
        self.minimum_upgradable_pixel = manifest["releaseUpdate"]["minimumUpgradablePixel"]
        self.manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        compatibility = json.loads((ROOT / "OPENCLAW-COMPATIBILITY.json").read_text(encoding="utf-8"))
        current = next(item for item in compatibility["combinations"] if item["pixel"] == self.current_version)
        current["pixel"] = self.version
        current["status"] = "supported"
        current["evidence"]["sourceCommit"] = self.qualification_source_commit
        current["evidence"]["liveAudit"] = f"LIVE-AUDIT-{self.version}.md"
        self.compatibility = (json.dumps(compatibility, indent=2) + "\n").encode("utf-8")
        self.archive_path = self.root / f"pixel-{self.version}.tar.gz"
        self.sbom_path = self.root / f"pixel-{self.version}.cdx.json"
        self.provenance_path = self.root / f"pixel-{self.version}.intoto.jsonl"
        self.envelope_path = self.root / f"pixel-{self.version}.update.json"
        self.key_path = self.root / "release-key"
        self.allowed_signers = self.root / "allowed-signers"
        self._write_bundle()
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.key_path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        public_key = Path(f"{self.key_path}.pub").read_text(encoding="ascii").strip()
        self.allowed_signers.write_text(f"pixel-release {public_key}\n", encoding="ascii")
        self.allowed_signers.chmod(0o600)

    def _archive(self, sbom):
        members = {
            "VERSION": f"{self.version}\n".encode("ascii"),
            "RELEASE-MANIFEST.json": self.manifest,
            "OPENCLAW-COMPATIBILITY.json": self.compatibility,
            "OPENCLAW-COMPATIBILITY.md": b"Synthetic generated compatibility evidence.\n",
            f"LIVE-AUDIT-{self.version}.md": b"Synthetic versioned live-audit evidence.\n",
            "SBOM.cdx.json": sbom,
            "README.txt": b"Synthetic release fixture; never executed.\n",
            "scripts/safe.sh": b"#!/usr/bin/env bash\nset -euo pipefail\nexit 91\n",
            "scripts/safe.mjs": b"throw new Error('candidate code must not execute');\n",
            "scripts/safe.py": b"raise RuntimeError('candidate code must not execute')\n",
        }
        if self.executable:
            configure_failure = "process.exit(41);\n" if self.failure_phase == "configure" else ""
            bootstrap_failure = b"exit 44\n" if self.failure_phase == "bootstrap" else b""
            plan_failure = b"exit 42\n" if self.failure_phase == "plan" else b""
            apply_failure = "exit 43\n" if self.failure_phase == "apply" else ""
            members.update({
                "scripts/configure.mjs": (
                    "import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';\n"
                    "import { dirname, resolve } from 'node:path';\n"
                    "import { fileURLToPath } from 'node:url';\n"
                    "const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');\n"
                    "if (!existsSync(resolve(root, '..', 'ACTIVATION.json')) && !existsSync(resolve(root, '..', 'REACTIVATION.json'))) process.exit(92);\n"
                    "const index = process.argv.indexOf('--answers');\n"
                    "readFileSync(process.argv[index + 1]);\n"
                    "const generated = resolve(root, '.generated');\n"
                    "mkdirSync(generated, { recursive: true, mode: 0o700 });\n"
                    "const deployment = resolve(generated, 'deployment.json');\n"
                    "if (!existsSync(deployment)) writeFileSync(deployment, JSON.stringify({ generatedAt: '2026-08-09T00:00:00.000Z', fixture: true }) + '\\n', { mode: 0o600 });\n"
                    "appendFileSync(process.env.PIXEL_ACTIVATION_TRACE, 'configure\\n');\n"
                    f"{configure_failure}"
                ).encode("utf-8"),
                "scripts/bootstrap.sh": b"#!/usr/bin/env bash\nset -euo pipefail\nprintf 'bootstrap\\n' >> \"$PIXEL_ACTIVATION_TRACE\"\n" + bootstrap_failure,
                "scripts/plan.sh": b"#!/usr/bin/env bash\nset -euo pipefail\nprintf 'plan\\n' >> \"$PIXEL_ACTIVATION_TRACE\"\n" + plan_failure,
                "scripts/apply.sh": (
                    "#!/usr/bin/env bash\nset -euo pipefail\n"
                    "fixture_root=$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/..\" && pwd)\n"
                    "[[ ${1:-} == --confirm ]]\n"
                    "printf 'apply\\n' >> \"$PIXEL_ACTIVATION_TRACE\"\n"
                    f"{apply_failure}"
                    "if [[ -n ${PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER:-} ]]; then "
                    "( set -o noclobber; umask 077; printf 'pixel-release-live-mutation-started-v1\\n' > \"$PIXEL_RELEASE_UPDATE_LIVE_MUTATION_MARKER\" ); fi\n"
                    f"release=\"$PIXEL_INSTALL_DIR/releases/{self.version}\"\n"
                    "backup=\"$OPENCLAW_HOME/backups/fixture-update\"\n"
                    "mkdir -p \"$release\" \"$backup\"\n"
                    "chmod 700 \"$release\" \"$backup\"\n"
                    "(cd \"$fixture_root\" && sha256sum .generated/deployment.json) > \"$release/deployment-inputs.sha256\"\n"
                    "(cd \"$release\" && sha256sum ./deployment-inputs.sha256) > \"$release/install-manifest.sha256\"\n"
                    "chmod 600 \"$release/deployment-inputs.sha256\" \"$release/install-manifest.sha256\"\n"
                    "readlink \"$PIXEL_INSTALL_DIR/current\" > \"$backup/previous-release\"\n"
                    f"printf '%s\\n' '{self.version}' > \"$release/VERSION\"\n"
                    "ln -s \"$release\" \"$PIXEL_INSTALL_DIR/.current-new\"\n"
                    "mv -Tf \"$PIXEL_INSTALL_DIR/.current-new\" \"$PIXEL_INSTALL_DIR/current\"\n"
                    "printf '%s\\n' \"$backup\" > \"$OPENCLAW_HOME/backups/last-apply\"\n"
                ).encode("utf-8"),
            })
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for relative, payload in members.items():
                info = tarfile.TarInfo(f"pixel-{self.version}/{relative}")
                info.mode = 0o644
                info.mtime = 1
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        return buffer.getvalue()

    def _write_bundle(self):
        sbom = encoded({
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "application",
                    "name": "Pixel",
                    "version": self.version,
                    "properties": [
                        {"name": "pixel:source-commit", "value": self.source_commit},
                        {"name": "pixel:source-tree", "value": self.source_tree},
                        {"name": "pixel:release-manifest-sha256", "value": digest(self.manifest)},
                    ],
                }
            },
        })
        archive = self._archive(sbom)
        if self.identity == "legacy":
            build_type = "https://github.com/Osmantic/Pixel-Personal-Helper/blob/main/scripts/package-release.sh"
            source_uri = f"git+https://github.com/Osmantic/Pixel-Personal-Helper@{self.source_commit}"
        else:
            build_type = "https://github.com/Osmantic/Pixel/blob/main/scripts/package-release.sh"
            source_uri = f"git+https://github.com/Osmantic/Pixel@{self.source_commit}"
        provenance = encoded({
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {"name": self.archive_path.name, "digest": {"sha256": digest(archive)}},
                {"name": self.sbom_path.name, "digest": {"sha256": digest(sbom)}},
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {
                "buildDefinition": {
                    "buildType": build_type,
                    "externalParameters": {"version": self.version},
                    "internalParameters": {},
                    "resolvedDependencies": [
                        {
                            "uri": source_uri,
                            "digest": {"gitCommit": self.source_commit, "gitTree": self.source_tree},
                        },
                        {"uri": "RELEASE-MANIFEST.json", "digest": {"sha256": digest(self.manifest)}},
                        {"uri": "OPENCLAW-COMPATIBILITY.json", "digest": {"sha256": digest(self.compatibility)}},
                    ],
                }
            },
        })
        envelope = {
            "schemaVersion": 1,
            "operation": "pixel-release-update",
            "product": "Pixel",
            "version": self.version,
            "channel": "stable",
            "minimumUpgradablePixel": self.minimum_upgradable_pixel,
            "sourceCommit": self.source_commit,
            "sourceTree": self.source_tree,
            "qualificationSourceCommit": self.qualification_source_commit,
            "supportedHosts": ["Ubuntu 24.04 LTS", "Debian 12"],
            "releaseManifestSha256": digest(self.manifest),
            "compatibilitySha256": digest(self.compatibility),
            "artifacts": {
                "archive": {"name": self.archive_path.name, "sha256": digest(archive), "bytes": len(archive)},
                "sbom": {"name": self.sbom_path.name, "sha256": digest(sbom), "bytes": len(sbom)},
                "provenance": {
                    "name": self.provenance_path.name,
                    "sha256": digest(provenance),
                    "bytes": len(provenance),
                },
            },
            "boundary": "Signed release metadata for verification and preparation only; activation requires a separate exact confirmation.",
        }
        self.archive_path.write_bytes(archive)
        self.sbom_path.write_bytes(sbom)
        self.provenance_path.write_bytes(provenance)
        self.envelope_path.write_bytes(encoded(envelope))

    @property
    def signature_path(self):
        return Path(f"{self.envelope_path}.sig")

    @property
    def qualification_signature_path(self):
        return Path(f"{self.envelope_path}.qualification.sig")

    def set_compatibility_status(self, status):
        compatibility = json.loads(self.compatibility.decode("utf-8"))
        matches = [item for item in compatibility["combinations"] if item["pixel"] == self.version]
        if len(matches) != 1:
            raise AssertionError("fixture compatibility record is ambiguous")
        matches[0]["status"] = status
        self.compatibility = (json.dumps(compatibility, indent=2) + "\n").encode("utf-8")
        self._write_bundle()

    def sign_arguments(self, confirm=True):
        return argparse.Namespace(
            envelope=self.envelope_path,
            signing_key=self.key_path,
            confirm=confirm,
        )

    def inspect_arguments(self, identity="pixel-release"):
        return argparse.Namespace(
            envelope=self.envelope_path,
            allowed_signers=self.allowed_signers,
            identity=identity,
        )


class ReleaseSigningSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("git") is None or shutil.which("ssh-keygen") is None:
            raise unittest.SkipTest("Git and OpenSSH signing support are required")
        cls.release_update = load_release_update()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repo"
        self.repository.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Pixel test")
        self.git("config", "user.email", "pixel-test@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.manifest = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        self.version = self.manifest["pixel"]
        compatibility = {
            "$schema": "./schemas/openclaw-compatibility-v1.schema.json",
            "schemaVersion": 1,
            "combinations": [{
                "pixel": self.version,
                "openclaw": self.manifest["openclaw"],
                "plugins": self.manifest["openclawPlugins"],
                "status": "candidate",
                "qualifiedAt": "2026-08-09",
                "evidence": {"sourceCommit": "0" * 40, "liveAudit": f"LIVE-AUDIT-{self.version}.md"},
            }],
        }
        (self.repository / "RELEASE-MANIFEST.json").write_text(
            json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8",
        )
        (self.repository / "OPENCLAW-COMPATIBILITY.json").write_text(
            json.dumps(compatibility, indent=2) + "\n", encoding="utf-8",
        )
        (self.repository / "OPENCLAW-COMPATIBILITY.md").write_text("Qualified source placeholder.\n", encoding="utf-8")
        (self.repository / f"LIVE-AUDIT-{self.version}.md").write_text("Qualification pending.\n", encoding="utf-8")
        (self.repository / "docs" / "releases").mkdir(parents=True)
        (self.repository / "docs" / "status.json").write_text('{"status":"qualification-pending"}\n', encoding="utf-8")
        (self.repository / "docs" / "status.md").write_text("Qualification pending.\n", encoding="utf-8")
        (self.repository / "docs" / "releases" / "evidence-index.md").write_text("Qualification pending.\n", encoding="utf-8")
        (self.repository / "QUALIFICATION-MATRIX.json").write_text("{}\n", encoding="utf-8")
        (self.repository / "VERSION").write_text(f"{self.version}\n", encoding="ascii")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "qualified functional source")
        self.qualification = self.git("rev-parse", "HEAD")
        compatibility["combinations"][0]["status"] = "supported"
        compatibility["combinations"][0]["evidence"]["sourceCommit"] = self.qualification
        (self.repository / "OPENCLAW-COMPATIBILITY.json").write_text(
            json.dumps(compatibility, indent=2) + "\n", encoding="utf-8",
        )
        (self.repository / "OPENCLAW-COMPATIBILITY.md").write_text("Supported release evidence.\n", encoding="utf-8")
        (self.repository / f"LIVE-AUDIT-{self.version}.md").write_text("Versioned live-audit evidence.\n", encoding="utf-8")
        (self.repository / "docs" / "status.json").write_text('{"status":"supported"}\n', encoding="utf-8")
        (self.repository / "docs" / "status.md").write_text("Supported release evidence.\n", encoding="utf-8")
        (self.repository / "docs" / "releases" / "evidence-index.md").write_text("Supported release evidence.\n", encoding="utf-8")
        self.git(
            "add", f"LIVE-AUDIT-{self.version}.md", "OPENCLAW-COMPATIBILITY.json", "OPENCLAW-COMPATIBILITY.md",
            "docs/status.json", "docs/status.md", "docs/releases/evidence-index.md",
        )
        self.git("commit", "-q", "-m", "bind release evidence")
        self.envelope = {
            "version": self.version,
            "sourceCommit": self.git("rev-parse", "HEAD"),
            "sourceTree": self.git("rev-parse", "HEAD^{tree}"),
            "qualificationSourceCommit": self.qualification,
            "releaseManifestSha256": digest((self.repository / "RELEASE-MANIFEST.json").read_bytes()),
            "compatibilitySha256": digest((self.repository / "OPENCLAW-COMPATIBILITY.json").read_bytes()),
        }

    def git(self, *arguments, input_text=None):
        return subprocess.check_output(
            ["git", *arguments], cwd=self.repository, text=True, input=input_text,
        ).strip()

    def validate(self, envelope=None):
        with mock.patch.object(self.release_update, "ROOT", self.repository):
            self.release_update.validate_signing_source(envelope or self.envelope)

    def source_archive(self, sbom=b'{"bomFormat":"CycloneDX"}\n', *, overrides=None, omit=None, mode_overrides=None):
        overrides = overrides or {}
        omit = omit or set()
        mode_overrides = mode_overrides or {}
        files = {
            relative: (self.repository / relative).read_bytes()
            for relative in self.git("ls-files").splitlines()
            if relative not in omit
        }
        compatibility = json.loads((self.repository / "OPENCLAW-COMPATIBILITY.json").read_text(encoding="utf-8"))
        record = compatibility["combinations"][0]
        release_identity = {
            "schemaVersion": 1,
            "kind": "pixel-release-source-identity",
            "pixel": self.version,
            "source": {
                "state": "git-clean",
                "commit": self.envelope["sourceCommit"],
                "tree": self.envelope["sourceTree"],
            },
            "manifests": {
                "releaseSha256": digest((self.repository / "RELEASE-MANIFEST.json").read_bytes()),
                "compatibilitySha256": digest((self.repository / "OPENCLAW-COMPATIBILITY.json").read_bytes()),
                "qualificationMatrixSha256": digest((self.repository / "QUALIFICATION-MATRIX.json").read_bytes()),
            },
            "qualification": {
                "recordStatus": record["status"],
                "sourceCommit": record["evidence"]["sourceCommit"],
                "qualifiedAt": record["qualifiedAt"],
                "liveAudit": record["evidence"]["liveAudit"],
                "relationship": "qualified-ancestor",
            },
            "boundary": self.release_update.RELEASE_IDENTITY_BOUNDARY,
        }
        if "RELEASE-IDENTITY.json" not in omit:
            files["RELEASE-IDENTITY.json"] = json.dumps(release_identity, indent=2).encode("utf-8") + b"\n"
        files.update(overrides)
        files["SBOM.cdx.json"] = sbom
        prefix = f"pixel-{self.version}"
        directories = {prefix}
        for relative in files:
            parts = PurePosixPath(relative).parts[:-1]
            for index in range(1, len(parts) + 1):
                directories.add(f"{prefix}/{'/'.join(parts[:index])}")
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            for directory in sorted(directories):
                info = tarfile.TarInfo(directory)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            for relative, payload in sorted(files.items()):
                info = tarfile.TarInfo(f"{prefix}/{relative}")
                info.mode = mode_overrides.get(relative, self.release_update.normalized_release_mode(relative))
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        return output.getvalue(), sbom

    def exact_bundle(self):
        bundle = self.root / "bundle"
        bundle.mkdir()
        version = self.version
        archive_path = bundle / f"pixel-{version}.tar.gz"
        sbom_path = bundle / f"pixel-{version}.cdx.json"
        provenance_path = bundle / f"pixel-{version}.intoto.jsonl"
        envelope_path = bundle / f"pixel-{version}.update.json"
        key_path = bundle / "release-key"
        allowed_signers = bundle / "allowed-signers"
        manifest = (self.repository / "RELEASE-MANIFEST.json").read_bytes()
        compatibility = (self.repository / "OPENCLAW-COMPATIBILITY.json").read_bytes()
        sbom = encoded({
            "bomFormat": "CycloneDX",
            "specVersion": "1.6",
            "version": 1,
            "metadata": {"component": {
                "type": "application", "name": "Pixel", "version": version,
                "properties": [
                    {"name": "pixel:source-commit", "value": self.envelope["sourceCommit"]},
                    {"name": "pixel:source-tree", "value": self.envelope["sourceTree"]},
                    {"name": "pixel:release-manifest-sha256", "value": digest(manifest)},
                ],
            }},
        })
        archive, _ = self.source_archive(sbom)
        provenance = encoded({
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [
                {"name": archive_path.name, "digest": {"sha256": digest(archive)}},
                {"name": sbom_path.name, "digest": {"sha256": digest(sbom)}},
            ],
            "predicateType": "https://slsa.dev/provenance/v1",
            "predicate": {"buildDefinition": {
                "buildType": "https://github.com/Osmantic/Pixel/blob/main/scripts/package-release.sh",
                "externalParameters": {"version": version},
                "internalParameters": {},
                "resolvedDependencies": [
                    {
                        "uri": f"git+https://github.com/Osmantic/Pixel@{self.envelope['sourceCommit']}",
                        "digest": {"gitCommit": self.envelope["sourceCommit"], "gitTree": self.envelope["sourceTree"]},
                    },
                    {"uri": "RELEASE-MANIFEST.json", "digest": {"sha256": digest(manifest)}},
                    {"uri": "OPENCLAW-COMPATIBILITY.json", "digest": {"sha256": digest(compatibility)}},
                ],
            }},
        })
        envelope = {
            "schemaVersion": 1,
            "operation": "pixel-release-update",
            "product": "Pixel",
            "version": version,
            "channel": "stable",
            "minimumUpgradablePixel": self.manifest["releaseUpdate"]["minimumUpgradablePixel"],
            **self.envelope,
            "supportedHosts": self.manifest["supportedHosts"],
            "releaseManifestSha256": digest(manifest),
            "compatibilitySha256": digest(compatibility),
            "artifacts": {
                "archive": {"name": archive_path.name, "sha256": digest(archive), "bytes": len(archive)},
                "sbom": {"name": sbom_path.name, "sha256": digest(sbom), "bytes": len(sbom)},
                "provenance": {"name": provenance_path.name, "sha256": digest(provenance), "bytes": len(provenance)},
            },
            "boundary": "Signed release metadata for verification and preparation only; activation requires a separate exact confirmation.",
        }
        archive_path.write_bytes(archive)
        sbom_path.write_bytes(sbom)
        provenance_path.write_bytes(provenance)
        envelope_path.write_bytes(encoded(envelope))
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        public_key = Path(f"{key_path}.pub").read_text(encoding="ascii").strip()
        allowed_signers.write_text(f"pixel-release {public_key}\n", encoding="ascii")
        allowed_signers.chmod(0o600)
        return envelope_path, key_path, allowed_signers

    def test_evidence_only_descendant_is_accepted_and_signing_uses_the_proof(self):
        self.validate()
        envelope_path, key_path, allowed_signers = self.exact_bundle()
        with mock.patch.object(self.release_update, "ROOT", self.repository):
            receipt = self.release_update.sign(argparse.Namespace(
                envelope=envelope_path, signing_key=key_path, confirm=True,
            ))
            inspected = self.release_update.inspect(argparse.Namespace(
                envelope=envelope_path, allowed_signers=allowed_signers, identity="pixel-release",
            ))
        self.assertEqual(receipt["sourceCommit"], self.envelope["sourceCommit"])
        self.assertEqual(receipt["qualificationSourceCommit"], self.qualification)
        self.assertEqual(inspected["sourceCommit"], self.envelope["sourceCommit"])
        self.assertEqual(inspected["qualificationSourceCommit"], self.qualification)

    def test_generated_support_matrix_may_accompany_required_evidence(self):
        reference = self.repository / "docs" / "reference"
        reference.mkdir()
        (reference / "support-matrix.md").write_text(
            "Generated supported compatibility summary.\n", encoding="utf-8",
        )
        self.git("add", "docs/reference/support-matrix.md")
        self.git("commit", "-q", "-m", "update generated support summary")
        envelope = {
            **self.envelope,
            "sourceCommit": self.git("rev-parse", "HEAD"),
            "sourceTree": self.git("rev-parse", "HEAD^{tree}"),
        }
        self.validate(envelope)

    def test_generated_support_matrix_must_be_an_ordinary_file(self):
        reference = self.repository / "docs" / "reference"
        reference.mkdir()
        (reference / "support-matrix.md").write_text(
            "Generated supported compatibility summary.\n", encoding="utf-8",
        )
        self.git("add", "docs/reference/support-matrix.md")
        self.git("update-index", "--chmod=+x", "docs/reference/support-matrix.md")
        (reference / "support-matrix.md").chmod(0o755)
        self.git("commit", "-q", "-m", "make generated support summary executable")
        envelope = {
            **self.envelope,
            "sourceCommit": self.git("rev-parse", "HEAD"),
            "sourceTree": self.git("rev-parse", "HEAD^{tree}"),
        }
        with self.assertRaisesRegex(self.release_update.UpdateError, "ordinary tracked file"):
            self.validate(envelope)

    def test_archive_must_exactly_match_git_source_and_packaging_modes(self):
        archive, sbom = self.source_archive()
        with mock.patch.object(self.release_update, "ROOT", self.repository):
            self.release_update.validate_signing_archive(self.envelope, archive, sbom)

            changed, _ = self.source_archive(sbom, overrides={"VERSION": b"tampered\n"})
            with self.assertRaisesRegex(self.release_update.UpdateError, "content differs"):
                self.release_update.validate_signing_archive(self.envelope, changed, sbom)

            extra, _ = self.source_archive(sbom, overrides={"untracked.txt": b"extra\n"})
            with self.assertRaisesRegex(self.release_update.UpdateError, "file set differs"):
                self.release_update.validate_signing_archive(self.envelope, extra, sbom)

            missing, _ = self.source_archive(sbom, omit={"VERSION"})
            with self.assertRaisesRegex(self.release_update.UpdateError, "file set differs"):
                self.release_update.validate_signing_archive(self.envelope, missing, sbom)

            missing_identity, _ = self.source_archive(sbom, omit={"RELEASE-IDENTITY.json"})
            with self.assertRaisesRegex(self.release_update.UpdateError, "file set differs"):
                self.release_update.validate_signing_archive(self.envelope, missing_identity, sbom)

            wrong_mode, _ = self.source_archive(sbom, mode_overrides={"VERSION": 0o755})
            with self.assertRaisesRegex(self.release_update.UpdateError, "file mode differs"):
                self.release_update.validate_signing_archive(self.envelope, wrong_mode, sbom)

            wrong_identity, _ = self.source_archive(
                sbom, overrides={"RELEASE-IDENTITY.json": b'{"schemaVersion":1}\n'},
            )
            with self.assertRaisesRegex(self.release_update.UpdateError, "identity differs"):
                self.release_update.validate_signing_archive(self.envelope, wrong_identity, sbom)

    def test_functional_change_after_qualification_is_rejected(self):
        scripts = self.repository / "scripts"
        scripts.mkdir()
        (scripts / "runtime.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        self.git("add", "scripts/runtime.py")
        self.git("commit", "-q", "-m", "unqualified functional change")
        envelope = {
            **self.envelope,
            "sourceCommit": self.git("rev-parse", "HEAD"),
            "sourceTree": self.git("rev-parse", "HEAD^{tree}"),
        }
        with self.assertRaisesRegex(self.release_update.UpdateError, "non-evidence files"):
            self.validate(envelope)

    def test_unlisted_documentation_change_after_qualification_is_rejected(self):
        (self.repository / "docs" / "operator-runbook.md").write_text(
            "Unqualified operator procedure.\n", encoding="utf-8",
        )
        self.git("add", "docs/operator-runbook.md")
        self.git("commit", "-q", "-m", "unqualified documentation change")
        envelope = {
            **self.envelope,
            "sourceCommit": self.git("rev-parse", "HEAD"),
            "sourceTree": self.git("rev-parse", "HEAD^{tree}"),
        }
        with self.assertRaisesRegex(self.release_update.UpdateError, "non-evidence files"):
            self.validate(envelope)

    def test_incomplete_evidence_set_is_rejected(self):
        self.git("checkout", "-q", "-b", "partial-evidence", self.qualification)
        (self.repository / "OPENCLAW-COMPATIBILITY.json").write_text("partial evidence\n", encoding="utf-8")
        self.git("add", "OPENCLAW-COMPATIBILITY.json")
        self.git("commit", "-q", "-m", "partial evidence")
        envelope = {
            **self.envelope,
            "sourceCommit": self.git("rev-parse", "HEAD"),
            "sourceTree": self.git("rev-parse", "HEAD^{tree}"),
        }
        with self.assertRaisesRegex(self.release_update.UpdateError, "update every required evidence file"):
            self.validate(envelope)

    def test_deleted_evidence_files_are_rejected(self):
        self.git("checkout", "-q", "-b", "deleted-evidence", self.qualification)
        self.git(
            "rm", "-q", f"LIVE-AUDIT-{self.version}.md",
            "OPENCLAW-COMPATIBILITY.json", "OPENCLAW-COMPATIBILITY.md",
            "docs/status.json", "docs/status.md", "docs/releases/evidence-index.md",
        )
        self.git("commit", "-q", "-m", "delete evidence")
        envelope = {
            **self.envelope,
            "sourceCommit": self.git("rev-parse", "HEAD"),
            "sourceTree": self.git("rev-parse", "HEAD^{tree}"),
        }
        with self.assertRaisesRegex(self.release_update.UpdateError, "ordinary tracked file"):
            self.validate(envelope)

    def test_unknown_equal_nonancestor_and_dirty_sources_are_rejected(self):
        unknown = {**self.envelope, "qualificationSourceCommit": "f" * 40}
        with self.assertRaisesRegex(self.release_update.UpdateError, "unknown"):
            self.validate(unknown)
        equal = {**self.envelope, "qualificationSourceCommit": self.envelope["sourceCommit"]}
        with self.assertRaisesRegex(self.release_update.UpdateError, "must precede"):
            self.validate(equal)
        unrelated = self.git("commit-tree", self.envelope["sourceTree"], "-m", "unrelated")
        with self.assertRaisesRegex(self.release_update.UpdateError, "not an ancestor"):
            self.validate({**self.envelope, "qualificationSourceCommit": unrelated})
        (self.repository / "VERSION").write_text("dirty\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "clean Pixel worktree"):
            self.validate()

    def test_head_and_tree_mismatch_are_rejected(self):
        with self.assertRaisesRegex(self.release_update.UpdateError, "not the signing repository HEAD"):
            self.validate({**self.envelope, "sourceCommit": self.qualification})
        with self.assertRaisesRegex(self.release_update.UpdateError, "HEAD tree"):
            self.validate({**self.envelope, "sourceTree": "e" * 40})


class ReleaseUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("ssh-keygen") is None:
            raise unittest.SkipTest("OpenSSH signing support is unavailable")
        cls.release_update = load_release_update()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ReleaseFixture(self.temporary.name)
        self.signing_source = mock.patch.object(self.release_update, "validate_signing_source", autospec=True)
        self.signing_source_mock = self.signing_source.start()
        self.addCleanup(self.signing_source.stop)
        self.signing_archive = mock.patch.object(self.release_update, "validate_signing_archive", autospec=True)
        self.signing_archive_mock = self.signing_archive.start()
        self.addCleanup(self.signing_archive.stop)

    def test_valid_bundle_signs_and_inspects_without_candidate_execution(self):
        signed = self.release_update.sign(self.fixture.sign_arguments())
        inspected = self.release_update.inspect(self.fixture.inspect_arguments())
        self.assertEqual(signed["status"], "signed")
        self.assertEqual(inspected["status"], "verified")
        self.assertEqual(inspected["relation"], "same")
        self.assertFalse(inspected["upgradeEligible"])
        self.assertFalse(inspected["candidateCodeExecuted"])
        self.assertEqual(inspected["qualificationSourceCommit"], self.fixture.qualification_source_commit)
        self.assertEqual(inspected["activationAuthority"], "external-exact-confirmation-only")
        self.signing_source_mock.assert_called_once()
        self.signing_archive_mock.assert_called_once()
        self.assertNotIn(str(self.fixture.root), json.dumps(inspected))

    def test_frozen_4_0_bridge_and_later_forward_policy_are_both_strict(self):
        self.assertEqual(self.release_update.qualification_mode_for_release("4.0.0"), "bootstrap")
        self.assertEqual(self.release_update.qualification_mode_for_release("4.1.0"), "bootstrap")
        self.assertEqual(self.release_update.qualification_mode_for_release("4.1.1"), "forward")

        future_root = Path(self.temporary.name) / "forward-policy"
        future_root.mkdir()
        future = ReleaseFixture(future_root, version="4.1.1")
        self.release_update.load_unsigned_bundle(future.envelope_path)

        manifest = json.loads(future.manifest.decode("utf-8"))
        manifest["releaseUpdate"]["qualificationMode"] = "bootstrap"
        future.manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        future._write_bundle()
        with self.assertRaisesRegex(self.release_update.UpdateError, "release update policy is invalid"):
            self.release_update.load_unsigned_bundle(future.envelope_path)

    def test_exact_qualified_4_0_updater_accepts_the_4_1_bridge_policy(self):
        legacy_source = subprocess.check_output(
            [
                "git", "show",
                "69f0cbaaeeaef85e914792cb6db157cc6197474d:scripts/release-update.py",
            ],
            cwd=ROOT,
        )
        legacy_directory = Path(self.temporary.name) / "qualified-4.0-updater"
        legacy_directory.mkdir()
        legacy_script = legacy_directory / "release-update.py"
        legacy_script.write_bytes(legacy_source)
        legacy = load_release_update(legacy_script, "pixel_release_update_qualified_4_0")
        bridge_root = Path(self.temporary.name) / "bridge-release"
        bridge_root.mkdir()
        bridge = ReleaseFixture(
            bridge_root,
            version="4.1.0",
            identity="legacy",
            pre_archive_policy=True,
        )
        legacy.load_unsigned_bundle(bridge.envelope_path)

        manifest = json.loads(bridge.manifest.decode("utf-8"))
        manifest["releaseUpdate"]["qualificationMode"] = "forward"
        bridge.manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        bridge._write_bundle()
        with self.assertRaisesRegex(legacy.UpdateError, "release update policy is invalid"):
            legacy.load_unsigned_bundle(bridge.envelope_path)

    def test_exact_4_3_21_updater_accepts_the_4_3_23_archive_policy_bridge(self):
        legacy_source = subprocess.check_output(
            [
                "git", "show",
                "c63ce5f8f1eabcdbae34d1bf78686a9ee3c6a21d:scripts/release-update.py",
            ],
            cwd=ROOT,
        )
        legacy_directory = Path(self.temporary.name) / "qualified-4.3.21-updater"
        legacy_directory.mkdir()
        legacy_script = legacy_directory / "release-update.py"
        legacy_script.write_bytes(legacy_source)
        legacy = load_release_update(legacy_script, "pixel_release_update_qualified_4_3_21")

        bridge_root = Path(self.temporary.name) / "bridge-release"
        bridge_root.mkdir()
        bridge = ReleaseFixture(
            bridge_root, version="4.3.23", release_archive_bridge=True,
        )
        envelope, _envelope_bytes, _artifacts, manifest = legacy.load_unsigned_bundle(
            bridge.envelope_path,
        )
        self.assertEqual(envelope["version"], "4.3.23")
        self.assertNotIn("archiveReceiptSchema", manifest["releaseUpdate"])
        self.assertNotIn("archive", manifest["releaseUpdate"])
        self.assertEqual(
            manifest["releaseArchive"]["receiptSchema"],
            "./schemas/release-update-archive-v1.schema.json",
        )

    def test_4_3_23_archive_policy_bridge_requires_exact_separate_policy(self):
        bridge_root = Path(self.temporary.name) / "bridge-release-current-controller"
        bridge_root.mkdir()
        bridge = ReleaseFixture(
            bridge_root, version="4.3.23", release_archive_bridge=True,
        )
        self.release_update.load_unsigned_bundle(bridge.envelope_path)
        manifest = json.loads(bridge.manifest.decode("utf-8"))
        manifest["releaseArchive"]["operation"] = "unsafe-broadened-operation"
        bridge.manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        bridge._write_bundle()
        with self.assertRaisesRegex(self.release_update.UpdateError, "archive bridge policy is invalid"):
            self.release_update.load_unsigned_bundle(bridge.envelope_path)

    def test_exact_4_3_24_updater_accepts_the_4_3_27_reactivation_archive_policy_bridge(self):
        legacy_source = subprocess.check_output(
            [
                "git", "show",
                "70f44c90ac40b8409ebc965becc5b085a053e270:scripts/release-update.py",
            ],
            cwd=ROOT,
        )
        legacy_directory = Path(self.temporary.name) / "qualified-4.3.24-updater"
        legacy_directory.mkdir()
        legacy_script = legacy_directory / "release-update.py"
        legacy_script.write_bytes(legacy_source)
        legacy = load_release_update(legacy_script, "pixel_release_update_qualified_4_3_24")

        bridge_root = Path(self.temporary.name) / "reactivation-bridge-release"
        bridge_root.mkdir()
        bridge = ReleaseFixture(
            bridge_root, version="4.3.27", reactivation_archive_bridge=True,
        )
        envelope, _envelope_bytes, _artifacts, manifest = legacy.load_unsigned_bundle(
            bridge.envelope_path,
        )
        self.assertEqual(envelope["version"], "4.3.27")
        self.assertIn("archiveReceiptSchema", manifest["releaseUpdate"])
        self.assertIn("archive", manifest["releaseUpdate"])
        self.assertNotIn("reactivationArchiveReceiptSchema", manifest["releaseUpdate"])
        self.assertNotIn("reactivationArchive", manifest["releaseUpdate"])

    def test_4_3_27_reactivation_archive_policy_bridge_requires_exact_version_and_marker(self):
        bridge_root = Path(self.temporary.name) / "reactivation-bridge-current-controller"
        bridge_root.mkdir()
        bridge = ReleaseFixture(
            bridge_root, version="4.3.27", reactivation_archive_bridge=True,
        )
        self.release_update.load_unsigned_bundle(bridge.envelope_path)

        manifest = json.loads(bridge.manifest.decode("utf-8"))
        manifest["releaseReactivationArchive"]["operation"] = "unsafe-broadened-operation"
        bridge.manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        bridge._write_bundle()
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "reactivation archive bridge policy is invalid",
        ):
            self.release_update.load_unsigned_bundle(bridge.envelope_path)

        future_root = Path(self.temporary.name) / "non-bridge-future-release"
        future_root.mkdir()
        future = ReleaseFixture(
            future_root, version="4.3.28", pre_reactivation_archive_policy=True,
        )
        future_manifest = json.loads(future.manifest.decode("utf-8"))
        future_manifest["releaseReactivationArchive"] = self.release_update.REACTIVATION_ARCHIVE_POLICY_BRIDGE
        future.manifest = (json.dumps(future_manifest, indent=2) + "\n").encode("utf-8")
        future._write_bundle()
        with self.assertRaisesRegex(self.release_update.UpdateError, "release update policy is invalid"):
            self.release_update.load_unsigned_bundle(future.envelope_path)

    def test_4_3_27_reactivation_archive_policy_bridge_rejects_missing_marker_and_additive_policy(self):
        bridge_root = Path(self.temporary.name) / "reactivation-bridge-negative-cases"
        bridge_root.mkdir()
        bridge = ReleaseFixture(
            bridge_root, version="4.3.27", reactivation_archive_bridge=True,
        )
        manifest = json.loads(bridge.manifest.decode("utf-8"))
        manifest.pop("releaseReactivationArchive")
        bridge.manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        bridge._write_bundle()
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "reactivation archive bridge policy is invalid",
        ):
            self.release_update.load_unsigned_bundle(bridge.envelope_path)
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "reactivation archive bridge policy is invalid",
        ):
            self.release_update.load_unsigned_bundle(
                bridge.envelope_path, allow_pre_archive_policy=True,
            )

        manifest["releaseReactivationArchive"] = self.release_update.REACTIVATION_ARCHIVE_POLICY_BRIDGE
        manifest["releaseUpdate"]["unexpectedAuthority"] = "unsafe"
        bridge.manifest = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        bridge._write_bundle()
        with self.assertRaisesRegex(self.release_update.UpdateError, "release update policy is invalid"):
            self.release_update.load_unsigned_bundle(bridge.envelope_path)

    def test_4_3_27_reactivation_archive_policy_bridge_rejects_future_historical_bypass(self):
        future_root = Path(self.temporary.name) / "future-historical-policy-bypass"
        future_root.mkdir()
        future = ReleaseFixture(
            future_root, version="4.3.28", pre_reactivation_archive_policy=True,
        )
        future_manifest = json.loads(future.manifest.decode("utf-8"))
        future_manifest["releaseReactivationArchive"] = (
            self.release_update.REACTIVATION_ARCHIVE_POLICY_BRIDGE
        )
        future.manifest = (json.dumps(future_manifest, indent=2) + "\n").encode("utf-8")
        future._write_bundle()
        with self.assertRaisesRegex(self.release_update.UpdateError, "release update policy is invalid"):
            self.release_update.load_unsigned_bundle(
                future.envelope_path, allow_pre_archive_policy=True,
            )

        pre_archive_root = Path(self.temporary.name) / "future-pre-archive-policy-bypass"
        pre_archive_root.mkdir()
        pre_archive = ReleaseFixture(
            pre_archive_root, version="4.3.28", pre_archive_policy=True,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "release update policy is invalid"):
            self.release_update.load_unsigned_bundle(
                pre_archive.envelope_path, allow_pre_archive_policy=True,
            )

    def test_4_3_27_reactivation_archive_policy_bridge_marker_is_exclusive_to_bridge_shape(self):
        full_root = Path(self.temporary.name) / "full-policy-with-bridge-marker"
        full_root.mkdir()
        full = ReleaseFixture(full_root, version="4.3.27")
        full_manifest = json.loads(full.manifest.decode("utf-8"))
        full_manifest["releaseReactivationArchive"] = (
            self.release_update.REACTIVATION_ARCHIVE_POLICY_BRIDGE
        )
        full.manifest = (json.dumps(full_manifest, indent=2) + "\n").encode("utf-8")
        full._write_bundle()
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "reactivation archive bridge policy is invalid",
        ):
            self.release_update.load_unsigned_bundle(full.envelope_path)

        bridge_root = Path(self.temporary.name) / "bridge-marker-with-additive-key"
        bridge_root.mkdir()
        bridge = ReleaseFixture(
            bridge_root, version="4.3.27", reactivation_archive_bridge=True,
        )
        bridge_manifest = json.loads(bridge.manifest.decode("utf-8"))
        bridge_manifest["releaseReactivationArchive"]["unexpectedAuthority"] = True
        bridge.manifest = (json.dumps(bridge_manifest, indent=2) + "\n").encode("utf-8")
        bridge._write_bundle()
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "reactivation archive bridge policy is invalid",
        ):
            self.release_update.load_unsigned_bundle(
                bridge.envelope_path, allow_pre_archive_policy=True,
            )

    def test_candidate_qualification_signature_is_separate_and_grants_no_update_authority(self):
        self.fixture.set_compatibility_status("candidate")
        with self.assertRaisesRegex(self.release_update.UpdateError, "supported compatibility record"):
            self.release_update.sign(self.fixture.sign_arguments())
        qualified = self.release_update.qualification_sign(self.fixture.sign_arguments())
        self.assertEqual(qualified["status"], "qualification-signed")
        self.assertFalse(qualified["publicationAuthority"])
        self.assertFalse(qualified["activationAuthority"])
        self.assertTrue(self.fixture.qualification_signature_path.is_file())
        self.assertFalse(self.fixture.signature_path.exists())
        inspected = self.release_update.qualification_inspect(self.fixture.inspect_arguments())
        self.assertEqual(inspected["status"], "qualification-verified")
        self.assertFalse(inspected["publicationAuthority"])
        self.assertFalse(inspected["stagingAuthority"])
        self.assertFalse(inspected["activationAuthority"])
        self.assertFalse(inspected["candidateCodeExtracted"])
        self.assertFalse(inspected["candidateCodeExecuted"])
        with self.assertRaisesRegex(self.release_update.UpdateError, "signature is invalid"):
            self.release_update.verify_signature(
                self.fixture.envelope_path.read_bytes(),
                self.fixture.qualification_signature_path.read_bytes(),
                self.fixture.allowed_signers.read_bytes(),
                "pixel-release",
            )
        with self.assertRaisesRegex(self.release_update.UpdateError, "supported compatibility record"):
            self.release_update.inspect(self.fixture.inspect_arguments())

    def test_supported_bundle_cannot_use_qualification_signing_namespace(self):
        with self.assertRaisesRegex(self.release_update.UpdateError, "candidate compatibility record"):
            self.release_update.qualification_sign(self.fixture.sign_arguments())

    def test_sign_requires_exact_confirmation_and_never_overwrites(self):
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.release_update.sign(self.fixture.sign_arguments(confirm=False))
        self.assertFalse(self.fixture.signature_path.exists())
        self.release_update.sign(self.fixture.sign_arguments())
        original = self.fixture.signature_path.read_bytes()
        with self.assertRaisesRegex(self.release_update.UpdateError, "already exists"):
            self.release_update.sign(self.fixture.sign_arguments())
        self.assertEqual(self.fixture.signature_path.read_bytes(), original)

    def test_bundle_rejects_tampered_qualification_source_binding(self):
        envelope = json.loads(self.fixture.envelope_path.read_text(encoding="utf-8"))
        envelope["qualificationSourceCommit"] = "f" * 40
        self.fixture.envelope_path.write_bytes(encoded(envelope))
        with self.assertRaisesRegex(self.release_update.UpdateError, "supported compatibility record"):
            self.release_update.load_unsigned_bundle(self.fixture.envelope_path)

    def test_bundle_rejects_ambiguous_or_misdirected_compatibility_evidence(self):
        compatibility = json.loads(self.fixture.compatibility.decode("utf-8"))
        current = next(item for item in compatibility["combinations"] if item["pixel"] == self.fixture.version)
        compatibility["combinations"].append(json.loads(json.dumps(current)))
        self.fixture.compatibility = (json.dumps(compatibility, indent=2) + "\n").encode("utf-8")
        self.fixture._write_bundle()
        with self.assertRaisesRegex(self.release_update.UpdateError, "record is ambiguous"):
            self.release_update.load_unsigned_bundle(self.fixture.envelope_path)

        compatibility["combinations"].pop()
        current["evidence"]["liveAudit"] = "LIVE-AUDIT.md"
        self.fixture.compatibility = (json.dumps(compatibility, indent=2) + "\n").encode("utf-8")
        self.fixture._write_bundle()
        with self.assertRaisesRegex(self.release_update.UpdateError, "versioned live audit"):
            self.release_update.load_unsigned_bundle(self.fixture.envelope_path)

    @unittest.skipUnless(sys.platform == "linux", "signing-key mode enforcement is qualified only on Linux")
    def test_sign_rejects_an_exposed_source_key(self):
        exposed = self.fixture.root / "exposed-key"
        exposed.write_bytes(self.fixture.key_path.read_bytes())
        if os.name != "nt":
            exposed.chmod(0o644)
        arguments = self.fixture.sign_arguments()
        arguments.signing_key = exposed
        with self.assertRaisesRegex(self.release_update.UpdateError, "permissions are unsafe|group or world accessible|unprotected"):
            self.release_update.sign(arguments)
        self.assertFalse(self.fixture.signature_path.exists())

    def test_inspection_rejects_artifact_signature_and_identity_tampering(self):
        self.release_update.sign(self.fixture.sign_arguments())
        original_archive = self.fixture.archive_path.read_bytes()
        self.fixture.archive_path.write_bytes(original_archive + b"tamper")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the signed envelope"):
            self.release_update.inspect(self.fixture.inspect_arguments())
        self.fixture.archive_path.write_bytes(original_archive)
        with self.assertRaisesRegex(self.release_update.UpdateError, "publisher is not trusted"):
            self.release_update.inspect(self.fixture.inspect_arguments(identity="other-release"))
        signature = self.fixture.signature_path.read_bytes()
        self.fixture.signature_path.write_bytes(signature.replace(b"A", b"B", 1))
        with self.assertRaisesRegex(self.release_update.UpdateError, "signature"):
            self.release_update.inspect(self.fixture.inspect_arguments())

    def test_duplicate_json_and_nonfinite_numbers_are_denied(self):
        with self.assertRaisesRegex(self.release_update.UpdateError, "duplicate JSON field"):
            self.release_update.parse_json(b'{"schemaVersion":1,"schemaVersion":1}', "fixture")
        with self.assertRaisesRegex(self.release_update.UpdateError, "non-finite JSON"):
            self.release_update.parse_json(b'{"bytes":NaN}', "fixture")

    def test_unsafe_archive_paths_and_links_are_denied_without_extraction(self):
        for name, member_type in [
            (f"pixel-{self.fixture.version}/../escape", tarfile.REGTYPE),
            (f"pixel-{self.fixture.version}/link", tarfile.SYMTYPE),
            (f"pixel-{self.fixture.version}/{'x' * 256}", tarfile.REGTYPE),
        ]:
            with self.subTest(name=name):
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                    info = tarfile.TarInfo(name)
                    info.type = member_type
                    info.linkname = "outside"
                    info.size = 0
                    archive.addfile(info)
                with self.assertRaisesRegex(self.release_update.UpdateError, "unsafe|special"):
                    self.release_update.archive_members(buffer.getvalue(), self.fixture.version)
        self.assertEqual(list(self.fixture.root.glob("escape")), [])

    def test_archive_implicit_directory_growth_is_bounded(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            payload = b"bounded\n"
            info = tarfile.TarInfo(f"pixel-{self.fixture.version}/one/two/three/value")
            info.mode = 0o600
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        destination = self.fixture.root / "bounded-extraction"
        destination.mkdir(mode=0o700)
        with mock.patch.object(self.release_update, "MAX_TREE_DIRECTORIES", 2):
            with self.assertRaisesRegex(self.release_update.UpdateError, "too many directories"):
                self.release_update.extract_archive_privately(buffer.getvalue(), self.fixture.version, destination)
        self.assertEqual(list(destination.iterdir()), [])

    def test_signing_uses_the_validated_bytes_even_if_original_path_changes(self):
        original_envelope = self.fixture.envelope_path.read_bytes()
        real_run = self.release_update.subprocess.run
        changed = False

        def mutate_then_run(*arguments, **keywords):
            nonlocal changed
            if not changed:
                changed = True
                self.fixture.envelope_path.write_bytes(b'{"swapped":true}\n')
            return real_run(*arguments, **keywords)

        with mock.patch.object(self.release_update.subprocess, "run", side_effect=mutate_then_run):
            self.release_update.sign(self.fixture.sign_arguments())
        self.fixture.envelope_path.write_bytes(original_envelope)
        inspected = self.release_update.inspect(self.fixture.inspect_arguments())
        self.assertEqual(inspected["status"], "verified")

    def test_wrong_sbom_source_identity_is_denied_before_signature_work(self):
        sbom = json.loads(self.fixture.sbom_path.read_text(encoding="utf-8"))
        properties = sbom["metadata"]["component"]["properties"]
        next(item for item in properties if item["name"] == "pixel:source-tree")["value"] = "0" * 40
        bad_sbom = encoded(sbom)
        archive = self.fixture._archive(bad_sbom)
        self.fixture.archive_path.write_bytes(archive)
        self.fixture.sbom_path.write_bytes(bad_sbom)
        envelope = json.loads(self.fixture.envelope_path.read_text(encoding="utf-8"))
        envelope["artifacts"]["archive"].update(sha256=digest(archive), bytes=len(archive))
        envelope["artifacts"]["sbom"].update(sha256=digest(bad_sbom), bytes=len(bad_sbom))
        provenance = json.loads(self.fixture.provenance_path.read_text(encoding="utf-8"))
        subjects = {item["name"]: item for item in provenance["subject"]}
        subjects[self.fixture.archive_path.name]["digest"]["sha256"] = digest(archive)
        subjects[self.fixture.sbom_path.name]["digest"]["sha256"] = digest(bad_sbom)
        provenance_bytes = encoded(provenance)
        self.fixture.provenance_path.write_bytes(provenance_bytes)
        envelope["artifacts"]["provenance"].update(sha256=digest(provenance_bytes), bytes=len(provenance_bytes))
        self.fixture.envelope_path.write_bytes(encoded(envelope))
        with self.assertRaisesRegex(self.release_update.UpdateError, "SBOM does not match"):
            self.release_update.sign(self.fixture.sign_arguments())

    def test_provenance_rejects_ambiguous_extra_dependencies(self):
        envelope = json.loads(self.fixture.envelope_path.read_text(encoding="utf-8"))
        provenance = json.loads(self.fixture.provenance_path.read_text(encoding="utf-8"))
        provenance["predicate"]["buildDefinition"]["resolvedDependencies"].append({
            "uri": "RELEASE-MANIFEST.json",
            "digest": {"sha256": envelope["releaseManifestSha256"]},
        })
        with self.assertRaisesRegex(self.release_update.UpdateError, "dependencies are invalid"):
            self.release_update.validate_provenance(provenance, envelope)

    @unittest.skipUnless(sys.platform == "linux", "release staging is qualified only on Linux")
    def test_prepare_copies_one_forward_release_privately_without_extraction(self):
        bundle_root = self.fixture.root / "future-bundle"
        bundle_root.mkdir(mode=0o700)
        future = ReleaseFixture(bundle_root, version=next_patch(self.fixture.current_version))
        self.release_update.sign(future.sign_arguments())
        staging_root = self.fixture.root / "update-staging"
        candidates_root = staging_root / "candidates"
        staging_root.mkdir(mode=0o700)
        candidates_root.mkdir(mode=0o700)
        envelope_hash = digest(future.envelope_path.read_bytes())
        incomplete = candidates_root / f".pixel-{future.version}-{envelope_hash}.stage-0123456789abcdef"
        incomplete.mkdir(mode=0o700)
        partial = incomplete / future.envelope_path.name
        partial.write_bytes(b"interrupted")
        partial.chmod(0o600)
        arguments = argparse.Namespace(
            envelope=future.envelope_path,
            allowed_signers=future.allowed_signers,
            identity="pixel-release",
            staging_root=staging_root,
            confirm=True,
        )
        receipt = self.release_update.prepare(arguments)
        self.assertFalse(incomplete.exists())
        self.assertEqual(receipt["status"], "prepared")
        self.assertTrue(receipt["upgradeEligible"])
        self.assertFalse(receipt["candidateCodeExtracted"])
        self.assertFalse(receipt["candidateCodeExecuted"])
        candidate = staging_root / "candidates" / receipt["candidateId"]
        expected_files = {
            "STAGED-UPDATE.json",
            future.archive_path.name,
            future.sbom_path.name,
            future.provenance_path.name,
            future.envelope_path.name,
            future.signature_path.name,
        }
        self.assertEqual({path.name for path in candidate.iterdir()}, expected_files)
        self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o700)
        for path in candidate.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse((candidate / "README.txt").exists())
        repeated = self.release_update.prepare(arguments)
        self.assertEqual(repeated["status"], "already-prepared")
        self.assertEqual(repeated["candidateId"], receipt["candidateId"])
        rehearsal_arguments = argparse.Namespace(
            candidate_id=receipt["candidateId"],
            allowed_signers=future.allowed_signers,
            identity="pixel-release",
            staging_root=staging_root,
            confirm=True,
        )
        rehearsals_root = staging_root / "rehearsals"
        rehearsals_root.mkdir(mode=0o700)
        incomplete_rehearsal = rehearsals_root / f".{receipt['candidateId']}.rehearsal-0123456789abcdef"
        incomplete_source = incomplete_rehearsal / "source"
        incomplete_rehearsal.mkdir(mode=0o700)
        incomplete_source.mkdir(mode=0o700)
        interrupted_file = incomplete_source / "partial"
        interrupted_file.write_text("interrupted\n", encoding="utf-8")
        interrupted_file.chmod(0o600)
        rehearsal = self.release_update.rehearse(rehearsal_arguments)
        self.assertFalse(incomplete_rehearsal.exists())
        self.assertEqual(rehearsal["status"], "rehearsed")
        self.assertTrue(rehearsal["candidateCodeExtracted"])
        self.assertTrue(rehearsal["candidateCodeParsed"])
        self.assertFalse(rehearsal["candidateCodeExecuted"])
        self.assertFalse(rehearsal["activeDeploymentChanged"])
        self.assertFalse(rehearsal["networkUsed"])
        self.assertEqual(rehearsal["checks"]["shellFiles"], 1)
        self.assertEqual(rehearsal["checks"]["javascriptFiles"], 1)
        self.assertEqual(rehearsal["checks"]["pythonFiles"], 1)
        repeated_rehearsal = self.release_update.rehearse(rehearsal_arguments)
        self.assertEqual(repeated_rehearsal["status"], "already-rehearsed")
        rehearsal_source = staging_root / "rehearsals" / receipt["candidateId"] / "source"
        self.assertEqual((rehearsal_source / "README.txt").read_text(encoding="utf-8"), "Synthetic release fixture; never executed.\n")
        activation_arguments = argparse.Namespace(
            candidate_id=receipt["candidateId"],
            allowed_signers=future.allowed_signers,
            identity="pixel-release",
            staging_root=staging_root,
        )
        preview = self.release_update.activation_preview(activation_arguments)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["candidateId"], receipt["candidateId"])
        self.assertTrue(preview["candidateCodeWillExecute"])
        self.assertTrue(preview["privateConfigurationWillBeRead"])
        self.assertTrue(preview["activeDeploymentWillChange"])
        self.assertTrue(preview["networkMayBeUsed"])
        self.assertFalse(preview["candidateCodeExecuted"])
        self.assertFalse(preview["activeDeploymentChanged"])
        self.assertFalse(preview["networkUsed"])
        self.assertNotIn(str(self.fixture.root), json.dumps(preview))
        claim_arguments = argparse.Namespace(
            **vars(activation_arguments), activation_hash="0" * 64, confirm=True,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the current verified preview"):
            self.release_update.claim_activation(claim_arguments)
        self.assertFalse((staging_root / "activations").exists())
        claim_arguments.activation_hash = preview["activationHash"]
        claim_arguments.confirm = False
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.release_update.claim_activation(claim_arguments)
        claim_arguments.confirm = True
        claimed = self.release_update.claim_activation(claim_arguments)
        self.assertEqual(claimed["status"], "claimed")
        self.assertEqual(claimed["activationHash"], preview["activationHash"])
        self.assertTrue(claimed["candidateCodeCopied"])
        self.assertFalse(claimed["candidateCodeExecuted"])
        activation = staging_root / "activations" / receipt["candidateId"]
        self.assertEqual({path.name for path in activation.iterdir()}, {"source", "ACTIVATION.json"})
        self.assertEqual(stat.S_IMODE(activation.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((activation / "ACTIVATION.json").stat().st_mode), 0o600)
        self.assertEqual(
            self.release_update.tree_identity(activation / "source"),
            self.release_update.tree_identity(rehearsal_source),
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.activation_preview(activation_arguments)
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.claim_activation(claim_arguments)
        active_version_file = self.fixture.root / "active-version"
        active_version_file.write_text(f"{future.version}\n", encoding="ascii")
        active_version_file.chmod(0o600)
        rollback_marker = self.fixture.root / "last-apply"
        rollback_marker.write_text("content-free-fixture-marker\n", encoding="ascii")
        rollback_marker.chmod(0o600)
        post_activation_source = rehearsal_source / "scripts" / "safe.py"
        post_activation_source.write_text("def damaged(:\n", encoding="utf-8")
        post_activation_source.chmod(0o600)
        recovery_arguments = argparse.Namespace(
            **vars(activation_arguments), activation_hash=preview["activationHash"],
            active_version_file=active_version_file, rollback_marker=rollback_marker,
        )
        activation_recovery = self.release_update.recovery_preview(recovery_arguments)
        self.assertEqual(activation_recovery["state"], "activation-applied-result-missing")
        self.assertEqual(activation_recovery["safeAction"], "finalize-activation-result")
        self.assertTrue(activation_recovery["confirmationAvailable"])
        self.assertFalse(activation_recovery["candidateCodeWillExecute"])
        self.assertFalse(activation_recovery["networkWillBeUsed"])
        self.assertNotIn(str(self.fixture.root), json.dumps(activation_recovery))
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the current diagnosis"):
            self.release_update.finalize_recovery(argparse.Namespace(
                **vars(recovery_arguments), recovery_hash="0" * 64, confirm=True,
            ))
        activation_result = self.release_update.finalize_recovery(argparse.Namespace(
            **vars(recovery_arguments), recovery_hash=activation_recovery["recoveryHash"], confirm=True,
        ))
        self.assertEqual(activation_result["status"], "activated")
        self.assertEqual(activation_result["activeVersion"], future.version)
        self.assertTrue(activation_result["candidateCodeExecuted"])
        self.assertTrue(activation_result["privateConfigurationRead"])
        self.assertTrue(activation_result["activeDeploymentChanged"])
        self.assertTrue(activation_result["networkMayHaveBeenUsed"])
        self.assertTrue(activation_result["rollbackAvailable"])
        self.assertEqual(activation_result["rollbackMarkerSha256"], digest(rollback_marker.read_bytes()))
        self.assertNotIn(str(self.fixture.root), json.dumps(activation_result))
        result_path = activation / "ACTIVATION-RESULT.json"
        self.assertEqual(stat.S_IMODE(result_path.stat().st_mode), 0o600)
        result_arguments = argparse.Namespace(
            **vars(recovery_arguments), outcome="activated", phase="record", confirm=True,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "already recorded"):
            self.release_update.record_activation_result(result_arguments)
        rollback_arguments = argparse.Namespace(
            **vars(activation_arguments), activation_hash=preview["activationHash"],
            active_version_file=active_version_file, rollback_marker=rollback_marker,
        )
        rollback_preview = self.release_update.rollback_preview(rollback_arguments)
        self.assertEqual(rollback_preview["status"], "ready")
        self.assertTrue(rollback_preview["trustedControllerOnly"])
        self.assertTrue(rollback_preview["singleUse"])
        self.assertFalse(rollback_preview["activeDeploymentChanged"])
        self.assertNotIn(str(self.fixture.root), json.dumps(rollback_preview))
        rollback_claim_arguments = argparse.Namespace(
            **vars(rollback_arguments), rollback_hash="0" * 64, confirm=True,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the current verified preview"):
            self.release_update.claim_update_rollback(rollback_claim_arguments)
        self.assertFalse((activation / "ROLLBACK.json").exists())
        rollback_claim_arguments.rollback_hash = rollback_preview["rollbackHash"]
        rollback_marker.write_text("tampered-marker\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "marker differs"):
            self.release_update.claim_update_rollback(rollback_claim_arguments)
        rollback_marker.write_text("content-free-fixture-marker\n", encoding="ascii")
        rollback_marker.chmod(0o600)
        rollback_claim = self.release_update.claim_update_rollback(rollback_claim_arguments)
        self.assertEqual(rollback_claim["status"], "claimed")
        self.assertFalse(rollback_claim["activeDeploymentChanged"])
        active_version_file.write_text(f"{self.fixture.current_version}\n", encoding="ascii")
        rollback_marker.unlink()
        rollback_recovery = self.release_update.recovery_preview(recovery_arguments)
        self.assertEqual(rollback_recovery["state"], "rollback-restored-result-missing")
        self.assertEqual(rollback_recovery["safeAction"], "finalize-rollback-result")
        rollback_result = self.release_update.finalize_recovery(argparse.Namespace(
            **vars(recovery_arguments), recovery_hash=rollback_recovery["recoveryHash"], confirm=True,
        ))
        rollback_result_arguments = argparse.Namespace(
            **vars(rollback_arguments), rollback_hash=rollback_preview["rollbackHash"], outcome="rolled-back",
            phase="record", confirm=True,
        )
        self.assertEqual(rollback_result["status"], "rolled-back")
        self.assertEqual(rollback_result["restoredVersion"], self.fixture.current_version)
        self.assertTrue(rollback_result["activeDeploymentRestored"])
        self.assertTrue(rollback_result["rollbackMarkerConsumed"])
        self.assertFalse(rollback_result["recoveryRequired"])
        self.assertNotIn(str(self.fixture.root), json.dumps(rollback_result))
        rollback_result_path = activation / "ROLLBACK-RESULT.json"
        successful_rollback_result = rollback_result_path.read_bytes()
        failed_rollback_result = {
            **rollback_result,
            "status": "failed",
            "recoveryRequired": True,
        }
        rollback_result_path.write_bytes(encoded(failed_rollback_result))
        rollback_result_path.chmod(0o600)
        failed_rollback_recovery = self.release_update.recovery_preview(recovery_arguments)
        self.assertEqual(failed_rollback_recovery["state"], "rollback-failed")
        self.assertEqual(failed_rollback_recovery["status"], "manual-review")
        self.assertIsNone(failed_rollback_recovery["safeAction"])
        self.assertIsNone(failed_rollback_recovery["recoveryHash"])
        rollback_result_path.write_bytes(encoded({**failed_rollback_result, "unexpected": True}))
        rollback_result_path.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "exact failed rollback receipt"):
            self.release_update.recovery_preview(recovery_arguments)
        rollback_result_path.write_bytes(successful_rollback_result)
        rollback_result_path.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.claim_update_rollback(rollback_claim_arguments)
        with mock.patch.object(
            self.release_update.shutil, "which", return_value=str(rehearsal_source / "scripts" / "safe.sh")
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "node parser is unsafe"):
                self.release_update.candidate_syntax_checks(rehearsal_source, json.loads(future.manifest))
        python_source = rehearsal_source / "scripts" / "safe.py"
        python_source.write_text("def broken(:\n", encoding="utf-8")
        python_source.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "Python syntax check failed"):
            self.release_update.rehearse(rehearsal_arguments)
        staged_sbom = candidate / future.sbom_path.name
        staged_sbom.write_bytes(staged_sbom.read_bytes() + b"tamper")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the verified bundle"):
            self.release_update.prepare(arguments)
        staged_sbom.write_bytes(future.sbom_path.read_bytes())
        staged_sbom.chmod(0o600)
        cleanup_arguments = argparse.Namespace(
            **vars(activation_arguments), activation_hash=preview["activationHash"],
            active_version_file=active_version_file, rollback_marker=rollback_marker,
        )
        restored_preview = self.release_update.cleanup_preview(cleanup_arguments)
        self.assertEqual(restored_preview["activeVersionAtCleanup"], self.fixture.current_version)
        active_version_file.write_text(f"{future.version}\n", encoding="ascii")
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "release cleanup refuses the active candidate version",
        ):
            self.release_update.cleanup_preview(cleanup_arguments)
        active_version_file.write_text("0.0.1\n", encoding="ascii")
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "release cleanup requires the active Pixel version to be restored or later",
        ):
            self.release_update.cleanup_preview(cleanup_arguments)
        later_version = next_patch(future.version)
        active_version_file.write_text(f"{later_version}\n", encoding="ascii")
        cleanup_preview = self.release_update.cleanup_preview(cleanup_arguments)
        self.assertEqual(cleanup_preview["status"], "ready")
        self.assertEqual(cleanup_preview["activeVersionAtCleanup"], later_version)
        self.assertFalse(cleanup_preview["installedReleaseWillBeDeleted"])
        self.assertFalse(cleanup_preview["activeDeploymentWillChange"])
        self.assertTrue(cleanup_preview["auditTombstoneWillBePreserved"])
        self.assertNotIn(str(self.fixture.root), json.dumps(cleanup_preview))
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the current completed update"):
            self.release_update.cleanup_completed_update(argparse.Namespace(
                **vars(cleanup_arguments), cleanup_hash="0" * 64, confirm=True,
            ))
        cleanup_run_arguments = argparse.Namespace(
            **vars(cleanup_arguments), cleanup_hash=cleanup_preview["cleanupHash"], confirm=True,
        )
        original_remove = self.release_update.safe_remove_cleanup_tree
        removal_calls = 0

        def interrupt_after_one_tree(path):
            nonlocal removal_calls
            removal_calls += 1
            if removal_calls == 2:
                raise self.release_update.UpdateError("simulated cleanup interruption")
            original_remove(path)

        with mock.patch.object(self.release_update, "safe_remove_cleanup_tree", side_effect=interrupt_after_one_tree):
            with self.assertRaisesRegex(self.release_update.UpdateError, "simulated cleanup interruption"):
                self.release_update.cleanup_completed_update(cleanup_run_arguments)
        history_path = staging_root / "cleanup-history" / f"{receipt['candidateId']}.json"
        self.assertTrue(history_path.is_file())
        self.assertFalse((staging_root / "cleanup-quarantine" / receipt["candidateId"] / "candidate").exists())
        original_history = history_path.read_bytes()
        tampered_history = json.loads(original_history)
        self.assertEqual(tampered_history["status"], "deleting")
        self.assertIsNone(tampered_history["cleanedAt"])
        self.assertFalse(tampered_history["candidateEvidenceDeleted"])
        self.assertFalse(tampered_history["rehearsalEvidenceDeleted"])
        interrupted_preview = self.release_update.cleanup_preview(cleanup_arguments)
        self.assertEqual(interrupted_preview["status"], "interrupted")
        self.assertTrue(interrupted_preview["confirmationRequired"])
        self.assertEqual(interrupted_preview["cleanupHash"], cleanup_preview["cleanupHash"])
        tampered_history["installedReleaseWillBeDeleted"] = True
        history_path.write_bytes(self.release_update.canonical_json(tampered_history))
        history_path.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "intent is invalid"):
            self.release_update.cleanup_completed_update(cleanup_run_arguments)
        history_path.write_bytes(original_history)
        history_path.chmod(0o600)
        resumed_cleanup = self.release_update.cleanup_completed_update(cleanup_run_arguments)
        self.assertEqual(resumed_cleanup["status"], "cleaned")
        self.assertTrue(resumed_cleanup["candidateEvidenceDeleted"])
        self.assertFalse(candidate.exists())
        self.assertFalse((staging_root / "rehearsals" / receipt["candidateId"]).exists())
        self.assertFalse(activation.exists())
        self.assertTrue(history_path.is_file())
        self.assertEqual(json.loads(history_path.read_bytes())["status"], "cleaned")
        self.assertEqual(active_version_file.read_text(encoding="ascii").strip(), later_version)
        repeated_cleanup = self.release_update.cleanup_preview(cleanup_arguments)
        self.assertEqual(repeated_cleanup["status"], "already-cleaned")
        self.assertEqual(repeated_cleanup["cleanupHash"], cleanup_preview["cleanupHash"])

    @unittest.skipUnless(sys.platform == "linux", "release staging is qualified only on Linux")
    def test_prepare_rejects_same_version_and_missing_confirmation(self):
        self.release_update.sign(self.fixture.sign_arguments())
        arguments = argparse.Namespace(
            envelope=self.fixture.envelope_path,
            allowed_signers=self.fixture.allowed_signers,
            identity="pixel-release",
            staging_root=self.fixture.root / "update-staging",
            confirm=False,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.release_update.prepare(arguments)
        arguments.confirm = True
        with self.assertRaisesRegex(self.release_update.UpdateError, "eligible forward release"):
            self.release_update.prepare(arguments)
        self.assertFalse(arguments.staging_root.exists())

    @unittest.skipUnless(sys.platform == "linux", "release staging is qualified only on Linux")
    def test_prepare_rejects_exposed_symlinked_and_locked_staging(self):
        bundle_root = self.fixture.root / "future-bundle"
        bundle_root.mkdir(mode=0o700)
        future = ReleaseFixture(bundle_root, version=next_patch(self.fixture.current_version))
        self.release_update.sign(future.sign_arguments())
        arguments = argparse.Namespace(
            envelope=future.envelope_path,
            allowed_signers=future.allowed_signers,
            identity="pixel-release",
            staging_root=self.fixture.root / "exposed-staging",
            confirm=True,
        )
        arguments.staging_root.mkdir(mode=0o755)
        arguments.staging_root.chmod(0o755)
        with self.assertRaisesRegex(self.release_update.UpdateError, "mode 0700"):
            self.release_update.prepare(arguments)
        private = self.fixture.root / "private-staging"
        private.mkdir(mode=0o700)
        linked = self.fixture.root / "linked-staging"
        linked.symlink_to(private, target_is_directory=True)
        arguments.staging_root = linked
        with self.assertRaisesRegex(self.release_update.UpdateError, "without symbolic-link components"):
            self.release_update.prepare(arguments)
        arguments.staging_root = private
        with self.release_update.exclusive_stage_lock(private):
            with self.assertRaisesRegex(self.release_update.UpdateError, "another release staging operation"):
                self.release_update.prepare(arguments)
        self.assertFalse((private / "candidates").exists())

    @unittest.skipUnless(sys.platform == "linux", "release staging is qualified only on Linux")
    def test_prepare_enforces_candidate_retention_without_deleting_valid_stages(self):
        staging_root = self.fixture.root / "update-staging"
        first_root = self.fixture.root / "first-bundle"
        second_root = self.fixture.root / "second-bundle"
        first_root.mkdir(mode=0o700)
        second_root.mkdir(mode=0o700)
        first = ReleaseFixture(first_root, version=next_patch(self.fixture.current_version))
        second = ReleaseFixture(second_root, version=next_patch(self.fixture.current_version, 2))
        self.release_update.sign(first.sign_arguments())
        self.release_update.sign(second.sign_arguments())

        def arguments(fixture):
            return argparse.Namespace(
                envelope=fixture.envelope_path,
                allowed_signers=fixture.allowed_signers,
                identity="pixel-release",
                staging_root=staging_root,
                confirm=True,
            )

        with mock.patch.object(self.release_update, "MAX_STAGED_CANDIDATES", 1):
            first_receipt = self.release_update.prepare(arguments(first))
            with self.assertRaisesRegex(self.release_update.UpdateError, "retention limit"):
                self.release_update.prepare(arguments(second))
        candidates = list((staging_root / "candidates").iterdir())
        self.assertEqual([path.name for path in candidates], [first_receipt["candidateId"]])

    @unittest.skipUnless(sys.platform == "linux", "release staging is qualified only on Linux")
    def test_stage_publication_never_replaces_an_existing_directory(self):
        source = self.fixture.root / "source-stage"
        destination = self.fixture.root / "existing-stage"
        source.mkdir(mode=0o700)
        destination.mkdir(mode=0o700)
        marker = destination / "owned"
        marker.write_text("preserve\n", encoding="utf-8")
        marker.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "appeared during staging"):
            self.release_update.rename_directory_noreplace(source, destination)
        self.assertTrue(source.is_dir())
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve\n")

    @unittest.skipUnless(sys.platform == "linux", "release cleanup is qualified only on Linux")
    def test_cleanup_unlinks_symlinks_without_touching_targets_and_rejects_hardlinks(self):
        outside = self.fixture.root / "outside"
        outside.write_text("preserve\n", encoding="utf-8")
        outside.chmod(0o600)
        cleanup = self.fixture.root / "cleanup-tree"
        cleanup.mkdir(mode=0o700)
        evidence = cleanup / "receipt"
        evidence.write_text("bounded evidence\n", encoding="utf-8")
        evidence.chmod(0o600)
        (cleanup / "outside-link").symlink_to(outside)
        shape = self.release_update.cleanup_tree_shape(cleanup)
        self.assertEqual(shape["entries"], 2)
        self.release_update.safe_remove_cleanup_tree(cleanup)
        self.assertEqual(outside.read_text(encoding="utf-8"), "preserve\n")
        hardlinks = self.fixture.root / "hardlinks"
        hardlinks.mkdir(mode=0o700)
        first = hardlinks / "first"
        first.write_text("shared\n", encoding="utf-8")
        first.chmod(0o600)
        os.link(first, hardlinks / "second")
        with self.assertRaisesRegex(self.release_update.UpdateError, "hard-linked"):
            self.release_update.cleanup_tree_shape(hardlinks)
        invalid_arguments = argparse.Namespace(
            candidate_id="../../outside", activation_hash="0" * 64,
            staging_root=self.fixture.root / "missing-staging",
            active_version_file=self.fixture.root / "VERSION",
            rollback_marker=self.fixture.root / "last-apply",
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "candidate ID is invalid"):
            self.release_update.cleanup_preview(invalid_arguments)
        self.assertFalse(invalid_arguments.staging_root.exists())

        candidate_id = f"pixel-3.3.1-{'a' * 64}"
        activation_hash = "b" * 64
        staging_root = self.fixture.root / "resume-staging"
        quarantine = staging_root / "cleanup-quarantine" / candidate_id
        history_path = staging_root / "cleanup-history" / f"{candidate_id}.json"
        staging_root.mkdir(mode=0o700)
        (staging_root / "cleanup-quarantine").mkdir(mode=0o700)
        quarantine.mkdir(mode=0o700)
        history_path.parent.mkdir(mode=0o700)
        intent = {
            "schemaVersion": 1, "operation": "pixel-release-update-cleanup",
            "candidateId": candidate_id, "product": "Pixel", "version": "3.3.1",
            "restoredVersion": "3.3.0", "activationHash": activation_hash,
            "sourceCommit": "c" * 40, "sourceTree": "d" * 40,
            "stageReceiptSha256": "e" * 64, "rehearsalReceiptSha256": "f" * 64,
            "activationClaimSha256": "0" * 64, "activationResultSha256": "1" * 64,
            "rollbackClaimSha256": "2" * 64, "rollbackResultSha256": "3" * 64,
            "trees": {
                name: {"shapeSha256": character * 64, "entries": 1, "regularBytes": 1}
                for name, character in (("candidate", "4"), ("rehearsal", "5"), ("activation", "6"))
            },
            "deleteScope": ["verified-bundle-copy", "rehearsal-copy", "completed-activation-workspace"],
            "installedReleaseWillBeDeleted": False, "activeDeploymentWillChange": False,
            "auditTombstoneWillBePreserved": True,
        }
        cleanup_hash = self.release_update.sha256(self.release_update.canonical_json(intent))
        claim = self.release_update.cleanup_claim_value(intent, cleanup_hash, "2026-08-09T12:00:00Z")
        history = self.release_update.cleanup_history_value(claim, "2026-08-09T12:01:00Z")
        self.release_update.write_private(history_path, self.release_update.canonical_json(history))
        stale_replacement = history_path.parent / f".{history_path.name}.replace-{'7' * 16}"
        self.release_update.write_private(stale_replacement, b"stale finalization\n")
        resume_arguments = argparse.Namespace(
            candidate_id=candidate_id, activation_hash=activation_hash, cleanup_hash=cleanup_hash,
            staging_root=staging_root, active_version_file=self.fixture.root / "VERSION",
            rollback_marker=self.fixture.root / "last-apply", confirm=True,
        )
        history_path.chmod(0o644)
        with self.assertRaisesRegex(self.release_update.UpdateError, "permissions are unsafe"):
            self.release_update.cleanup_completed_update(resume_arguments)
        history_path.chmod(0o600)
        resumed_empty = self.release_update.cleanup_completed_update(resume_arguments)
        self.assertEqual(resumed_empty["status"], "already-cleaned")
        self.assertFalse(quarantine.exists())
        self.assertFalse(stale_replacement.exists())
        self.assertTrue(history_path.is_file())

    @unittest.skipUnless(sys.platform == "linux", "release rehearsal is qualified only on Linux")
    def test_candidate_syntax_rehearsal_covers_repository_contracts(self):
        manifest = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        toolchain, checks = self.release_update.candidate_syntax_checks(ROOT, manifest)
        self.assertTrue(toolchain["nodeCompatible"])
        self.assertTrue(toolchain["pythonCompatible"])
        self.assertGreater(checks["jsonDocuments"], 20)
        self.assertGreater(checks["shellFiles"], 20)
        self.assertGreater(checks["javascriptFiles"], 10)
        self.assertGreater(checks["pythonFiles"], 10)
        self.assertEqual(checks["syntaxFailures"], 0)

    @unittest.skipUnless(sys.platform == "linux", "release rehearsal is qualified only on Linux")
    def test_candidate_syntax_rehearsal_ignores_local_build_and_dependency_output(self):
        source = Path(self.temporary.name) / "syntax-source"
        (source / "scripts").mkdir(parents=True)
        (source / "scripts" / "valid.mjs").write_text("export const valid = true;\n", encoding="utf-8")
        for component in (".git", "__pycache__", "dist", "node_modules", "plugin/node_modules"):
            directory = source / component
            directory.mkdir(parents=True)
            for index in range(260):
                (directory / f"generated-{index}.mjs").write_text("not valid JavaScript {{{\n", encoding="utf-8")
        manifest = json.loads((ROOT / "RELEASE-MANIFEST.json").read_text(encoding="utf-8"))
        _toolchain, checks = self.release_update.candidate_syntax_checks(source, manifest)
        self.assertEqual(checks["javascriptFiles"], 1)
        self.assertEqual(checks["syntaxFailures"], 0)

    def _terminal_activation_wrapper_journey(self):
        bundle_root = self.fixture.root / "activation-bundle"
        bundle_root.mkdir(mode=0o700)
        future = ReleaseFixture(
            bundle_root, version=next_patch(self.fixture.current_version), executable=True,
        )
        self.release_update.sign(future.sign_arguments())
        install_root = self.fixture.root / "install"
        staging_root = install_root / "update-staging"
        staging_root.mkdir(parents=True, mode=0o700)
        prepare_arguments = argparse.Namespace(
            envelope=future.envelope_path, allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root, confirm=True,
        )
        prepared = self.release_update.prepare(prepare_arguments)
        rehearsal_arguments = argparse.Namespace(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root, confirm=True,
        )
        self.release_update.rehearse(rehearsal_arguments)
        preview = self.release_update.activation_preview(argparse.Namespace(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
        ))
        releases = install_root / "releases"
        current_release = releases / self.fixture.current_version
        current_release.mkdir(parents=True, mode=0o700)
        (current_release / "VERSION").write_text(f"{self.fixture.current_version}\n", encoding="ascii")
        (current_release / "VERSION").chmod(0o600)
        (install_root / "current").symlink_to(current_release, target_is_directory=True)
        openclaw_home = self.fixture.root / "openclaw"
        (openclaw_home / "backups").mkdir(parents=True, mode=0o700)
        onboarding = self.fixture.root / "onboarding.json"
        onboarding.write_text("{}\n", encoding="utf-8")
        onboarding.chmod(0o600)
        trace = self.fixture.root / "activation-trace"
        controller = self.fixture.root / "controller"
        (controller / "scripts" / "lib").mkdir(parents=True, mode=0o700)
        shutil.copy2(ROOT / "scripts" / "activate-release-update.sh", controller / "scripts" / "activate-release-update.sh")
        shutil.copy2(ROOT / "scripts" / "reactivate-release-update.sh", controller / "scripts" / "reactivate-release-update.sh")
        shutil.copy2(ROOT / "scripts" / "rollback-reactivated-release-update.sh", controller / "scripts" / "rollback-reactivated-release-update.sh")
        shutil.copy2(ROOT / "scripts" / "recover-reactivated-release-update.sh", controller / "scripts" / "recover-reactivated-release-update.sh")
        shutil.copy2(ROOT / "scripts" / "rollback-release-update.sh", controller / "scripts" / "rollback-release-update.sh")
        shutil.copy2(ROOT / "scripts" / "recover-release-update.sh", controller / "scripts" / "recover-release-update.sh")
        shutil.copy2(ROOT / "scripts" / "cleanup-release-update.sh", controller / "scripts" / "cleanup-release-update.sh")
        shutil.copy2(ROOT / "scripts" / "release-update.py", controller / "scripts" / "release-update.py")
        shutil.copy2(ROOT / "scripts" / "lib" / "common.sh", controller / "scripts" / "lib" / "common.sh")
        (controller / "VERSION").write_text(f"{self.fixture.current_version}\n", encoding="ascii")
        (controller / ".env").write_text(
            "\n".join([
                f"PIXEL_INSTALL_DIR='{install_root}'",
                f"OPENCLAW_HOME='{openclaw_home}'",
                f"PIXEL_RELEASE_VERSION='{self.fixture.current_version}'",
                f"PIXEL_PRIVATE_ONBOARDING_PATH='{onboarding}'",
                f"PIXEL_ACTIVATION_TRACE='{trace}'",
                "",
            ]),
            encoding="utf-8",
        )
        (controller / "scripts" / "rollback.sh").write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "ROOT=$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/..\" && pwd)\n"
            "source \"$ROOT/scripts/lib/common.sh\"\n"
            "[[ ${1:-} == --confirm ]]\n"
            "pixel_load_env\npixel_acquire_deployment_lock exclusive\n"
            "marker=\"$OPENCLAW_HOME/backups/last-apply\"\nbackup=$(cat \"$marker\")\n"
            "previous=$(cat \"$backup/previous-release\")\n"
            "ln -s \"$previous\" \"$PIXEL_INSTALL_DIR/.rollback-current\"\n"
            "mv -Tf \"$PIXEL_INSTALL_DIR/.rollback-current\" \"$PIXEL_INSTALL_DIR/current\"\n"
            "mv \"$marker\" \"$marker.used\"\n",
            encoding="utf-8",
        )
        untrusted_override = self.fixture.root / "untrusted-update-staging"
        activation_wrapper_preview = subprocess.run([
            "bash", str(controller / "scripts" / "activate-release-update.sh"), "--preview",
            "--candidate-id", prepared["candidateId"], "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--staging-root", str(untrusted_override),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(activation_wrapper_preview.returncode, 0, activation_wrapper_preview.stderr)
        self.assertEqual(json.loads(activation_wrapper_preview.stdout)["activationHash"], preview["activationHash"])
        self.assertFalse(untrusted_override.exists())
        command = [
            "bash", str(controller / "scripts" / "activate-release-update.sh"),
            "--candidate-id", prepared["candidateId"],
            "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release",
            "--activation-hash", preview["activationHash"], "--confirm",
        ]
        headless = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(headless.returncode, 0)
        self.assertIn("real interactive terminal", headless.stderr)
        self.assertFalse((staging_root / "activations" / prepared["candidateId"]).exists())
        completed = run_in_pty(command)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status"], "activated")
        self.assertEqual(result["activeVersion"], future.version)
        self.assertEqual(
            trace.read_text(encoding="utf-8").splitlines(),
            ["configure", "bootstrap", "plan", "apply"],
        )
        activation = staging_root / "activations" / prepared["candidateId"]
        self.assertTrue((activation / "ACTIVATION.json").is_file())
        self.assertTrue((activation / "ACTIVATION-RESULT.json").is_file())
        self.assertEqual((install_root / "current" / "VERSION").read_text(encoding="ascii").strip(), future.version)
        (activation / "ACTIVATION-RESULT.json").unlink()
        recovery_base = [
            "--candidate-id", prepared["candidateId"], "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--activation-hash", preview["activationHash"],
        ]
        activation_recovery_process = subprocess.run([
            "bash", str(controller / "scripts" / "recover-release-update.sh"), "--preview", *recovery_base,
            "--staging-root", str(untrusted_override),
            "--active-version-file", str(self.fixture.root / "untrusted-VERSION"),
            "--rollback-marker", str(self.fixture.root / "untrusted-last-apply"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(activation_recovery_process.returncode, 0, activation_recovery_process.stderr)
        activation_recovery = json.loads(activation_recovery_process.stdout)
        self.assertEqual(activation_recovery["safeAction"], "finalize-activation-result")
        finalized_activation = subprocess.run([
            "bash", str(controller / "scripts" / "recover-release-update.sh"), *recovery_base,
            "--recovery-hash", activation_recovery["recoveryHash"], "--confirm",
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(finalized_activation.returncode, 0, finalized_activation.stderr)
        self.assertTrue((activation / "ACTIVATION-RESULT.json").is_file())
        rollback_preview_process = subprocess.run([
            "bash", str(controller / "scripts" / "rollback-release-update.sh"), "--preview",
            "--candidate-id", prepared["candidateId"], "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--activation-hash", preview["activationHash"],
            "--staging-root", str(untrusted_override),
            "--active-version-file", str(self.fixture.root / "untrusted-VERSION"),
            "--rollback-marker", str(self.fixture.root / "untrusted-last-apply"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(rollback_preview_process.returncode, 0, rollback_preview_process.stderr)
        rollback_preview = json.loads(rollback_preview_process.stdout)
        rollback_command = [
            "bash", str(controller / "scripts" / "rollback-release-update.sh"),
            "--candidate-id", prepared["candidateId"], "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--activation-hash", preview["activationHash"],
            "--rollback-hash", rollback_preview["rollbackHash"], "--confirm",
        ]
        headless_rollback = subprocess.run(
            rollback_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(headless_rollback.returncode, 0)
        self.assertIn("real interactive terminal", headless_rollback.stderr)
        self.assertFalse((activation / "ROLLBACK.json").exists())
        rollback_marker_bytes = (openclaw_home / "backups" / "last-apply").read_bytes()
        rolled_back = run_in_pty(rollback_command)
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        rollback_result = json.loads(rolled_back.stdout.strip().splitlines()[-1])
        self.assertEqual(rollback_result["status"], "rolled-back")
        self.assertEqual((install_root / "current" / "VERSION").read_text(encoding="ascii").strip(), self.fixture.current_version)
        self.assertFalse((openclaw_home / "backups" / "last-apply").exists())
        self.assertTrue((activation / "ROLLBACK.json").is_file())
        self.assertTrue((activation / "ROLLBACK-RESULT.json").is_file())
        (activation / "ROLLBACK-RESULT.json").unlink()
        rollback_recovery_process = subprocess.run([
            "bash", str(controller / "scripts" / "recover-release-update.sh"), "--preview", *recovery_base,
            "--staging-root", str(untrusted_override),
            "--active-version-file", str(self.fixture.root / "untrusted-VERSION"),
            "--rollback-marker", str(self.fixture.root / "untrusted-last-apply"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(rollback_recovery_process.returncode, 0, rollback_recovery_process.stderr)
        rollback_recovery = json.loads(rollback_recovery_process.stdout)
        self.assertEqual(rollback_recovery["safeAction"], "finalize-rollback-result")
        finalized_rollback = subprocess.run([
            "bash", str(controller / "scripts" / "recover-release-update.sh"), *recovery_base,
            "--recovery-hash", rollback_recovery["recoveryHash"], "--confirm",
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(finalized_rollback.returncode, 0, finalized_rollback.stderr)
        self.assertTrue((activation / "ROLLBACK-RESULT.json").is_file())
        return {
            "activation": activation,
            "activationHash": preview["activationHash"],
            "controller": controller,
            "future": future,
            "installRoot": install_root,
            "openclawHome": openclaw_home,
            "prepared": prepared,
            "recoveryBase": recovery_base,
            "rollbackCommand": rollback_command,
            "rollbackMarkerBytes": rollback_marker_bytes,
            "stagingRoot": staging_root,
            "trace": trace,
            "untrustedOverride": untrusted_override,
        }

    @unittest.skipUnless(sys.platform == "linux", "release reactivation is qualified only on Linux")
    def test_reactivation_deployment_record_is_private_hash_bound_and_timestamped(self):
        staging_root = self.fixture.root / "deployment-record-staging"
        version = next_patch(self.fixture.current_version)
        candidate_id = f"pixel-{version}-{'a' * 64}"
        generated = staging_root / "activations" / candidate_id / "source" / ".generated"
        generated.mkdir(parents=True, mode=0o700)
        generated.parent.chmod(0o700)
        record = generated / "deployment.json"
        payload = encoded({"generatedAt": "2026-08-09T00:00:00.000Z", "fixture": True})
        record.write_bytes(payload)
        record.chmod(0o600)
        retained = staging_root.parent / "releases" / version
        retained.mkdir(parents=True, mode=0o700)
        deployment_inputs = retained / "deployment-inputs.sha256"
        deployment_inputs.write_text(
            f"{digest(payload)}  .generated/deployment.json\n", encoding="ascii",
        )
        deployment_inputs.chmod(0o600)
        install_manifest = retained / "install-manifest.sha256"
        install_manifest.write_text(
            f"{digest(deployment_inputs.read_bytes())}  ./deployment-inputs.sha256\n",
            encoding="ascii",
        )
        install_manifest.chmod(0o600)
        observed, observed_hash, inputs_hash, manifest_hash = \
            self.release_update.reactivation_deployment_record(
                staging_root, candidate_id, version,
            )
        self.assertEqual(observed, payload)
        self.assertEqual(observed_hash, digest(payload))
        self.assertEqual(inputs_hash, digest(deployment_inputs.read_bytes()))
        self.assertEqual(manifest_hash, digest(install_manifest.read_bytes()))

        original_inputs = deployment_inputs.read_bytes()
        deployment_inputs.write_text(
            f"{'0' * 64}  .generated/deployment.json\n", encoding="ascii",
        )
        deployment_inputs.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the retained release"):
            self.release_update.reactivation_deployment_record(staging_root, candidate_id, version)
        deployment_inputs.write_bytes(original_inputs)
        deployment_inputs.chmod(0o600)

        original_manifest = install_manifest.read_bytes()
        install_manifest.write_text(
            f"{'0' * 64}  ./deployment-inputs.sha256\n", encoding="ascii",
        )
        install_manifest.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "differ from its install manifest"):
            self.release_update.reactivation_deployment_record(staging_root, candidate_id, version)
        install_manifest.write_bytes(original_manifest)
        install_manifest.chmod(0o600)

        record.chmod(0o644)
        with self.assertRaisesRegex(self.release_update.UpdateError, "permissions are unsafe"):
            self.release_update.reactivation_deployment_record(staging_root, candidate_id, version)
        record.chmod(0o600)
        parked = generated / "deployment.parked.json"
        record.rename(parked)
        record.symlink_to(parked)
        with self.assertRaises(self.release_update.UpdateError):
            self.release_update.reactivation_deployment_record(staging_root, candidate_id, version)
        record.unlink()
        parked.rename(record)
        record.write_bytes(encoded({"generatedAt": "not-a-timestamp", "fixture": True}))
        record.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "timestamp is invalid"):
            self.release_update.reactivation_deployment_record(staging_root, candidate_id, version)

    @unittest.skipUnless(sys.platform == "linux", "release activation is qualified only on Linux")
    def test_terminal_activation_wrapper_claims_before_fixed_candidate_sequence(self):
        journey = self._terminal_activation_wrapper_journey()
        activation = journey["activation"]
        controller = journey["controller"]
        future = journey["future"]
        install_root = journey["installRoot"]
        prepared = journey["prepared"]
        recovery_base = journey["recoveryBase"]
        rollback_command = journey["rollbackCommand"]
        staging_root = journey["stagingRoot"]
        untrusted_override = journey["untrustedOverride"]
        cleanup_preview_process = subprocess.run([
            "bash", str(controller / "scripts" / "cleanup-release-update.sh"), "--preview", *recovery_base,
            "--staging-root", str(untrusted_override),
            "--active-version-file", str(self.fixture.root / "untrusted-VERSION"),
            "--rollback-marker", str(self.fixture.root / "untrusted-last-apply"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(cleanup_preview_process.returncode, 0, cleanup_preview_process.stderr)
        cleanup_preview = json.loads(cleanup_preview_process.stdout)
        self.assertEqual(cleanup_preview["status"], "ready")
        cleaned = subprocess.run([
            "bash", str(controller / "scripts" / "cleanup-release-update.sh"), *recovery_base,
            "--cleanup-hash", cleanup_preview["cleanupHash"], "--confirm",
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
        cleanup_result = json.loads(cleaned.stdout)
        self.assertEqual(cleanup_result["status"], "cleaned")
        self.assertFalse((staging_root / "candidates" / prepared["candidateId"]).exists())
        self.assertFalse((staging_root / "rehearsals" / prepared["candidateId"]).exists())
        self.assertFalse(activation.exists())
        self.assertTrue((staging_root / "cleanup-history" / f"{prepared['candidateId']}.json").is_file())
        self.assertTrue((install_root / "releases" / future.version / "VERSION").is_file())
        replay = subprocess.run(rollback_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(replay.returncode, 0)

    @unittest.skipUnless(sys.platform == "linux", "release archive is qualified only on Linux")
    def test_historical_bundle_loader_accepts_only_the_exact_pre_archive_policy(self):
        bundle_root = self.fixture.root / "pre-archive-policy-bundle"
        bundle_root.mkdir(mode=0o700)
        historical = ReleaseFixture(
            bundle_root, version=next_patch(self.fixture.current_version, -1), pre_archive_policy=True,
        )
        subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-f", str(historical.key_path),
             "-n", self.release_update.NAMESPACE, str(historical.envelope_path)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "policy is invalid"):
            self.release_update.load_signed_bundle(
                historical.envelope_path, historical.allowed_signers, "pixel-release",
            )
        envelope, _envelope_bytes, _artifacts, manifest, _signature = self.release_update.load_signed_bundle(
            historical.envelope_path, historical.allowed_signers, "pixel-release",
            allow_pre_archive_policy=True,
        )
        self.assertEqual(envelope["version"], historical.version)
        self.assertNotIn("archiveReceiptSchema", manifest["releaseUpdate"])
        self.assertNotIn("archive", manifest["releaseUpdate"])

    @unittest.skipUnless(sys.platform == "linux", "release archive is qualified only on Linux")
    def test_historical_bundle_loader_accepts_only_exact_pre_reactivation_archive_policy(self):
        bundle_root = self.fixture.root / "pre-reactivation-archive-policy-bundle"
        bundle_root.mkdir(mode=0o700)
        historical = ReleaseFixture(
            bundle_root, version=next_patch(self.fixture.current_version, -1),
            pre_reactivation_archive_policy=True,
        )
        subprocess.run(
            ["ssh-keygen", "-q", "-Y", "sign", "-f", str(historical.key_path),
             "-n", self.release_update.NAMESPACE, str(historical.envelope_path)],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "policy is invalid"):
            self.release_update.load_signed_bundle(
                historical.envelope_path, historical.allowed_signers, "pixel-release",
            )
        envelope, _envelope_bytes, _artifacts, manifest, _signature = self.release_update.load_signed_bundle(
            historical.envelope_path, historical.allowed_signers, "pixel-release",
            allow_pre_archive_policy=True,
        )
        self.assertEqual(envelope["version"], historical.version)
        self.assertIn("archiveReceiptSchema", manifest["releaseUpdate"])
        self.assertIn("archive", manifest["releaseUpdate"])
        self.assertNotIn("reactivationArchiveReceiptSchema", manifest["releaseUpdate"])
        self.assertNotIn("reactivationArchive", manifest["releaseUpdate"])

    @unittest.skipUnless(sys.platform == "linux", "release archive is qualified only on Linux")
    def test_terminal_failed_rollback_archives_exact_evidence_and_resumes_interruption(self):
        journey = self._terminal_activation_wrapper_journey()
        activation = journey["activation"]
        future = journey["future"]
        install_root = journey["installRoot"]
        prepared = journey["prepared"]
        staging_root = journey["stagingRoot"]
        candidate_id = prepared["candidateId"]
        rollback_result_path = activation / "ROLLBACK-RESULT.json"
        rollback_result = json.loads(rollback_result_path.read_text(encoding="utf-8"))
        rollback_result_path.write_bytes(encoded({
            **rollback_result, "status": "failed", "recoveryRequired": True,
        }))
        rollback_result_path.chmod(0o600)
        for index in range(7):
            retained = staging_root / "candidates" / f"pixel-1.0.{index}-{'a' * 63}{index}"
            retained.mkdir(mode=0o700)
        archive_root = install_root / "update-archive"
        arguments = argparse.Namespace(
            candidate_id=candidate_id, allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root, archive_root=archive_root,
            activation_hash=journey["activationHash"],
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=journey["openclawHome"] / "backups" / "last-apply",
        )
        services = {
            "openclaw-gateway.service": "active",
            "pixel-ops-broker.service": "active",
            "pixel-web-courier.service": "active",
        }
        marker = arguments.rollback_marker
        marker.write_bytes(journey["rollbackMarkerBytes"])
        marker.chmod(0o600)
        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            with self.assertRaisesRegex(self.release_update.UpdateError, "live rollback marker"):
                self.release_update.archive_preview(arguments)
        marker.unlink()
        reactivation = staging_root / "reactivations" / candidate_id
        reactivation.mkdir(parents=True, mode=0o700)
        reactivation.parent.chmod(0o700)
        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            with self.assertRaisesRegex(self.release_update.UpdateError, "reactivation-bearing"):
                self.release_update.archive_preview(arguments)
        reactivation.rmdir()
        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            preview = self.release_update.archive_preview(arguments)
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["initialCandidateCount"], 8)
        self.assertEqual(preview["finalCandidateCount"], 7)
        self.assertEqual(preview["rollbackOutcome"], "failed")
        self.assertFalse(preview["installedReleaseWillMove"])
        self.assertFalse(preview["activeDeploymentWillChange"])
        self.assertTrue(preview["failedReceiptsWillBePreserved"])
        run_arguments = argparse.Namespace(
            **vars(arguments), archive_hash=preview["archiveHash"], confirm=True,
        )
        original_rename = self.release_update.rename_directory_noreplace
        rename_calls = 0

        def interrupt_after_candidate(source, destination):
            nonlocal rename_calls
            rename_calls += 1
            if rename_calls == 3:
                raise self.release_update.UpdateError("simulated archive interruption")
            original_rename(source, destination)

        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            with mock.patch.object(
                self.release_update, "rename_directory_noreplace", side_effect=interrupt_after_candidate,
            ):
                with self.assertRaisesRegex(self.release_update.UpdateError, "simulated archive interruption"):
                    self.release_update.archive_failed_update(run_arguments)
        destination = archive_root / candidate_id
        self.assertTrue((destination / "candidate").is_dir())
        self.assertTrue((staging_root / "rehearsals" / candidate_id).is_dir())
        archive_claim = json.loads((destination / "ARCHIVE.json").read_text(encoding="utf-8"))
        self.assertEqual(
            archive_claim["claim"],
            "pre-mutation-replay-tombstone-and-three-root-atomic-rename-with-idempotent-recovery",
        )
        reactivation.mkdir(mode=0o700)
        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            with self.assertRaisesRegex(self.release_update.UpdateError, "reactivation-bearing"):
                self.release_update.archive_preview(arguments)
            with self.assertRaisesRegex(self.release_update.UpdateError, "reactivation-bearing"):
                self.release_update.archive_failed_update(run_arguments)
        reactivation.rmdir()
        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            interrupted = self.release_update.archive_preview(arguments)
            self.assertEqual(interrupted["status"], "interrupted")
        original_noreplace = self.release_update.rename_noreplace

        def interrupt_result_publication(source, target, label):
            if target.name == "ARCHIVE-RESULT.json":
                raise self.release_update.UpdateError("simulated result publication interruption")
            original_noreplace(source, target, label)

        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            with mock.patch.object(
                self.release_update, "rename_noreplace", side_effect=interrupt_result_publication,
            ):
                with self.assertRaisesRegex(self.release_update.UpdateError, "result publication interruption"):
                    self.release_update.archive_failed_update(run_arguments)
        self.assertTrue((destination / ".ARCHIVE-RESULT.pending").is_file())
        self.assertFalse((destination / "ARCHIVE-RESULT.json").exists())
        pending_result_path = destination / ".ARCHIVE-RESULT.pending"
        pending_result = json.loads(pending_result_path.read_text(encoding="utf-8"))
        pending_result["archivedAt"] = "2026-08-09T12:00:00Z"
        pending_result_path.write_bytes(self.release_update.canonical_json(pending_result))
        pending_result_path.chmod(0o600)
        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            resumed = self.release_update.archive_failed_update(run_arguments)
        self.assertEqual(resumed["archivedAt"], "2026-08-09T12:00:00Z")
        self.assertEqual(resumed["status"], "archived")
        self.assertEqual(self.release_update.staged_candidate_count(staging_root), 7)
        self.assertTrue((destination / "candidate" / "STAGED-UPDATE.json").is_file())
        self.assertTrue((destination / "rehearsal" / "REHEARSAL.json").is_file())
        self.assertTrue((destination / "activation" / "ROLLBACK-RESULT.json").is_file())
        self.assertTrue((destination / "ARCHIVE.json").is_file())
        self.assertTrue((destination / "MANIFEST.json").is_file())
        self.assertTrue((destination / "ARCHIVE-RESULT.json").is_file())
        with mock.patch.object(self.release_update, "archive_service_state", return_value=services):
            repeated = self.release_update.archive_preview(arguments)
        self.assertEqual(repeated["status"], "already-archived")
        with self.assertRaisesRegex(self.release_update.UpdateError, "already archived"):
            self.release_update.prepare(argparse.Namespace(
                envelope=future.envelope_path, allowed_signers=future.allowed_signers,
                identity="pixel-release", staging_root=staging_root, confirm=True,
            ))

    @unittest.skipUnless(sys.platform == "linux", "release archive is qualified only on Linux")
    def test_archive_manifest_rejects_symlinks_hardlinks_and_extended_attributes(self):
        root = self.fixture.root / "archive-evidence"
        root.mkdir(mode=0o700)
        first = root / "first"
        first.write_text("evidence\n", encoding="utf-8")
        first.chmod(0o600)
        outside = self.fixture.root / "outside-archive-evidence"
        outside.write_text("outside\n", encoding="utf-8")
        outside.chmod(0o600)
        linked = root / "linked"
        linked.symlink_to(outside)
        with self.assertRaisesRegex(self.release_update.UpdateError, "symbolic link"):
            self.release_update.archive_tree_manifest(root)
        linked.unlink()
        hardlink = root / "hardlink"
        os.link(first, hardlink)
        with self.assertRaisesRegex(self.release_update.UpdateError, "hard-linked"):
            self.release_update.archive_tree_manifest(root)
        hardlink.unlink()
        first.chmod(0o4600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "special"):
            self.release_update.archive_tree_manifest(root)
        first.chmod(0o600)
        if hasattr(os, "setxattr"):
            try:
                os.setxattr(first, "user.pixel-test", b"denied")
            except OSError:
                self.skipTest("test filesystem does not support user extended attributes")
            with self.assertRaisesRegex(self.release_update.UpdateError, "extended attributes"):
                self.release_update.archive_tree_manifest(root)

    @unittest.skipUnless(sys.platform == "linux", "release reactivation is qualified only on Linux")
    def test_terminal_reactivation_has_fresh_rollback_and_recovery_chain(self):
        journey = self._terminal_activation_wrapper_journey()
        activation = journey["activation"]
        activation_hash = journey["activationHash"]
        controller = journey["controller"]
        future = journey["future"]
        install_root = journey["installRoot"]
        openclaw_home = journey["openclawHome"]
        prepared = journey["prepared"]
        staging_root = journey["stagingRoot"]
        trace = journey["trace"]
        untrusted_override = journey["untrustedOverride"]
        original_receipts = {
            name: digest((activation / name).read_bytes())
            for name in ("ACTIVATION.json", "ACTIVATION-RESULT.json", "ROLLBACK.json", "ROLLBACK-RESULT.json")
        }
        (controller / "VERSION").write_text(f"{future.version}\n", encoding="ascii")
        environment = (controller / ".env").read_text(encoding="utf-8")
        environment = environment.replace(
            f"PIXEL_RELEASE_VERSION='{self.fixture.current_version}'",
            f"PIXEL_RELEASE_VERSION='{future.version}'",
        )
        (controller / ".env").write_text(environment, encoding="utf-8")
        base = [
            "--candidate-id", prepared["candidateId"], "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--activation-hash", activation_hash,
        ]
        wrong_activation = [*base[:-1], "0" * 64]
        wrong_activation_process = subprocess.run([
            "bash", str(controller / "scripts" / "reactivate-release-update.sh"),
            "--preview", *wrong_activation,
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(wrong_activation_process.returncode, 0)
        rehearsal_readme = (
            staging_root / "rehearsals" / prepared["candidateId"] / "source" / "README.txt"
        )
        rehearsal_readme_bytes = rehearsal_readme.read_bytes()
        rehearsal_readme.write_bytes(rehearsal_readme_bytes + b"tampered\n")
        tampered_source_process = subprocess.run([
            "bash", str(controller / "scripts" / "reactivate-release-update.sh"),
            "--preview", *base,
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(tampered_source_process.returncode, 0)
        self.assertIn("source differs", tampered_source_process.stderr)
        rehearsal_readme.write_bytes(rehearsal_readme_bytes)
        preview_process = subprocess.run([
            "bash", str(controller / "scripts" / "reactivate-release-update.sh"), "--preview", *base,
            "--staging-root", str(untrusted_override),
            "--active-version-file", str(self.fixture.root / "untrusted-VERSION"),
            "--rollback-marker", str(self.fixture.root / "untrusted-last-apply"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(preview_process.returncode, 0, preview_process.stderr)
        preview = json.loads(preview_process.stdout)
        self.assertEqual(preview["status"], "ready")
        activation_record = activation / "source" / ".generated" / "deployment.json"
        self.assertEqual(
            preview["activationDeploymentRecordSha256"], digest(activation_record.read_bytes()),
        )
        self.assertFalse(untrusted_override.exists())
        command = [
            "bash", str(controller / "scripts" / "reactivate-release-update.sh"), *base,
            "--reactivation-hash", preview["reactivationHash"], "--confirm",
        ]
        wrong_hash_command = [
            "bash", str(controller / "scripts" / "reactivate-release-update.sh"), *base,
            "--reactivation-hash", "0" * 64, "--confirm",
        ]
        wrong_hash_process = subprocess.run(
            wrong_hash_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(wrong_hash_process.returncode, 0)
        headless = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(headless.returncode, 0)
        self.assertIn("real interactive terminal", headless.stderr)
        self.assertFalse((staging_root / "reactivations" / prepared["candidateId"]).exists())
        completed = run_in_pty(command)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status"], "reactivated")
        self.assertEqual(result["activeVersion"], future.version)
        self.assertEqual(
            trace.read_text(encoding="utf-8").splitlines(),
            ["configure", "bootstrap", "plan", "apply"] * 2,
        )
        reactivation = staging_root / "reactivations" / prepared["candidateId"]
        self.assertTrue((reactivation / "REACTIVATION.json").is_file())
        self.assertTrue((reactivation / "REACTIVATION-RESULT.json").is_file())
        copied_record = reactivation / "source" / ".generated" / "deployment.json"
        self.assertEqual(copied_record.read_bytes(), activation_record.read_bytes())
        self.assertEqual(stat.S_IMODE(copied_record.stat().st_mode), 0o600)
        replay = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(replay.returncode, 0)

        (reactivation / "REACTIVATION-RESULT.json").unlink()
        recovery_base = [*base, "--reactivation-hash", preview["reactivationHash"]]
        recovery_process = subprocess.run([
            "bash", str(controller / "scripts" / "recover-reactivated-release-update.sh"),
            "--preview", *recovery_base,
            "--staging-root", str(untrusted_override),
            "--active-version-file", str(self.fixture.root / "untrusted-VERSION"),
            "--rollback-marker", str(self.fixture.root / "untrusted-last-apply"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(recovery_process.returncode, 0, recovery_process.stderr)
        recovery = json.loads(recovery_process.stdout)
        self.assertEqual(recovery["safeAction"], "finalize-reactivation-result")
        wrong_recovery = subprocess.run([
            "bash", str(controller / "scripts" / "recover-reactivated-release-update.sh"),
            *recovery_base, "--recovery-hash", "0" * 64, "--confirm",
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(wrong_recovery.returncode, 0)
        self.assertFalse((reactivation / "REACTIVATION-RESULT.json").exists())
        finalized = subprocess.run([
            "bash", str(controller / "scripts" / "recover-reactivated-release-update.sh"),
            *recovery_base, "--recovery-hash", recovery["recoveryHash"], "--confirm",
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(finalized.returncode, 0, finalized.stderr)
        self.assertTrue((reactivation / "REACTIVATION-RESULT.json").is_file())
        reactivation_claim = reactivation / "REACTIVATION.json"
        reactivation_claim.chmod(0o644)
        unsafe_claim_process = subprocess.run([
            "bash", str(controller / "scripts" / "recover-reactivated-release-update.sh"),
            "--preview", *recovery_base,
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(unsafe_claim_process.returncode, 0)
        self.assertIn("permissions are unsafe", unsafe_claim_process.stderr)
        reactivation_claim.chmod(0o600)

        rollback_preview_process = subprocess.run([
            "bash", str(controller / "scripts" / "rollback-reactivated-release-update.sh"),
            "--preview", *recovery_base,
            "--staging-root", str(untrusted_override),
            "--active-version-file", str(self.fixture.root / "untrusted-VERSION"),
            "--rollback-marker", str(self.fixture.root / "untrusted-last-apply"),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(rollback_preview_process.returncode, 0, rollback_preview_process.stderr)
        rollback_preview = json.loads(rollback_preview_process.stdout)
        rollback_command = [
            "bash", str(controller / "scripts" / "rollback-reactivated-release-update.sh"),
            *recovery_base, "--rollback-hash", rollback_preview["rollbackHash"], "--confirm",
        ]
        headless_rollback = subprocess.run(
            rollback_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertNotEqual(headless_rollback.returncode, 0)
        self.assertIn("real interactive terminal", headless_rollback.stderr)
        self.assertFalse((reactivation / "ROLLBACK.json").exists())
        rolled_back = run_in_pty(rollback_command)
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        rollback_result = json.loads(rolled_back.stdout.strip().splitlines()[-1])
        self.assertEqual(rollback_result["status"], "rolled-back")
        self.assertEqual(
            (install_root / "current" / "VERSION").read_text(encoding="ascii").strip(),
            self.fixture.current_version,
        )
        self.assertFalse((openclaw_home / "backups" / "last-apply").exists())
        self.assertTrue((reactivation / "ROLLBACK.json").is_file())
        self.assertTrue((reactivation / "ROLLBACK-RESULT.json").is_file())

        (reactivation / "ROLLBACK-RESULT.json").unlink()
        rollback_recovery_process = subprocess.run([
            "bash", str(controller / "scripts" / "recover-reactivated-release-update.sh"),
            "--preview", *recovery_base,
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(rollback_recovery_process.returncode, 0, rollback_recovery_process.stderr)
        rollback_recovery = json.loads(rollback_recovery_process.stdout)
        self.assertEqual(
            rollback_recovery["safeAction"], "finalize-reactivation-rollback-result",
        )
        finalized_rollback = subprocess.run([
            "bash", str(controller / "scripts" / "recover-reactivated-release-update.sh"),
            *recovery_base, "--recovery-hash", rollback_recovery["recoveryHash"], "--confirm",
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(finalized_rollback.returncode, 0, finalized_rollback.stderr)
        self.assertTrue((reactivation / "ROLLBACK-RESULT.json").is_file())
        second_reactivation = subprocess.run([
            "bash", str(controller / "scripts" / "reactivate-release-update.sh"),
            "--preview", *base,
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertNotEqual(second_reactivation.returncode, 0)
        self.assertIn("already been claimed", second_reactivation.stderr)
        self.assertEqual(
            original_receipts,
            {name: digest((activation / name).read_bytes()) for name in original_receipts},
        )
        cleanup_arguments = argparse.Namespace(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
            activation_hash=activation_hash,
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=openclaw_home / "backups" / "last-apply",
        )
        with self.assertRaisesRegex(
            self.release_update.UpdateError, "dedicated immutable receipt chain",
        ):
            self.release_update.cleanup_preview(cleanup_arguments)

    def _claimed_reactivation_fixture(self, module_suffix):
        journey = self._terminal_activation_wrapper_journey()
        controller = journey["controller"]
        future = journey["future"]
        install_root = journey["installRoot"]
        openclaw_home = journey["openclawHome"]
        prepared = journey["prepared"]
        staging_root = journey["stagingRoot"]
        (controller / "VERSION").write_text(f"{future.version}\n", encoding="ascii")
        (controller / ".env").write_text(
            (controller / ".env").read_text(encoding="utf-8").replace(
                f"PIXEL_RELEASE_VERSION='{self.fixture.current_version}'",
                f"PIXEL_RELEASE_VERSION='{future.version}'",
            ),
            encoding="utf-8",
        )
        update = load_release_update(
            controller / "scripts" / "release-update.py",
            f"pixel_release_update_{module_suffix}_{time.time_ns()}",
        )
        common = dict(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
            activation_hash=journey["activationHash"],
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=openclaw_home / "backups" / "last-apply",
        )
        preview = update.reactivation_preview(argparse.Namespace(**common))
        reactivation_hash = preview["reactivationHash"]
        update.claim_reactivation(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash, confirm=True,
        ))
        reactivation = staging_root / "reactivations" / prepared["candidateId"]
        return journey, update, common, reactivation_hash, reactivation

    @unittest.skipUnless(sys.platform == "linux", "release activation is qualified only on Linux")
    def test_terminal_no_mutation_reactivation_failure_requires_authorized_hash_bound_retry(self):
        journey = self._terminal_activation_wrapper_journey()
        activation_hash = journey["activationHash"]
        controller = journey["controller"]
        future = journey["future"]
        install_root = journey["installRoot"]
        openclaw_home = journey["openclawHome"]
        prepared = journey["prepared"]
        staging_root = journey["stagingRoot"]
        trace = journey["trace"]

        (controller / "VERSION").write_text(f"{future.version}\n", encoding="ascii")
        environment = (controller / ".env").read_text(encoding="utf-8").replace(
            f"PIXEL_RELEASE_VERSION='{self.fixture.current_version}'",
            f"PIXEL_RELEASE_VERSION='{future.version}'",
        )
        (controller / ".env").write_text(environment, encoding="utf-8")
        update = load_release_update(
            controller / "scripts" / "release-update.py",
            f"pixel_release_update_retry_{time.time_ns()}",
        )
        common = dict(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
            activation_hash=activation_hash,
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=openclaw_home / "backups" / "last-apply",
        )
        first_preview = update.reactivation_preview(argparse.Namespace(**common))
        first_hash = first_preview["reactivationHash"]
        update.claim_reactivation(argparse.Namespace(
            **common, reactivation_hash=first_hash, confirm=True,
        ))
        failed = update.record_reactivation_result(argparse.Namespace(
            **common, reactivation_hash=first_hash, outcome="failed",
            phase="apply", confirm=True,
        ))
        self.assertFalse(failed["activeDeploymentChanged"])
        self.assertFalse(failed["rollbackAvailable"])
        reactivation = staging_root / "reactivations" / prepared["candidateId"]
        immutable_receipts = {
            name: digest((reactivation / name).read_bytes())
            for name in ("REACTIVATION.json", "REACTIVATION-RESULT.json")
        }
        with self.assertRaisesRegex(update.UpdateError, "already been claimed"):
            update.reactivation_preview(argparse.Namespace(**common))

        marker = openclaw_home / "backups" / "last-apply"
        marker.write_text("/ambiguous/retry-marker\n", encoding="utf-8")
        marker.chmod(0o600)
        ambiguous = update.reactivation_recovery_preview(argparse.Namespace(
            **common, reactivation_hash=first_hash,
        ))
        self.assertEqual(ambiguous["status"], "manual-review")
        self.assertIsNone(ambiguous["safeAction"])
        marker.unlink()

        recovery = update.reactivation_recovery_preview(argparse.Namespace(
            **common, reactivation_hash=first_hash,
        ))
        self.assertEqual(recovery["state"], "reactivation-failed-before-active-change")
        self.assertEqual(recovery["safeAction"], "authorize-reactivation-retry")
        with self.assertRaisesRegex(update.UpdateError, "hash differs"):
            update.finalize_reactivation_recovery(argparse.Namespace(
                **common, reactivation_hash=first_hash,
                recovery_hash="0" * 64, confirm=True,
            ))
        self.assertFalse((reactivation / "RETRY-AUTHORIZATION.json").exists())
        active_version_file = install_root / "current" / "VERSION"
        active_version_bytes = active_version_file.read_bytes()
        active_version_file.write_text(f"{future.version}\n", encoding="ascii")
        with self.assertRaises(update.UpdateError):
            update.finalize_reactivation_recovery(argparse.Namespace(
                **common, reactivation_hash=first_hash,
                recovery_hash=recovery["recoveryHash"], confirm=True,
            ))
        active_version_file.write_bytes(active_version_bytes)
        self.assertFalse((reactivation / "RETRY-AUTHORIZATION.json").exists())

        rollback_artifact = reactivation / "ROLLBACK.json"
        rollback_artifact.write_text("{}\n", encoding="utf-8")
        rollback_artifact.chmod(0o600)
        with self.assertRaises(update.UpdateError):
            update.finalize_reactivation_recovery(argparse.Namespace(
                **common, reactivation_hash=first_hash,
                recovery_hash=recovery["recoveryHash"], confirm=True,
            ))
        rollback_artifact.unlink()
        self.assertFalse((reactivation / "RETRY-AUTHORIZATION.json").exists())

        result_path = reactivation / "REACTIVATION-RESULT.json"
        result_bytes = result_path.read_bytes()
        result_path.write_bytes(result_bytes + b"\n")
        result_path.chmod(0o600)
        with self.assertRaises(update.UpdateError):
            update.finalize_reactivation_recovery(argparse.Namespace(
                **common, reactivation_hash=first_hash,
                recovery_hash=recovery["recoveryHash"], confirm=True,
            ))
        result_path.write_bytes(result_bytes)
        result_path.chmod(0o600)
        self.assertFalse((reactivation / "RETRY-AUTHORIZATION.json").exists())

        authorization = update.finalize_reactivation_recovery(argparse.Namespace(
            **common, reactivation_hash=first_hash,
            recovery_hash=recovery["recoveryHash"], confirm=True,
        ))
        self.assertEqual(authorization["failedReactivationHash"], first_hash)
        self.assertEqual(
            immutable_receipts,
            {name: digest((reactivation / name).read_bytes()) for name in immutable_receipts},
        )
        authorized = update.reactivation_recovery_preview(argparse.Namespace(
            **common, reactivation_hash=first_hash,
        ))
        self.assertEqual(authorized["state"], "reactivation-retry-authorized")
        self.assertEqual(authorized["status"], "complete")
        authorization_path = reactivation / "RETRY-AUTHORIZATION.json"
        authorization_bytes = authorization_path.read_bytes()
        with self.assertRaises(update.UpdateError):
            update.finalize_reactivation_recovery(argparse.Namespace(
                **common, reactivation_hash=first_hash,
                recovery_hash=recovery["recoveryHash"], confirm=True,
            ))
        self.assertEqual(authorization_path.read_bytes(), authorization_bytes)

        attempt_root = staging_root / "reactivation-attempts"
        attempt_root.mkdir(mode=0o700)
        attempts = attempt_root / prepared["candidateId"]
        attempts.mkdir(mode=0o700)
        unknown = attempts / ".unknown-custody"
        unknown.mkdir(mode=0o700)
        with self.assertRaisesRegex(update.UpdateError, "unrecognized entry"):
            update.reactivation_preview(argparse.Namespace(**common))
        with self.assertRaisesRegex(update.UpdateError, "unrecognized entry"):
            update.claim_reactivation(argparse.Namespace(
                **common, reactivation_hash="0" * 64, confirm=True,
            ))
        unknown.rmdir()
        fork = attempts / ("0" * 64)
        fork.mkdir(mode=0o700)
        with self.assertRaisesRegex(update.UpdateError, "forked or malformed"):
            update.reactivation_preview(argparse.Namespace(**common))
        fork.rmdir()

        retry_preview = update.reactivation_preview(argparse.Namespace(**common))
        retry_hash = retry_preview["reactivationHash"]
        self.assertNotEqual(retry_hash, first_hash)
        self.assertEqual(retry_preview["attemptNumber"], 2)
        self.assertEqual(retry_preview["previousReactivationHash"], first_hash)
        command = [
            "bash", str(controller / "scripts" / "reactivate-release-update.sh"),
            "--candidate-id", prepared["candidateId"],
            "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--activation-hash", activation_hash,
            "--reactivation-hash", retry_hash, "--confirm",
        ]
        retried = run_in_pty(command)
        self.assertEqual(retried.returncode, 0, retried.stderr)
        result = json.loads(retried.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status"], "reactivated")
        self.assertEqual(result["activeVersion"], future.version)
        self.assertTrue(result["liveMutationStarted"])
        self.assertRegex(result["liveMutationMarkerSha256"], r"^[0-9a-f]{64}$")
        self.assertFalse(any((install_root / "releases" / future.version).rglob("*.pyc")))
        retry_attempt = attempts / retry_hash
        self.assertTrue((retry_attempt / "REACTIVATION.json").is_file())
        self.assertTrue((retry_attempt / "REACTIVATION-RESULT.json").is_file())
        self.assertEqual(
            trace.read_text(encoding="utf-8").splitlines(),
            ["configure", "bootstrap", "plan", "apply"] * 2,
        )
        rollback_preview = update.reactivation_rollback_preview(argparse.Namespace(
            **common, reactivation_hash=retry_hash,
        ))
        rolled_back = run_in_pty([
            "bash", str(controller / "scripts" / "rollback-reactivated-release-update.sh"),
            "--candidate-id", prepared["candidateId"],
            "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--activation-hash", activation_hash,
            "--reactivation-hash", retry_hash,
            "--rollback-hash", rollback_preview["rollbackHash"], "--confirm",
        ])
        self.assertEqual(rolled_back.returncode, 0, rolled_back.stderr)
        self.assertEqual(
            (install_root / "current" / "VERSION").read_text(encoding="ascii").strip(),
            self.fixture.current_version,
        )
        self.assertTrue((retry_attempt / "ROLLBACK.json").is_file())
        self.assertTrue((retry_attempt / "ROLLBACK-RESULT.json").is_file())
        with self.assertRaisesRegex(update.UpdateError, "already been claimed"):
            update.reactivation_preview(argparse.Namespace(**common))

    @unittest.skipUnless(sys.platform == "linux", "release activation is qualified only on Linux")
    def test_reactivation_live_mutation_marker_and_result_disagreement_fail_closed(self):
        _journey, update, common, reactivation_hash, reactivation = \
            self._claimed_reactivation_fixture("marker_adversarial")
        marker = reactivation / "LIVE-MUTATION-STARTED"
        result_path = reactivation / "REACTIVATION-RESULT.json"

        marker.write_bytes(b"")
        marker.chmod(0o600)
        with self.assertRaises(update.UpdateError):
            update.record_reactivation_result(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
                outcome="failed", phase="apply", confirm=True,
            ))
        self.assertFalse(result_path.exists())

        marker.write_bytes(b"truncated\n")
        marker.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "invalid"):
            update.record_reactivation_result(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
                outcome="failed", phase="apply", confirm=True,
            ))

        marker.write_bytes(update.REACTIVATION_LIVE_MUTATION_MARKER)
        marker.chmod(0o644)
        with self.assertRaisesRegex(update.UpdateError, "permissions are unsafe"):
            update.record_reactivation_result(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
                outcome="failed", phase="apply", confirm=True,
            ))
        marker.chmod(0o600)
        alias = reactivation / "LIVE-MUTATION-STARTED-hardlink"
        os.link(marker, alias)
        with self.assertRaises(update.UpdateError):
            update.record_reactivation_result(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
                outcome="failed", phase="apply", confirm=True,
            ))
        alias.unlink()

        parked = reactivation / "LIVE-MUTATION-STARTED-parked"
        marker.rename(parked)
        marker.symlink_to(parked)
        with self.assertRaises(update.UpdateError):
            update.record_reactivation_result(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
                outcome="failed", phase="apply", confirm=True,
            ))
        marker.unlink()
        parked.rename(marker)

        result = update.record_reactivation_result(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash,
            outcome="failed", phase="apply", confirm=True,
        ))
        self.assertTrue(result["liveMutationStarted"])
        result_bytes = result_path.read_bytes()
        marker_bytes = marker.read_bytes()

        marker.unlink()
        with self.assertRaisesRegex(update.UpdateError, "differs from its live-mutation marker"):
            update.reactivation_recovery_preview(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
            ))
        marker.write_bytes(marker_bytes)
        marker.chmod(0o600)

        result_path.write_bytes(encoded({**result, "liveMutationMarkerSha256": "0" * 64}))
        result_path.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "differs from its live-mutation marker"):
            update.reactivation_recovery_preview(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
            ))

        result_path.write_bytes(encoded({
            **result, "liveMutationStarted": False, "liveMutationMarkerSha256": None,
        }))
        result_path.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "differs from its live-mutation marker"):
            update.reactivation_recovery_preview(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
            ))

        result_path.write_bytes(result_bytes)
        result_path.chmod(0o600)
        marker.unlink()
        marker.symlink_to(reactivation / "missing-live-mutation-marker")
        with self.assertRaises(update.UpdateError):
            update.reactivation_recovery_preview(argparse.Namespace(
                **common, reactivation_hash=reactivation_hash,
            ))
        marker.unlink()
        marker.write_bytes(marker_bytes)
        marker.chmod(0o600)
        recovery = update.reactivation_recovery_preview(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash,
        ))
        self.assertEqual(recovery["status"], "manual-review")
        self.assertIsNone(recovery["safeAction"])

    @unittest.skipUnless(sys.platform == "linux", "release activation is qualified only on Linux")
    def test_reactivation_retry_attempt_limit_is_bounded_without_evidence_overwrite(self):
        _journey, update, common, current_hash, reactivation = \
            self._claimed_reactivation_fixture("retry_retention")
        candidate_id = common["candidate_id"]
        claim_hashes = []
        for attempt_number in range(1, update.MAX_REACTIVATION_ATTEMPTS + 1):
            attempt = reactivation if attempt_number == 1 else (
                common["staging_root"] / "reactivation-attempts" / candidate_id / current_hash
            )
            claim_path = attempt / "REACTIVATION.json"
            claim_hashes.append(digest(claim_path.read_bytes()))
            update.record_reactivation_result(argparse.Namespace(
                **common, reactivation_hash=current_hash,
                outcome="failed", phase="apply", confirm=True,
            ))
            recovery = update.reactivation_recovery_preview(argparse.Namespace(
                **common, reactivation_hash=current_hash,
            ))
            self.assertEqual(recovery["safeAction"], "authorize-reactivation-retry")
            update.finalize_reactivation_recovery(argparse.Namespace(
                **common, reactivation_hash=current_hash,
                recovery_hash=recovery["recoveryHash"], confirm=True,
            ))
            if attempt_number < update.MAX_REACTIVATION_ATTEMPTS:
                preview = update.reactivation_preview(argparse.Namespace(**common))
                self.assertEqual(preview["attemptNumber"], attempt_number + 1)
                current_hash = preview["reactivationHash"]
                update.claim_reactivation(argparse.Namespace(
                    **common, reactivation_hash=current_hash, confirm=True,
                ))

        with self.assertRaisesRegex(update.UpdateError, "retention limit"):
            update.reactivation_preview(argparse.Namespace(**common))
        attempts = common["staging_root"] / "reactivation-attempts" / candidate_id
        self.assertEqual(
            len([entry for entry in attempts.iterdir() if entry.is_dir()]),
            update.MAX_REACTIVATION_ATTEMPTS - 1,
        )
        observed_claim_hashes = [digest((reactivation / "REACTIVATION.json").read_bytes())]
        observed_claim_hashes.extend(
            digest((attempt / "REACTIVATION.json").read_bytes())
            for attempt in sorted(attempts.iterdir())
        )
        self.assertCountEqual(observed_claim_hashes, claim_hashes)

    @unittest.skipUnless(sys.platform == "linux", "release activation is qualified only on Linux")
    def test_reactivation_failure_after_live_mutation_marker_remains_manual_review(self):
        journey = self._terminal_activation_wrapper_journey()
        controller = journey["controller"]
        future = journey["future"]
        install_root = journey["installRoot"]
        openclaw_home = journey["openclawHome"]
        prepared = journey["prepared"]
        staging_root = journey["stagingRoot"]
        (controller / "VERSION").write_text(f"{future.version}\n", encoding="ascii")
        (controller / ".env").write_text(
            (controller / ".env").read_text(encoding="utf-8").replace(
                f"PIXEL_RELEASE_VERSION='{self.fixture.current_version}'",
                f"PIXEL_RELEASE_VERSION='{future.version}'",
            ),
            encoding="utf-8",
        )
        update = load_release_update(
            controller / "scripts" / "release-update.py",
            f"pixel_release_update_post_mutation_{time.time_ns()}",
        )
        common = dict(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
            activation_hash=journey["activationHash"],
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=openclaw_home / "backups" / "last-apply",
        )
        preview = update.reactivation_preview(argparse.Namespace(**common))
        reactivation_hash = preview["reactivationHash"]
        update.claim_reactivation(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash, confirm=True,
        ))
        reactivation = staging_root / "reactivations" / prepared["candidateId"]
        marker = reactivation / "LIVE-MUTATION-STARTED"
        marker.write_bytes(update.REACTIVATION_LIVE_MUTATION_MARKER)
        marker.chmod(0o600)
        result = update.record_reactivation_result(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash,
            outcome="failed", phase="apply", confirm=True,
        ))
        self.assertTrue(result["liveMutationStarted"])
        recovery = update.reactivation_recovery_preview(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash,
        ))
        self.assertEqual(recovery["status"], "manual-review")
        self.assertEqual(recovery["state"], "reactivation-failed")
        self.assertIsNone(recovery["safeAction"])
        self.assertFalse(recovery["confirmationAvailable"])

    @unittest.skipUnless(sys.platform == "linux", "release activation is qualified only on Linux")
    def test_terminal_activation_failure_is_content_free_and_leaves_active_release_unchanged(self):
        bundle_root = self.fixture.root / "failure-bundle"
        bundle_root.mkdir(mode=0o700)
        future = ReleaseFixture(
            bundle_root, version=next_patch(self.fixture.current_version), executable=True,
            failure_phase="bootstrap",
        )
        self.release_update.sign(future.sign_arguments())
        install_root = self.fixture.root / "install"
        staging_root = install_root / "update-staging"
        staging_root.mkdir(parents=True, mode=0o700)
        prepared = self.release_update.prepare(argparse.Namespace(
            envelope=future.envelope_path, allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root, confirm=True,
        ))
        common_arguments = dict(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
        )
        self.release_update.rehearse(argparse.Namespace(**common_arguments, confirm=True))
        preview = self.release_update.activation_preview(argparse.Namespace(**common_arguments))
        current_release = install_root / "releases" / self.fixture.current_version
        current_release.mkdir(parents=True, mode=0o700)
        (current_release / "VERSION").write_text(f"{self.fixture.current_version}\n", encoding="ascii")
        (current_release / "VERSION").chmod(0o600)
        (install_root / "current").symlink_to(current_release, target_is_directory=True)
        openclaw_home = self.fixture.root / "openclaw"
        backups = openclaw_home / "backups"
        backups.mkdir(parents=True, mode=0o700)
        old_marker = backups / "last-apply"
        old_marker.write_text("/private/older-rollback\n", encoding="utf-8")
        old_marker.chmod(0o600)
        old_marker_bytes = old_marker.read_bytes()
        old_marker_stat = old_marker.stat()
        onboarding = self.fixture.root / "onboarding.json"
        onboarding.write_text("{}\n", encoding="utf-8")
        onboarding.chmod(0o600)
        trace = self.fixture.root / "activation-trace"
        controller = self.fixture.root / "controller"
        (controller / "scripts" / "lib").mkdir(parents=True, mode=0o700)
        for relative in ["activate-release-update.sh", "release-update.py"]:
            shutil.copy2(ROOT / "scripts" / relative, controller / "scripts" / relative)
        shutil.copy2(ROOT / "scripts" / "lib" / "common.sh", controller / "scripts" / "lib" / "common.sh")
        (controller / "VERSION").write_text(f"{self.fixture.current_version}\n", encoding="ascii")
        (controller / ".env").write_text("\n".join([
            f"PIXEL_INSTALL_DIR='{install_root}'", f"OPENCLAW_HOME='{openclaw_home}'",
            f"PIXEL_RELEASE_VERSION='{self.fixture.current_version}'",
            f"PIXEL_PRIVATE_ONBOARDING_PATH='{onboarding}'", f"PIXEL_ACTIVATION_TRACE='{trace}'", "",
        ]), encoding="utf-8")
        completed = run_in_pty([
            "bash", str(controller / "scripts" / "activate-release-update.sh"),
            "--candidate-id", prepared["candidateId"], "--allowed-signers", str(future.allowed_signers),
            "--identity", "pixel-release", "--activation-hash", preview["activationHash"], "--confirm",
        ])
        self.assertEqual(completed.returncode, 44)
        self.assertEqual(trace.read_text(encoding="utf-8").splitlines(), ["configure", "bootstrap"])
        self.assertEqual((install_root / "current" / "VERSION").read_text(encoding="ascii").strip(), self.fixture.current_version)
        result = json.loads((staging_root / "activations" / prepared["candidateId"] / "ACTIVATION-RESULT.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["executionPhase"], "bootstrap")
        self.assertFalse(result["activeDeploymentChanged"])
        self.assertFalse(result["rollbackAvailable"])
        self.assertIsNone(result["rollbackMarkerSha256"])
        self.assertEqual(old_marker.read_text(encoding="utf-8"), "/private/older-rollback\n")
        recovery_arguments = argparse.Namespace(
            **common_arguments,
            activation_hash=preview["activationHash"],
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=old_marker,
        )
        failed_recovery = self.release_update.recovery_preview(recovery_arguments)
        self.assertEqual(failed_recovery["state"], "activation-failed")
        self.assertEqual(failed_recovery["status"], "manual-review")
        self.assertIsNone(failed_recovery["safeAction"])
        self.assertIsNone(failed_recovery["recoveryHash"])
        result_path = staging_root / "activations" / prepared["candidateId"] / "ACTIVATION-RESULT.json"
        result_path.write_bytes(encoded({**result, "unexpected": True}))
        result_path.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "exact failed activation receipt"):
            self.release_update.recovery_preview(recovery_arguments)
        result_path.write_bytes(encoded(result))
        result_path.chmod(0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.claim_activation(argparse.Namespace(
                **common_arguments, activation_hash=preview["activationHash"], confirm=True,
            ))

        # The explicit existing cleanup transaction may archive a terminal activation
        # failure that provably made no live mutation. It preserves a hash-bound
        # tombstone and that tombstone permanently prevents candidate-ID reuse. An
        # unrelated rollback marker is validated as a trust anchor but not consumed.
        old_marker.chmod(0o622)
        with self.assertRaisesRegex(self.release_update.UpdateError, "permissions are unsafe"):
            self.release_update.cleanup_preview(recovery_arguments)
        old_marker.chmod(0o600)

        hardlink = backups / "last-apply-hardlink"
        os.link(old_marker, hardlink)
        with self.assertRaisesRegex(self.release_update.UpdateError, "single-link"):
            self.release_update.cleanup_preview(recovery_arguments)
        hardlink.unlink()

        old_marker.write_bytes(b"x" * 4097)
        with self.assertRaisesRegex(self.release_update.UpdateError, "bounded regular"):
            self.release_update.cleanup_preview(recovery_arguments)
        old_marker.write_bytes(old_marker_bytes)

        parked_marker = backups / "last-apply-parked"
        old_marker.rename(parked_marker)
        old_marker.symlink_to(parked_marker)
        with self.assertRaisesRegex(self.release_update.UpdateError, "unavailable or unsafe"):
            self.release_update.cleanup_preview(recovery_arguments)
        old_marker.unlink()
        parked_marker.rename(old_marker)

        old_marker.rename(parked_marker)
        os.mkfifo(old_marker, mode=0o600)
        with self.assertRaisesRegex(self.release_update.UpdateError, "bounded regular"):
            self.release_update.cleanup_preview(recovery_arguments)
        old_marker.unlink()
        parked_marker.rename(old_marker)

        cleanup_preview = self.release_update.cleanup_preview(recovery_arguments)
        self.assertEqual(cleanup_preview["status"], "ready")
        self.assertEqual(cleanup_preview["terminalOutcome"], "activation-failed")
        self.assertEqual(cleanup_preview["activeVersionAtCleanup"], self.fixture.current_version)
        self.assertIsNone(cleanup_preview["rollbackClaimSha256"])
        self.assertIsNone(cleanup_preview["rollbackResultSha256"])
        cleaned = self.release_update.cleanup_completed_update(argparse.Namespace(
            **vars(recovery_arguments), cleanup_hash=cleanup_preview["cleanupHash"], confirm=True,
        ))
        self.assertEqual(cleaned["status"], "cleaned")
        self.assertEqual(cleaned["terminalOutcome"], "activation-failed")
        self.assertFalse((staging_root / "candidates" / prepared["candidateId"]).exists())
        self.assertFalse((staging_root / "rehearsals" / prepared["candidateId"]).exists())
        self.assertFalse((staging_root / "activations" / prepared["candidateId"]).exists())
        preserved_marker_stat = old_marker.stat()
        self.assertEqual(old_marker.read_bytes(), old_marker_bytes)
        self.assertEqual(preserved_marker_stat.st_dev, old_marker_stat.st_dev)
        self.assertEqual(preserved_marker_stat.st_ino, old_marker_stat.st_ino)
        self.assertEqual(stat.S_IMODE(preserved_marker_stat.st_mode), stat.S_IMODE(old_marker_stat.st_mode))
        history = staging_root / "cleanup-history" / f"{prepared['candidateId']}.json"
        self.assertEqual(json.loads(history.read_text(encoding="utf-8"))["terminalOutcome"], "activation-failed")
        with self.assertRaisesRegex(self.release_update.UpdateError, "permanently cleaned"):
            self.release_update.prepare(argparse.Namespace(
                envelope=future.envelope_path, allowed_signers=future.allowed_signers,
                identity="pixel-release", staging_root=staging_root, confirm=True,
            ))

class QualificationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("ssh-keygen") is None:
            raise unittest.SkipTest("OpenSSH signing support is unavailable")
        cls.release_update = load_release_update()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.fixture = ReleaseFixture(self.temporary.name)
        self.signing_source = mock.patch.object(self.release_update, "validate_signing_source", autospec=True)
        self.signing_source_mock = self.signing_source.start()
        self.addCleanup(self.signing_source.stop)
        self.signing_archive = mock.patch.object(self.release_update, "validate_signing_archive", autospec=True)
        self.signing_archive_mock = self.signing_archive.start()
        self.addCleanup(self.signing_archive.stop)
        self.bundle_root = self.fixture.root / "qualification-bundle"
        self.bundle_root.mkdir(mode=0o700)
        self.candidate = ReleaseFixture(self.bundle_root, version=next_patch(self.fixture.current_version))
        self.candidate.set_compatibility_status("candidate")
        self.release_update.qualification_sign(self.candidate.sign_arguments())
        self.qualification_root = self.fixture.root / "qualification-root"
        self.production_root = self.fixture.root / "production-install"
        self.production_root.mkdir(mode=0o700)
        self.baseline = self.fixture.current_version

    def prepare_arguments(self, confirm=True):
        return argparse.Namespace(
            envelope=self.candidate.envelope_path,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
            confirm=confirm,
        )

    def common_arguments(self, candidate_id):
        return argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
        )

    def assert_qualification_authority(self, receipt):
        for field in (
            "publicationAuthority", "productionActivationAuthority",
            "compatibilityMutationAuthority", "productionStateChanged",
        ):
            self.assertIs(receipt[field], False)

    def test_qualification_prepare_rehearse_activation_positive(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        self.assertEqual(prepared["status"], "prepared")
        self.assertEqual(prepared["compatibilityStatus"], "candidate")
        self.assertEqual(prepared["baselineVersion"], self.baseline)
        self.assertEqual(prepared["relation"], "upgrade")
        self.assertEqual(
            prepared["signatureName"],
            f"pixel-{self.candidate.version}.update.json.qualification.sig",
        )
        self.assert_qualification_authority(prepared)
        self.assertFalse(prepared["candidateCodeExtracted"])
        self.assertFalse(prepared["candidateCodeExecuted"])
        attestation = self.qualification_root / "QUALIFICATION-ROOT.json"
        self.assertTrue(attestation.is_file())
        attestation_info = attestation.lstat()
        self.assertEqual(attestation_info.st_nlink, 1)
        self.assertEqual(stat.S_IMODE(attestation_info.st_mode), 0o600)
        candidate = self.qualification_root / "candidates" / prepared["candidateId"]
        expected_files = {
            "QUALIFICATION-STAGED.json",
            self.candidate.archive_path.name,
            self.candidate.sbom_path.name,
            self.candidate.provenance_path.name,
            self.candidate.envelope_path.name,
            f"pixel-{self.candidate.version}.update.json.qualification.sig",
        }
        self.assertEqual({path.name for path in candidate.iterdir()}, expected_files)
        repeated = self.release_update.qualification_prepare(self.prepare_arguments())
        self.assertEqual(repeated["status"], "already-prepared")
        self.assertEqual(repeated["candidateId"], prepared["candidateId"])

        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        rehearsed = self.release_update.qualification_rehearse(rehearsal_arguments)
        self.assertEqual(rehearsed["status"], "rehearsed")
        self.assertTrue(rehearsed["candidateCodeExtracted"])
        self.assertTrue(rehearsed["candidateCodeParsed"])
        self.assertFalse(rehearsed["candidateCodeExecuted"])
        self.assertFalse(rehearsed["activeDeploymentChanged"])
        self.assertFalse(rehearsed["networkUsed"])
        self.assert_qualification_authority(rehearsed)
        self.assertEqual(rehearsed["checks"]["shellFiles"], 1)
        self.assertEqual(rehearsed["checks"]["javascriptFiles"], 1)
        self.assertEqual(rehearsed["checks"]["pythonFiles"], 1)
        repeated_rehearsal = self.release_update.qualification_rehearse(rehearsal_arguments)
        self.assertEqual(repeated_rehearsal["status"], "already-rehearsed")

        preview_arguments = self.common_arguments(prepared["candidateId"])
        preview = self.release_update.qualification_activation_preview(preview_arguments)
        self.assertEqual(preview["status"], "ready")
        self.assertFalse(preview["candidateCodeWillExecute"])
        self.assertFalse(preview["activeDeploymentWillChange"])
        self.assertFalse(preview["networkMayBeUsed"])
        self.assertFalse(preview["candidateCodeExecuted"])
        self.assertFalse(preview["activeDeploymentChanged"])
        self.assert_qualification_authority(preview)
        self.assertNotIn(str(self.fixture.root), json.dumps(preview))

        wrong_claim = argparse.Namespace(
            **vars(preview_arguments), activation_hash="0" * 64, confirm=True,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the current verified preview"):
            self.release_update.qualification_claim_activation(wrong_claim)
        no_confirm = argparse.Namespace(
            **vars(preview_arguments), activation_hash=preview["activationHash"], confirm=False,
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.release_update.qualification_claim_activation(no_confirm)
        claim_arguments = argparse.Namespace(
            **vars(preview_arguments), activation_hash=preview["activationHash"], confirm=True,
        )
        claimed = self.release_update.qualification_claim_activation(claim_arguments)
        self.assertEqual(claimed["status"], "claimed")
        self.assertFalse(claimed["candidateCodeExecuted"])
        self.assertFalse(claimed["activeDeploymentChanged"])
        self.assertFalse(claimed["networkUsed"])
        self.assert_qualification_authority(claimed)
        activation = self.qualification_root / "activations" / prepared["candidateId"]
        self.assertTrue((activation / "QUALIFICATION-ACTIVATION.json").is_file())
        self.assertTrue((activation / "source").is_dir())
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.qualification_claim_activation(claim_arguments)
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.qualification_activation_preview(preview_arguments)

        production_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
        self.assertEqual(production_version, self.fixture.current_version)
        self.assertEqual(list(self.production_root.iterdir()), [])

    def test_qualification_requires_confirm_and_exact_forward_upgrade(self):
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.release_update.qualification_prepare(self.prepare_arguments(confirm=False))
        same_version_root = self.fixture.root / "same-bundle"
        same_version_root.mkdir(mode=0o700)
        same_version = ReleaseFixture(same_version_root, version=self.fixture.current_version)
        same_version.set_compatibility_status("candidate")
        self.release_update.qualification_sign(same_version.sign_arguments())
        with self.assertRaisesRegex(self.release_update.UpdateError, "exact forward qualification candidate"):
            self.release_update.qualification_prepare(argparse.Namespace(
                envelope=same_version.envelope_path,
                allowed_signers=same_version.allowed_signers,
                identity="pixel-release",
                qualification_root=self.qualification_root,
                baseline_version=self.baseline,
                production_install_root=self.production_root,
                confirm=True,
            ))

    def test_qualification_rejects_wrong_identity_and_supported_status(self):
        intruder = self.prepare_arguments()
        intruder.identity = "intruder"
        with self.assertRaisesRegex(self.release_update.UpdateError, "signature is invalid"):
            self.release_update.qualification_prepare(intruder)
        supported_root = self.fixture.root / "supported-bundle"
        supported_root.mkdir(mode=0o700)
        supported = ReleaseFixture(supported_root, version=next_patch(self.fixture.current_version, 2))
        with self.assertRaisesRegex(self.release_update.UpdateError, "candidate compatibility record"):
            self.release_update.qualification_prepare(argparse.Namespace(
                envelope=supported.envelope_path,
                allowed_signers=supported.allowed_signers,
                identity="pixel-release",
                qualification_root=self.qualification_root,
                baseline_version=self.baseline,
                production_install_root=self.production_root,
                confirm=True,
            ))

    def test_qualification_parser_requires_production_install_root(self):
        parser = self.release_update.parser()
        zero_candidate = "pixel-1.0.0-" + "0" * 64
        commands = {
            "qualification-prepare": [
                "--envelope", str(self.candidate.envelope_path),
                "--allowed-signers", str(self.candidate.allowed_signers),
                "--identity", "pixel-release",
                "--qualification-root", str(self.qualification_root),
                "--baseline-version", self.baseline,
                "--confirm",
            ],
            "qualification-rehearse": [
                "--candidate-id", zero_candidate,
                "--allowed-signers", str(self.candidate.allowed_signers),
                "--identity", "pixel-release",
                "--qualification-root", str(self.qualification_root),
                "--baseline-version", self.baseline,
                "--confirm",
            ],
            "qualification-activation-preview": [
                "--candidate-id", zero_candidate,
                "--allowed-signers", str(self.candidate.allowed_signers),
                "--identity", "pixel-release",
                "--qualification-root", str(self.qualification_root),
                "--baseline-version", self.baseline,
            ],
            "qualification-activation-claim": [
                "--candidate-id", zero_candidate,
                "--allowed-signers", str(self.candidate.allowed_signers),
                "--identity", "pixel-release",
                "--qualification-root", str(self.qualification_root),
                "--baseline-version", self.baseline,
                "--activation-hash", "0" * 64,
                "--confirm",
            ],
            "qualification-host-run": [
                "--candidate-id", zero_candidate,
                "--allowed-signers", str(self.candidate.allowed_signers),
                "--identity", "pixel-release",
                "--qualification-root", str(self.qualification_root),
                "--baseline-version", self.baseline,
                "--confirm",
            ],
        }
        for command, arguments in commands.items():
            with self.subTest(command=command):
                with self.assertRaises(SystemExit):
                    parser.parse_args([command, *arguments])

    def test_qualification_rejects_production_overlap(self):
        overlapping = self.prepare_arguments()
        overlapping.production_install_root = self.fixture.root
        with self.assertRaisesRegex(self.release_update.UpdateError, "must not equal, contain, or be contained by the production install root"):
            self.release_update.qualification_prepare(overlapping)

    def test_qualification_rejects_unknown_top_level_on_idempotent_reuse(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        (self.qualification_root / "unexpected").write_text("state\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "contains an unexpected entry"):
            self.release_update.qualification_prepare(self.prepare_arguments())

    def test_qualification_rejects_symlinked_top_level_state(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        candidates = self.qualification_root / "candidates"
        os.symlink(candidates, self.qualification_root / "rehearsals")
        with self.assertRaisesRegex(self.release_update.UpdateError, "must not contain symbolic links"):
            self.release_update.qualification_prepare(self.prepare_arguments())

    def test_qualification_rejects_special_top_level_state(self):
        self.release_update.qualification_prepare(self.prepare_arguments())
        os.mkfifo(self.qualification_root / "activations")
        with self.assertRaisesRegex(self.release_update.UpdateError, "not a real directory"):
            self.release_update.qualification_prepare(self.prepare_arguments())

    def test_qualification_rejects_tampered_or_non_timestamp_attestation(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        attestation = self.qualification_root / "QUALIFICATION-ROOT.json"
        value = json.loads(attestation.read_text(encoding="utf-8"))
        value["createdAt"] = "not-a-timestamp"
        attestation.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(self.release_update.UpdateError, "timestamp is invalid"):
            self.release_update.qualification_rehearse(argparse.Namespace(
                **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
            ))
        value["createdAt"] = "2026-01-01T00:00:00Z"
        value["candidateVersion"] = "9.9.9"
        attestation.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the verified bundle"):
            self.release_update.qualification_rehearse(argparse.Namespace(
                **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
            ))

    def test_qualification_rejects_production_loaders_with_copied_qualification_signature(self):
        self.release_update.qualification_prepare(self.prepare_arguments())
        copied = Path(f"{self.candidate.envelope_path}.sig")
        shutil.copyfile(Path(f"{self.candidate.envelope_path}.qualification.sig"), copied)
        with self.assertRaisesRegex(self.release_update.UpdateError, "supported compatibility record"):
            self.release_update.load_signed_bundle(
                self.candidate.envelope_path, self.candidate.allowed_signers, "pixel-release",
            )
        with self.assertRaisesRegex(self.release_update.UpdateError, "supported compatibility record"):
            self.release_update.prepare(argparse.Namespace(
                envelope=self.candidate.envelope_path,
                allowed_signers=self.candidate.allowed_signers,
                identity="pixel-release",
                staging_root=self.fixture.root / "prod-staging",
                confirm=True,
            ))

    def test_qualification_rejects_wrong_signature_namespace(self):
        envelope_bytes = self.candidate.envelope_path.read_bytes()
        trust = self.candidate.allowed_signers.read_bytes()
        qualification_signature = Path(f"{self.candidate.envelope_path}.qualification.sig").read_bytes()
        with self.assertRaisesRegex(self.release_update.UpdateError, "signature is invalid"):
            self.release_update.verify_signature(
                envelope_bytes, qualification_signature, trust, "pixel-release",
                namespace=self.release_update.NAMESPACE,
            )
        self.release_update.verify_signature(
            envelope_bytes, qualification_signature, trust, "pixel-release",
            namespace=self.release_update.QUALIFICATION_NAMESPACE,
        )

    def test_qualification_rejects_invalid_activation_baseline(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        self.release_update.qualification_rehearse(rehearsal_arguments)
        preview_arguments = self.common_arguments(prepared["candidateId"])
        preview_arguments.baseline_version = "not-a-version"
        with self.assertRaisesRegex(self.release_update.UpdateError, "baseline version is not a semantic version"):
            self.release_update.qualification_activation_preview(preview_arguments)
        claim_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])),
            activation_hash="0" * 64, confirm=True,
        )
        claim_arguments.baseline_version = "not-a-version"
        with self.assertRaisesRegex(self.release_update.UpdateError, "baseline version is not a semantic version"):
            self.release_update.qualification_claim_activation(claim_arguments)

    def test_qualification_rejects_tampered_rehearsal_source_at_preview(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        self.release_update.qualification_rehearse(rehearsal_arguments)
        rehearsal = self.qualification_root / "rehearsals" / prepared["candidateId"]
        target = next(
            path for path in (rehearsal / "source").rglob("*")
            if path.is_file() and path.suffix == ".py"
        )
        target.write_bytes(target.read_bytes() + b"\n\n")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the revalidated candidate"):
            self.release_update.qualification_activation_preview(self.common_arguments(prepared["candidateId"]))

    def test_qualification_rejects_missing_or_symlinked_attestation(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        attestation = self.qualification_root / "QUALIFICATION-ROOT.json"
        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        self.release_update.qualification_rehearse(rehearsal_arguments)
        attestation.unlink()
        with self.assertRaisesRegex(self.release_update.UpdateError, "attestation is unavailable or unsafe"):
            self.release_update.qualification_rehearse(rehearsal_arguments)
        os.symlink(self.candidate.envelope_path, attestation)
        with self.assertRaisesRegex(self.release_update.UpdateError, "attestation is unavailable or unsafe"):
            self.release_update.qualification_rehearse(rehearsal_arguments)

    def test_qualification_rejects_tampered_staged_candidate_and_production_loaders(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        candidate = self.qualification_root / "candidates" / prepared["candidateId"]
        staged_archive = candidate / self.candidate.archive_path.name
        staged_archive.write_bytes(staged_archive.read_bytes() + b"tamper")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the verified bundle"):
            self.release_update.qualification_prepare(self.prepare_arguments())
        with self.assertRaisesRegex(self.release_update.UpdateError, "supported compatibility record"):
            self.release_update.load_signed_bundle(
                self.candidate.envelope_path, self.candidate.allowed_signers, "pixel-release",
            )
        with self.assertRaisesRegex(self.release_update.UpdateError, "supported compatibility record"):
            self.release_update.prepare(argparse.Namespace(
                envelope=self.candidate.envelope_path,
                allowed_signers=self.candidate.allowed_signers,
                identity="pixel-release",
                staging_root=self.fixture.root / "prod-staging",
                confirm=True,
            ))

    def test_qualification_rejects_hardlink_symlink_and_traversal_entries(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        candidate = self.qualification_root / "candidates" / prepared["candidateId"]
        envelope_name = f"pixel-{self.candidate.version}.update.json"
        os.link(candidate / envelope_name, candidate / "hardlink.json")
        with self.assertRaisesRegex(self.release_update.UpdateError, "file set is invalid"):
            self.release_update.qualification_prepare(self.prepare_arguments())
        (candidate / "hardlink.json").unlink()
        os.symlink(candidate / envelope_name, candidate / "symlink.json")
        with self.assertRaisesRegex(self.release_update.UpdateError, "file set is invalid"):
            self.release_update.qualification_prepare(self.prepare_arguments())
        (candidate / "symlink.json").unlink()
        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        self.release_update.qualification_rehearse(rehearsal_arguments)
        rehearsal = self.qualification_root / "rehearsals" / prepared["candidateId"]
        os.symlink(candidate / envelope_name, rehearsal / "source" / "escape")
        with self.assertRaisesRegex(self.release_update.UpdateError, "rehearsal source file is unavailable or unsafe"):
            self.release_update.qualification_rehearse(rehearsal_arguments)


class QualificationHostRunTests(QualificationLifecycleTests):
    def claim_activation(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        self.release_update.qualification_rehearse(rehearsal_arguments)
        preview = self.release_update.qualification_activation_preview(
            self.common_arguments(prepared["candidateId"]),
        )
        claim_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])),
            activation_hash=preview["activationHash"], confirm=True,
        )
        self.release_update.qualification_claim_activation(claim_arguments)
        return prepared["candidateId"], preview["activationHash"]

    def run_arguments(self, candidate_id, confirm=True, probe="scripts/safe.sh", probe_timeout=30.0):
        return argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
            probe=probe,
            probe_timeout=probe_timeout,
            confirm=confirm,
        )

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def assert_private_single_link(self, path):
        info = path.lstat()
        self.assertEqual(info.st_nlink, 1)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

    def assert_schema_shape(self, artifact, filename):
        schema = json.loads(
            (ROOT / "schemas" / filename).read_text(encoding="utf-8"),
        )
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["required"]), set(artifact))
        self.assertEqual(set(schema["properties"]), set(artifact))

    def test_host_run_positive_and_exactly_once(self):
        candidate_id, activation_hash = self.claim_activation()
        result = self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        self.assertEqual(result["status"], "acquired")
        self.assertFalse(result["candidateCodeExecuted"])
        self.assertFalse(result["terminalPromotionEvidence"])
        self.assert_qualification_authority(result)
        run_dir = self.qualification_root / "runs" / candidate_id
        self.assertEqual(
            {path.name for path in run_dir.iterdir()},
            {"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json"},
        )
        harness = self.read_json(run_dir / "HARNESS-RUN.json")
        tombstone = self.read_json(run_dir / "EXECUTION-TOMBSTONE.json")
        self.assert_private_single_link(run_dir / "HARNESS-RUN.json")
        self.assert_private_single_link(run_dir / "EXECUTION-TOMBSTONE.json")
        self.assert_schema_shape(
            harness, "release-update-qualification-harness-run-v1.schema.json",
        )
        self.assert_schema_shape(
            tombstone, "release-update-qualification-execution-tombstone-v1.schema.json",
        )
        for artifact in (harness, tombstone):
            self.assertEqual(artifact["candidateId"], candidate_id)
            self.assertEqual(artifact["activationHash"], activation_hash)
            self.assertEqual(artifact["version"], self.candidate.version)
            self.assertEqual(artifact["sourceTree"], self.candidate.source_tree)
            self.assertEqual(artifact["sourceCommit"], self.candidate.source_commit)
            self.assertFalse(artifact["candidateCodeExecuted"])
            self.assertFalse(artifact["terminalPromotionEvidence"])
            self.assertFalse(artifact["productionStateChanged"])
            self.assert_qualification_authority(artifact)
        self.assertEqual(harness["rehashedTreeSha256"], harness["extractedTreeSha256"])
        self.assertFalse(tombstone["executionObserved"])
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been acquired"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))

    def test_host_run_marker_private_and_binds_tombstone(self):
        candidate_id, activation_hash = self.claim_activation()
        self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        activation = self.qualification_root / "activations" / candidate_id
        marker_path = activation / self.release_update.QUALIFICATION_ACQUISITION_FILE
        self.assertTrue(marker_path.is_file())
        self.assert_private_single_link(marker_path)
        self.assertEqual(
            {path.name for path in activation.iterdir()},
            {"source", "QUALIFICATION-ACTIVATION.json", "QUALIFICATION-ACQUISITION.json"},
        )
        marker = self.read_json(marker_path)
        claim = self.read_json(activation / "QUALIFICATION-ACTIVATION.json")
        run_dir = self.qualification_root / "runs" / candidate_id
        self.assert_schema_shape(
            marker, "release-update-qualification-acquisition-v1.schema.json",
        )
        harness = self.read_json(run_dir / "HARNESS-RUN.json")
        tombstone = self.read_json(run_dir / "EXECUTION-TOMBSTONE.json")
        for artifact in (harness, tombstone):
            self.assertEqual(marker["candidateId"], artifact["candidateId"])
            self.assertEqual(marker["activationHash"], artifact["activationHash"])
            self.assertEqual(marker["claimedAt"], artifact["claimedAt"])
            self.assertEqual(marker["extractedTreeSha256"], artifact["extractedTreeSha256"])
            self.assertEqual(marker["rehashedTreeSha256"], artifact["rehashedTreeSha256"])
        self.assertEqual(marker["activationHash"], activation_hash)
        self.assertEqual(
            marker["activationReceiptSha256"],
            self.release_update.sha256(self.release_update.canonical_json(claim)),
        )
        self.assertEqual(
            marker["executionTombstoneSha256"],
            self.release_update.sha256(self.release_update.canonical_json(tombstone)),
        )
        self.assertEqual(
            marker["harnessRunSha256"],
            self.release_update.sha256(self.release_update.canonical_json(harness)),
        )
        self.assertFalse(marker["candidateCodeExecuted"])
        self.assertFalse(marker["terminalPromotionEvidence"])
        self.assertFalse(marker["productionStateChanged"])
        self.assert_qualification_authority(marker)

    def test_host_run_deleting_run_dir_does_not_permit_replay(self):
        candidate_id, _ = self.claim_activation()
        self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        run_dir = self.qualification_root / "runs" / candidate_id
        self.assertTrue(run_dir.is_dir())
        shutil.rmtree(run_dir)
        self.assertFalse(run_dir.exists())
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been acquired or requires recovery"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))

    def test_host_run_failure_after_marker_is_fail_closed(self):
        candidate_id, _ = self.claim_activation()
        run_dir = self.qualification_root / "runs" / candidate_id
        with mock.patch.object(
            self.release_update, "rename_directory_noreplace", autospec=True,
            side_effect=self.release_update.UpdateError("injected final publication failure"),
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "injected final publication failure"):
                self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        self.assertFalse(run_dir.exists())
        activation = self.qualification_root / "activations" / candidate_id
        marker_path = activation / self.release_update.QUALIFICATION_ACQUISITION_FILE
        self.assertTrue(marker_path.is_file())
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been acquired or requires recovery"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))

    def test_host_run_blocks_reclaim_and_preview_after_consume(self):
        candidate_id, activation_hash = self.claim_activation()
        self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been acquired"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.qualification_claim_activation(argparse.Namespace(
                **vars(self.common_arguments(candidate_id)),
                activation_hash=activation_hash, confirm=True,
            ))
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.release_update.qualification_activation_preview(
                self.common_arguments(candidate_id),
            )

    def test_host_run_requires_confirm_and_claimed_activation(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        self.release_update.qualification_rehearse(rehearsal_arguments)
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.release_update.qualification_host_run(
                self.run_arguments(prepared["candidateId"], confirm=False),
            )
        with self.assertRaisesRegex(self.release_update.UpdateError, "qualification activation"):
            self.release_update.qualification_host_run(
                self.run_arguments(prepared["candidateId"]),
            )

    def test_host_run_blocks_concurrent_operation(self):
        candidate_id, _ = self.claim_activation()
        with self.release_update.exclusive_stage_lock(self.qualification_root):
            with self.assertRaisesRegex(self.release_update.UpdateError, "another release staging operation"):
                self.release_update.qualification_host_run(self.run_arguments(candidate_id))

    def test_host_run_rejects_tampered_activation_source(self):
        candidate_id, _ = self.claim_activation()
        activation = self.qualification_root / "activations" / candidate_id
        target = next(
            path for path in (activation / "source").rglob("*")
            if path.is_file() and path.suffix == ".py"
        )
        target.write_bytes(target.read_bytes() + b"\n\n")
        with self.assertRaisesRegex(self.release_update.UpdateError, "staged qualification source tree differs"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))

    def test_host_run_rejects_unexpected_runs_entry(self):
        candidate_id, _ = self.claim_activation()
        runs = self.qualification_root / "runs"
        runs.mkdir(mode=0o700)
        (runs / "unexpected").write_text("state\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "contains an unrecognized entry"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))

    def test_host_run_rejects_production_overlap(self):
        candidate_id, _ = self.claim_activation()
        arguments = self.run_arguments(candidate_id)
        arguments.production_install_root = self.fixture.root
        with self.assertRaisesRegex(
            self.release_update.UpdateError,
            "must not equal, contain, or be contained by the production install root",
        ):
            self.release_update.qualification_host_run(arguments)


class QualificationExecutionTests(QualificationHostRunTests):
    def acquire_run(self):
        candidate_id, activation_hash = self.claim_activation()
        self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        return candidate_id, activation_hash

    def claim_execution(self, candidate_id, probe="scripts/safe.sh", probe_timeout=30.0):
        return self.release_update.qualification_execution_claim(
            self.run_arguments(candidate_id, probe=probe, probe_timeout=probe_timeout),
        )

    def observation(self, candidate_id, **overrides):
        run_dir = self.qualification_root / "runs" / candidate_id
        spec_path = run_dir / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        if spec_path.exists():
            spec = self.read_json(spec_path)
            spec_sha = self.release_update.sha256(spec_path.read_bytes())
            runtime = spec["runtime"]
            sandbox = {
                "image": runtime["image"],
                "imageDigest": runtime["imageDigest"],
                "containerName": runtime["containerName"],
                "network": spec["network"],
                "capDropAll": spec["capDropAll"],
                "noNewPrivileges": spec["noNewPrivileges"],
                "readOnlyRoot": spec["readOnlyRoot"],
                "uid": runtime["uid"],
                "gid": runtime["gid"],
            }
        else:
            spec_sha = "ab" * 32
            sandbox = {
                "image": "debian:bookworm-slim@sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
                "imageDigest": "sha256:7b140f374b289a7c2befc338f42ebe6441b7ea838a042bbd5acbfca6ec875818",
                "containerName": "pixel-qual-exec-" + ("cd" * 10),
                "network": "none",
                "capDropAll": True,
                "noNewPrivileges": True,
                "readOnlyRoot": True,
                "uid": 1,
                "gid": 1,
            }
        base = {
            "schemaVersion": 1,
            "operation": "pixel-release-qualification-execution-observation",
            "candidateId": candidate_id,
            "outcome": "deferred",
            "phase": "install",
            "reason": "install-contract-deferred",
            "exitCode": None,
            "signal": None,
            "timedOut": False,
            "observedVersion": None,
            "candidateCodeExecuted": True,
            "executionObserved": True,
            "executionSpecSha256": spec_sha,
            "sandbox": sandbox,
            "containerRemoved": True,
            "absenceProven": True,
            "networkUsed": False,
            "terminalPromotionEvidence": False,
            "publicationAuthority": False,
            "productionActivationAuthority": False,
            "compatibilityMutationAuthority": False,
            "productionStateChanged": False,
            "boundary": self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_BOUNDARY,
        }
        base.update(overrides)
        return base

    def write_start_marker(self, candidate_id):
        run_dir = self.qualification_root / "runs" / candidate_id
        self.release_update.ensure_private_directory(run_dir, "qualification host run", create=True)
        path = run_dir / self.release_update.QUALIFICATION_EXECUTION_START_FILE
        if not path.exists():
            claim_bytes = self.claim_path(candidate_id).read_bytes()
            spec_path = run_dir / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
            spec_bytes = spec_path.read_bytes()
            spec = json.loads(spec_bytes)
            start = self.release_update.qualification_execution_start_value(
                {"candidate_id": candidate_id}, claim_bytes, spec_bytes, spec, "ab" * 16,
            )
            path.write_text(
                self.release_update.canonical_json(start).decode("utf-8"), encoding="utf-8",
            )
            path.chmod(0o600)
        return path

    def write_observation(self, observation, candidate_id):
        run_dir = self.qualification_root / "runs" / candidate_id
        self.release_update.ensure_private_directory(run_dir, "qualification host run", create=True)
        self.write_start_marker(candidate_id)
        path = run_dir / self.release_update.QUALIFICATION_EXECUTION_OBSERVATION_FILE
        path.write_text(self.release_update.canonical_json(observation).decode("utf-8"), encoding="utf-8")
        path.chmod(0o600)
        return path

    def result_arguments(self, candidate_id, observation_path, confirm=True):
        return argparse.Namespace(
            candidate_id=candidate_id,
            allowed_signers=self.candidate.allowed_signers,
            identity="pixel-release",
            qualification_root=self.qualification_root,
            baseline_version=self.baseline,
            production_install_root=self.production_root,
            observation=observation_path,
            confirm=confirm,
        )

    def record_result(self, candidate_id, observation):
        observation_path = self.write_observation(observation, candidate_id)
        return self.release_update.qualification_execution_result(
            self.result_arguments(candidate_id, observation_path),
        )

    def claim_path(self, candidate_id):
        return self.qualification_root / "activations" / candidate_id / self.release_update.QUALIFICATION_EXECUTION_CLAIM_FILE

    def result_path(self, candidate_id):
        return self.qualification_root / "runs" / candidate_id / self.release_update.QUALIFICATION_EXECUTION_RESULT_FILE

    def result_marker_path(self, candidate_id):
        return self.qualification_root / "activations" / candidate_id / self.release_update.QUALIFICATION_EXECUTION_RESULT_MARKER

    def test_execution_claim_positive_and_exactly_once(self):
        candidate_id, activation_hash = self.acquire_run()
        result = self.claim_execution(candidate_id)
        self.assertEqual(result["status"], "executing")
        self.assertEqual(result["candidateId"], candidate_id)
        self.assertEqual(result["activationHash"], activation_hash)
        self.assertFalse(result["candidateCodeExecuted"])
        self.assertFalse(result["executionObserved"])
        self.assertFalse(result["terminalPromotionEvidence"])
        self.assert_qualification_authority(result)
        claim_path = self.claim_path(candidate_id)
        self.assertTrue(claim_path.is_file())
        self.assert_private_single_link(claim_path)
        claim = self.read_json(claim_path)
        self.assert_schema_shape(
            claim, "release-update-qualification-execution-claim-v1.schema.json",
        )
        self.assertEqual(claim["status"], "executing")
        self.assertEqual(claim["candidateId"], candidate_id)
        self.assertEqual(claim["activationHash"], activation_hash)
        self.assertEqual(claim["version"], self.candidate.version)
        self.assertFalse(claim["candidateCodeExecuted"])
        self.assertTrue(claim["candidateCodeWillExecute"])
        self.assertFalse(claim["executionObserved"])
        self.assert_qualification_authority(claim)
        self.assertEqual(
            {path.name for path in (self.qualification_root / "activations" / candidate_id).iterdir()},
            {"source", "QUALIFICATION-ACTIVATION.json", "QUALIFICATION-ACQUISITION.json", "QUALIFICATION-EXECUTION-CLAIM.json"},
        )
        self.assertEqual(
            {path.name for path in (self.qualification_root / "runs" / candidate_id).iterdir()},
            {"HARNESS-RUN.json", "EXECUTION-TOMBSTONE.json", self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE},
        )
        with self.assertRaisesRegex(
            self.release_update.UpdateError,
            "automatic re-execution is forbidden",
        ):
            self.claim_execution(candidate_id)

    def test_execution_claim_binds_all_durable_hashes(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        claim = self.read_json(self.claim_path(candidate_id))
        marker = self.read_json(
            self.qualification_root / "activations" / candidate_id / "QUALIFICATION-ACQUISITION.json",
        )
        harness = self.read_json(self.qualification_root / "runs" / candidate_id / "HARNESS-RUN.json")
        tombstone = self.read_json(self.qualification_root / "runs" / candidate_id / "EXECUTION-TOMBSTONE.json")
        self.assertEqual(
            claim["acquisitionMarkerSha256"],
            self.release_update.sha256(
                self.release_update.canonical_json(marker),
            ),
        )
        self.assertEqual(
            claim["harnessRunSha256"],
            self.release_update.sha256(self.release_update.canonical_json(harness)),
        )
        self.assertEqual(
            claim["executionTombstoneSha256"],
            self.release_update.sha256(self.release_update.canonical_json(tombstone)),
        )
        self.assertEqual(claim["host"], harness["host"])
        self.assertEqual(claim["rehashedTreeSha256"], harness["rehashedTreeSha256"])
        self.assertEqual(claim["qualificationRootAttestationSha256"], harness["qualificationRootAttestationSha256"])
        self.assertEqual(claim["activationReceiptSha256"], harness["activationReceiptSha256"])

    def test_execution_claim_requires_confirm_and_acquired_run(self):
        prepared = self.release_update.qualification_prepare(self.prepare_arguments())
        rehearsal_arguments = argparse.Namespace(
            **vars(self.common_arguments(prepared["candidateId"])), confirm=True,
        )
        self.release_update.qualification_rehearse(rehearsal_arguments)
        candidate_id = prepared["candidateId"]
        with self.assertRaisesRegex(self.release_update.UpdateError, "requires --confirm"):
            self.release_update.qualification_execution_claim(
                self.run_arguments(candidate_id, confirm=False),
            )
        with self.assertRaisesRegex(self.release_update.UpdateError, "qualification activation"):
            self.release_update.qualification_execution_claim(
                self.run_arguments(candidate_id),
            )

    def test_execution_claim_rejects_tampered_source_and_host(self):
        candidate_id, _ = self.acquire_run()
        activation = self.qualification_root / "activations" / candidate_id
        target = next(
            path for path in (activation / "source").rglob("*")
            if path.is_file() and path.suffix == ".py"
        )
        target.write_bytes(target.read_bytes() + b"\n\n")
        with self.assertRaisesRegex(self.release_update.UpdateError, "staged qualification source tree differs"):
            self.claim_execution(candidate_id)

    def test_execution_claim_rejects_host_mismatch(self):
        candidate_id, _ = self.acquire_run()
        harness_path = self.qualification_root / "runs" / candidate_id / "HARNESS-RUN.json"
        harness = self.read_json(harness_path)
        original_host = harness["host"]
        wrong_host = dict(original_host)
        if original_host.get("id") == "debian":
            wrong_host.update({"id": "ubuntu", "versionId": "24.04", "label": "Ubuntu 24.04 LTS"})
        else:
            wrong_host.update({"id": "debian", "versionId": "12", "label": "Debian 12"})
        harness["host"] = wrong_host
        harness_path.write_text(self.release_update.canonical_json(harness).decode("utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(
            self.release_update.UpdateError,
            "differs from the acquisition marker",
        ):
            self.claim_execution(candidate_id)

    def test_execution_claim_rejects_unexpected_activation_and_run_entries(self):
        candidate_id, _ = self.acquire_run()
        activation = self.qualification_root / "activations" / candidate_id
        (activation / "unexpected").write_text("state\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "activation file set is invalid"):
            self.claim_execution(candidate_id)
        (activation / "unexpected").unlink()
        runs = self.qualification_root / "runs"
        (runs / "unexpected").write_text("state\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "run directory contains an unrecognized entry"):
            self.claim_execution(candidate_id)
        (runs / "unexpected").unlink()
        run_dir = runs / candidate_id
        (run_dir / "QUALIFICATION-EXECUTION-CLAIM.json").write_text("stale\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "run file set is invalid"):
            self.claim_execution(candidate_id)

    def test_execution_claim_rejects_tampered_harness_tombstone_and_marker(self):
        candidate_id, _ = self.acquire_run()
        tombstone_path = self.qualification_root / "runs" / candidate_id / "EXECUTION-TOMBSTONE.json"
        tombstone = self.read_json(tombstone_path)
        tombstone["executionObserved"] = True
        tombstone_path.write_text(self.release_update.canonical_json(tombstone).decode("utf-8"), encoding="utf-8")
        with self.assertRaisesRegex(self.release_update.UpdateError, "differs from the acquisition marker"):
            self.claim_execution(candidate_id)

    def test_execution_claim_rejects_hardlink_symlink_and_owner_mode_hazards(self):
        candidate_id, _ = self.acquire_run()
        run_dir = self.qualification_root / "runs" / candidate_id
        harness_path = run_dir / "HARNESS-RUN.json"
        outside = Path(self.temporary.name) / "outside-hardlink-probe"
        os.link(harness_path, outside)
        with self.assertRaisesRegex(self.release_update.UpdateError, "single-link"):
            self.claim_execution(candidate_id)

    def test_execution_claim_rejects_symlinked_claim_destination(self):
        candidate_id, _ = self.acquire_run()
        activation = self.qualification_root / "activations" / candidate_id
        os.symlink(
            activation / "source",
            activation / "QUALIFICATION-EXECUTION-CLAIM.json",
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been claimed"):
            self.claim_execution(candidate_id)

    def test_execution_claim_blocks_concurrent_operation(self):
        candidate_id, _ = self.acquire_run()
        with self.release_update.exclusive_stage_lock(self.qualification_root):
            with self.assertRaisesRegex(self.release_update.UpdateError, "another release staging operation"):
                self.claim_execution(candidate_id)

    def test_execution_claim_rejects_production_overlap(self):
        candidate_id, _ = self.acquire_run()
        arguments = self.run_arguments(candidate_id)
        arguments.production_install_root = self.fixture.root
        with self.assertRaisesRegex(
            self.release_update.UpdateError,
            "must not equal, contain, or be contained by the production install root",
        ):
            self.release_update.qualification_execution_claim(arguments)

    def test_execution_result_positive_deferred_and_exactly_once(self):
        candidate_id, activation_hash = self.acquire_run()
        self.claim_execution(candidate_id)
        recorded = self.record_result(candidate_id, self.observation(candidate_id))
        self.assertEqual(recorded["status"], "recorded")
        self.assertEqual(recorded["outcome"], "deferred")
        self.assertFalse(recorded["success"])
        self.assertTrue(recorded["candidateCodeExecuted"])
        self.assertTrue(recorded["executionObserved"])
        self.assert_qualification_authority(recorded)
        result_path = self.result_path(candidate_id)
        marker_path = self.result_marker_path(candidate_id)
        self.assertTrue(result_path.is_file())
        self.assertTrue(marker_path.is_file())
        self.assert_private_single_link(result_path)
        self.assert_private_single_link(marker_path)
        result = self.read_json(result_path)
        marker = self.read_json(marker_path)
        self.assert_schema_shape(
            result, "release-update-qualification-execution-result-v1.schema.json",
        )
        self.assert_schema_shape(
            marker, "release-update-qualification-execution-result-marker-v1.schema.json",
        )
        self.assertEqual(result["candidateId"], candidate_id)
        self.assertEqual(result["activationHash"], activation_hash)
        self.assertEqual(result["outcome"], "deferred")
        self.assertEqual(result["phase"], "install")
        self.assertEqual(result["reason"], "install-contract-deferred")
        self.assertFalse(result["success"])
        self.assertTrue(result["candidateCodeExecuted"])
        self.assertTrue(result["executionObserved"])
        self.assertFalse(result["terminalPromotionEvidence"])
        self.assert_qualification_authority(result)
        self.assertEqual(
            marker["executionResultSha256"],
            self.release_update.sha256(self.release_update.canonical_json(result)),
        )
        self.assert_qualification_authority(marker)
        with self.assertRaisesRegex(self.release_update.UpdateError, "replay is forbidden"):
            self.record_result(candidate_id, self.observation(candidate_id))

    def test_execution_result_success_requires_exact_version_and_exit_zero(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        recorded = self.record_result(candidate_id, self.observation(
            candidate_id,
            outcome="success", phase=None, reason=None, exitCode=0,
            observedVersion=self.candidate.version,
        ))
        self.assertTrue(recorded["success"])
        result = self.read_json(self.result_path(candidate_id))
        self.assertEqual(result["outcome"], "success")
        self.assertTrue(result["success"])
        self.assertEqual(result["observedVersion"], self.candidate.version)

    def test_execution_result_rejects_fabricated_success(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        bad_observations = [
            dict(self.observation(candidate_id, outcome="success", exitCode=0,
                                  observedVersion=self.candidate.version), phase="install"),
            dict(self.observation(candidate_id, outcome="success", exitCode=0,
                                  observedVersion=self.candidate.version), timedOut=True),
            dict(self.observation(candidate_id, outcome="success", exitCode=1,
                                  observedVersion=self.candidate.version)),
            dict(self.observation(candidate_id, outcome="success", exitCode=0,
                                  observedVersion="9.9.9")),
            dict(self.observation(candidate_id, outcome="timeout", timedOut=True), exitCode=0),
            dict(self.observation(candidate_id, outcome="signal", signal="SIGKILL"), exitCode=0),
        ]
        for observation in bad_observations:
            with self.assertRaisesRegex(self.release_update.UpdateError, "success|timeout|signal|version"):
                self.record_result(candidate_id, observation)
        self.assertFalse(self.result_path(candidate_id).exists())

    def test_execution_result_failure_and_timeout_and_signal_shape(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        failure = self.record_result(candidate_id, self.observation(
            candidate_id, outcome="failure", phase="install", reason="non-zero-exit", exitCode=41,
        ))
        self.assertFalse(failure["success"])
        result = self.read_json(self.result_path(candidate_id))
        self.assertEqual(result["outcome"], "failure")
        self.assertEqual(result["phase"], "install")
        self.assertEqual(result["reason"], "non-zero-exit")
        self.assertEqual(result["exitCode"], 41)
        self.assertFalse(result["success"])

    def test_execution_result_timeout_and_signal_never_become_success(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        timeout = self.record_result(candidate_id, self.observation(
            candidate_id, outcome="timeout", phase="install", timedOut=True,
        ))
        self.assertFalse(timeout["success"])
        self.assertEqual(timeout["outcome"], "timeout")
        result = self.read_json(self.result_path(candidate_id))
        self.assertTrue(result["timedOut"])
        self.assertFalse(result["success"])

    def test_execution_result_signal_shape(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        recorded = self.record_result(candidate_id, self.observation(
            candidate_id, outcome="signal", phase="probe", signal="SIGKILL",
        ))
        self.assertFalse(recorded["success"])
        result = self.read_json(self.result_path(candidate_id))
        self.assertEqual(result["outcome"], "signal")
        self.assertEqual(result["signal"], "SIGKILL")
        self.assertFalse(result["success"])

    def test_execution_result_binds_execution_claim_hash(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        self.record_result(candidate_id, self.observation(candidate_id))
        claim = self.read_json(self.claim_path(candidate_id))
        result = self.read_json(self.result_path(candidate_id))
        marker = self.read_json(self.result_marker_path(candidate_id))
        claim_bytes = self.release_update.canonical_json(claim)
        result_bytes = self.release_update.canonical_json(result)
        self.assertEqual(result["executionClaimSha256"], self.release_update.sha256(claim_bytes))
        self.assertEqual(result["acquisitionMarkerSha256"], claim["acquisitionMarkerSha256"])
        self.assertEqual(marker["executionClaimSha256"], self.release_update.sha256(claim_bytes))
        self.assertEqual(marker["executionResultSha256"], self.release_update.sha256(result_bytes))
        self.assertEqual(result["version"], claim["version"])
        self.assertEqual(result["activationHash"], claim["activationHash"])
        self.assert_qualification_authority(result)
        self.assert_qualification_authority(marker)

    def test_execution_result_rejects_missing_claim_and_unexpected_entries(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        run_dir = self.qualification_root / "runs" / candidate_id
        (run_dir / "unexpected").write_text("state\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "run file set is invalid"):
            self.record_result(candidate_id, self.observation(candidate_id))
        (run_dir / "unexpected").unlink()
        activation = self.qualification_root / "activations" / candidate_id
        (activation / "unexpected").write_text("state\n", encoding="ascii")
        with self.assertRaisesRegex(self.release_update.UpdateError, "activation file set is invalid"):
            self.record_result(candidate_id, self.observation(candidate_id))
        (activation / "unexpected").unlink()
        self.claim_path(candidate_id).unlink()
        with self.assertRaisesRegex(self.release_update.UpdateError, "activation file set is invalid"):
            self.record_result(candidate_id, self.observation(candidate_id))

    def test_execution_result_blocks_before_claim(self):
        candidate_id, _ = self.acquire_run()
        with self.assertRaisesRegex(self.release_update.UpdateError, "activation file set is invalid"):
            self.release_update.qualification_execution_result(
                self.result_arguments(candidate_id, None, confirm=True),
            )

    def test_execution_result_rejects_wrong_observation_candidate_and_authority(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        wrong = dict(self.observation(candidate_id))
        wrong["candidateId"] = "pixel-9.9.9-" + ("ab" * 32)
        with self.assertRaisesRegex(self.release_update.UpdateError, "candidate ID differs"):
            self.record_result(candidate_id, wrong)
        granted = dict(self.observation(candidate_id))
        granted["publicationAuthority"] = True
        with self.assertRaisesRegex(self.release_update.UpdateError, "grants authority"):
            self.record_result(candidate_id, granted)

    def test_execution_result_recovery_after_secondary_marker_crash(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        observation = self.observation(candidate_id)
        observation_path = self.write_observation(observation, candidate_id)
        args = self.result_arguments(candidate_id, observation_path)
        state = self.release_update.qualification_execution_state(
            args,
            activation_files=set(self.release_update.QUALIFICATION_ACTIVATION_CLAIMED_FILES),
            run_files=set(self.release_update.QUALIFICATION_RUN_OBSERVED_FILES),
        )
        claim_path = state["activation"] / self.release_update.QUALIFICATION_EXECUTION_CLAIM_FILE
        claim_bytes = self.release_update.read_regular(
            claim_path, self.release_update.MAX_STAGE_RECEIPT, "qualification execution claim",
        )
        claim, spec_bytes, spec = self.release_update.qualification_validate_execution_claim(state, claim_bytes)
        spec_path = state["run_dir"] / self.release_update.QUALIFICATION_EXECUTION_SPEC_FILE
        start_path = state["run_dir"] / self.release_update.QUALIFICATION_EXECUTION_START_FILE
        start_bytes = self.release_update.read_regular(
            start_path, self.release_update.MAX_STAGE_RECEIPT, "qualification execution start",
        )
        start = self.release_update.parse_json(start_bytes, "qualification execution start")
        result, result_bytes, _marker, _marker_bytes = self.release_update.qualification_execution_result_value(
            state, claim, claim_bytes, spec, spec_bytes, start, start_bytes,
            observation, "2026-08-24T00:00:00Z",
        )
        # Simulate a crash after the immutable result is published but before the secondary marker.
        self.release_update.write_private(self.result_path(candidate_id), result_bytes)
        self.release_update.fsync_directory(state["run_dir"])
        self.assertTrue(self.result_path(candidate_id).is_file())
        self.assertFalse(self.result_marker_path(candidate_id).exists())
        recovered = self.release_update.qualification_execution_result(args)
        self.assertEqual(recovered["status"], "recovered")
        self.assertTrue(self.result_marker_path(candidate_id).is_file())
        self.assertEqual(
            self.read_json(self.result_marker_path(candidate_id))["executionResultSha256"],
            self.release_update.sha256(result_bytes),
        )
        with self.assertRaisesRegex(self.release_update.UpdateError, "replay is forbidden"):
            self.release_update.qualification_execution_result(args)

    def test_execution_result_rejects_replay_and_run_path_deletion(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        self.record_result(candidate_id, self.observation(candidate_id))
        # Deleting run paths (including the immutable result) must not permit re-execution.
        run_dir = self.qualification_root / "runs" / candidate_id
        shutil.rmtree(run_dir)
        self.assertFalse(run_dir.exists())
        with self.assertRaisesRegex(self.release_update.UpdateError, "already been acquired"):
            self.release_update.qualification_host_run(self.run_arguments(candidate_id))
        with self.assertRaisesRegex(self.release_update.UpdateError, "automatic re-execution is forbidden"):
            self.claim_execution(candidate_id)
        with self.assertRaisesRegex(self.release_update.UpdateError, "marker exists without its result"):
            self.release_update.qualification_execution_result(
                self.result_arguments(candidate_id, None, confirm=True),
            )

    def test_execution_crash_before_claim_may_retry_and_after_claim_is_forbidden(self):
        candidate_id, _ = self.acquire_run()
        with mock.patch.object(
            self.release_update, "write_private", autospec=True,
            side_effect=self.release_update.UpdateError("injected claim write failure"),
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "injected claim write failure"):
                self.claim_execution(candidate_id)
        self.assertFalse(self.claim_path(candidate_id).exists())
        self.claim_execution(candidate_id)
        self.assertTrue(self.claim_path(candidate_id).is_file())
        with self.assertRaisesRegex(self.release_update.UpdateError, "automatic re-execution is forbidden"):
            self.claim_execution(candidate_id)

    def test_execution_result_crash_after_claim_before_result_is_interrupted(self):
        candidate_id, _ = self.acquire_run()
        self.claim_execution(candidate_id)
        observation = dict(self.observation(candidate_id))
        observation["outcome"] = "deferred"
        observation["phase"] = "install"
        observation["reason"] = "execution-interrupted"
        with mock.patch.object(
            self.release_update, "write_private", autospec=True,
            side_effect=self.release_update.UpdateError("injected result write failure"),
        ):
            with self.assertRaisesRegex(self.release_update.UpdateError, "injected result write failure"):
                self.record_result(candidate_id, observation)
        self.assertFalse(self.result_path(candidate_id).exists())
        with self.assertRaisesRegex(self.release_update.UpdateError, "automatic re-execution is forbidden"):
            self.claim_execution(candidate_id)


class ReactivationArchiveTests(unittest.TestCase):
    """Focused tests for the update-reactivation-archive command."""

    @classmethod
    def setUpClass(cls):
        ReleaseUpdateTests.setUpClass()

    def setUp(self):
        self.base = ReleaseUpdateTests(methodName="runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)

    @staticmethod
    def _controller_binding(version):
        return {
            "controllerVersion": version,
            "controllerSourceCommit": "1" * 40,
            "controllerSourceTree": "2" * 40,
            "controllerArchiveSha256": "3" * 64,
            "controllerEnvelopeSha256": "4" * 64,
            "controllerSignatureSha256": "5" * 64,
            "controllerDispatcherSha256": "6" * 64,
            "controllerEngineSha256": "7" * 64,
            "controllerCommandSha256": "8" * 64,
            "controllerAuthority": "exact-production-signed-special-operator-only",
        }

    def _failed_reactivation_archive_fixture(self):
        journey = self.base._terminal_activation_wrapper_journey()
        controller = journey["controller"]
        future = journey["future"]
        install_root = journey["installRoot"]
        openclaw_home = journey["openclawHome"]
        prepared = journey["prepared"]
        staging_root = journey["stagingRoot"]
        (controller / "VERSION").write_text(f"{future.version}\n", encoding="ascii")
        (controller / ".env").write_text(
            (controller / ".env").read_text(encoding="utf-8").replace(
                f"PIXEL_RELEASE_VERSION='{self.base.fixture.current_version}'",
                f"PIXEL_RELEASE_VERSION='{future.version}'",
            ),
            encoding="utf-8",
        )
        update = load_release_update(
            controller / "scripts" / "release-update.py",
            f"pixel_release_update_reactivation_archive_{time.time_ns()}",
        )
        common = dict(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
            activation_hash=journey["activationHash"],
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=openclaw_home / "backups" / "last-apply",
        )
        reactivation_hash = update.reactivation_preview(argparse.Namespace(**common))["reactivationHash"]
        update.claim_reactivation(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash, confirm=True,
        ))
        reactivation = staging_root / "reactivations" / prepared["candidateId"]
        update.record_reactivation_result(argparse.Namespace(
            **common, reactivation_hash=reactivation_hash, outcome="failed",
            phase="apply", confirm=True,
        ))
        arguments = argparse.Namespace(
            candidate_id=prepared["candidateId"], allowed_signers=future.allowed_signers,
            identity="pixel-release", staging_root=staging_root,
            archive_root=install_root / "update-reactivation-archive",
            controller_root=controller,
            controller_envelope=future.envelope_path,
            activation_hash=journey["activationHash"],
            active_version_file=install_root / "current" / "VERSION",
            rollback_marker=openclaw_home / "backups" / "last-apply",
        )
        services = {
            "openclaw-gateway.service": "active",
            "pixel-ops-broker.service": "active",
            "pixel-web-courier.service": "active",
        }
        controller_binding = self._controller_binding(next_patch(future.version, 2))
        return journey, update, arguments, services, controller_binding, reactivation

    @staticmethod
    def _preview(update, arguments, services, controller_binding):
        with mock.patch.object(update, "archive_service_state", return_value=services), mock.patch.object(
            update, "special_operator_controller_context", return_value=controller_binding,
        ):
            return update.reactivation_archive_preview(arguments)

    @unittest.skipUnless(sys.platform == "linux", "release reactivation archive is qualified only on Linux")
    def test_terminal_no_live_mutation_reactivation_failure_archives_four_roots_and_resumes_interruption(self):
        journey, update, arguments, services, controller_binding, reactivation = self._failed_reactivation_archive_fixture()
        staging_root = arguments.staging_root
        install_root = journey["installRoot"]
        candidate_id = arguments.candidate_id
        for index in range(6):
            retained = staging_root / "candidates" / f"pixel-1.0.{index}-{'a' * 63}{index}"
            retained.mkdir(mode=0o700)

        def previewed_with_services():
            return self._preview(update, arguments, services, controller_binding)

        with self.assertRaisesRegex(update.UpdateError, "capacity boundary"):
            previewed_with_services()
        retained = staging_root / "candidates" / f"pixel-1.0.6-{'a' * 63}6"
        retained.mkdir(mode=0o700)
        live_marker = reactivation / "LIVE-MUTATION-STARTED"
        live_marker.write_bytes(update.REACTIVATION_LIVE_MUTATION_MARKER)
        live_marker.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "no-live-mutation"):
            previewed_with_services()
        live_marker.unlink()
        result_path = reactivation / "REACTIVATION-RESULT.json"
        original_result = result_path.read_bytes()
        result = json.loads(original_result.decode("utf-8"))
        result["executionPhase"] = "record"
        result["liveMutationStarted"] = True
        result["liveMutationMarkerSha256"] = "c" * 64
        result_path.write_bytes(update.canonical_json(result))
        result_path.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "live-mutation marker"):
            previewed_with_services()
        result_path.write_bytes(original_result)
        result_path.chmod(0o600)
        marker = arguments.rollback_marker
        marker.write_text("/ambiguous/restored-marker\n", encoding="utf-8")
        marker.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "rollback marker"):
            previewed_with_services()
        marker.unlink()
        later_version = next_patch(journey["future"].version)
        later_release = install_root / "releases" / later_version
        later_release.mkdir(mode=0o700)
        (later_release / "VERSION").write_text(f"{later_version}\n", encoding="ascii")
        (later_release / "VERSION").chmod(0o600)
        current_link = install_root / "current"
        restored_target = current_link.readlink()
        current_link.unlink()
        current_link.symlink_to(later_release, target_is_directory=True)
        marker.write_text("/current-release-rollback-marker\n", encoding="utf-8")
        marker.chmod(0o600)
        later_preview = previewed_with_services()
        self.assertEqual(later_preview["activeVersionAtArchive"], later_version)
        self.assertEqual(
            later_preview["liveRollbackMarkerSha256"], digest(marker.read_bytes()),
        )
        marker.unlink()
        current_link.unlink()
        current_link.symlink_to(restored_target, target_is_directory=True)
        preview = previewed_with_services()
        self.assertEqual(preview["status"], "ready")
        self.assertEqual(preview["operation"], "pixel-release-update-reactivation-archive")
        self.assertEqual(preview["initialCandidateCount"], 8)
        self.assertEqual(preview["finalCandidateCount"], 7)
        self.assertEqual(preview["failedExecutionPhase"], "apply")
        self.assertIsNone(preview["liveRollbackMarkerSha256"])
        self.assertFalse(preview["installedReleaseWillMove"])
        self.assertFalse(preview["activeDeploymentWillChange"])
        self.assertTrue(preview["failedReceiptsWillBePreserved"])
        self.assertEqual(
            set(preview["trees"]), {"candidate", "rehearsal", "activation", "reactivation"},
        )
        run_arguments = argparse.Namespace(
            **vars(arguments), archive_hash=preview["archiveHash"], confirm=True,
        )
        original_rename = update.rename_directory_noreplace
        rename_calls = 0

        def interrupt_after_third_root(source, destination):
            nonlocal rename_calls
            rename_calls += 1
            if rename_calls == 3:
                raise update.UpdateError("simulated reactivation archive interruption")
            original_rename(source, destination)

        with mock.patch.object(update, "archive_service_state", return_value=services), mock.patch.object(
            update, "special_operator_controller_context", return_value=controller_binding,
        ):
            with mock.patch.object(
                update, "rename_directory_noreplace", side_effect=interrupt_after_third_root,
            ):
                with self.assertRaisesRegex(update.UpdateError, "simulated reactivation archive interruption"):
                    update.reactivation_archive_failed_update(run_arguments)
        destination = arguments.archive_root / candidate_id
        self.assertTrue((destination / "candidate").is_dir())
        self.assertTrue((staging_root / "rehearsals" / candidate_id).is_dir())
        self.assertTrue((staging_root / "activations" / candidate_id).is_dir())
        self.assertTrue((staging_root / "reactivations" / candidate_id).is_dir())
        archive_claim = json.loads((destination / "ARCHIVE.json").read_text(encoding="utf-8"))
        self.assertEqual(
            archive_claim["claim"],
            "pre-mutation-episode-four-root-atomic-rename-with-idempotent-recovery",
        )
        drifted_binding = {**controller_binding, "controllerEngineSha256": "9" * 64}
        with mock.patch.object(
            update, "special_operator_controller_context", return_value=drifted_binding,
        ):
            with self.assertRaisesRegex(update.UpdateError, "controller changed"):
                update.reactivation_archive_preview(arguments)
        with mock.patch.object(
            update, "special_operator_controller_context", return_value=controller_binding,
        ):
            with self.assertRaisesRegex(update.UpdateError, "claim is invalid"):
                update.reactivation_archive_failed_update(argparse.Namespace(**{
                    **vars(run_arguments), "archive_hash": "0" * 64,
                }))
        interrupted = previewed_with_services()
        self.assertEqual(interrupted["status"], "interrupted")
        original_noreplace = update.rename_noreplace

        def interrupt_result_publication(source, target, label):
            if target.name == "ARCHIVE-RESULT.json":
                raise update.UpdateError("simulated result publication interruption")
            original_noreplace(source, target, label)

        with mock.patch.object(update, "archive_service_state", return_value=services), mock.patch.object(
            update, "special_operator_controller_context", return_value=controller_binding,
        ):
            with mock.patch.object(
                update, "rename_noreplace", side_effect=interrupt_result_publication,
            ):
                with self.assertRaisesRegex(update.UpdateError, "result publication interruption"):
                    update.reactivation_archive_failed_update(run_arguments)
        pending_path = destination / ".ARCHIVE-RESULT.pending"
        self.assertTrue(pending_path.is_file())
        self.assertFalse((destination / "ARCHIVE-RESULT.json").exists())
        pending_result = json.loads(pending_path.read_text(encoding="utf-8"))
        pending_result["archivedAt"] = "2026-09-03T12:00:00Z"
        pending_path.write_bytes(update.canonical_json(pending_result))
        pending_path.chmod(0o600)
        with mock.patch.object(update, "archive_service_state", return_value=services), mock.patch.object(
            update, "special_operator_controller_context", return_value=controller_binding,
        ):
            resumed = update.reactivation_archive_failed_update(run_arguments)
        self.assertEqual(resumed["archivedAt"], "2026-09-03T12:00:00Z")
        self.assertEqual(resumed["status"], "archived")
        self.assertEqual(resumed["activeDeploymentChanged"], False)
        self.assertEqual(update.staged_candidate_count(staging_root), 7)
        self.assertTrue((destination / "candidate" / "STAGED-UPDATE.json").is_file())
        self.assertTrue((destination / "rehearsal" / "REHEARSAL.json").is_file())
        self.assertTrue((destination / "activation" / "ROLLBACK-RESULT.json").is_file())
        self.assertTrue((destination / "reactivation" / "REACTIVATION-RESULT.json").is_file())
        self.assertTrue((destination / "ARCHIVE.json").is_file())
        self.assertTrue((destination / "MANIFEST.json").is_file())
        self.assertTrue((destination / "ARCHIVE-RESULT.json").is_file())
        with self.assertRaisesRegex(update.UpdateError, "prepared release candidate is unavailable"):
            update.reactivation_preview(argparse.Namespace(
                candidate_id=candidate_id, allowed_signers=arguments.allowed_signers,
                identity="pixel-release", staging_root=staging_root,
                activation_hash=arguments.activation_hash,
                active_version_file=arguments.active_version_file,
                rollback_marker=arguments.rollback_marker,
            ))
        repeated = previewed_with_services()
        self.assertEqual(repeated["status"], "already-archived")
        self.assertFalse(repeated["confirmationRequired"])
        # The legacy failed-rollback archive namespace remains a distinct, unused sibling.
        self.assertFalse((install_root / "update-archive").exists())

    @unittest.skipUnless(sys.platform == "linux", "release reactivation archive is qualified only on Linux")
    def test_reactivation_archive_rejects_uneligible_episodes(self):
        journey, update, arguments, services, controller_binding, reactivation = self._failed_reactivation_archive_fixture()
        staging_root = arguments.staging_root
        for index in range(7):
            retained = staging_root / "candidates" / f"pixel-1.0.{index}-{'a' * 63}{index}"
            retained.mkdir(mode=0o700)

        def previewed_with_services():
            return self._preview(update, arguments, services, controller_binding)

        journaled = staging_root / "reactivation-attempts" / arguments.candidate_id
        journaled.mkdir(parents=True, mode=0o700)
        with self.assertRaisesRegex(update.UpdateError, "journaled reactivation attempts"):
            previewed_with_services()
        journaled.rmdir()
        result_path = reactivation / "REACTIVATION-RESULT.json"
        original_result = result_path.read_bytes()
        result = json.loads(original_result.decode("utf-8"))
        result["status"] = "reactivated"
        result["activeVersion"] = journey["future"].version
        result["rollbackAvailable"] = True
        result["rollbackMarkerSha256"] = "b" * 64
        result["executionPhase"] = "record"
        result["liveMutationStarted"] = True
        result["liveMutationMarkerSha256"] = "c" * 64
        result_path.write_bytes(update.canonical_json(result))
        result_path.chmod(0o600)
        (reactivation / "LIVE-MUTATION-STARTED").write_bytes(
            update.REACTIVATION_LIVE_MUTATION_MARKER,
        )
        (reactivation / "LIVE-MUTATION-STARTED").chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "no-live-mutation"):
            previewed_with_services()
        (reactivation / "LIVE-MUTATION-STARTED").unlink()
        result_path.write_bytes(original_result)
        result_path.chmod(0o600)
        rollback_artifact = reactivation / "ROLLBACK.json"
        rollback_artifact.write_text("{}\n", encoding="utf-8")
        rollback_artifact.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "no-live-mutation reactivation result"):
            previewed_with_services()
        rollback_artifact.unlink()
        preview = previewed_with_services()
        self.assertEqual(preview["status"], "ready")
        # Preview binds an exact hash; confirmation requires that exact hash.
        with self.assertRaisesRegex(update.UpdateError, "requires --confirm"):
            update.reactivation_archive_failed_update(argparse.Namespace(
                **vars(arguments), archive_hash=preview["archiveHash"], confirm=False,
            ))
        with mock.patch.object(
            update, "archive_service_state", return_value=services,
        ), mock.patch.object(
            update, "special_operator_controller_context", return_value=controller_binding,
        ):
            with self.assertRaisesRegex(update.UpdateError, "hash differs"):
                update.reactivation_archive_failed_update(argparse.Namespace(
                    **vars(arguments), archive_hash="0" * 64, confirm=True,
                ))
        self.assertFalse((arguments.archive_root / arguments.candidate_id).exists())

    @unittest.skipUnless(sys.platform == "linux", "release reactivation archive is qualified only on Linux")
    def test_reactivation_archive_refuses_the_active_candidate_version(self):
        journey, update, arguments, services, controller_binding, _reactivation = self._failed_reactivation_archive_fixture()
        staging_root = arguments.staging_root
        for index in range(7):
            retained = staging_root / "candidates" / f"pixel-1.0.{index}-{'a' * 63}{index}"
            retained.mkdir(mode=0o700)
        install_root = journey["installRoot"]
        current_link = install_root / "current"
        current_link.unlink()
        current_link.symlink_to(
            install_root / "releases" / journey["future"].version,
            target_is_directory=True,
        )
        with self.assertRaisesRegex(update.UpdateError, "refuses the active candidate version"):
            self._preview(update, arguments, services, controller_binding)
        self.assertFalse((arguments.archive_root / arguments.candidate_id).exists())

    @unittest.skipUnless(sys.platform == "linux", "release reactivation archive is qualified only on Linux")
    def test_reactivation_archive_stops_at_first_move_boundary_after_live_state_drift(self):
        journey, update, arguments, services, controller_binding, _reactivation = self._failed_reactivation_archive_fixture()
        staging_root = arguments.staging_root
        candidate_id = arguments.candidate_id
        for index in range(7):
            retained = staging_root / "candidates" / f"pixel-1.0.{index}-{'a' * 63}{index}"
            retained.mkdir(mode=0o700)
        preview = self._preview(update, arguments, services, controller_binding)
        run_arguments = argparse.Namespace(
            **vars(arguments), archive_hash=preview["archiveHash"], confirm=True,
        )
        install_root = journey["installRoot"]
        later_version = next_patch(journey["future"].version)
        later_release = install_root / "releases" / later_version
        later_release.mkdir(mode=0o700)
        (later_release / "VERSION").write_text(f"{later_version}\n", encoding="ascii")
        (later_release / "VERSION").chmod(0o600)
        current_link = install_root / "current"
        original_rename = update.rename_directory_noreplace

        def drift_after_first_evidence_move(source, destination):
            original_rename(source, destination)
            if destination.name == "candidate":
                current_link.unlink()
                current_link.symlink_to(later_release, target_is_directory=True)

        with mock.patch.object(update, "archive_service_state", return_value=services), mock.patch.object(
            update, "special_operator_controller_context", return_value=controller_binding,
        ), mock.patch.object(
            update, "rename_directory_noreplace", side_effect=drift_after_first_evidence_move,
        ):
            with self.assertRaisesRegex(update.UpdateError, "active Pixel changed"):
                update.reactivation_archive_failed_update(run_arguments)
        destination = arguments.archive_root / candidate_id
        self.assertTrue((destination / "candidate").is_dir())
        self.assertFalse((destination / "rehearsal").exists())
        self.assertTrue((staging_root / "rehearsals" / candidate_id).is_dir())
        self.assertTrue((staging_root / "activations" / candidate_id).is_dir())
        self.assertTrue((staging_root / "reactivations" / candidate_id).is_dir())
        self.assertFalse((destination / "ARCHIVE-RESULT.json").exists())

    def test_legacy_no_marker_bridge_is_exact_and_single_source(self):
        update = ReleaseUpdateTests.release_update
        reactivation = Path(self.base.temporary.name) / "legacy-reactivation"
        reactivation.mkdir(mode=0o700)
        candidate_id = "pixel-4.3.15-" + "a" * 64
        reactivation_hash = "b" * 64
        claim_bytes = b"legacy-claim\n"
        result = {
            "schemaVersion": 1,
            "operation": "pixel-release-reactivation-result",
            "status": "failed",
            "candidateId": candidate_id,
            "product": "Pixel",
            "version": "4.3.15",
            "previousVersion": "4.3.14",
            "activeVersion": "4.3.14",
            "reactivationHash": reactivation_hash,
            "reactivationClaimSha256": digest(claim_bytes),
            "executionPhase": "apply",
            "completedAt": "2026-08-31T00:00:00Z",
            "candidateCodeExecuted": True,
            "privateConfigurationRead": True,
            "activeDeploymentChanged": False,
            "networkMayHaveBeenUsed": True,
            "rollbackAvailable": False,
            "rollbackMarkerSha256": None,
            "boundary": update.REACTIVATION_RESULT_BOUNDARY,
        }
        result_path = reactivation / "REACTIVATION-RESULT.json"
        result_path.write_bytes(update.canonical_json(result))
        result_path.chmod(0o600)
        envelope = {
            "version": "4.3.15",
            "sourceCommit": "057ace691d2db552335349ca33187cf251e90f27",
            "sourceTree": "0f15f606d340b7eef4a3af49d2f754298aa429ce",
        }
        observed, observed_bytes = update.validate_archivable_reactivation_result(
            reactivation, envelope, "4.3.14", reactivation_hash, claim_bytes, candidate_id,
        )
        self.assertEqual(observed, result)
        self.assertEqual(observed_bytes, update.canonical_json(result))
        with self.assertRaisesRegex(update.UpdateError, "unrecognized legacy"):
            update.validate_archivable_reactivation_result(
                reactivation, {**envelope, "sourceTree": "0" * 40}, "4.3.14",
                reactivation_hash, claim_bytes, candidate_id,
            )
        result["networkMayHaveBeenUsed"] = False
        result_path.write_bytes(update.canonical_json(result))
        result_path.chmod(0o600)
        with self.assertRaisesRegex(update.UpdateError, "legacy.*invalid"):
            update.validate_archivable_reactivation_result(
                reactivation, envelope, "4.3.14", reactivation_hash, claim_bytes, candidate_id,
            )

    def test_legacy_reactivation_intent_bridge_removes_only_post_4_3_15_bindings(self):
        update = ReleaseUpdateTests.release_update
        modern = {
            "operation": "pixel-release-reactivation",
            "activationDeploymentRecordSha256": "a" * 64,
            "retainedDeploymentInputsSha256": "b" * 64,
            "retainedInstallManifestSha256": "c" * 64,
            "previouslyActivatedController": True,
        }
        legacy_envelope = dict(update.LEGACY_NO_MARKER_REACTIVATION_ARCHIVE_SOURCE)
        legacy = update.archivable_reactivation_intent(legacy_envelope, modern)
        self.assertEqual(
            legacy,
            {"operation": "pixel-release-reactivation", "previouslyActivatedController": True},
        )
        self.assertEqual(
            update.archivable_reactivation_intent(
                {**legacy_envelope, "sourceTree": "0" * 40}, modern,
            ),
            modern,
        )


if __name__ == "__main__":
    unittest.main()
