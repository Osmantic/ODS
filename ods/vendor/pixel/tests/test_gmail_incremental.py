"""Bounded first-sync/resume with synthetic Gmail, never a real account."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

if __package__:
    from . import test_source_broker as fixtures
else:
    import test_source_broker as fixtures

BROKER = fixtures.BROKER


class Mailbox:
    def __init__(self, inbox=(), sent=(), unread=()):
        self.ids = {'inbox': list(inbox), 'sent': list(sent), 'unread': list(unread)}
        self.calls = []
        self.clock = 0.0
        self.cost = 0.01
        self.before = None

    def get(self, _base, path, _token, params, *, timeout=25):
        if self.before:
            self.before(path, params)
        self.calls.append((path, dict(params), timeout))
        self.clock += self.cost
        if path == '/messages':
            name = 'sent' if params['q'].startswith('in:sent') else 'unread' if 'is:unread' in params['q'] else 'inbox'
            offset = int(params.get('pageToken', '0'))
            count = params['maxResults']
            ids = self.ids[name]
            result = {'messages': [{'id': item} for item in ids[offset:offset + count]], 'resultSizeEstimate': len(ids)}
            if offset + count < len(ids):
                result['nextPageToken'] = str(offset + count)
            return result
        message_id = path.rsplit('/', 1)[-1]
        if message_id not in set(self.ids['inbox']) | set(self.ids['sent']):
            raise BROKER.UpstreamHTTPError(404)
        value = fixtures.message('Synthetic inbound fact, without an instruction.')
        value['id'] = message_id
        value['labelIds'] = []
        for folder, label in (('inbox', 'INBOX'), ('sent', 'SENT'), ('unread', 'UNREAD')):
            if message_id in self.ids[folder]:
                value['labelIds'].append(label)
        if params['format'] == 'full':
            if 'SENT' in value['labelIds']:
                raise AssertionError('Sent content was requested')
        else:
            value['payload'].pop('body', None)
        return value


class GmailIncrementalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pixel-gmail-incremental-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'email.json'
        self.cursor = self.root / '.gmail-sync-progress.json'
        self.generation = 'a' * 64
        environment = patch.dict(os.environ, {
            'PIXEL_SOURCE_GMAIL_QUERY': 'in:inbox', 'PIXEL_SOURCE_GMAIL_SENT_QUERY': 'in:sent',
            'PIXEL_SOURCE_GMAIL_PAGE_SIZE': '2', 'PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE': '2',
            'PIXEL_SOURCE_GMAIL_MAX_PAGES': '1000', 'PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES': '1000',
            'PIXEL_SOURCE_GMAIL_REQUEST_BUDGET': '8', 'PIXEL_SOURCE_GMAIL_TIME_BUDGET_SECONDS': '90'})
        environment.start()
        self.addCleanup(environment.stop)

    def batch(self, mailbox, *, commit_cursor=True):
        cached = BROKER.retained_projection(self.path, 'gmail')
        with patch.object(BROKER, 'google_get', mailbox.get), patch.object(BROKER.time, 'monotonic', lambda: mailbox.clock):
            records, coverage, progress = BROKER.gmail_incremental_records('synthetic-access-token', cached,
                progress_path=self.cursor, generation=self.generation)
        BROKER.atomic_json(self.path, BROKER.projection('gmail', records, BROKER.iso(BROKER.utc_now()), True, coverage))
        if commit_cursor:
            BROKER.atomic_json(self.cursor, progress, mode=0o600)
        return records, coverage, progress

    def finish(self, mailbox):
        for _ in range(200):
            records, coverage, progress = self.batch(mailbox)
            self.assertLessEqual(coverage['sync']['requestsThisRun'], 8)
            if progress['cycleDone']:
                return records, coverage, progress
        self.fail('Synthetic mailbox never finished its bounded cursor')

    def test_first_batch_is_usable_without_listing_all_sent_history(self):
        mailbox = Mailbox(inbox=[f'i{n}' for n in range(100)], sent=[f's{n}' for n in range(1000)])
        records, coverage, progress = self.batch(mailbox)
        self.assertTrue(any('inbox' in item['mailboxes'] for item in records))
        self.assertEqual(coverage['sync']['state'], 'in-progress')
        self.assertFalse(BROKER.coverage_complete('email', coverage))
        self.assertLessEqual(len(mailbox.calls), 8)
        self.assertLess(progress['folders']['sent']['pagesFetched'], 500)
        self.assertEqual(self.cursor.stat().st_mode & 0o777, 0o600)
        private = self.cursor.read_text()
        for value in ('synthetic-access-token', 'Synthetic inbound fact', 'vendor@example.com', 'Quarterly review'):
            self.assertNotIn(value, private)

    def test_cursor_resumes_and_does_not_restart_pages_on_each_batch(self):
        mailbox = Mailbox(inbox=[f'i{n}' for n in range(7)], sent=[f's{n}' for n in range(5)], unread=['i0', 'i2'])
        records, coverage, _progress = self.finish(mailbox)
        self.assertEqual({item['id'] for item in records}, set(mailbox.ids['inbox'] + mailbox.ids['sent']))
        self.assertTrue(BROKER.coverage_complete('email', coverage))
        for query in ('in:inbox', 'in:sent', 'in:inbox is:unread'):
            first_pages = [params for path, params, _timeout in mailbox.calls
                if path == '/messages' and params['q'] == query and 'pageToken' not in params]
            self.assertEqual(len(first_pages), 1)
        for item in records:
            if item['id'] in mailbox.ids['sent']:
                self.assertEqual(item['contentProjection'], 'metadata-only')
                self.assertEqual(item['summary'], '')
            self.assertEqual('UNREAD' in item['labelIds'], item['id'] in mailbox.ids['unread'])

    def test_crash_between_projection_and_cursor_repeats_reads_without_skipping(self):
        mailbox = Mailbox(inbox=[f'i{n}' for n in range(4)], sent=['s0'])
        self.batch(mailbox, commit_cursor=False)
        records, coverage, _progress = self.finish(mailbox)
        self.assertTrue(BROKER.coverage_complete('email', coverage))
        self.assertEqual({item['id'] for item in records}, {'i0', 'i1', 'i2', 'i3', 's0'})
        self.assertEqual(len(records), 5)

    def test_cached_rows_are_pruned_only_after_a_complete_new_scan(self):
        mailbox = Mailbox(inbox=['keep', 'removed'])
        self.finish(mailbox)
        mailbox.ids = {'inbox': ['keep'] + [f'new{n}' for n in range(10)], 'sent': [], 'unread': []}
        records, coverage, _progress = self.batch(mailbox)
        self.assertFalse(BROKER.coverage_complete('email', coverage))
        self.assertIn('removed', {item['id'] for item in records})
        removed = next(item for item in records if item['id'] == 'removed')
        self.assertTrue(removed['retainedFromEarlierScan'])
        records, coverage, _progress = self.finish(mailbox)
        self.assertTrue(BROKER.coverage_complete('email', coverage))
        self.assertNotIn('removed', {item['id'] for item in records})

    def test_new_grant_or_query_never_reuses_previous_cursor_or_cache(self):
        for change in ('generation', 'query'):
            with self.subTest(change=change):
                self.path.unlink(missing_ok=True)
                self.cursor.unlink(missing_ok=True)
                mailbox = Mailbox(inbox=['old'])
                self.finish(mailbox)
                old = json.loads(self.cursor.read_text())['binding']
                mailbox.ids = {'inbox': ['new'], 'sent': [], 'unread': []}
                if change == 'generation':
                    self.generation = 'b' * 64
                else:
                    os.environ['PIXEL_SOURCE_GMAIL_QUERY'] = 'in:inbox newer_than:1d'
                records, coverage, _progress = self.batch(mailbox)
                self.assertNotEqual(coverage['sync']['binding'], old)
                self.assertNotIn('old', {item['id'] for item in records})

    def test_inbox_sent_label_is_checked_before_any_full_fetch_even_during_partial_listing(self):
        mailbox = Mailbox(inbox=['dual'], sent=[f's{n}' for n in range(100)] + ['dual'])
        records, coverage, _progress = self.batch(mailbox)
        dual = next(item for item in records if item['id'] == 'dual')
        self.assertFalse(BROKER.coverage_complete('email', coverage))
        self.assertEqual(dual['contentProjection'], 'metadata-only')
        self.assertEqual(dual['summary'], '')

    def test_cached_inbox_summary_is_downgraded_when_the_message_moves_to_sent(self):
        mailbox = Mailbox(inbox=['moved'])
        self.finish(mailbox)
        mailbox.ids = {'inbox': [], 'sent': ['moved'], 'unread': []}
        before = len(mailbox.calls)
        records, coverage, _progress = self.finish(mailbox)
        self.assertTrue(BROKER.coverage_complete('email', coverage))
        self.assertEqual(records[0]['contentProjection'], 'metadata-only')
        self.assertEqual(records[0]['summary'], '')
        self.assertFalse(any(params.get('format') == 'full' for _path, params, _timeout in mailbox.calls[before:]))

    def test_time_budget_and_per_request_remaining_timeout_are_enforced(self):
        mailbox = Mailbox(inbox=[f'i{n}' for n in range(100)], sent=[f's{n}' for n in range(100)])
        mailbox.cost = 0.4
        with patch.dict(os.environ, {'PIXEL_SOURCE_GMAIL_REQUEST_BUDGET': '256',
                                    'PIXEL_SOURCE_GMAIL_TIME_BUDGET_SECONDS': '1'}):
            _records, coverage, _progress = self.batch(mailbox)
        self.assertLessEqual(len(mailbox.calls), 3)
        self.assertTrue(all(0 < timeout <= 1 for _path, _params, timeout in mailbox.calls))
        self.assertEqual(coverage['sync']['state'], 'in-progress')
        self.assertFalse(BROKER.coverage_complete('email', coverage))

    def test_a_deleted_projection_cannot_leave_an_advanced_cursor_claiming_complete(self):
        mailbox = Mailbox(inbox=[f'i{n}' for n in range(10)], sent=['s0'])
        self.batch(mailbox)
        self.path.unlink()
        records, coverage, _progress = self.finish(mailbox)
        self.assertEqual({item['id'] for item in records}, set(mailbox.ids['inbox'] + mailbox.ids['sent']))
        self.assertTrue(BROKER.coverage_complete('email', coverage))

    def test_unsafe_cursor_and_nonfinite_budget_fail_without_contacting_a_provider(self):
        mailbox = Mailbox(inbox=['i0'])
        outside = self.root / 'preserved'
        outside.write_text('preserved')
        self.cursor.symlink_to(outside)
        with self.assertRaises((OSError, BROKER.SourceUnavailable)):
            self.batch(mailbox)
        self.assertEqual(outside.read_text(), 'preserved')
        self.assertEqual(mailbox.calls, [])
        self.cursor.unlink()
        with patch.dict(os.environ, {'PIXEL_SOURCE_GMAIL_TIME_BUDGET_SECONDS': 'nan'}):
            with self.assertRaises(BROKER.SourceUnavailable):
                self.batch(mailbox)

    def test_a_safety_page_limit_is_honest_partial_coverage(self):
        mailbox = Mailbox(inbox=[f'i{n}' for n in range(10)])
        with patch.dict(os.environ, {'PIXEL_SOURCE_GMAIL_MAX_PAGES': '1'}):
            _records, coverage, progress = self.finish(mailbox)
        self.assertTrue(progress['cycleDone'])
        self.assertEqual(coverage['sync']['state'], 'limited')
        self.assertEqual(coverage['folders']['inbox']['truncationReason'], 'safety-page-limit')
        self.assertFalse(BROKER.coverage_complete('email', coverage))

    def test_mismatched_detail_identifier_is_not_published_as_a_complete_record(self):
        mailbox = Mailbox(inbox=['i0'])
        original = mailbox.get

        def mismatched(base, path, token, params, **kwargs):
            value = original(base, path, token, params, **kwargs)
            if path != '/messages':
                value['id'] = 'other'
            return value

        mailbox.get = mismatched
        with self.assertRaises(BROKER.SourceUnavailable):
            self.batch(mailbox)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.cursor.exists())

    def test_invalid_listing_id_aborts_the_batch_without_pruning_or_advancing_its_cursor(self):
        mailbox = Mailbox(inbox=['preserved'])
        self.finish(mailbox)
        before_projection, before_cursor = self.path.read_bytes(), self.cursor.read_bytes()
        original = mailbox.get

        def malformed(base, path, token, params, **kwargs):
            if path == '/messages' and params['q'] == 'in:inbox':
                return {'messages': [{'id': 'new'}, {'id': None}]}
            return original(base, path, token, params, **kwargs)

        mailbox.get = malformed
        with self.assertRaises(BROKER.SourceUnavailable):
            self.batch(mailbox)
        self.assertEqual(self.path.read_bytes(), before_projection)
        self.assertEqual(self.cursor.read_bytes(), before_cursor)
        self.assertEqual(json.loads(self.path.read_text())['records'][0]['id'], 'preserved')


class IndependentRefreshTests(unittest.TestCase):
    def test_known_error_is_preserved_during_retry_until_a_source_actually_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('private').mkdir()
            token = root / 'private/google-token.json'
            fixtures.write_token(token)
            output = root / 'projection'
            seen = []

            def denied(*_args, **_kwargs):
                seen.append(json.loads((output / 'status.json').read_text())['sources']['email'])
                raise BROKER.UpstreamHTTPError(403)

            with patch.dict(os.environ, {'PIXEL_SOURCE_TOKEN_PATH': str(token),
                    'PIXEL_SOURCE_PROJECTION_DIR': str(output), 'PIXEL_LIMB_EMAIL_ENABLED': '1',
                    'PIXEL_LIMB_CALENDAR_ENABLED': '0', 'PIXEL_LIMB_SOCIAL_ENABLED': '0'}), \
                    patch.object(BROKER, 'refresh_access_token', return_value='fixture'), \
                    contextlib.redirect_stdout(io.StringIO()):
                with patch.object(BROKER, 'google_get', return_value={'messages': []}):
                    self.assertEqual(BROKER.refresh(), 0)
                with patch.object(BROKER, 'google_get', denied):
                    self.assertEqual(BROKER.refresh(), 0)
                    prior = json.loads((output / 'status.json').read_text())['sources']['email']
                    self.assertEqual(BROKER.refresh(), 0)
                self.assertEqual(seen[-1]['state'], 'error')
                self.assertEqual(seen[-1]['httpStatus'], 403)
                self.assertEqual(seen[-1]['lastSuccessAt'], prior['lastSuccessAt'])
                self.assertEqual(seen[-1]['failingSince'], prior['failingSince'])
                with patch.object(BROKER, 'google_get', return_value={'messages': []}):
                    self.assertEqual(BROKER.refresh(), 0)
            final = json.loads((output / 'status.json').read_text())['sources']['email']
            self.assertEqual(final['state'], 'ok')
            self.assertIsNone(final['failingSince'])

    def test_calendar_and_status_are_published_before_any_gmail_history_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('private').mkdir()
            fixtures.write_token(root / 'private/google-token.json')
            output = root / 'projection'
            calls = []

            def get(base, _path, _token, _params, *, timeout=25):
                calls.append(base)
                if base == BROKER.CALENDAR_API:
                    status = json.loads((output / 'status.json').read_text())
                    self.assertFalse(status['sources']['email']['complete'])
                    return {'items': [fixtures.calendar_event('e0')]}
                self.assertTrue((output / 'calendar.json').is_file())
                status = json.loads((output / 'status.json').read_text())
                self.assertTrue(status['sources']['calendar']['complete'])
                self.assertEqual(status['sources']['calendar']['recordCount'], 1)
                return {'messages': []}

            with patch.dict(os.environ, {'PIXEL_SOURCE_TOKEN_PATH': str(root / 'private/google-token.json'),
                    'PIXEL_SOURCE_PROJECTION_DIR': str(output), 'PIXEL_LIMB_EMAIL_ENABLED': '1',
                    'PIXEL_LIMB_CALENDAR_ENABLED': '1', 'PIXEL_LIMB_SOCIAL_ENABLED': '0'}), \
                    patch.object(BROKER, 'refresh_access_token', return_value='fixture'), \
                    patch.object(BROKER, 'google_get', get), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(BROKER.refresh(), 0)
            self.assertEqual(calls[0], BROKER.CALENDAR_API)
            cursor = output / '.gmail-sync-progress.json'
            self.assertEqual(cursor.stat().st_mode & 0o777, 0o600)

    def test_a_new_grant_stops_the_old_run_before_email_cursor_or_status_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('private').mkdir()
            token = root / 'private/google-token.json'
            fixtures.write_token(token)
            output = root / 'projection'
            saved = []

            def get(base, _path, _token, _params, *, timeout=25):
                if base == BROKER.CALENDAR_API:
                    return {'items': []}
                if not saved:
                    saved.append((output / 'status.json').read_bytes())
                    credentials = json.loads(token.read_text())
                    credentials['refresh_token'] = 'new-synthetic-grant'
                    fixtures.write_protected(token, json.dumps(credentials))
                return {'messages': []}

            with patch.dict(os.environ, {'PIXEL_SOURCE_TOKEN_PATH': str(token), 'PIXEL_SOURCE_PROJECTION_DIR': str(output),
                    'PIXEL_LIMB_EMAIL_ENABLED': '1', 'PIXEL_LIMB_CALENDAR_ENABLED': '1', 'PIXEL_LIMB_SOCIAL_ENABLED': '0'}), \
                    patch.object(BROKER, 'refresh_access_token', return_value='fixture'), \
                    patch.object(BROKER, 'google_get', get), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(BROKER.refresh(), 1)
            self.assertEqual((output / 'status.json').read_bytes(), saved[0])
            self.assertFalse((output / 'email.json').exists())
            self.assertFalse((output / '.gmail-sync-progress.json').exists())

    def test_another_grants_api_failure_never_retains_the_previous_accounts_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('private').mkdir()
            token = root / 'private/google-token.json'
            fixtures.write_token(token)
            output = root / 'projection'
            mailbox = Mailbox(inbox=['old-account-message'])

            def get(base, path, access, params, **kwargs):
                if base == BROKER.CALENDAR_API:
                    return {'items': [fixtures.calendar_event('old-account-event')]}
                return mailbox.get(base, path, access, params, **kwargs)

            environment = {'PIXEL_SOURCE_TOKEN_PATH': str(token), 'PIXEL_SOURCE_PROJECTION_DIR': str(output),
                'PIXEL_LIMB_EMAIL_ENABLED': '1', 'PIXEL_LIMB_CALENDAR_ENABLED': '1', 'PIXEL_LIMB_SOCIAL_ENABLED': '0'}
            with patch.dict(os.environ, environment), patch.object(BROKER, 'refresh_access_token', return_value='fixture'), \
                    patch.object(BROKER, 'google_get', get), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(BROKER.refresh(), 0)
                before = json.loads((output / 'email.json').read_text())
                self.assertEqual(before['recordCount'], 1)
                credentials = json.loads(token.read_text())
                credentials['refresh_token'] = 'different-account-generation'
                fixtures.write_protected(token, json.dumps(credentials))
                with patch.object(BROKER, 'google_get', side_effect=BROKER.UpstreamHTTPError(403)):
                    self.assertEqual(BROKER.refresh(), 0)
            for source in ('email', 'calendar'):
                value = json.loads((output / f'{source}.json').read_text())
                self.assertEqual(value['recordCount'], 0)
                self.assertIsNone(value['generatedAt'])
            status = json.loads((output / 'status.json').read_text())
            self.assertTrue(all(status['sources'][source]['lastSuccessAt'] is None for source in ('email', 'calendar')))


if __name__ == '__main__':
    unittest.main()
