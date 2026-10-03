import importlib.util
import io
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('Preview broker requires POSIX ownership and sockets')
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'host/workspace_preview.py'
spec = importlib.util.spec_from_file_location('preview_owner_fixture', SOURCE)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class PreviewOwnerTests(unittest.TestCase):
    def test_request_cli_forwards_only_valid_bounded_publish(self):
        payload = b'{"schemaVersion":1,"action":"publish","relativeDirectory":"demo"}'
        with patch.object(preview.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(payload))), \
                patch.object(preview.sys, 'stdout', io.StringIO()), \
                patch.object(preview, 'client', return_value={'schemaVersion': 1}) as client:
            self.assertEqual(preview.main(['preview', 'request']), 0)
            self.assertEqual(client.call_args.args[0], preview.SOCKET_PATH)
            self.assertEqual(client.call_args.args[1]['relativeDirectory'], 'demo')
        for raw in (b'x' * 2049, payload.replace(b'demo', b'../etc'),
                    payload.replace(b'publish', b'health')):
            with patch.object(preview.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw))), \
                    patch.object(preview.sys, 'stderr', io.StringIO()), \
                    patch.object(preview, 'client') as client:
                self.assertEqual(preview.main(['preview', 'request']), 1)
                client.assert_not_called()

    def test_numeric_identity_requires_matching_real_and_effective_uid(self):
        for uid in (501, 732, 1000):
            with patch.object(preview.os, 'getuid', return_value=uid), \
                    patch.object(preview.os, 'geteuid', return_value=uid), \
                    patch.object(preview.pwd, 'getpwnam') as lookup:
                self.assertEqual(preview.preview_owner_uid(str(uid)), uid)
                lookup.assert_not_called()
                with self.assertRaises(preview.PreviewError):
                    preview.preview_owner_uid(str(uid + 1))
                with patch.object(preview.os, 'geteuid', return_value=0), \
                        self.assertRaises(preview.PreviewError):
                    preview.preview_owner_uid(str(uid))

    def test_invalid_or_root_numeric_owner_rejected(self):
        for owner in ('0', '-1', '0501', '+501', '501\n', '1.0', '', None, 501):
            with self.subTest(owner=owner), self.assertRaises(preview.PreviewError):
                preview.preview_owner_uid(owner)

    def test_named_linux_owner_still_uses_account_database(self):
        with patch.object(preview.pwd, 'getpwnam', return_value=SimpleNamespace(pw_uid=1234)) as lookup:
            self.assertEqual(preview.preview_owner_uid('pixel-owner'), 1234)
            lookup.assert_called_once_with('pixel-owner')


if __name__ == '__main__':
    unittest.main()
