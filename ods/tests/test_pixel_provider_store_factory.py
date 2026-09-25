"""Platform choice is explicit; unsupported systems never get POSIX fallbacks."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_provider import store_factory as F
from pixel_provider.host_api import _directory
from pixel_provider.store import ProviderStore, StoreError
from pixel_provider.vault import CredentialStore
from pixel_provider.windows_store import WindowsProviderStore
from pixel_provider.windows_vault import WindowsCredentialStore


class StoreFactory(unittest.TestCase):
    def test_legacy_scope_directory_helper_retains_its_platform_boundary(self):
        if os.name == 'posix':
            self.assertEqual(_directory('/not-created-by-this-test'),
                             Path('/not-created-by-this-test/pixel-providers'))
        else:
            with self.assertRaises(StoreError) as context:
                _directory('/not-created-by-this-test')
            self.assertEqual(context.exception.code, 'unsupported-platform')

    def test_actual_platform_selects_correct_explicit_classes_without_io(self):
        if os.name == 'nt':
            value, classes = r'C:\not-created-by-this-test', (WindowsProviderStore, WindowsCredentialStore)
        elif os.name == 'posix':
            value, classes = '/not-created-by-this-test', (ProviderStore, CredentialStore)
        else:
            self.skipTest('No qualified storage platform')
        self.assertIs(type(F.provider_store(value)), classes[0])
        self.assertIs(type(F.credential_store(value)), classes[1])

    def test_unknown_platform_refuses_every_factory_entrypoint(self):
        with patch.object(F.os, 'name', 'unqualified'):
            for call in (F.provider_directory, F.provider_store, F.credential_store, F.prepare_directory):
                with self.assertRaises(StoreError) as context:
                    call('/must-not-be-created')
                self.assertEqual(context.exception.code, 'unsupported-platform')
            with self.assertRaises(StoreError) as context:
                with F.existing_directory('/must-not-be-created'):
                    self.fail('Unqualified platform admitted')
            self.assertEqual(context.exception.code, 'unsupported-platform')


if __name__ == '__main__':
    unittest.main()
