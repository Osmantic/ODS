"""CI policy contracts: no paid AI automation, immutable action references."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIRS = [ROOT / '.github/workflows', ROOT / 'ods/.github/workflows']


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


def workflow_files():
    for directory in WORKFLOW_DIRS:
        if directory.is_dir():
            yield from sorted(directory.glob('*.yml'))
            yield from sorted(directory.glob('*.yaml'))


class WorkflowSecurityTests(unittest.TestCase):
    def test_ai_automation_workflows_stay_retired(self):
        # The AI triage, review, issue-to-PR, nightly and release-notes
        # workflows were retired on 2026-10-03: the repository holds no model
        # API secrets, so they only produced skipped "green" checks and
        # failures, and release-notes.yml let untrusted PR titles reach an
        # agent with `gh release edit`. Reintroducing one is a deliberate
        # policy change: update docs/AI_WORKFLOW_GUARDRAILS.md and this test.
        files = list(workflow_files())
        self.assertTrue(files, 'no workflow files found; run from a full ODS checkout')
        for path in files:
            text = path.read_text(encoding='utf-8')
            with self.subTest(workflow=path.name):
                self.assertNotIn('claude-code-action', text)
                self.assertNotIn('ANTHROPIC_API_KEY', text)

    def test_reviewed_test_actions_are_immutable(self):
        for filename in ('test-cli-link-precedence.yml', 'test-egress-headers.yml',
                         'test-migration-ceiling.yml', 'test-pixel-tool-grammar.yml',
                         'weaviate-telemetry.yml'):
            for job in workflow(filename)['jobs'].values():
                for step in job.get('steps', []):
                    uses = step.get('uses', '')
                    if uses.startswith('actions/'):
                        self.assertRegex(uses, r'@([a-f0-9]{40})$', filename)


if __name__ == '__main__':
    unittest.main()
