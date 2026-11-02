"""Bounded scalar/address validation at the public connected-apps boundary."""
import itertools
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import access_mode as contract


class ActionTextTests(unittest.TestCase):
    def test_all_text_patterns_keep_their_maximum_valid_values(self):
        samples = {
            '_FLOW': 'a' * 32,
            '_STATE': 'A' * 128,
            '_CODE': '!' * 2048,
            '_CLIENT_ID': '1' * 32 + '-' + 'a' * 64 + '.apps.googleusercontent.com',
            '_CLIENT_SECRET': '!' * 256,
            '_PROJECT': 'a' + '-' * 28 + 'z',
            '_ACCOUNT': '!' * 64 + '@' + '!' * 253,
            '_TIME_ZONE': 'A' + '/' * 63,
            '_SECOND': '2026-10-29T00:00:00Z',
            '_REASON': 'a' + '-' * 95,
            '_AUTH_URL': 'https://accounts.google.com/o/oauth2/auth?' + 'A' * 4000,
            '_PROPOSAL': 'calendar-' + '1' * 13 + '-' + 'a' * 8,
            '_DRAFT': 'gmail-draft-' + '1' * 13 + '-' + 'a' * 8,
            '_PLUGIN': 'a' + '-' * 62,
            '_HEX': 'a' * 64,
        }
        for name, value in samples.items():
            with self.subTest(pattern=name):
                pattern = getattr(contract, name)
                self.assertIsNotNone(pattern.fullmatch(value))
                self.assertTrue(contract._text(value, pattern))
        self.assertEqual(max(map(len, samples.values())), contract._ACTION_TEXT_LIMIT)

    def test_oversized_uncontrolled_input_never_reaches_the_regex_engine(self):
        for tail in ('', '@', '@!', '\n'):
            with self.subTest(tail=tail):
                regex = Mock()
                self.assertFalse(contract._text('!' * 1_000_000 + tail, regex))
                regex.fullmatch.assert_not_called()
        for value in (None, 1, True, [], {}):
            with self.subTest(value=value):
                regex = Mock()
                self.assertFalse(contract._text(value, regex))
                regex.fullmatch.assert_not_called()

    def test_bounded_input_still_requires_exact_full_match(self):
        for value in ('a' * 63, 'a' * 65, 'a' * 64 + '\n', '!' * 64):
            with self.subTest(value=value[-4:]):
                self.assertFalse(contract._text(value, contract._HEX))
        self.assertFalse(contract._text('https://accounts.google.com/o/oauth2/auth?' + 'A' * 4001,
                                        contract._AUTH_URL))

    def test_the_exact_sarif_request_fields_remain_strict(self):
        requests = [
            ('connection-approve', {'proposalId': 'calendar-1712345678901-a1b2c3d4', 'sha256': 'a' * 64}, 'sha256'),
            ('connection-plugin', {'plugin': 'memory-wiki', 'enabled': True}, 'plugin'),
        ]
        for operation, request, field in requests:
            self.assertTrue(contract.valid_connection_action({'operation': operation, 'request': request}))
            for invalid in ('!' * 1_000_000, '!' * 1_000_000 + '@!', None, True, {}):
                with self.subTest(operation=operation, field=field, kind=type(invalid).__name__):
                    self.assertFalse(contract.valid_connection_action({
                        'operation': operation, 'request': {**request, field: invalid}}))


class AttendeeTests(unittest.TestCase):
    def test_structural_mailbox_matches_the_previous_bounded_wire_grammar(self):
        previous = re.compile(r'[^\s@<>\x00-\x1f\x7f]+@[^\s@<>\x00-\x1f\x7f]+')
        alphabet = ['a', '!', '@', '<', '>', '\x00', '\x1f', '\x7f', '\t', '\r', '\n',
                    ' ', '\x85', '\u00a0', '\u200b', '\u2028', 'é', '例', '\ud800']
        samples = [''.join(chars) for chars in itertools.product(alphabet[:6], repeat=3)]
        samples += [side + '@domain' for side in alphabet] + ['local@' + side for side in alphabet]
        samples += ['a@b', '!@!', 'a@' + 'b' * 318, 'a@' + 'b' * 319, '@b', 'a@', 'a@@b']
        for value in samples:
            with self.subTest(value=ascii(value)):
                expected = len(value) <= 320 and previous.fullmatch(value) is not None
                self.assertEqual(contract._attendee(value), expected)

    def test_adversarial_mailboxes_are_bounded_without_regex(self):
        for value in ('!' * 1_000_000, '!' * 1_000_000 + '@x', 'x@' + '!' * 1_000_000,
                      '@' * 1_000_000, 'a@b@c', None, 1, True):
            self.assertFalse(contract._attendee(value))
        self.assertTrue(contract._attendee('!' * 159 + '@' + '!' * 160))

    @staticmethod
    def draft(address):
        return {'proposalId': 'gmail-draft-1712345678901-a1b2c3d4', 'sha256': 'a' * 64,
                'state': 'waiting', 'createdAt': '2026-10-29T00:00:00Z', 'tooLarge': False,
                'draft': {'to': [address], 'cc': [], 'subject': 'Example', 'body': 'Example body', 'replyTo': None}}

    @staticmethod
    def calendar(address):
        return {'proposalId': 'calendar-1712345678901-a1b2c3d4', 'sha256': 'a' * 64,
                'state': 'waiting', 'action': 'create', 'createdAt': '2026-10-29T00:00:00Z',
                'tooLarge': False, 'event': None,
                'change': {'summary': 'Example', 'start': None, 'end': None, 'allDay': None,
                           'timeZone': None, 'location': None, 'description': None,
                           'attendees': [address], 'sendUpdates': 'none'}}

    def test_both_public_reply_callers_preserve_the_320_character_bound(self):
        for operation, key, make_entry in (('connection-drafts', 'drafts', self.draft),
                                            ('connection-proposals', 'proposals', self.calendar)):
            for address in ('a@b', '!' * 159 + '@' + '!' * 160):
                with self.subTest(operation=operation, acceptedLength=len(address)):
                    value = {key: [make_entry(address)], 'more': 0}
                    self.assertEqual(contract.public_connection_action(operation, value), value)
            for address in ('!' * 1_000_000, 'a@' + 'b' * 319, 'a@@b', 'a@b\n'):
                with self.subTest(operation=operation, refusedLength=len(address)):
                    with self.assertRaisesRegex(ValueError, '^invalid-connection-reply$'):
                        contract.public_connection_action(operation, {key: [make_entry(address)], 'more': 0})


if __name__ == '__main__':
    unittest.main()
