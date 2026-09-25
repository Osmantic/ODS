import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


class ReleaseArtifactTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.version = (self.root / "VERSION").read_text(encoding="utf-8").strip()

    def run_node(self, *arguments):
        result = subprocess.run(
            ["node", *map(str, arguments)],
            cwd=self.root,
            text=True,
            capture_output=True,
        )
        if result.returncode:
            self.fail(f"Node command failed:\n{result.stdout}\n{result.stderr}")

    def test_release_sbom_is_complete_and_source_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pixel.cdx.json"
            self.run_node("scripts/generate-release-sbom.mjs", "--output", output)
            sbom = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.6")
        root = sbom["metadata"]["component"]
        self.assertEqual(root["name"], "Pixel")
        self.assertEqual(root["version"], self.version)
        properties = {item["name"]: item["value"] for item in root["properties"]}
        expected_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip()
        expected_tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=self.root, text=True).strip()
        self.assertEqual(properties["pixel:source-commit"], expected_commit)
        self.assertEqual(properties["pixel:source-tree"], expected_tree)

        components = sbom["components"]
        refs = [item["bom-ref"] for item in components]
        self.assertEqual(len(refs), len(set(refs)))
        names = {item["name"] for item in components}
        self.assertTrue({
            "openclaw-pixel-source-broker",
            "openclaw-pixel-operations-broker",
            "openclaw-pixel-frontier-broker",
            "typebox",
            "openclaw",
            "playwright",
            "trafilatura",
            "node",
            "pixel-sandbox-base",
            "duckdb",
            "polars",
            "polars-runtime-32",
            "python3",
            "sqlite3",
        }.issubset(names))
        root_dependency = next(item for item in sbom["dependencies"] if item["ref"] == root["bom-ref"])
        graph = {item["ref"]: item["dependsOn"] for item in sbom["dependencies"]}
        reachable = set(root_dependency["dependsOn"])
        pending = list(reachable)
        while pending:
            for dependency in graph.get(pending.pop(), []):
                if dependency not in reachable:
                    reachable.add(dependency)
                    pending.append(dependency)
        self.assertTrue(set(refs).issubset(reachable))

    def test_provenance_binds_artifact_sbom_source_and_release_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            artifact = directory / f"pixel-{self.version}.tar.gz"
            sbom = directory / f"pixel-{self.version}.cdx.json"
            output = directory / f"pixel-{self.version}.intoto.jsonl"
            artifact.write_bytes(b"synthetic release artifact\n")
            sbom_bytes = b'{"bomFormat":"CycloneDX"}\n'
            sbom.write_bytes(sbom_bytes)
            self.run_node(
                "scripts/generate-release-provenance.mjs",
                "--artifact", artifact,
                "--sbom", sbom,
                "--output", output,
            )
            statement = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(statement["_type"], "https://in-toto.io/Statement/v1")
        self.assertEqual(statement["predicateType"], "https://slsa.dev/provenance/v1")
        subjects = {item["name"]: item["digest"]["sha256"] for item in statement["subject"]}
        self.assertEqual(subjects[artifact.name], hashlib.sha256(b"synthetic release artifact\n").hexdigest())
        self.assertEqual(subjects[sbom.name], hashlib.sha256(sbom_bytes).hexdigest())
        resolved = statement["predicate"]["buildDefinition"]["resolvedDependencies"]
        self.assertEqual(len(resolved), 3)
        self.assertTrue(resolved[0]["uri"].startswith("git+https://github.com/Osmantic/Pixel@"))
        self.assertEqual(statement["predicate"]["buildDefinition"]["externalParameters"]["version"], self.version)

    def test_provenance_rejects_symlink_input(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            target = directory / "target"
            target.write_text("artifact", encoding="utf-8")
            link = directory / "artifact"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            sbom = directory / "sbom"
            sbom.write_text("{}", encoding="utf-8")
            result = subprocess.run(
                ["node", "scripts/generate-release-provenance.mjs", "--artifact", str(link), "--sbom", str(sbom), "--output", str(directory / "out")],
                cwd=self.root,
                text=True,
                capture_output=True,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a regular release input", result.stderr)

    def test_release_update_envelope_binds_all_artifacts_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            archive = directory / f"pixel-{self.version}.tar.gz"
            sbom = directory / f"pixel-{self.version}.cdx.json"
            provenance = directory / f"pixel-{self.version}.intoto.jsonl"
            output = directory / f"pixel-{self.version}.update.json"
            payloads = {
                archive: b"synthetic archive\n",
                sbom: b'{"bomFormat":"CycloneDX"}\n',
                provenance: b'{"_type":"https://in-toto.io/Statement/v1"}\n',
            }
            for path, payload in payloads.items():
                path.write_bytes(payload)
            self.run_node(
                "scripts/generate-release-update.mjs",
                "--artifact", archive,
                "--sbom", sbom,
                "--provenance", provenance,
                "--output", output,
            )
            envelope = json.loads(output.read_text(encoding="utf-8"))

        manifest = (self.root / "RELEASE-MANIFEST.json").read_bytes()
        compatibility = (self.root / "OPENCLAW-COMPATIBILITY.json").read_bytes()
        compatibility_value = json.loads(compatibility)
        manifest_value = json.loads(manifest)
        current = next(
            item for item in compatibility_value["combinations"]
            if item["pixel"] == self.version and item["openclaw"] == manifest_value["openclaw"]
            and item["plugins"] == manifest_value["openclawPlugins"]
        )
        self.assertEqual(envelope["operation"], "pixel-release-update")
        self.assertEqual(envelope["version"], self.version)
        self.assertEqual(envelope["releaseManifestSha256"], hashlib.sha256(manifest).hexdigest())
        self.assertEqual(envelope["compatibilitySha256"], hashlib.sha256(compatibility).hexdigest())
        self.assertEqual(envelope["sourceCommit"], subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root, text=True).strip())
        self.assertEqual(envelope["sourceTree"], subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=self.root, text=True).strip())
        self.assertEqual(envelope["qualificationSourceCommit"], current["evidence"]["sourceCommit"])
        for kind, path in {"archive": archive, "sbom": sbom, "provenance": provenance}.items():
            self.assertEqual(envelope["artifacts"][kind]["name"], path.name)
            self.assertEqual(envelope["artifacts"][kind]["sha256"], hashlib.sha256(payloads[path]).hexdigest())
            self.assertEqual(envelope["artifacts"][kind]["bytes"], len(payloads[path]))


if __name__ == "__main__":
    unittest.main()
