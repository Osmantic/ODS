"""Self-test for the commit identity check, with synthetic identities of each kind seen on main."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("check-commit-identities.py")
SPEC = importlib.util.spec_from_file_location("commit_identities", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CommitIdentities(unittest.TestCase):
    def test_placeholders_and_machine_local_identities_are_rejected(self):
        for name, email in [
            ('User Name', 'user@example.com'),
            ('someone', 'someone@example.com'),
            ('Portal agent', 'portal@local'),
            ('A Person', 'person@MacBook-Air.local'),
            ('root', 'root@DESKTOP-ABC1234'),
            ('Dev', 'dev@box.lan'),
            ('Dev', 'dev@printer.home.arpa'),
            ('Dev', 'dev@ci.example.org'),
            ('Dev', 'dev'),
        ]:
            with self.subTest(email=email):
                self.assertIsNotNone(MODULE.problem(name, email))

    def test_real_and_noreply_identities_pass(self):
        for name, email in [
            ('Jane Doe', 'jane@company.dev'),
            ('Octo Cat', '12345678+octocat@users.noreply.github.com'),
            ('GitHub', 'noreply@github.com'),
            ('Claude', 'noreply@anthropic.com'),
            ('Jane Doe', 'jane.doe@gmail.com'),
            ('Someone', 'someone@examples.com'),
        ]:
            with self.subTest(email=email):
                self.assertIsNone(MODULE.problem(name, email))

    def test_only_the_offending_commit_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            def git(*args, name='Real Person', email='real@person.dev'):
                env = {**os.environ, 'GIT_AUTHOR_NAME': name, 'GIT_AUTHOR_EMAIL': email,
                       'GIT_COMMITTER_NAME': name, 'GIT_COMMITTER_EMAIL': email,
                       'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1'}
                return subprocess.run(['git', *args], cwd=directory, check=True, capture_output=True,
                                      text=True, env=env).stdout.strip()
            git('init', '-q')
            git('commit', '-q', '--allow-empty', '-m', 'base')
            base = git('rev-parse', 'HEAD')
            git('commit', '-q', '--allow-empty', '-m', 'good')
            git('commit', '-q', '--allow-empty', '-m', 'bad', name='User Name', email='user@example.com')
            head = git('rev-parse', 'HEAD')

            result = subprocess.run([sys.executable, str(SCRIPT), base, head], cwd=directory,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn('placeholder name "User Name"', result.stdout)
            self.assertEqual(result.stdout.count('::error::'), 2)  # author and committer of one commit

            clean = subprocess.run([sys.executable, str(SCRIPT), base, git('rev-parse', 'HEAD~1')],
                                   cwd=directory, capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)


if __name__ == '__main__':
    unittest.main()
