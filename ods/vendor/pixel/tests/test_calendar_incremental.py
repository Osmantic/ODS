"""Calendar's frozen-window batches cannot starve Gmail or claim partial free time."""
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


class Calendar:
    def __init__(self, pages=5):
        self.pages = pages
        self.prefix = 'event'
        self.calls = []
        self.clock = 0.0
        self.cost = 0.01
        self.failure_page = None

    def get(self, base, path, _token, params, *, timeout=25):
        self.calls.append((base, path, dict(params), timeout))
        self.clock += self.cost
        page = int(params.get('pageToken', 'page-0').split('-')[1])
        if page == self.failure_page:
            raise BROKER.UpstreamHTTPError(503)
        result = {'items': [fixtures.calendar_event(f'{self.prefix}-{page}')]}
        if page + 1 < self.pages:
            result['nextPageToken'] = f'page-{page + 1}'
        return result


class CalendarIncrementalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pixel-calendar-incremental-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'calendar.json'
        self.cursor = self.root / '.calendar-sync-progress.json'
        self.generation = 'a' * 64
        self.now = BROKER.datetime(2026, 8, 5, 12, tzinfo=BROKER.timezone.utc)
        environment = patch.dict(os.environ, {'PIXEL_SOURCE_CALENDAR_ID': 'primary',
            'PIXEL_SOURCE_CALENDAR_PAST_DAYS': '7', 'PIXEL_SOURCE_CALENDAR_FUTURE_DAYS': '90',
            'PIXEL_SOURCE_CALENDAR_MAX_PAGES': '20', 'PIXEL_SOURCE_CALENDAR_REQUEST_BUDGET': '2',
            'PIXEL_SOURCE_CALENDAR_TIME_BUDGET_SECONDS': '45'})
        environment.start()
        self.addCleanup(environment.stop)

    def batch(self, calendar, *, commit_cursor=True):
        before = len(calendar.calls)
        with patch.object(BROKER, 'utc_now', lambda: self.now), \
                patch.object(BROKER.time, 'monotonic', lambda: calendar.clock), \
                patch.object(BROKER, 'google_get', calendar.get):
            records, coverage, progress = BROKER.calendar_incremental_records('synthetic-access-token',
                BROKER.retained_projection(self.path, 'google-calendar'), progress_path=self.cursor,
                generation=self.generation)
        self.assertLessEqual(len(calendar.calls) - before, int(os.environ['PIXEL_SOURCE_CALENDAR_REQUEST_BUDGET']))
        BROKER.atomic_json(self.path, BROKER.projection('google-calendar', records, BROKER.iso(self.now), True, coverage))
        if commit_cursor:
            BROKER.atomic_json(self.cursor, progress, mode=0o600)
        return records, coverage, progress

    def finish(self, calendar):
        for _ in range(30):
            records, coverage, progress = self.batch(calendar)
            if progress['cycleDone']:
                return records, coverage, progress
        self.fail('Synthetic Calendar never completed its bounded pages')

    def test_frozen_window_resumes_pages_and_marks_complete_only_at_the_end(self):
        calendar = Calendar(5)
        records, coverage, _progress = self.batch(calendar)
        self.assertEqual(len(records), 2)
        self.assertFalse(coverage['completeWithinWindow'])
        self.assertEqual(coverage['sync']['state'], 'in-progress')
        self.now += BROKER.timedelta(days=10)
        records, coverage, _progress = self.finish(calendar)
        self.assertEqual(len(records), 5)
        self.assertTrue(coverage['completeWithinWindow'])
        self.assertEqual({params['timeMin'] for _base, _path, params, _timeout in calendar.calls}, {coverage['windowStart']})
        self.assertEqual({params['timeMax'] for _base, _path, params, _timeout in calendar.calls}, {coverage['windowEnd']})
        self.assertEqual([params.get('pageToken') for _base, _path, params, _timeout in calendar.calls],
                         [None, 'page-1', 'page-2', 'page-3', 'page-4'])
        self.assertEqual(self.cursor.stat().st_mode & 0o777, 0o600)
        for value in ('synthetic-access-token', 'Planning', 'owner@example.com'):
            self.assertNotIn(value, self.cursor.read_text())

    def test_monotonic_budget_limits_pages_and_http_timeout(self):
        calendar = Calendar(10)
        calendar.cost = 0.6
        with patch.dict(os.environ, {'PIXEL_SOURCE_CALENDAR_REQUEST_BUDGET': '8',
                                    'PIXEL_SOURCE_CALENDAR_TIME_BUDGET_SECONDS': '1'}):
            _records, coverage, _progress = self.batch(calendar)
        self.assertEqual(len(calendar.calls), 2)
        self.assertTrue(all(0 < timeout <= 1 for _base, _path, _params, timeout in calendar.calls))
        self.assertFalse(coverage['completeWithinWindow'])

    def test_partial_scan_keeps_old_rows_until_full_new_window_finishes(self):
        calendar = Calendar(2)
        self.finish(calendar)
        calendar.prefix = 'new'
        calendar.pages = 5
        self.now += BROKER.timedelta(days=1)
        records, coverage, _progress = self.batch(calendar)
        self.assertFalse(coverage['completeWithinWindow'])
        self.assertTrue({'event-0', 'event-1'} <= {item['id'] for item in records})
        self.assertTrue(all(item['retainedFromEarlierScan'] for item in records if item['id'].startswith('event')))
        records, coverage, _progress = self.finish(calendar)
        self.assertTrue(coverage['completeWithinWindow'])
        self.assertEqual({item['id'] for item in records}, {f'new-{n}' for n in range(5)})

    def test_new_generation_calendar_or_window_drops_previous_scope(self):
        for change in ('generation', 'calendar', 'window', 'sanitizer'):
            with self.subTest(change=change):
                self.path.unlink(missing_ok=True)
                self.cursor.unlink(missing_ok=True)
                calendar = Calendar(2)
                self.finish(calendar)
                old = json.loads(self.cursor.read_text())['binding']
                calendar.prefix = 'new'
                with contextlib.ExitStack() as stack:
                    if change == 'generation':
                        self.generation = 'b' * 64
                    elif change == 'calendar':
                        stack.enter_context(patch.dict(os.environ, {'PIXEL_SOURCE_CALENDAR_ID': 'other@example.invalid'}))
                    elif change == 'window':
                        stack.enter_context(patch.dict(os.environ, {'PIXEL_SOURCE_CALENDAR_FUTURE_DAYS': '30'}))
                    else:
                        stack.enter_context(patch.object(BROKER, 'SANITIZER_REVISION', BROKER.SANITIZER_REVISION + 1))
                    records, coverage, _progress = self.batch(calendar)
                self.assertNotEqual(coverage['sync']['binding'], old)
                self.assertEqual({item['id'] for item in records}, {'new-0', 'new-1'})

    def test_crash_after_projection_before_cursor_never_skips_a_page(self):
        calendar = Calendar(5)
        self.batch(calendar, commit_cursor=False)
        records, coverage, _progress = self.finish(calendar)
        self.assertEqual({item['id'] for item in records}, {f'event-{n}' for n in range(5)})
        self.assertTrue(coverage['completeWithinWindow'])

    def test_deleted_projection_cannot_satisfy_advanced_cursor(self):
        calendar = Calendar(5)
        self.batch(calendar)
        self.path.unlink()
        records, coverage, _progress = self.finish(calendar)
        self.assertEqual(len(records), 5)
        self.assertTrue(coverage['completeWithinWindow'])

    def test_later_page_failure_keeps_this_batches_successful_pages_and_resumes(self):
        calendar = Calendar(5)
        calendar.failure_page = 1
        records, coverage, _progress = self.batch(calendar)
        self.assertEqual([item['id'] for item in records], ['event-0'])
        self.assertFalse(coverage['completeWithinWindow'])
        self.assertEqual(coverage['sync']['failure']['errorClass'], 'network')
        calendar.failure_page = None
        records, coverage, _progress = self.finish(calendar)
        self.assertEqual(len(records), 5)
        self.assertTrue(coverage['completeWithinWindow'])

    def test_page_limit_is_disclosed_instead_of_claiming_full_window(self):
        calendar = Calendar(5)
        with patch.dict(os.environ, {'PIXEL_SOURCE_CALENDAR_MAX_PAGES': '1'}):
            records, coverage, progress = self.batch(calendar)
        self.assertEqual(len(records), 1)
        self.assertTrue(progress['cycleDone'])
        self.assertTrue(coverage['truncated'])
        self.assertFalse(coverage['completeWithinWindow'])
        self.assertEqual(coverage['sync']['state'], 'limited')

    def test_mutated_frozen_window_is_refused_before_network(self):
        calendar = Calendar(5)
        self.batch(calendar)
        value = json.loads(self.cursor.read_text())
        value['windowStart'] = '2020-01-01T00:00:00Z'
        BROKER.atomic_json(self.cursor, value, mode=0o600)
        calls = len(calendar.calls)
        with self.assertRaises(BROKER.SourceUnavailable):
            self.batch(calendar)
        self.assertEqual(len(calendar.calls), calls)


