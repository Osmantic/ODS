import base64
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_runtime_protocol import (
    InvalidProjectDependencies, MAX_PYTHON_LOCK_BYTES, MAX_PYTHON_PACKAGES,
    select_project_runtime, validate_project_lock, validate_python_lock,
)


class ProjectDependenciesTests(unittest.TestCase):
    def fixture(self):
        package = {"dependencies": {"example": "^1.2.3"}}
        lock = {"lockfileVersion": 3, "packages": {"": copy.deepcopy(package),
            "node_modules/example": {"version": "1.2.3",
                "resolved": "https://registry.npmjs.org/example/-/example-1.2.3.tgz",
                "integrity": "sha512-" + base64.b64encode(bytes(64)).decode()}}}
        return package, lock

    def test_locked_registry_packages_and_scoped_nested_packages(self):
        package, lock = self.fixture()
        lock["packages"]["node_modules/example/node_modules/@org/helper"] = copy.deepcopy(lock["packages"]["node_modules/example"])
        self.assertEqual(validate_project_lock(package, lock)["packageCount"], 2)

    def test_refuses_nonregistry_and_ambiguous_downloads(self):
        for url in ("http://registry.npmjs.org/a.tgz", "https://127.0.0.1/a.tgz",
                    "https://registry.npmjs.org@evil.test/a.tgz", "https://registry.npmjs.org:443/a.tgz",
                    "https://registry.npmjs.org/a.tgz?redirect=x", "https://registry.npmjs.org/../a.tgz",
                    "https://registry.npmjs.org/%2e%2e/a.tgz", "file:///home/user/private",
                    "https://registry.npmjs.org/\na.tgz"):
            with self.subTest(url=url):
                package, lock = self.fixture()
                lock["packages"]["node_modules/example"]["resolved"] = url
                with self.assertRaises(InvalidProjectDependencies):
                    validate_project_lock(package, lock)

    def test_rejects_missing_integrity_and_wrong_digest_length(self):
        for value in (None, "sha1-aabb", "sha512-YQ==", "sha512-!!!!"):
            package, lock = self.fixture()
            lock["packages"]["node_modules/example"]["integrity"] = value
            with self.assertRaises(InvalidProjectDependencies):
                validate_project_lock(package, lock)

    def test_rejects_drift_local_dependencies_and_links(self):
        for version in ("file:../secret", "git+https://example.com/repo", "owner/repo", "npm:other@1", "latest"):
            package, lock = self.fixture()
            package["dependencies"]["example"] = version
            lock["packages"][""] = copy.deepcopy(package)
            with self.assertRaises(InvalidProjectDependencies):
                validate_project_lock(package, lock)
        package, lock = self.fixture()
        package["dependencies"]["example"] = "^2.0.0"
        with self.assertRaises(InvalidProjectDependencies):
            validate_project_lock(package, lock)
        package, lock = self.fixture()
        lock["packages"]["node_modules/example"]["link"] = True
        with self.assertRaises(InvalidProjectDependencies):
            validate_project_lock(package, lock)

    def test_rejects_paths_escaping_package_tree(self):
        for path in ("../secret", "node_modules/../secret", "node_modules/example/../../x", "node_modules/@org/../x"):
            package, lock = self.fixture()
            lock["packages"][path] = lock["packages"].pop("node_modules/example")
            with self.assertRaises(InvalidProjectDependencies):
                validate_project_lock(package, lock)


