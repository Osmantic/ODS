"""Offline checks for the container's actual Python installation boundary."""
import hashlib
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
import zipfile


SERVICE = Path(__file__).resolve().parents[1]
ROOT = SERVICE.parents[3]
LOCK = SERVICE / "requirements-runtime.lock"


def locked_packages(path):
    packages = {}
    for line in path.read_text(encoding="utf-8").replace("\\\n", " ").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)(?:\s|$)", line)
        if not match:
            raise ValueError("Unpinned or unsupported requirement: " + line)
        name = re.sub(r"[-_.]+", "-", match[1]).lower()
        hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})(?:\s|$)", line))
        if not hashes or name in packages or "://" in line:
            raise ValueError("Missing hashes, repeated package or external source: " + name)
        packages[name] = (match[2], hashes)
    if not packages:
        raise ValueError("Empty lock")
    return packages


def docker_pip_command():
    dockerfile = (SERVICE / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", " ")
    commands = [shlex.split(line[4:]) for line in dockerfile.splitlines()
                if line.startswith("RUN ") and "pip" in line and "install" in line]
    if len(commands) != 1:
        raise ValueError("Expected one auditable dependency installation command")
    return commands[0]


class RuntimeDependencyLockTests(unittest.TestCase):
    def test_complete_runtime_lock_matches_reviewed_ci_versions_and_hashes(self):
        runtime = locked_packages(LOCK)
        ci = locked_packages(ROOT / ".github/requirements/dashboard-tests.txt")
        for name, (version, hashes) in runtime.items():
            with self.subTest(package=name):
                self.assertIn(name, ci)
                self.assertEqual(version, ci[name][0])
                self.assertTrue(hashes.issubset(ci[name][1]), "Review and regenerate both locks")
        self.assertTrue({"fastapi", "uvicorn", "aiohttp", "httpx", "pydantic",
                         "python-multipart", "pyyaml", "jsonschema", "qrcode",
                         "pillow", "uvloop", "httptools", "watchfiles", "websockets"}
                        .issubset(runtime))
        self.assertFalse({"pytest", "pytest-asyncio", "pytest-cov", "coverage",
                          "iniconfig", "pluggy"}.intersection(runtime))

    def test_docker_install_uses_only_reviewed_wheels_and_pinned_base(self):
        dockerfile = (SERVICE / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r"(?m)^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$")
        self.assertIn("COPY requirements-runtime.lock .", dockerfile)
        command = docker_pip_command()
        self.assertEqual(command[:5], ["python", "-m", "pip", "--isolated", "install"])
        self.assertIn("--require-hashes", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertEqual(command[command.index("--index-url") + 1], "https://pypi.org/simple")
        self.assertEqual(command[command.index("-r") + 1], LOCK.name)
        for forbidden in ("--no-deps", "--extra-index-url", "--trusted-host", "||", ";", "&&"):
            self.assertNotIn(forbidden, command)

    def test_real_pip_accepts_approved_wheel_and_rejects_substitution(self):
        # Reuse the Dockerfile command, replacing only the interpreter and input
        # paths. An offline wheel fixture makes this safe without Docker/network.
        with tempfile.TemporaryDirectory(prefix="ods-dashboard-hashes-") as directory:
            stage = Path(directory)
            wheels = stage / "wheels"
            wheels.mkdir()
            wheel = wheels / "ods_runtime_hash_fixture-0.0.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("ods_runtime_hash_fixture.py", 'VALUE = "approved"\n')
                archive.writestr("ods_runtime_hash_fixture-0.0.0.dist-info/METADATA",
                                 "Metadata-Version: 2.1\nName: ods-runtime-hash-fixture\nVersion: 0.0.0\n")
                archive.writestr("ods_runtime_hash_fixture-0.0.0.dist-info/WHEEL",
                                 "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\n"
                                 "Tag: py3-none-any\n")
                archive.writestr("ods_runtime_hash_fixture-0.0.0.dist-info/RECORD", "")
            approved = hashlib.sha256(wheel.read_bytes()).hexdigest()
            lock = stage / LOCK.name
            lock.write_text("ods-runtime-hash-fixture==0.0.0 --hash=sha256:" + approved + "\n")
            command = docker_pip_command()
            command[0] = sys.executable
            command[command.index("-r") + 1] = str(lock)
            index = command.index("--index-url")
            del command[index:index + 2]
            command.extend(["--no-index", "--find-links", str(wheels), "--target"])
            accepted = subprocess.run([*command, str(stage / "approved")], capture_output=True,
                                      text=True, timeout=60)
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertEqual((stage / "approved/ods_runtime_hash_fixture.py").read_text(),
                             'VALUE = "approved"\n')
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("substituted.txt", "unreviewed artifact bytes")
            rejected = subprocess.run([*command, str(stage / "rejected")], capture_output=True,
                                      text=True, timeout=60)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("HASHES", rejected.stdout + rejected.stderr)
            self.assertFalse((stage / "rejected/ods_runtime_hash_fixture.py").exists())


if __name__ == "__main__":
    unittest.main()
