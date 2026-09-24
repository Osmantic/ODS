"""Public-boundary tests for scripts/generate-runtime-sbom.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate-runtime-sbom.py"


def _generate(*args: str) -> tuple[bytes, dict]:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout, json.loads(result.stdout)


def test_cli_emits_deterministic_cyclonedx_inventory():
    first_bytes, first = _generate()
    second_bytes, second = _generate()

    assert first_bytes == second_bytes
    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert first["specVersion"] == "1.5"
    assert first["metadata"]["component"]["name"] == "ODS"
    assert first["components"]


def test_every_source_image_is_represented_once():
    _, bom = _generate()
    references = [
        next(
            prop["value"]
            for prop in component["properties"]
            if prop["name"] == "ods:image-reference"
        )
        for component in bom["components"]
    ]

    assert references == sorted(set(references))
    assert any("extension-library" in prop["value"]
               for component in bom["components"]
               for prop in component["properties"]
               if prop["name"] == "ods:scopes")
    root_dependency = bom["dependencies"][0]
    assert root_dependency["dependsOn"] == [
        component["bom-ref"] for component in bom["components"]
    ]


def test_output_path_is_parseable_and_matches_stdout(tmp_path):
    expected_bytes, expected = _generate()
    output = tmp_path / "artifacts" / "ods-runtime.cdx.json"

    subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )

    assert output.read_bytes() == expected_bytes
    assert json.loads(output.read_text(encoding="utf-8")) == expected


def test_source_commit_ties_the_inventory_to_an_exact_candidate():
    commit = "a" * 40

    first_bytes, bom = _generate("--source-commit", commit)
    second_bytes, _ = _generate("--source-commit", commit)

    assert first_bytes == second_bytes
    assert {
        prop["name"]: prop["value"]
        for prop in bom["metadata"]["properties"]
    }["ods:source-commit"] == commit
    assert bom["metadata"]["component"]["externalReferences"] == [{
        "type": "vcs",
        "url": f"https://github.com/Osmantic/ODS/tree/{commit}",
    }]
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / "candidate.cdx.json"
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--source-commit", commit,
                "--output", str(output),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        assert output.read_bytes() == first_bytes


def test_source_commit_rejects_moving_or_abbreviated_refs():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--source-commit", "main"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "full 40- or 64-character hexadecimal object id" in result.stderr


if __name__ == "__main__":
    test_cli_emits_deterministic_cyclonedx_inventory()
    test_every_source_image_is_represented_once()
    with tempfile.TemporaryDirectory() as temporary:
        test_output_path_is_parseable_and_matches_stdout(Path(temporary))
    test_source_commit_ties_the_inventory_to_an_exact_candidate()
    test_source_commit_rejects_moving_or_abbreviated_refs()
    print("PASS: 5 runtime SBOM tests")
