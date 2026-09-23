"""Offline mutation tests for the workflow pip install boundary."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from check_workflow_python_locks import audit


SAFE = (
    "python -m pip install --require-hashes --only-binary=:all: "
    "-r .github/requirements/example.txt"
)
PIN = "example==1.2.3 --hash=sha256:" + "a" * 64 + "\n"


class WorkflowPythonLocks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-python-locks-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".github/workflows").mkdir(parents=True)
        (self.root / ".github/requirements").mkdir()
        (self.root / ".github/requirements/example.txt").write_text(PIN)
        (self.root / "ods/service").mkdir(parents=True)

    def workflow(self, run=SAFE, *, name="ci.yml", job=None, step=None, **workflow):
        selected_step = {"run": run, **(step or {})}
        document = {
            "name": "fixture",
            "on": ["push"],
            "jobs": {
                "test": {
                    "runs-on": "ubuntu-latest",
                    "steps": [selected_step],
                    **(job or {}),
                }
            },
            **workflow,
        }
        (self.root / ".github/workflows" / name).write_text(yaml.safe_dump(document))
        return audit(self.root)

    def test_direct_executables_and_requirement_spellings(self):
        for executable in (
            "pip",
            "pip3",
            "python -m pip",
            "python3 -m pip",
            "/usr/bin/python3.12 -m pip",
        ):
            for flag in ("-r ", "-r", "--requirement ", "--requirement="):
                with self.subTest(executable=executable, flag=flag):
                    report = self.workflow(
                        executable
                        + " install --require-hashes --only-binary=:all: "
                        + flag
                        + ".github/requirements/example.txt"
                    )
                    self.assertFalse(report["errors"])
                    self.assertEqual(
                        report["commands"][0]["requirements"],
                        [".github/requirements/example.txt"],
                    )

    def test_shell_and_yaml_multiline_forms(self):
        variants = [
            "python -m pip install \\\n  --require-hashes \\\n"
            "  --only-binary=:all: \\\n  -r .github/requirements/example.txt\n"
            "python -m pip check\n",
            SAFE.replace(" --require-hashes", " `\n --require-hashes"),
            SAFE + " && python -m pip check\n",
        ]
        for run in variants:
            with self.subTest(run=run):
                report = self.workflow(run)
                self.assertFalse(report["errors"])
                self.assertEqual(len(report["commands"]), 1)
        path = self.root / ".github/workflows/ci.yml"
        path.write_text(
            "name: folded\njobs:\n  test:\n    steps:\n      - run: >-\n"
            "          python -m pip install\n"
            "          --require-hashes --only-binary=:all:\n"
            "          -r .github/requirements/example.txt\n"
        )
        self.assertFalse(audit(self.root)["errors"])

    def test_every_workflow_extension_and_each_command_is_checked(self):
        self.workflow(name="a.yml")
        report = self.workflow(SAFE + "\npip3 install loose-package\n", name="b.yaml")
        self.assertEqual(len(report["commands"]), 3)
        self.assertEqual(len(report["errors"]), 1)
        self.assertEqual(report["errors"][0]["workflow"], ".github/workflows/b.yaml")

    def test_defaults_and_step_working_directory_precedence(self):
        relative = SAFE.replace(
            ".github/requirements/example.txt", "../../.github/requirements/example.txt"
        )
        for kwargs in (
            {"defaults": {"run": {"working-directory": "ods/service"}}},
            {
                "defaults": {"run": {"working-directory": "missing"}},
                "job": {"defaults": {"run": {"working-directory": "ods/service"}}},
            },
            {
                "job": {"defaults": {"run": {"working-directory": "missing"}}},
                "step": {"working-directory": "ods/service"},
            },
        ):
            with self.subTest(kwargs=kwargs):
                report = self.workflow(relative, **kwargs)
                self.assertFalse(report["errors"])
                self.assertEqual(
                    report["commands"][0]["workingDirectory"], "ods/service"
                )

    def test_missing_hashes_binary_policy_and_unlocked_arguments_are_rejected(self):
        mutations = [
            SAFE.replace("--require-hashes ", ""),
            SAFE.replace("--only-binary=:all: ", ""),
            SAFE.replace("--only-binary=:all:", "--only-binary=example"),
            SAFE.replace("--require-hashes", "--require-hashes=false"),
            SAFE + " pytest",
            SAFE + " --no-deps",
            SAFE + " --no-binary=:all:",
            SAFE + " --extra-index-url https://other.invalid/simple",
            SAFE + " --index-url https://other.invalid/simple",
            SAFE + " --find-links ./wheels",
            SAFE + " -e ./package",
            SAFE + " --editable=./package",
            SAFE + " -c constraints.txt",
            SAFE.replace("-r .github/requirements/example.txt", ""),
            SAFE.replace("-r .github/requirements/example.txt", "-r"),
            SAFE.replace("example.txt", "missing.txt"),
            SAFE.replace(".github/requirements/example.txt", "$LOCK"),
            SAFE.replace(".github/requirements/example.txt", "../../../outside.txt"),
            SAFE.replace(
                ".github/requirements/example.txt", "https://other.invalid/lock.txt"
            ),
            "PIP_NO_DEPS=1 " + SAFE,
            "env PIP_INDEX_URL=https://other.invalid " + SAFE,
            "cd ods/service\n" + SAFE,
            "bash -c " + json.dumps(SAFE),
            SAFE + " > pip-output.txt",
            'python -m pip "$ACTION" pytest',
            "export PIP_NO_DEPS=1\n" + SAFE,
            "p'ip' install unsafe",
            "p\\ip install unsafe",
            "pip install -r .github/requirements/example.txt "
            "# --require-hashes --only-binary=:all:",
        ]
        for run in mutations:
            with self.subTest(run=run):
                self.assertTrue(self.workflow(run)["errors"])

    def test_pip_environment_overrides_fail_at_each_scope(self):
        for kwargs in (
            {"env": {"PIP_NO_DEPS": "1"}},
            {"job": {"env": {"PIP_EXTRA_INDEX_URL": "https://other.invalid"}}},
            {"step": {"env": {"PIP_CONSTRAINT": "/tmp/constraints.txt"}}},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(self.workflow(**kwargs)["errors"])

    def test_lock_content_cannot_hide_unpinned_inputs_or_index_options(self):
        lock = self.root / ".github/requirements/example.txt"
        for content in (
            "",
            "example>=1.0\n",
            "example==1.2.3\n",
            PIN + "--extra-index-url https://other.invalid\n",
            PIN + "-r other.txt\n",
            PIN + "-e ./local\n",
            PIN.rstrip() + " --no-deps\n",
            PIN.rstrip() + " -e ./local\n",
            "example==1.2.3 loose-package --hash=sha256:" + "a" * 64 + "\n",
            "example @ https://other.invalid/wheel.whl\n",
        ):
            with self.subTest(content=content):
                lock.write_text(content)
                self.assertTrue(self.workflow()["errors"])

    def test_lock_with_markers_and_continued_hashes(self):
        lock = self.root / ".github/requirements/example.txt"
        lock.write_text(
            '# generated\nexample==1.2.3 ; python_version < "3.13" \\\n'
            "  --hash=sha256:" + "a" * 64 + " \\\n  --hash=sha256:" + "b" * 64 + "\n"
        )
        self.assertFalse(self.workflow()["errors"])

    def test_literal_matrix_include_expands_every_lock_and_cwd(self):
        (self.root / ".github/requirements/second.txt").write_text(PIN)
        run = SAFE.replace(".github/requirements/example.txt", "${{ matrix.lock }}.txt")
        report = self.workflow(
            run,
            job={
                "strategy": {
                    "matrix": {
                        "include": [
                            {
                                "lock": "../../.github/requirements/example",
                                "directory": "ods/service",
                            },
                            {"lock": ".github/requirements/second", "directory": "."},
                        ]
                    }
                },
                "defaults": {"run": {"working-directory": "${{ matrix.directory }}"}},
            },
        )
        self.assertFalse(report["errors"])
        self.assertEqual(
            [item["requirements"] for item in report["commands"]],
            [[".github/requirements/example.txt"], [".github/requirements/second.txt"]],
        )

    def test_literal_matrix_axes_and_excludes(self):
        report = self.workflow(
            job={
                "strategy": {
                    "matrix": {
                        "python": ["3.11", "3.12"],
                        "os": ["ubuntu-latest", "macos-latest"],
                        "exclude": [{"python": "3.11", "os": "macos-latest"}],
                    }
                }
            }
        )
        self.assertFalse(report["errors"])
        self.assertEqual(len(report["commands"]), 3)

    def test_dynamic_missing_or_malformed_matrix_fails_closed(self):
        for matrix in (
            "${{ fromJSON(needs.prepare.outputs.matrix) }}",
            {"include": [{"lock": "${{ github.ref }}"}]},
            {"lock": []},
            {"include": [{"other": "value"}]},
        ):
            with self.subTest(matrix=matrix):
                run = SAFE.replace("example.txt", "${{ matrix.lock }}.txt")
                self.assertTrue(
                    self.workflow(run, job={"strategy": {"matrix": matrix}})["errors"]
                )
        for expression in ("${{ env.LOCK }}", '${{ matrix.lock || "example" }}'):
            self.assertTrue(
                self.workflow(SAFE.replace("example", expression))["errors"]
            )

    def test_comments_and_non_install_pip_commands_do_not_become_installs(self):
        report = self.workflow(
            "# pip install unsafe\npython -m pip check\npython -m pip --version\n"
        )
        self.assertFalse(report["errors"])
        self.assertEqual(report["commands"], [])

    def test_cli_always_outputs_json_and_fails_on_an_unsafe_install(self):
        script = Path(__file__).with_name("check_workflow_python_locks.py")
        self.workflow("pip install unsafe")
        result = subprocess.run(
            [sys.executable, str(script), "--root", str(self.root)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertTrue(report["errors"])
        self.assertEqual(len(report["commands"]), 1)


if __name__ == "__main__":
    unittest.main()
