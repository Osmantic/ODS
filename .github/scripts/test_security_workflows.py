"""CI policy contracts: paid public triggers and immutable action references."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load((ROOT / '.github/workflows' / name).read_text(), Loader=yaml.BaseLoader)


class WorkflowSecurityTests(unittest.TestCase):
    def test_public_issue_triage_is_authorized_and_bounded(self):
        job = workflow('ai-issue-triage.yml')['jobs']['triage']
        condition = job['if']
        gate = "contains(fromJSON('[\"OWNER\", \"MEMBER\", \"COLLABORATOR\"]'), github.event.issue.author_association)"
        self.assertTrue(condition.strip().startswith(gate + ' &&'))
        self.assertEqual(job['concurrency']['group'], 'issue-triage-budget')
        self.assertLessEqual(int(job['timeout-minutes']), 5)
        ai = next(step for step in job['steps'] if 'claude-code-action@' in step.get('uses', ''))
        self.assertIn('--max-budget-usd 2', ai['with']['claude_args'])

    def test_review_comment_requires_trusted_association(self):
        config = workflow('claude-review.yml')
        self.assertIn("github.event_name == 'issue_comment' && github.run_id", config['concurrency']['group'])
        job = config['jobs']['basic-review']
        self.assertIn("contains(fromJSON('[\"OWNER\", \"MEMBER\", \"COLLABORATOR\"]'), github.event.comment.author_association)", job['if'])
        self.assertEqual(job['concurrency']['group'], 'claude-review-budget')
        ai = next(step for step in job['steps'] if 'claude-code-action@' in step.get('uses', ''))
        self.assertIn('--max-budget-usd 5', ai['with']['claude_args'])

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
