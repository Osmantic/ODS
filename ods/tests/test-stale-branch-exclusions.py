"""The maintainer CLI must not list candidates with unverified PR exclusions."""
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


class StaleBranchExclusionTest(unittest.TestCase):
    def test_cli_requires_complete_pr_inventory_or_explicit_override(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/maintainers/list-stale-branches.py'
        spec = importlib.util.spec_from_file_location('stale_branches', path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        for mode in ('unavailable', 'invalid-json', 'invalid-shape', 'truncated', 'empty', 'override'):
            with self.subTest(mode=mode):
                def run(command):
                    if command[0] == 'gh':
                        self.assertNotEqual(mode, 'override')
                        rows = [{'headRefName': f'feature-{i}'} for i in range(int(command[command.index('--limit') + 1]))]
                        payload = {'invalid-json': 'oops', 'invalid-shape': '{}', 'truncated': json.dumps(rows)}.get(mode, '[]')
                        return subprocess.CompletedProcess(command, 1 if mode == 'unavailable' else 0, payload, '')
                    if command[1] == 'rev-parse':
                        return subprocess.CompletedProcess(command, 0, '/repo\n', '')
                    return subprocess.CompletedProcess(command, 0, '2000-01-01T00:00:00+00:00\torigin/active-change\tabc123\n', '')

                output = io.StringIO()
                argv = ['list-stale-branches'] + (['--include-open-prs'] if mode == 'override' else [])
                with patch.object(script, 'run', side_effect=run), patch.object(sys, 'argv', argv), redirect_stdout(output):
                    if mode in ('empty', 'override'):
                        self.assertEqual(script.main(), 0)
                    else:
                        with self.assertRaises(SystemExit):
                            script.main()
                self.assertEqual('origin/active-change' in output.getvalue(), mode in ('empty', 'override'))


if __name__ == '__main__':
    unittest.main()
