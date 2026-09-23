import io
import json
from datetime import date
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from check_external_links import check, classify, load_exceptions, markdown_inventory, partial_findings, summarize
from install_lychee import install


class ExternalLinkTests(unittest.TestCase):
    def test_http_failures_keep_access_limits_distinct_from_missing_pages(self):
        cases = {401: 'access-unconfirmed', 403: 'access-unconfirmed', 429: 'transient',
                 500: 'transient', 503: 'transient', 404: 'missing', 410: 'missing'}
        for code, expected in cases.items():
            with self.subTest(code=code):
                self.assertEqual(classify({'status': {'text': f'{code} response'}}), expected)
        self.assertEqual(classify({'status': {'text': 'TLS certificate failure'}}), 'transport')
        self.assertEqual(classify({'status': {'text': 'Timed out'}}), 'transient')

    def test_exact_reviewed_deferral_never_becomes_a_verified_public_link(self):
        first = {'url': 'https://example.org/history', 'status': {'text': '403 Forbidden'}}
        second = {'url': 'https://example.org/new', 'status': {'text': '404 Not Found'}}
        raw = {'errors': 2, 'error_map': {'README.md': [first, second]}}
        exceptions = {first['url']: {'categories': ['access-unconfirmed'], 'reason': 'Historical review requires account access',
                                     'owner': 'maintainers', 'expires': '2026-10-01'}}
        report = summarize(raw, exceptions)
        self.assertEqual(len(report['failures']), 1)
        self.assertEqual(len(report['reviewed_deferred']), 1)
        self.assertFalse(report['external_fragments_checked'])
        self.assertTrue(report['anonymous'])
        first['status']['text'] = '404 Not Found'
        self.assertEqual(len(summarize(raw, exceptions)['failures']), 2)

    def test_timeouts_and_unsupported_failure_reports_cannot_pass(self):
        raw = {'timeouts': 1, 'timeout_map': {'README.md': [{'url': 'https://example.org/', 'status': {'text': 'Timeout'}}]}}
        self.assertEqual(summarize(raw, {})['failures'][0]['category'], 'transient')
        with self.assertRaisesRegex(ValueError, 'without supported'):
            summarize({'errors': 1}, {})

    def test_interrupted_scan_retains_unique_observations_without_claiming_completeness(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / 'lychee.log'
            log.write_text('[200] https://example.org/good\n[429] https://example.org/rate (at 3:1)\n'
                           '[429] https://example.org/rate (at 9:1)\n[TIMEOUT] https://example.org/slow\n'
                           '[EXCLUDED] http://localhost:3000/\n', encoding='utf-8')
            observed = partial_findings(log)
            self.assertEqual(len(observed), 2)
            self.assertTrue(all(item['partial_observation'] for item in observed))
            self.assertTrue(all(item['category'] == 'transient' for item in observed))

    def test_deadline_fails_closed_removes_stale_report_and_strips_auth_tokens(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.github').mkdir()
            (root / '.github/lychee-tool.json').write_text(json.dumps({'version': '0.24.2'}))
            (root / '.github/external-link-exceptions.json').write_text(json.dumps({'schema_version': 1, 'exceptions': []}))
            output = root / 'output'
            output.mkdir()
            (output / 'lychee.json').write_text(json.dumps({'successful': 10, 'errors': 0}))
            with patch('check_external_links.markdown_inventory', return_value=['README.md']), \
                 patch('check_external_links.subprocess.check_output', return_value='lychee 0.24.2'), \
                 patch('check_external_links.os.environ', {'GITHUB_TOKEN': 'fixture-only', 'GH_TOKEN': 'fixture-only', 'PATH': 'fixture'}), \
                 patch('check_external_links.subprocess.run', side_effect=subprocess.TimeoutExpired('lychee', 900)) as run, \
                 patch('builtins.print'):
                self.assertEqual(check('lychee', output, root=root), 2)
            self.assertFalse((output / 'lychee.json').exists())
            self.assertEqual(run.call_args.kwargs['env'], {'PATH': 'fixture'})
            report = json.loads((output / 'summary.json').read_text())
            self.assertIn('incomplete', report)
            self.assertNotIn('lychee_counts', report)

    def test_exception_requires_exact_url_reason_owner_and_short_expiry(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'exceptions.json'
            entry = {'url': 'https://example.org/review/1', 'reason': 'Review requires authentication',
                     'owner': 'maintainers', 'expires': '2026-10-01', 'categories': ['access-unconfirmed']}
            def write(item):
                config.write_text(json.dumps({'schema_version': 1, 'exceptions': [item]}))
            write(entry)
            self.assertIn(entry['url'], load_exceptions(config, date(2026, 9, 23)))
            for change in ({'expires': '2026-09-22'}, {'expires': '2027-01-01'}, {'reason': ''},
                           {'owner': ''}, {'url': 'https://owner:password@example.org/'}, {'categories': ['all']}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    write({**entry, **change})
                    load_exceptions(config, date(2026, 9, 23))

    def test_inventory_covers_vendor_and_new_docs_but_not_ignored_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            for name in ('README.md', 'ods/vendor/pixel/README.md', 'new.md', 'ignored/local.md', 'output/log.md', 'node_modules/pkg/README.md'):
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('# Fixture\n')
            (root / '.gitignore').write_text('ignored/\n')
            self.assertEqual(markdown_inventory(root), ['README.md', 'new.md', 'ods/vendor/pixel/README.md'])

    def test_tampered_checker_archive_cannot_install_executable(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'bin'
            with patch('install_lychee.platform.system', return_value='Linux'), \
                 patch('install_lychee.platform.machine', return_value='x86_64'), \
                 patch('install_lychee.urllib.request.urlopen', return_value=io.BytesIO(b'tampered archive')):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    install(destination)
            self.assertFalse(destination.exists())


if __name__ == '__main__':
    unittest.main()
