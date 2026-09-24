"""Focused regressions for public documentation link checking."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("doc_links", Path(__file__).with_name("check-doc-links.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DocumentationLinks(unittest.TestCase):
    def test_fences_do_not_hide_later_real_links(self):
        text = "```markdown\n[x](example)\n~~~~\n```\n[x](real.md)\n"
        self.assertEqual(list(MODULE.targets(text)), [(5, "real.md")])

    def test_reference_links_and_escaped_spaces(self):
        self.assertEqual(list(MODULE.targets('[guide]: <a b.md>\n[x](a%20b.md#section)')), [(1, '<a b.md>'), (2, 'a%20b.md#section')])
        self.assertEqual(MODULE.local_target('a%20b.md#section'), 'a b.md')
        self.assertIsNone(MODULE.local_target('https://example.test/x'))
        self.assertIsNone(MODULE.local_target('#section'))

    def test_baseline_cannot_hide_new_or_modified_debt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'guide.md'
            text = '[old](missing.md)\n'
            path.write_text(text, encoding='utf-8')
            baseline = {'guide.md': {'normalized_sha256': hashlib.sha256(text.encode()).hexdigest(), 'targets': ['missing.md']}}
            count, failures, known = MODULE.audit(root, ['guide.md'], baseline)
            self.assertEqual((count, len(failures), len(known)), (1, 0, 1))
            path.write_text(text + '[new](second.md)\n', encoding='utf-8')
            _, failures, known = MODULE.audit(root, ['guide.md'], baseline)
            self.assertEqual((len(failures), len(known)), (2, 0))

    def test_resolved_debt_is_not_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'guide.md').write_text('[good](target.md)\n', encoding='utf-8')
            (root / 'target.md').write_text('', encoding='utf-8')
            self.assertEqual(MODULE.audit(root, ['guide.md'], {}), (1, [], []))

    def test_paths_cannot_escape_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(MODULE.document_missing(root, 'guide.md', '[x](../outside.md)'), [(1, '../outside.md')])


if __name__ == '__main__':
    unittest.main()
