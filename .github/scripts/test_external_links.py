import io
import json
import os
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tomllib
import unittest
from unittest.mock import patch

from check_external_links import DEADLINE_SECONDS, ROOT, check, classify, load_exceptions, markdown_inventory, partial_findings, summarize
from install_lychee import install


def lychee_report(**changes):
    """Small report using the counters/maps from Lychee's ResponseStats schema."""
    raw = dict.fromkeys(('successful', 'unknown', 'unsupported', 'timeouts', 'redirects',
                         'remaps', 'excludes', 'errors', 'cached'), 0)
    raw.update(successful=1, success_map={}, error_map={}, timeout_map={}, excluded_map={})
    raw.update(changes)
    raw.setdefault('total', sum(raw[key] for key in ('successful', 'unknown', 'unsupported', 'timeouts', 'excludes', 'errors')))
    raw.setdefault('unique', raw['total'])
    return raw


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
        raw = lychee_report(errors=2, error_map={'README.md': [first, second]})
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
        raw = lychee_report(timeouts=1, timeout_map={'README.md': [{'url': 'https://example.org/', 'status': {'text': 'Timeout'}}]})
        self.assertEqual(summarize(raw, {})['failures'][0]['category'], 'transient')
        with self.assertRaisesRegex(ValueError, 'without supported'):
            summarize(lychee_report(errors=1), {})

    def test_report_requires_real_nonempty_counter_and_map_shapes(self):
        invalid = [{}, [], None, 'report', lychee_report(total=0, unique=0, successful=0),
                   lychee_report(total=2), lychee_report(unique=2)]
        for key in ('total', 'unique', 'successful', 'unknown', 'unsupported', 'timeouts',
                    'redirects', 'remaps', 'excludes', 'errors', 'cached'):
            for value in (-1, '1', True, 1.5, None):
                raw = lychee_report()
                raw[key] = value
                invalid.append(raw)
            raw = lychee_report()
            del raw[key]
            invalid.append(raw)
        for key in ('success_map', 'error_map', 'timeout_map', 'excluded_map'):
            for value in (None, [], {'README.md': {}}, {'README.md': [None]},
                          {'README.md': [{'url': 'https://example.org/', 'status': []}]}):
                raw = lychee_report()
                raw[key] = value
                invalid.append(raw)
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                summarize(raw, {})
        self.assertEqual(summarize(lychee_report(), {})['failures'], [])

    def test_exit_zero_with_invalid_json_report_still_fails_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / '.github').mkdir()
            (root / '.github/lychee-tool.json').write_text(json.dumps({'version': '0.24.2'}))
            (root / '.github/external-link-exceptions.json').write_text(json.dumps({'schema_version': 1, 'exceptions': []}))
            output = root / 'output'
            for payload in ('{}', '[]', '{"total":', json.dumps({**lychee_report(), 'errors': '0'})):
                with self.subTest(payload=payload):
                    def fake_run(command, **_kwargs):
                        (output / 'lychee.json').write_text(payload)
                        return subprocess.CompletedProcess(command, 0)
                    with patch('check_external_links.markdown_inventory', return_value=['README.md']), \
                         patch('check_external_links.subprocess.check_output', return_value='lychee 0.24.2'), \
                         patch('check_external_links.subprocess.run', side_effect=fake_run), patch('builtins.print'):
                        self.assertEqual(check('lychee', output, root=root), 2)
                    report = json.loads((output / 'summary.json').read_text())
                    self.assertIn('Unusable Lychee report', report['incomplete'])
                    self.assertNotIn('lychee_counts', report)

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
                 patch('check_external_links.subprocess.run', side_effect=subprocess.TimeoutExpired('lychee', DEADLINE_SECONDS)) as run, \
                 patch('builtins.print'):
                self.assertEqual(check('lychee', output, root=root), 2)
            self.assertFalse((output / 'lychee.json').exists())
            self.assertEqual(run.call_args.kwargs['env'], {'PATH': 'fixture'})
            self.assertEqual(run.call_args.kwargs['timeout'], 1200)
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


