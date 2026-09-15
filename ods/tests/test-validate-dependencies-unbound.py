#!/usr/bin/env python3
"""Regression test for lib/validate-dependencies.sh under set -u with uninitialized maps."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT_DIR = Path(__file__).resolve().parents[1]
VALIDATE_SH = ROOT_DIR / "lib" / "validate-dependencies.sh"


class ValidateDependenciesUnboundTests(unittest.TestCase):
    def test_uninitialized_compose_map_under_set_u(self):
        """Invoking validate_service_dependencies under set -u must not fail on unset associative array keys."""
        script = f"""
        set -u
        source "{VALIDATE_SH}"
        SERVICE_IDS=("unregistered_svc")
        declare -A SERVICE_COMPOSE
        declare -A SERVICE_DEPENDS
        validate_service_dependencies
        """
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"Failed with: {result.stderr}")

    def test_dependency_failure_reports_missing_service(self):
        """Missing dependencies must be reported cleanly with exit code 1."""
        with tempfile.NamedTemporaryFile(suffix=".yml") as compose_file:
            script = f"""
            set -u
            source "{VALIDATE_SH}"
            SERVICE_IDS=("svc_a")
            declare -A SERVICE_COMPOSE=( ["svc_a"]="{compose_file.name}" )
            declare -A SERVICE_DEPENDS=( ["svc_a"]="missing_dep" )
            validate_service_dependencies
            """
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("depends on 'missing_dep', but 'missing_dep' is not enabled", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