class PythonDependenciesTests(unittest.TestCase):
    digest = "a" * 64

    def requirement(self, name="humanize", version="4.13.0"):
        return f"{name}=={version} --hash=sha256:{self.digest}"

    def files(self):
        return {"ods-project.json": b'{"runtime":"python"}',
                "requirements.lock": self.requirement().encode(),
                "main.py": b"print('main')", "tests/test_main.py": b"import unittest"}

    def test_exact_pins_hashes_and_standard_continuations(self):
        lock = ("# generated lock\r\nHumanize==4.13.0 \\\r\n"
                f"  --hash=sha256:{self.digest} \\\r\n"
                f"  --hash=sha256:{'b' * 64}\r\n\r\n" + self.requirement("other", "1.2rc1.post2.dev3"))
        metadata = validate_python_lock(lock)
        self.assertEqual(metadata["packageCount"], 2)
        self.assertEqual(metadata["registry"], "https://pypi.org/simple")
        self.assertEqual(validate_python_lock("# stdlib only\n")["packageCount"], 0)
        self.assertEqual(select_project_runtime(self.files()), "python")

    def test_refuses_pip_escape_hatches_and_unpinned_sources(self):
        for line in ("humanize", "humanize>=4", "humanize==4.*", "humanize~=4.0", "humanize===4.0",
                     "humanize[extra]==4.0", "humanize==4.0;python_version>'3'", "-r other.txt",
                     "-c constraints.txt", "--index-url https://evil.test", "--extra-index-url=https://evil.test",
                     "--find-links /private", "-e .", "./dist/pkg.whl", "file:///private",
                     "humanize @ https://example.com/pkg.whl", "git+https://example.com/repo",
                     "humanize==4.0 --config-settings=x=y", "humanize==4.0 --no-binary=:all:",
                     "humanize==4.0 --trusted-host evil.test", "humanize==4.0 # ignored options",
                     "https://example.com/pkg.tar.gz"):
            with self.subTest(line=line), self.assertRaises(InvalidProjectDependencies):
                validate_python_lock(line + " --hash=sha256:" + self.digest)

    def test_hash_integrity_duplicates_and_limits(self):
        for lock in ("humanize==4.0", "humanize==4.0 --hash=sha1:" + self.digest,
                     "humanize==4.0 --hash=sha256:abcd", self.requirement() + "\x00",
                     self.requirement("some_pkg") + "\n" + self.requirement("Some-Pkg"),
                     "é==1 --hash=sha256:" + self.digest, "#" * (MAX_PYTHON_LOCK_BYTES + 1),
                     "\n".join(self.requirement("package" + str(i)) for i in range(MAX_PYTHON_PACKAGES + 1)),
                     "humanize==4.0 " + ("--hash=sha256:" + self.digest + " ") * 65,
                     self.requirement() + " \\\n", self.requirement() + " \\\n# break\n",
                     self.requirement() + " \\\n\n"):
            with self.subTest(lock=lock[:100]), self.assertRaises(InvalidProjectDependencies):
                validate_python_lock(lock)
        for invalid in (None, b"", "\ud800"):
            with self.assertRaises(InvalidProjectDependencies):
                validate_python_lock(invalid)

    def test_profile_cannot_supply_commands_images_or_duplicate_keys(self):
        for manifest in (b'{"runtime":"python","image":"evil"}', b'{"runtime":"python","command":"sh"}',
                         b'{"runtime":"python","runtime":"python"}', b'{"runtime":"ruby"}',
                         b'{"runtime":"npm"}', b'[]', b'null', b'{', b'\xff', b' ' * 8193):
            with self.subTest(manifest=manifest[:100]), self.assertRaises(InvalidProjectDependencies):
                select_project_runtime({**self.files(), "ods-project.json": manifest})

    def test_python_requires_lock_entrypoint_and_discoverable_tests(self):
        for missing in ("requirements.lock", "main.py", "tests/test_main.py"):
            files = self.files()
            del files[missing]
            with self.subTest(missing=missing), self.assertRaises(InvalidProjectDependencies):
                select_project_runtime(files)
        files = self.files()
        files["tests/nested/test_main.py"] = files.pop("tests/test_main.py")
        with self.assertRaises(InvalidProjectDependencies):
            select_project_runtime(files)

    def test_legacy_npm_projects_keep_existing_validation(self):
        package, lock = ProjectDependenciesTests().fixture()
        files = {"package.json": json.dumps(package).encode(), "package-lock.json": json.dumps(lock).encode()}
        self.assertEqual(select_project_runtime(files), "npm")
        lock["packages"]["node_modules/example"]["resolved"] = "https://evil.test/pkg.tgz"
        files["package-lock.json"] = json.dumps(lock).encode()
        with self.assertRaises(InvalidProjectDependencies):
            select_project_runtime(files)



if __name__ == "__main__":
    unittest.main()
