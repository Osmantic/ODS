#!/usr/bin/env python3
"""Native retirement must reject mismatched authority before mutation."""
import importlib.util
import copy
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

    def test_resume_requires_same_boot_and_authority(self):
        witness = dict(schema=1, owner=501, boot='boot', hashes={'file': 'digest'},
            trees={'system/com.ods.pixel-access': [[123, 456, 789]]})
        kwargs = dict(owner=501, boot='boot', hashes={'file': 'digest'},
            targets=['system/com.ods.pixel-access'])
        self.assertEqual(retirement.verify_witness(witness, **kwargs), witness['trees'])
        for key, bad in [('boot', 'next-boot'), ('owner', 502), ('hashes', {'file': 'changed'})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                retirement.verify_witness({**witness, key: bad}, **kwargs)

    def test_absence_without_process_birth_witness_is_not_accepted(self):
        kwargs = dict(owner=501, boot='boot', hashes={}, targets=['target'])
        for tree in ([], [[123]], [[True, 456, 789]], [[123, 456, 1000000]]):
            with self.subTest(tree=tree), self.assertRaises(ValueError):
                retirement.verify_witness(dict(schema=1, owner=501, boot='boot', hashes={},
                    trees={'target': tree}), **kwargs)

    def test_uninstaller_orders_retirement_before_container_mutation(self):
        source = (ROOT / 'ods-uninstall.sh').read_text()
        self.assertLess(source.index('pixel-native-uninstall.py'), source.index('# A pending Pixel transition'))
        self.assertIn('Native Pixel retirement failed before ODS uninstall mutation', source)

    def sandbox(self):
        base = str(self.root / 'data/pixel-native/home/.openclaw')
        return {'Id': 'a' * 64, 'Name': '/pixel-sbx-agent-pixel-12345678',
            'Image': 'sha256:' + 'b' * 64, 'State': {'Running': True},
            'Config': {'Labels': {'openclaw.sandbox': '1', 'openclaw.sessionKey': 'agent:pixel',
                'org.osmantic.pixel.sandbox-uid': '501'}},
            'Mounts': [{'Type': 'bind', 'Source': base + '/workspace-pixel', 'Destination': '/workspace', 'RW': True},
                {'Type': 'bind', 'Source': base + '/.ods-exec-control', 'Destination': '/run/pixel-ods-control', 'RW': False}]}

    def select_sandbox(self, value):
        return retirement.sandbox_selection(value, owner=self.owner, root=self.root)

    def test_sandbox_exact_owner_and_mounts(self):
        self.assertEqual(self.select_sandbox(self.sandbox())['id'], 'a' * 64)

    def test_sandbox_foreign_owner_and_extra_mount_refused(self):
        value = self.sandbox()
        value['Config']['Labels']['org.osmantic.pixel.sandbox-uid'] = '502'
        with self.assertRaisesRegex(ValueError, 'owner-mismatch'): self.select_sandbox(value)
        value = self.sandbox()
        value['Mounts'].append({'Type': 'bind', 'Source': '/Users/foreign', 'Destination': '/foreign'})
        with self.assertRaisesRegex(ValueError, 'mount-mismatch'): self.select_sandbox(value)

    def test_sandbox_foreign_root_and_other_consumers_untouched(self):
        value = self.sandbox()
        for mount in value['Mounts']: mount['Source'] = mount['Source'].replace('/ods/', '/ods-other/')
        self.assertIsNone(self.select_sandbox(value))
        value = self.sandbox()
        value['Name'] = '/ods-pixel-workspace-preview'
        value['Config']['Labels'] = {'com.docker.compose.project': 'ods'}
        self.assertIsNone(self.select_sandbox(value))

    def test_sandbox_wrong_control_binding_refused(self):
        for change in ({'Source': '/foreign'}, {'RW': True}, {'Type': 'volume'}):
            value = self.sandbox(); value['Mounts'][1].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'mount-mismatch'):
                self.select_sandbox(value)

    def test_sandbox_preserved_container_skipped_only_when_stopped(self):
        value = self.sandbox(); value['Name'] = '/ods-pixel-retired-' + 'a' * 16
        value['State']['Running'] = False
        self.assertIsNone(self.select_sandbox(value))
        value['State']['Running'] = True
        with self.assertRaisesRegex(ValueError, 'name-mismatch'): self.select_sandbox(value)

    def test_sandbox_retirement_stops_then_renames_exact_id(self):
        value = self.sandbox(); plan = self.select_sandbox(value)
        client = object.__new__(retirement.NativeSandboxes)
        def call(*args):
            if args[0] == 'stop': value['State']['Running'] = False
            elif args[0] == 'rename': value['Name'] = '/' + args[2]
        with patch.object(client, 'inspect', side_effect=lambda _: copy.deepcopy(value)), \
                patch.object(client, 'call', side_effect=call) as commands:
            client.preserve([plan])
            self.assertEqual(commands.call_args_list[0].args, ('stop', '--time', '10', 'a' * 64))
            self.assertEqual(commands.call_args_list[1].args, ('rename', 'a' * 64, 'ods-pixel-retired-' + 'a' * 16))
            commands.reset_mock(); client.preserve([plan]); commands.assert_not_called()

    def test_sandbox_mount_order_changes_between_inspections(self):
        value = self.sandbox(); plan = copy.deepcopy(self.select_sandbox(value))
        client = object.__new__(retirement.NativeSandboxes)
        def inspect(_):
            value['Mounts'].reverse()
            return copy.deepcopy(value)
        def call(*args):
            if args[0] == 'stop': value['State']['Running'] = False
            elif args[0] == 'rename': value['Name'] = '/' + args[2]
        with patch.object(client, 'inspect', side_effect=inspect), \
                patch.object(client, 'call', side_effect=call) as commands:
            client.preserve([plan])
            self.assertEqual([c.args[0] for c in commands.call_args_list], ['stop', 'rename'])
            commands.reset_mock(); client.preserve([plan]); commands.assert_not_called()

    def test_sandbox_changed_mount_fields_fail_before_stop(self):
        for field, replacement in [('Source', '/foreign'), ('RW', False),
                ('Type', 'volume'), ('Mode', 'unexpected'), ('Propagation', 'rshared'),
                ('Destination', '/foreign')]:
            value = self.sandbox(); plan = copy.deepcopy(self.select_sandbox(value))
            value['Mounts'][0][field] = replacement
            value['Mounts'].reverse()
            client = object.__new__(retirement.NativeSandboxes)
            with self.subTest(field=field), patch.object(client, 'inspect', return_value=value), \
                    patch.object(client, 'call') as commands:
                with self.assertRaisesRegex(ValueError, 'identity-changed'): client.preserve([plan])
                commands.assert_not_called()

    def test_sandbox_duplicate_missing_and_malformed_mounts_fail_before_stop(self):
        original = self.sandbox(); plan = copy.deepcopy(self.select_sandbox(original))
        for mounts in [original['Mounts'][:1], original['Mounts'] * 2, None, {},
                ['bad'], [{'Destination': None}], [{'Destination': ''}]]:
            value = copy.deepcopy(original); value['Mounts'] = mounts
            client = object.__new__(retirement.NativeSandboxes)
            with self.subTest(mounts=mounts), patch.object(client, 'inspect', return_value=value), \
                    patch.object(client, 'call') as commands:
                with self.assertRaisesRegex(ValueError, 'identity-changed'): client.preserve([plan])
                commands.assert_not_called()

    def test_sandbox_identity_changed_fails_before_stop(self):
        value = self.sandbox(); plan = self.select_sandbox(value)
        changed = copy.deepcopy(value); changed['Image'] = 'sha256:' + 'c' * 64
        client = object.__new__(retirement.NativeSandboxes)
        with patch.object(client, 'inspect', return_value=changed), patch.object(client, 'call') as commands:
            with self.assertRaisesRegex(ValueError, 'identity-changed'): client.preserve([plan])
            commands.assert_not_called()

    def test_docker_executes_as_owner_with_bound_context(self):
        self.owner.pw_dir, self.owner.pw_gid = '/Users/owner', 20
        definition = {'ProgramArguments': ['DOCKER_HOST=unix:///Users/owner/.colima/test/docker.sock',
            'DOCKER_CONFIG=/Users/owner/ods/data/pixel-native/home/docker-config',
            'PIXEL_HISTORY_DOCKER=/opt/homebrew/bin/docker']}
        client = retirement.NativeSandboxes(definition, owner=self.owner, root=self.root)
        with patch.object(retirement.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='')) as run:
            client.call('ps', '-aq')
            self.assertEqual(run.call_args.kwargs['user'], 501)
            self.assertEqual(run.call_args.kwargs['extra_groups'], [])
            self.assertEqual(run.call_args.kwargs['env']['DOCKER_HOST'], 'unix:///Users/owner/.colima/test/docker.sock')


if __name__ == '__main__':
    unittest.main()
