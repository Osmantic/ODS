import base64
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from project_runtime_protocol import InvalidProjectDependencies, validate_project_lock


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


if __name__ == "__main__":
    unittest.main()
