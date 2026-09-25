#!/usr/bin/env python3
"""The dry-run cleanup CLI keeps release and origin's default branch."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/maintainers/list-stale-branches.py"


class ReleaseBranches(unittest.TestCase):
    def test_old_release_and_default_refs_are_not_candidates(self):
        for default_branch in (None, "integration/current"):
            with self.subTest(default_branch=default_branch), tempfile.TemporaryDirectory() as tmp:
                env = {
                    **os.environ,
                    "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                    "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
                    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
                    "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
                }

                def git(*args):
                    return subprocess.check_output(["git", *args], cwd=tmp, env=env, text=True).strip()

                git("init", "-q")
                git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "old")
                sha = git("rev-parse", "HEAD")
                branches = ["main", "public-beta", "release/1.0", "support/1.0", "fix/abandoned"]
                if default_branch:
                    branches.append(default_branch)
                for name in branches:
                    git("update-ref", f"refs/remotes/origin/{name}", sha)
                if default_branch:
                    git("symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{default_branch}")
                result = subprocess.run(
                    ["python3", str(SCRIPT), "--days", "45", "--include-open-prs"],
                    cwd=tmp, env=env, text=True, capture_output=True, check=True,
                )
                candidate_lines = [line for line in result.stdout.splitlines() if f"  {sha[:7]}" in line]
                self.assertEqual(len(candidate_lines), 1, result.stdout)
                self.assertTrue(candidate_lines[0].endswith("origin/fix/abandoned"), result.stdout)


if __name__ == "__main__":
    unittest.main()
