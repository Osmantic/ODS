#!/usr/bin/env python3
"""Native retirement must reject mismatched authority before mutation."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('retirement', ROOT / 'installers/macos/lib/pixel-native-uninstall.py')
retirement = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retirement)


class RetirementSelection(unittest.TestCase):
    def setUp(self):
        self.owner = SimpleNamespace(pw_name='owner', pw_uid=501)
        self.root = Path('/Users/owner/ods')
        self.settings = dict(owner='owner', install_dir=str(self.root),
            settings_data_dir=str(self.root / 'data'), state_dir=str(retirement.STATE))
        self.installation = dict(owner=501, phase='active')
        self.services = dict(owner=501, progress={'phase': 'services-active'},
            selection={'bundle': str(self.root / 'data/pixel-native/preparation/services')})

    def verify(self):
        return retirement.selected(self.settings, self.installation, self.services,
            owner=self.owner, install_dir=self.root)

    def test_exact_owner_and_root(self):
        self.assertEqual(self.verify(), str(self.root))

    def test_other_install_fails_before_commands(self):
        self.settings['install_dir'] = '/Users/owner/ods-other'
        with patch.object(retirement, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'custody-mismatch'): self.verify()
            command.assert_not_called()

    def test_same_prefix_service_root_is_foreign(self):
        self.services['selection']['bundle'] = '/Users/owner/ods-other/data/pixel-native/services'
        with self.assertRaisesRegex(ValueError, 'service-root-mismatch'): self.verify()

    def test_traversal_in_service_selection_fails(self):
        self.services['selection']['bundle'] = '/Users/owner/ods/data/pixel-native/../../foreign'
        with self.assertRaisesRegex(ValueError, 'service-root-mismatch'): self.verify()

    def test_other_uid_fails(self):
        self.installation['owner'] = 502
        with self.assertRaisesRegex(ValueError, 'custody-mismatch'): self.verify()

    def test_pending_install_or_recovery_fails(self):
        self.installation['phase'] = 'staging'
        with self.assertRaises(ValueError): self.verify()
        self.installation['phase'] = 'active'
        self.services['requiresRecovery'] = True
        with self.assertRaises(ValueError): self.verify()

    def test_unknown_and_pending_state_names_rejected(self):
        for name in ('foreign.json', 'transition.json', 'runtime-upgrade.json', '../installation.json',
                     'runtime-upgrade-not-a-digest.completed.json'):
            with self.subTest(name=name): self.assertFalse(retirement.state_name_allowed(name))
        self.assertTrue(retirement.state_name_allowed('runtime-upgrade-' + 'a' * 64 + '.completed.json'))

    def test_platform_guard_has_no_side_effects(self):
        with patch.object(retirement.sys, 'platform', 'linux'), patch.object(retirement, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'macos-root-required'):
                retirement.retire(str(self.root), 'owner')
            command.assert_not_called()

    def test_uninstaller_orders_retirement_before_container_mutation(self):
        source = (ROOT / 'ods-uninstall.sh').read_text()
        self.assertLess(source.index('pixel-native-uninstall.py'), source.index('# A pending Pixel transition'))
        self.assertIn('Native Pixel retirement failed before ODS uninstall mutation', source)


if __name__ == '__main__':
    unittest.main()