@unittest.skipUnless(os.environ.get('LYCHEE_BINARY'), 'Set LYCHEE_BINARY to run the pinned checker against loopback fixtures')
class LycheeRetryTests(unittest.TestCase):
    """Use the real checker with isolated HTTP fixtures; never contact public hosts."""

    @classmethod
    def setUpClass(cls):
        cls.binary = str(Path(os.environ['LYCHEE_BINARY']).resolve())
        cls.policy = tomllib.loads((ROOT / '.github/lychee.toml').read_text(encoding='utf-8'))
        version = subprocess.check_output([cls.binary, '--version'], text=True, timeout=10).strip()
        expected = json.loads((ROOT / '.github/lychee-tool.json').read_text())['version']
        if version != f'lychee {expected}':
            raise AssertionError(f'Expected reviewed Lychee {expected}; got {version}')

    def run_fixture(self, sequences, interval='100ms'):
        observations = []
        counts = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                index = counts.get(self.path, 0)
                counts[self.path] = index + 1
                outcomes = sequences[self.path]
                status, headers = outcomes[min(index, len(outcomes) - 1)]
                observations.append((self.path, status, time.monotonic()))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header('Content-Length', '0')
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                # Project the production retry/accept policy onto loopback. A
                # separate real timing case uses the production HF interval.
                config = root / 'lychee.toml'
                config.write_text(
                    f'max_retries = {self.policy["max_retries"]}\n'
                    f'accept = {json.dumps(self.policy["accept"])}\n'
                    'max_concurrency = 1\nhost_concurrency = 1\n'
                    f'host_request_interval = {json.dumps(interval)}\n'
                    'timeout = 5\ncache = false\nno_progress = true\n', encoding='utf-8')
                source = root / 'fixture.md'
                source.write_text(''.join(f'[fixture](http://127.0.0.1:{server.server_port}{path})\n'
                                          for path in sequences), encoding='utf-8')
                report = root / 'result.json'
                completed = subprocess.run([self.binary, '--config', str(config), '--format', 'json',
                                            '--output', str(report), str(source)],
                                           cwd=root, capture_output=True, text=True, timeout=30)
                self.assertTrue(report.is_file(), completed.stdout + completed.stderr)
                return completed.returncode, json.loads(report.read_text()), observations
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

    def test_retry_after_recovers_only_after_successful_recheck(self):
        code, report, seen = self.run_fixture({'/recover': [(429, {'Retry-After': '2'}), (200, {})]})
        self.assertEqual([item[1] for item in seen], [429, 200])
        self.assertGreaterEqual(seen[1][2] - seen[0][2], 1.9)
        self.assertEqual(code, 0)
        self.assertEqual(report['errors'], 0)

    def test_rate_limit_then_missing_url_stays_missing(self):
        code, report, seen = self.run_fixture({'/missing': [(429, {}), (404, {})]})
        self.assertEqual([item[1] for item in seen], [429, 404])
        self.assertNotEqual(code, 0)
        self.assertEqual(summarize(report, {})['failures'][0]['category'], 'missing')

    def test_persistent_rate_limit_exhausts_one_retry_and_fails(self):
        code, report, seen = self.run_fixture({'/limited': [(429, {})]})
        self.assertEqual([item[1] for item in seen], [429, 429])
        self.assertNotEqual(code, 0)
        self.assertEqual(summarize(report, {})['failures'][0]['category'], 'transient')

    def test_initial_missing_url_is_not_retried(self):
        code, report, seen = self.run_fixture({'/missing': [(404, {})]})
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(code, 0)
        self.assertEqual(summarize(report, {})['failures'][0]['category'], 'missing')

    def test_hugging_face_spacing_applies_between_distinct_urls(self):
        interval = self.policy['hosts']['huggingface.co']['request_interval']
        code, report, seen = self.run_fixture({'/first': [(200, {})], '/second': [(200, {})]}, interval)
        self.assertEqual(code, 0)
        self.assertEqual(report['errors'], 0)
        self.assertEqual(len(seen), 2)
        self.assertGreaterEqual(seen[1][2] - seen[0][2], 3.4)


if __name__ == '__main__':
    unittest.main()