class IndependentCalendarFailureTests(unittest.TestCase):
    def test_slow_partial_calendar_finishes_its_budget_then_gmail_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('private').mkdir()
            token = root / 'private/google-token.json'
            fixtures.write_token(token)
            calendar = Calendar(20)
            calls = []

            def get(base, path, access, params, **kwargs):
                calls.append(base)
                if base == BROKER.CALENDAR_API:
                    return calendar.get(base, path, access, params, **kwargs)
                status = json.loads((root / 'projection/status.json').read_text())
                self.assertFalse(status['sources']['calendar']['complete'])
                self.assertEqual(status['sources']['calendar']['recordCount'], 2)
                return {'messages': []}

            with patch.dict(os.environ, {'PIXEL_SOURCE_TOKEN_PATH': str(token),
                    'PIXEL_SOURCE_PROJECTION_DIR': str(root / 'projection'), 'PIXEL_LIMB_EMAIL_ENABLED': '1',
                    'PIXEL_LIMB_CALENDAR_ENABLED': '1', 'PIXEL_LIMB_SOCIAL_ENABLED': '0',
                    'PIXEL_SOURCE_CALENDAR_REQUEST_BUDGET': '2'}), \
                    patch.object(BROKER, 'refresh_access_token', return_value='fixture'), \
                    patch.object(BROKER, 'google_get', get), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(BROKER.refresh(), 0)
            self.assertEqual(calls[:2], [BROKER.CALENDAR_API, BROKER.CALENDAR_API])
            self.assertIn(BROKER.GMAIL_API, calls[2:])

    def test_calendar_page_error_does_not_prevent_gmail_and_keeps_partial_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath('private').mkdir()
            token = root / 'private/google-token.json'
            fixtures.write_token(token)
            calendar = Calendar(20)
            calendar.failure_page = 1
            gmail = []

            def get(base, path, access, params, **kwargs):
                if base == BROKER.CALENDAR_API:
                    return calendar.get(base, path, access, params, **kwargs)
                gmail.append(True)
                return {'messages': []}

            with patch.dict(os.environ, {'PIXEL_SOURCE_TOKEN_PATH': str(token),
                    'PIXEL_SOURCE_PROJECTION_DIR': str(root / 'projection'), 'PIXEL_LIMB_EMAIL_ENABLED': '1',
                    'PIXEL_LIMB_CALENDAR_ENABLED': '1', 'PIXEL_LIMB_SOCIAL_ENABLED': '0'}), \
                    patch.object(BROKER, 'refresh_access_token', return_value='fixture'), \
                    patch.object(BROKER, 'google_get', get), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(BROKER.refresh(), 0)
            self.assertTrue(gmail)
            snapshot = json.loads((root / 'projection/calendar.json').read_text())
            self.assertEqual(snapshot['recordCount'], 1)
            self.assertFalse(snapshot['coverage']['completeWithinWindow'])
            status = json.loads((root / 'projection/status.json').read_text())
            self.assertEqual(status['sources']['calendar']['state'], 'error')
            self.assertEqual(status['sources']['email']['state'], 'ok')
            self.assertEqual((root / 'projection/.calendar-sync-progress.json').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__':
    unittest.main()
