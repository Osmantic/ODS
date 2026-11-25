"""Self-test for the test inventory: only real executions count as coverage."""

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("inventory", Path(__file__).with_name("list-unwired-tests.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CANDIDATES = ['ods/tests/test-a.sh', 'ods/tests/test_b.py', 'ods/tests/test-c.py', 'ods/tests/sub/test_s.py',
              'ods/tests/test-h.mjs', 'ods/tests/test-x-1.sh']


def files_run(text, wd):
    found = MODULE.executed(text, wd)
    return {c for c in CANDIDATES if any(MODULE.runs(kind, target, c) for kind, target in found)}


class Parser(unittest.TestCase):
    def test_commands_that_execute_tests(self):
        cases = [
            ('bash tests/test-a.sh', {'ods/tests/test-a.sh'}),
            ('./tests/test-a.sh', {'ods/tests/test-a.sh'}),
            ('timeout 60 bash tests/test-x-1.sh', {'ods/tests/test-x-1.sh'}),
            ('python3 tests/test-c.py', {'ods/tests/test-c.py'}),
            ('python -m pytest -q tests/test-c.py', {'ods/tests/test-c.py'}),
            ('sudo env A=1 "$(command -v python)" -m pytest tests/test_b.py -q', {'ods/tests/test_b.py'}),
            ('"$PYTHON" tests/test-c.py', {'ods/tests/test-c.py'}),
            ('node --test tests/test-h.mjs', {'ods/tests/test-h.mjs'}),
            ('grep -q x tests/test-a.sh && bash tests/test-x-1.sh', {'ods/tests/test-x-1.sh'}),
            ('for t in tests/test-x-*.sh; do\n  bash "$t"\ndone', {'ods/tests/test-x-1.sh'}),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(files_run(text, 'ods'), expected)

    def test_lint_syntax_and_prose_are_not_executions(self):
        for text in ['bash -n tests/test-a.sh', 'shellcheck tests/*.sh', 'python -m py_compile tests/test_b.py',
                     'python3 -c "import tests"', 'echo "then run bash tests/test-a.sh"', '# bash tests/test-a.sh',
                     'make test']:
            with self.subTest(text=text):
                self.assertEqual(files_run(text, 'ods'), set())

    def test_directory_runs_follow_the_runners_discovery(self):
        # pytest collects test_*.py and *_test.py only; hyphenated files need an explicit path.
        self.assertEqual(files_run('python -m pytest -q tests', 'ods'),
                         {'ods/tests/test_b.py', 'ods/tests/sub/test_s.py'})
        self.assertEqual(files_run('python -m pytest -q', 'ods'), {'ods/tests/test_b.py', 'ods/tests/sub/test_s.py'})

    def test_variable_paths_resolve_by_suffix(self):
        self.assertIn(('bash', 'VAR:test-z.sh'), MODULE.executed('bash "$SCRIPT_DIR/test-z.sh"', 'ods/tests'))
        self.assertIn(('bats', 'VAR:bats-tests/*.bats'),
                      MODULE.executed('"$DIR/bats/bin/bats" "$DIR"/bats-tests/*.bats', 'ods/tests'))


class Inventory(unittest.TestCase):
    def test_only_executed_tests_are_covered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout = {
                '.github/workflows/ci.yml': (
                    'on: [pull_request]\n'
                    'jobs:\n'
                    '  test:\n'
                    '    runs-on: ubuntu-latest\n'
                    '    defaults:\n'
                    '      run:\n'
                    '        working-directory: ods\n'
                    '    steps:\n'
                    '      - run: bash tests/test-runner.sh\n'
                    '      - run: shellcheck tests/test-linted.sh\n'
                    '      - run: python -m pytest -q tests/unit\n'
                    '  svc:\n'
                    '    runs-on: ubuntu-latest\n'
                    '    strategy:\n'
                    '      matrix:\n'
                    '        include:\n'
                    '          - service: alpha\n'
                    '    steps:\n'
                    '      - run: python -m pytest -q tests\n'
                    '        working-directory: ods/extensions/services/${{ matrix.service }}\n'),
                'ods/Makefile': 'test:\n\tbash tests/test-makefile-only.sh\n',
                'ods/tests/ci-suite.txt': '# suite\ntests/test-suite-listed.sh\n',
                'ods/tests/ci-not-run.txt': '# not run\n',
                'ods/tests/test-runner.sh': 'SCRIPT_DIR=x\nbash "$SCRIPT_DIR/test-child.sh"\n# bash tests/test-commented.sh\n',
                'ods/tests/test-child.sh': 'true\n',
                'ods/tests/test-commented.sh': 'true\n',
                'ods/tests/test-linted.sh': 'true\n',
                'ods/tests/test-makefile-only.sh': 'true\n',
                'ods/tests/test-suite-listed.sh': 'true\n',
                'ods/tests/unit/test_collected.py': '',
                'ods/tests/unit/test-hyphenated.py': '',
                'ods/extensions/services/alpha/tests/test_alpha.py': '',
                'ods/extensions/services/beta/tests/test_beta.py': '',
            }
            for name, text in layout.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text, encoding='utf-8')
            env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True, env=env)
            subprocess.run(['git', 'add', '-A'], cwd=root, check=True, env=env)

            tests, covered = MODULE.inventory(root)
            executed = {t for t in tests if covered(t)}
            self.assertEqual(executed, {
                'ods/tests/test-runner.sh', 'ods/tests/test-child.sh', 'ods/tests/test-suite-listed.sh',
                'ods/tests/unit/test_collected.py', 'ods/extensions/services/alpha/tests/test_alpha.py'})
            self.assertEqual(set(tests) - executed, {
                'ods/tests/test-commented.sh', 'ods/tests/test-linted.sh', 'ods/tests/test-makefile-only.sh',
                'ods/tests/unit/test-hyphenated.py', 'ods/extensions/services/beta/tests/test_beta.py'})


if __name__ == '__main__':
    unittest.main()
