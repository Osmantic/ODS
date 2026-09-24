"""Tests for the extension dependency graph CLI."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "extension-dependency-graph.py"
SPEC = importlib.util.spec_from_file_location("extension_dependency_graph", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _manifest(project: Path, service_id: str, dependencies=(), *, library=False):
    base = project / "extensions" / ("library/services" if library else "services")
    path = base / service_id / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    deps = ", ".join(dependencies)
    path.write_text(
        f"service:\n  id: {service_id}\n  name: {service_id.title()}\n  depends_on: [{deps}]\n",
        encoding="utf-8",
    )


def test_cli_json_reports_dependency_order_and_reverse_blast_radius(tmp_path):
    _manifest(tmp_path, "database")
    _manifest(tmp_path, "workflows", ("database",))
    _manifest(tmp_path, "portal", ("workflows",), library=True)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--project-dir", str(tmp_path),
            "--root", "database",
            "--direction", "dependents",
            "--format", "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(result.stdout)

    assert document["dependency_order"] == ["database", "workflows", "portal"]
    assert document["edges"] == [
        {"depends_on": "workflows", "service": "portal"},
        {"depends_on": "database", "service": "workflows"},
    ]


def test_validation_reports_missing_dependency(tmp_path):
    _manifest(tmp_path, "portal", ("missing-api",))
    extensions = MODULE.load_extensions(tmp_path)

    with pytest.raises(MODULE.GraphValidationError, match="portal->missing-api"):
        MODULE.validate_graph(extensions)


def test_validation_reports_the_cycle_path(tmp_path):
    _manifest(tmp_path, "alpha", ("beta",))
    _manifest(tmp_path, "beta", ("alpha",))
    extensions = MODULE.load_extensions(tmp_path)

    with pytest.raises(MODULE.GraphValidationError, match="alpha -> beta -> alpha"):
        MODULE.validate_graph(extensions)


def test_dot_output_includes_source_and_dependency_edges(tmp_path):
    _manifest(tmp_path, "api")
    _manifest(tmp_path, "portal", ("api",), library=True)
    extensions = MODULE.load_extensions(tmp_path)
    document = MODULE.graph_document(extensions, set(extensions))

    output = MODULE.render_dot(document)

    assert '"portal" -> "api" [label="depends on"]' in output
    assert 'Portal\\n(library)' in output
