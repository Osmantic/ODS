#!/usr/bin/env python3
"""Native retirement must reject mismatched authority before mutation."""
import importlib.util
import base64
import copy
from contextlib import contextmanager
import hashlib
import json
import os
import plistlib
from pathlib import Path
import tempfile
import sys
import subprocess
import time
import uuid
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

    def test_completed_model_changes_allow_retirement(self):
        names = {'installation.json', 'lock', 'model-before.json',
                 'model-completed.json', 'model-route-completed.json',
                 'model-promotion-completed.json'}
        retirement.validate_state_names(names)
        for pending in retirement.PENDING:
            with self.subTest(pending=pending), self.assertRaisesRegex(
                    ValueError, 'native-retirement-transition-pending'):
                retirement.validate_state_names(names | {pending})

    def test_model_record_allowlist_remains_exact(self):
        for name in ('model-journal.json', 'model-new.json', '../model-before.json',
                     'model-before.json.bak', 'model-route-completed.json/foreign'):
            with self.subTest(name=name), self.assertRaisesRegex(
                    ValueError, 'native-retirement-unknown-protected-state'):
                retirement.validate_state_names({'model-before.json', name})

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
            value = self.sandbox()
            value['Mounts'][1].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'mount-mismatch'):
                self.select_sandbox(value)

    def test_sandbox_preserved_container_skipped_only_when_stopped(self):
        value = self.sandbox()
        value['Name'] = '/ods-pixel-retired-' + 'a' * 16
        value['State']['Running'] = False
        self.assertIsNone(self.select_sandbox(value))
        value['State']['Running'] = True
        with self.assertRaisesRegex(ValueError, 'name-mismatch'): self.select_sandbox(value)

    def test_sandbox_retirement_stops_then_renames_exact_id(self):
        value = self.sandbox()
        plan = self.select_sandbox(value)
        client = object.__new__(retirement.NativeSandboxes)
        def call(*args):
            if args[0] == 'stop': value['State']['Running'] = False
            elif args[0] == 'rename': value['Name'] = '/' + args[2]
        with patch.object(client, 'inspect', side_effect=lambda _: copy.deepcopy(value)), \
                patch.object(client, 'call', side_effect=call) as commands:
            client.preserve([plan])
            self.assertEqual(commands.call_args_list[0].args, ('stop', '--time', '10', 'a' * 64))
            self.assertEqual(commands.call_args_list[1].args, ('rename', 'a' * 64, 'ods-pixel-retired-' + 'a' * 16))
            commands.reset_mock()
            client.preserve([plan])
            commands.assert_not_called()

    def test_sandbox_mount_order_changes_between_inspections(self):
        value = self.sandbox()
        plan = copy.deepcopy(self.select_sandbox(value))
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
            commands.reset_mock()
            client.preserve([plan])
            commands.assert_not_called()

    def test_sandbox_changed_mount_fields_fail_before_stop(self):
        for field, replacement in [('Source', '/foreign'), ('RW', False),
                ('Type', 'volume'), ('Mode', 'unexpected'), ('Propagation', 'rshared'),
                ('Destination', '/foreign')]:
            value = self.sandbox()
            plan = copy.deepcopy(self.select_sandbox(value))
            value['Mounts'][0][field] = replacement
            value['Mounts'].reverse()
            client = object.__new__(retirement.NativeSandboxes)
            with self.subTest(field=field), patch.object(client, 'inspect', return_value=value), \
                    patch.object(client, 'call') as commands:
                with self.assertRaisesRegex(ValueError, 'identity-changed'): client.preserve([plan])
                commands.assert_not_called()

    def test_sandbox_duplicate_missing_and_malformed_mounts_fail_before_stop(self):
        original = self.sandbox()
        plan = copy.deepcopy(self.select_sandbox(original))
        for mounts in [original['Mounts'][:1], original['Mounts'] * 2, None, {},
                ['bad'], [{'Destination': None}], [{'Destination': ''}]]:
            value = copy.deepcopy(original)
            value['Mounts'] = mounts
            client = object.__new__(retirement.NativeSandboxes)
            with self.subTest(mounts=mounts), patch.object(client, 'inspect', return_value=value), \
                    patch.object(client, 'call') as commands:
                with self.assertRaisesRegex(ValueError, 'identity-changed'): client.preserve([plan])
                commands.assert_not_called()

    def test_sandbox_identity_changed_fails_before_stop(self):
        value = self.sandbox()
        plan = self.select_sandbox(value)
        changed = copy.deepcopy(value)
        changed['Image'] = 'sha256:' + 'c' * 64
        client = object.__new__(retirement.NativeSandboxes)
        with patch.object(client, 'inspect', return_value=changed), patch.object(client, 'call') as commands:
            with self.assertRaisesRegex(ValueError, 'identity-changed'): client.preserve([plan])
            commands.assert_not_called()

    def test_prune_retired_removes_only_this_owners_stopped_retired_sandboxes(self):
        def container(cid, name, running=False, uid='501'):
            value = self.sandbox()
            value.update(Id=cid, Name=name)
            value['State']['Running'] = running
            value['Config']['Labels']['org.osmantic.pixel.sandbox-uid'] = uid
            return value
        old, running, foreign, kept, live = ('b' * 64, 'c' * 64, 'd' * 64, 'e' * 64, 'f' * 64)
        inventory = {
            old: container(old, '/ods-pixel-retired-' + old[:16]),
            running: container(running, '/ods-pixel-retired-' + running[:16], running=True),
            foreign: container(foreign, '/ods-pixel-retired-' + foreign[:16], uid='502'),
            kept: container(kept, '/ods-pixel-retired-' + kept[:16]),
            live: container(live, '/pixel-sbx-agent-pixel-' + 'f' * 8),
        }
        client = object.__new__(retirement.NativeSandboxes)
        client.owner = self.owner
        def call(*args):
            if args[:2] == ('ps', '-aq'): return '\n'.join(inventory) + '\n'
            return ''
        with patch.object(client, 'inspect', side_effect=lambda cid: copy.deepcopy(inventory[cid])), \
                patch.object(client, 'call', side_effect=call) as commands:
            client.prune_retired({kept})
        removed = [c.args[1] for c in commands.call_args_list if c.args[0] == 'rm']
        self.assertEqual(removed, [old])

    def test_prune_superseded_retirements_keeps_newest_and_incomplete(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            def archive(name, receipt):
                path = base / name
                path.mkdir()
                (path / 'item-0').mkdir()
                if receipt is not None:
                    (path / 'receipt.json').write_text(json.dumps(receipt))
                return path
            current = archive('a' * 32, {'schema': 1, 'status': 'retired'})
            completed = archive('b' * 32, {'schema': 1, 'status': 'retired'})
            interrupted = archive('c' * 32, {'schema': 1, 'status': 'retiring'})
            unreadable = archive('d' * 32, None)
            (unreadable / 'receipt.json').write_text('{"schema": 1, "status": ')
            other = archive('not-an-archive', {'schema': 1, 'status': 'retired'})
            retirement.prune_superseded_retirements(current)
            self.assertTrue(current.exists())
            self.assertFalse(completed.exists())
            self.assertTrue(interrupted.exists())
            self.assertTrue(unreadable.exists())
            self.assertTrue(other.exists())

    def test_retire_prunes_only_after_its_own_receipt_is_retired(self):
        source = (ROOT / 'installers/macos/lib/pixel-native-uninstall.py').read_text()
        retired = source.index("receipt['status'] = 'retired'")
        self.assertLess(retired, source.index('sandboxes.prune_retired('))
        self.assertLess(retired, source.index('prune_superseded_retirements(archive)'))

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


class RetirementReboot(unittest.TestCase):
    """Real file moves, with launchd and boot identity provided by a test host."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.autostart = self.root / 'LaunchDaemons'
        self.autostart.mkdir()
        self.plists = tuple(self.autostart / ('com.ods.pixel-' + label + '.plist')
                            for label in retirement.LABELS)
        for path in self.plists:
            path.write_bytes(('original:' + path.name).encode())
        self.settings = self.state / 'installation.json'
        self.settings.write_text('{"phase":"active"}')
        self.snapshots = {str(path): path.read_bytes() for path in (*self.plists, self.settings)}
        self.hashes = {name: hashlib.sha256(body).hexdigest() for name, body in self.snapshots.items()}
        self.targets = tuple('system/' + path.stem for path in self.plists)
        self.loaded = set(self.targets)
        self.calls = []
        self.boot = '11111111-1111-4111-8111-111111111111'
        self.next_boot = '22222222-2222-4222-8222-222222222222'
        self.module = retirement.helper('pixel-native-retirement-reboot')
        self.recovery = self.load()

    @contextmanager
    def directory(self, path, create=False):
        if create: path.mkdir(exist_ok=True)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)

    def save(self, path, value):
        path.write_text(json.dumps(value))

    def command(self, args):
        self.calls.append(args)
        self.assertEqual(args[:2], ['/bin/launchctl', 'bootout'])
        self.assertTrue(all(not path.exists() for path in self.plists))
        self.assertTrue(self.settings.exists())
        self.loaded.discard(args[2])
        return SimpleNamespace(returncode=0)

    def load(self):
        return self.module.Recovery(self.state, self.plists, read=lambda path: path.read_bytes(),
            directory=self.directory, save=self.save, command=self.command,
            absent=lambda target: target not in self.loaded)

    def prepare(self, boot=None):
        return self.recovery.prepare(owner=501, boot=boot or self.boot,
            hashes=self.hashes, snapshots=self.snapshots)

    def verify(self, boot=None):
        return self.recovery.verify_reboot(owner=501, boot=boot or self.next_boot,
            hashes=self.hashes, snapshots=self.snapshots)

    def test_stopped_gateway_preparation_preserves_originals_and_requires_reboot(self):
        # A loaded job without a PID still needs unloading; launchd presence
        # is deliberately independent of process-tree availability here.
        self.assertEqual(self.prepare(), {'status': 'reboot-required', 'phase': 'awaiting-reboot',
            'retired': False, 'installationPreserved': True})
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.loaded, set())
        for path in self.plists:
            self.assertFalse(path.exists())
            self.assertEqual((self.recovery.archive / path.name).read_bytes(), self.snapshots[str(path)])
        self.assertEqual(self.settings.read_bytes(), self.snapshots[str(self.settings)])
        with self.assertRaisesRegex(ValueError, 'reboot-required'): self.verify(self.boot)
        self.assertEqual(self.verify(), {target: () for target in self.targets})

    def test_repeated_prepare_does_not_repeat_stops_or_reset_the_boot_barrier(self):
        self.prepare()
        self.calls.clear()
        self.recovery = self.load()
        self.prepare()
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(ValueError, 'use-resume'): self.prepare(self.next_boot)

    def test_foreign_or_changed_authority_never_passes(self):
        self.prepare()
        for owner, hashes in ((502, self.hashes), (501, {**self.hashes, 'foreign': 'hash'})):
            with self.subTest(owner=owner), self.assertRaisesRegex(ValueError, 'authority-mismatch'):
                self.recovery.verify_reboot(owner=owner, boot=self.next_boot,
                    hashes=hashes, snapshots=self.snapshots)
        self.settings.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'authority-changed'): self.verify()

    def test_restored_plist_duplicate_archive_and_changed_archive_fail_closed(self):
        self.prepare()
        original = self.plists[0]
        archived = self.recovery.archive / original.name
        original.write_bytes(archived.read_bytes())
        with self.assertRaisesRegex(ValueError, 'location-ambiguous'): self.verify()
        archived.unlink()
        with self.assertRaisesRegex(ValueError, 'restored-before-recovery'): self.verify()
        original.rename(archived)
        archived.write_text('replacement')
        with self.assertRaisesRegex(ValueError, 'authority-changed'): self.verify()

    def test_reappeared_job_blocks_resume_and_idempotent_prepare(self):
        self.prepare()
        self.loaded.add(self.targets[0])
        with self.assertRaisesRegex(ValueError, 'job-reappeared'): self.verify()
        with self.assertRaisesRegex(ValueError, 'job-reappeared'): self.prepare()

    def test_existing_normal_witness_cannot_be_replaced(self):
        self.save(self.recovery.receipt, {'schema': 1, 'trees': {'previous': [[1, 2, 3]]}})
        self.recovery = self.load()
        before = self.recovery.receipt.read_bytes()
        with self.assertRaisesRegex(ValueError, 'existing-stop-witness'): self.prepare()
        self.assertEqual(self.recovery.receipt.read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_unbound_or_foreign_archive_is_refused(self):
        self.recovery.archive.mkdir()
        with self.assertRaisesRegex(ValueError, 'unbound-plist-archive'): self.load()
        self.recovery.archive.rmdir()
        self.prepare()
        (self.recovery.archive / 'foreign').write_text('unrelated')
        with self.assertRaisesRegex(ValueError, 'foreign-plist-archive'): self.load()

    def test_rename_interruption_retains_a_resumable_journal(self):
        rename = os.rename
        def interrupt(source, destination):
            if source == self.plists[2]: raise OSError('injected interruption')
            rename(source, destination)
        with patch.object(self.module.os, 'rename', side_effect=interrupt):
            with self.assertRaisesRegex(OSError, 'injected'): self.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(json.loads(self.recovery.receipt.read_text())['phase'], 'staging')
        self.recovery = self.load()
        self.prepare()
        self.verify()

    def test_reboot_during_partial_staging_requires_another_reboot(self):
        with patch.object(self.module.os, 'rename', side_effect=OSError('injected')):
            with self.assertRaises(OSError): self.prepare()
        self.recovery = self.load()
        with self.assertRaisesRegex(ValueError, 'staging-incomplete'): self.verify()
        self.prepare(self.next_boot)
        with self.assertRaisesRegex(ValueError, 'reboot-required'): self.verify(self.next_boot)
        self.verify('33333333-3333-4333-8333-333333333333')

    def test_bootout_failure_never_publishes_awaiting_reboot(self):
        with patch.object(self.recovery, 'command', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaisesRegex(ValueError, 'recovery-stop-failed'): self.prepare()
        self.assertEqual(self.recovery.document['phase'], 'staging')
        self.assertTrue(all((self.recovery.archive / path.name).exists() for path in self.plists))
        self.recovery = self.load()
        self.prepare()
        self.verify()

    def test_changed_receipt_is_not_overwritten(self):
        self.prepare()
        changed = dict(self.recovery.document, owner=502)
        self.save(self.recovery.receipt, changed)
        with self.assertRaisesRegex(ValueError, 'receipt-changed'): self.verify()
        self.assertEqual(json.loads(self.recovery.receipt.read_text()), changed)

    def test_invalid_schema_fields_are_not_adopted(self):
        self.prepare()
        original = self.recovery.document
        for change in ({'kind': 'foreign'}, {'owner': True}, {'boot': 'bad'},
                       {'phase': 'complete'}, {'hashes': []}, {'extra': True}):
            with self.subTest(change=change):
                self.save(self.recovery.receipt, dict(original, **change))
                with self.assertRaises(ValueError): self.load()

    def test_conflicting_cli_modes_fail_before_accessing_state(self):
        with patch.object(retirement.pwd, 'getpwnam') as lookup:
            with self.assertRaisesRegex(ValueError, 'conflicting-modes'):
                retirement.retire('/fixture', 'owner', validate_only=True, prepare_stopped_recovery=True)
        lookup.assert_not_called()


class RetirementRebootIntegration(unittest.TestCase):
    """Exercise the real retire orchestration on a disposable filesystem.

    Only host authority, launchd, Docker and kernel process/boot queries are
    fixtures. This is not privileged hardware or actual reboot evidence.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        class FixturePath(type(Path())):
            def lstat(path):
                value = super().lstat()
                return SimpleNamespace(st_mode=value.st_mode,
                    st_uid=501 if path.name == 'ods-pixel-manager' else 0, st_gid=0)
        def mapped(value):
            value = str(value)
            if value.startswith(('/private/var/lib/', '/private/var/run/',
                                 '/private/etc/ods', '/usr/local/libexec/', '/Library/LaunchDaemons')):
                return FixturePath(self.root / 'host' / value.lstrip('/'))
            return FixturePath(value)
        self.path = mapped
        self.install = mapped(self.root / 'ods')
        self.install.mkdir()
        (self.install / 'data/models').mkdir(parents=True)
        (self.install / 'data/models/retained.gguf').write_bytes(b'retained-model-fixture')
        (self.install / '.env').write_text('OWNER_SELECTION=retained\n')
        self.state = mapped('/private/var/lib/ods-pixel-access')
        self.state.mkdir(parents=True)
        self.settings = mapped('/private/etc/ods/pixel-access.json')
        self.settings.parent.mkdir(parents=True)
        for name in retirement.DIRECTORIES: mapped(name).mkdir(parents=True)
        (mapped('/private/var/lib/ods-pixel-native-config') / '501').mkdir()
        self.plists = [mapped('/Library/LaunchDaemons') / ('com.ods.pixel-' + label + '.plist')
                       for label in retirement.LABELS]
        self.plists[0].parent.mkdir(parents=True)
        definitions = {}
        for label, path in zip(retirement.LABELS, self.plists):
            user = ('root' if label in ('access', 'native-promoter') else
                    '_ods_pixel_ops' if label == 'native-operations' else 'owner')
            definition = {'Label': path.stem, 'UserName': user,
                'ProgramArguments': ['/usr/bin/env', '-i', 'HOME=' + str(self.install / 'data/pixel-native/home')]}
            path.write_bytes(plistlib.dumps(definition))
            definitions[label.removeprefix('native-')] = {'path': str(path),
                'body': base64.b64encode(path.read_bytes()).decode()}
        self.settings.write_text(json.dumps({'owner': 'owner', 'install_dir': str(self.install),
            'settings_data_dir': str(self.install / 'data'), 'state_dir': str(self.state),
            'gateway_binding': {'definition': 'fixture'}}))
        (self.state / 'installation.json').write_text(json.dumps({'owner': 501, 'phase': 'active'}))
        (self.state / 'service-installation.json').write_text(json.dumps({'owner': 501,
            'progress': {'phase': 'services-active'},
            'selection': {'bundle': str(self.install / 'data/pixel-native/preparation/services')},
            'recovery': {'definitions': definitions}}))
        (self.state / 'ops-identity.json').write_text('{}')
        (self.state / 'lock').touch()
        self.loaded = {'system/' + path.stem: (None if index == 0 else 1000 + index)
                       for index, path in enumerate(self.plists)}
        self.calls = []
        self.boot = '11111111-1111-4111-8111-111111111111'
        class ProcessError(ValueError):
            errno = 3
        def gone(pid): raise ProcessError('gone')
        custody = SimpleNamespace(protected_directory=self.directory,
            protected_bytes=lambda path, **kw: path.read_bytes(),
            protected_tree_metadata=lambda path: None, _verify_fd=lambda *a, **kw: None,
            verify_loaded_launchd_definition=lambda *a: None)
        modules = {'pixel_macos_custody': custody,
            'pixel_macos_process': SimpleNamespace(process_tree_snapshot=lambda pid: ((pid, 123, 456),),
                process_birth=gone, ProcessIdentityError=ProcessError),
            'pixel_gateway_service': SimpleNamespace(launchd_definition_digest=lambda *a: 'fixture'),
            'pixel_access_bridge': SimpleNamespace(atomic_json=lambda path, value: path.write_text(json.dumps(value)))}
        account = SimpleNamespace(validate_intent=lambda value: {'id': 700},
            verify_record=lambda *a, **kw: None, read_record=lambda *a: {},
            expected_attributes=lambda *a: {}, verify_identity_only=lambda: None,
            verify_empty_home_only=lambda: None)
        helper = retirement.helper
        def selected_helper(name):
            if name == 'pixel-native-ops-account': return account
            if name == 'pixel-runtime-bundle': return SimpleNamespace(verify=lambda *a, **kw: None)
            return helper(name)
        sandbox = SimpleNamespace(plan=lambda: [], preserve=lambda plans: None, prune_retired=lambda keep: None)
        patches = [patch.dict(sys.modules, modules), patch.object(retirement, 'Path', side_effect=mapped),
            patch.object(retirement, 'STATE', self.state), patch.object(retirement, 'SETTINGS', self.settings),
            patch.object(retirement, 'BROKER', mapped('/private/var/lib/pixel-ops-broker')),
            patch.object(retirement, 'helper', side_effect=selected_helper),
            patch.object(retirement, 'NativeSandboxes', return_value=sandbox),
            patch.object(retirement.sys, 'platform', 'darwin'), patch.object(retirement.os, 'geteuid', return_value=0),
            patch.object(retirement.pwd, 'getpwnam', return_value=SimpleNamespace(pw_name='owner', pw_uid=501,
                pw_gid=20, pw_dir=str(self.root / 'home'))),
            patch.object(retirement, 'command', side_effect=self.command)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    @contextmanager
    def directory(self, path, create=False):
        if create: path.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)

    def command(self, args):
        self.calls.append(args)
        if args[:3] == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return SimpleNamespace(returncode=0, stdout=self.boot)
        if args[:2] == ['/bin/launchctl', 'print']:
            pid = self.loaded.get(args[2])
            body = '\tstate = waiting\n' if pid is None else '\tstate = running\n\tpid = ' + str(pid) + '\n'
            return SimpleNamespace(returncode=0 if args[2] in self.loaded else 113, stdout=body)
        if args[:2] == ['/bin/launchctl', 'bootout']:
            del self.loaded[args[2]]
            return SimpleNamespace(returncode=0, stdout='')
        self.fail('unexpected host command')

    def run_retire(self, **kwargs):
        return retirement.retire(str(self.install), 'owner', **kwargs)

    def test_reporter_state_to_preparation_reboot_and_retirement(self):
        self.assertFalse((self.install / 'data/pixel-native/preparation').exists())
        with self.assertRaisesRegex(ValueError, 'stopped-job-needs-witness'):
            self.run_retire(validate_only=True)
        self.assertFalse((self.state / 'retirement.json').exists())
        self.assertFalse(any(args[1] == 'bootout' for args in self.calls))
        prepared = self.run_retire(prepare_stopped_recovery=True)
        self.assertEqual(prepared['status'], 'reboot-required')
        self.assertTrue(self.settings.exists())
        self.assertEqual(self.loaded, {})
        with self.assertRaisesRegex(ValueError, 'use-resume-stopped-recovery'): self.run_retire()
        with self.assertRaisesRegex(ValueError, 'reboot-required'):
            self.run_retire(resume_stopped_recovery=True)
        self.boot = '22222222-2222-4222-8222-222222222222'
        self.assertEqual(self.run_retire(validate_only=True)['status'], 'validated')
        self.assertTrue(self.settings.exists())
        result = self.run_retire(resume_stopped_recovery=True)
        self.assertEqual(result['status'], 'retired')
        receipt = json.loads((Path(result['archive']) / 'receipt.json').read_text())
        self.assertEqual(receipt['status'], 'retired')
        archive_move = next(item for item in receipt['moves'] if item['source'] == str(self.state / 'retirement-plists'))
        self.assertEqual({p.name for p in Path(archive_move['archive']).iterdir()}, {p.name for p in self.plists})
        self.assertFalse(self.settings.exists())
        self.assertTrue((self.state / 'ops-identity.json').exists())
        self.assertTrue(self.install.exists())
        self.assertEqual((self.install / 'data/models/retained.gguf').read_bytes(), b'retained-model-fixture')
        self.assertEqual((self.install / '.env').read_text(), 'OWNER_SELECTION=retained\n')

    def test_normal_retirement_and_no_unnecessary_recovery(self):
        self.loaded['system/' + self.plists[0].stem] = 1000
        with self.assertRaisesRegex(ValueError, 'use-normal-retirement'):
            self.run_retire(prepare_stopped_recovery=True)
        self.assertFalse((self.state / 'retirement.json').exists())
        self.assertEqual(self.run_retire(validate_only=True)['status'], 'validated')
        self.assertEqual(self.run_retire()['status'], 'retired')

    def test_pending_transition_is_not_accepted_for_reboot_recovery(self):
        (self.state / 'transition.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'transition-pending'):
            self.run_retire(prepare_stopped_recovery=True)
        self.assertFalse((self.state / 'retirement.json').exists())
        self.assertTrue(all(path.exists() for path in self.plists))
        self.assertFalse(any(args[1] == 'bootout' for args in self.calls))

    def test_new_boot_does_not_bypass_changed_authority_or_reappeared_job(self):
        self.run_retire(prepare_stopped_recovery=True)
        self.boot = '22222222-2222-4222-8222-222222222222'
        self.loaded['system/' + self.plists[0].stem] = 9999
        with self.assertRaisesRegex(ValueError, 'job-reappeared'):
            self.run_retire(resume_stopped_recovery=True)
        self.loaded.clear()
        original = (self.state / 'ops-identity.json').read_bytes()
        (self.state / 'ops-identity.json').write_text('{"changed": true}')
        with self.assertRaisesRegex(ValueError, 'authority-mismatch'):
            self.run_retire(resume_stopped_recovery=True)
        (self.state / 'ops-identity.json').write_bytes(original)
        self.assertTrue(self.settings.exists())
        self.assertTrue((self.state / 'retirement-plists').exists())

    def test_explicit_resume_requires_preparation(self):
        with self.assertRaisesRegex(ValueError, 'reboot-preparation-required'):
            self.run_retire(resume_stopped_recovery=True)
        self.assertFalse(any(args[1] == 'bootout' for args in self.calls))


@unittest.skipUnless(sys.platform == 'darwin' and os.environ.get('ODS_TEST_LAUNCHD_QUARANTINE') == '1',
                     'opt-in isolated GUI launchd fixture; does not touch native Pixel jobs')
class LivePlistQuarantine(unittest.TestCase):
    def test_launchd_keeps_bound_definition_after_plist_move_and_can_unload(self):
        spec = importlib.util.spec_from_file_location('quarantine_custody', ROOT / 'bin/pixel_macos_custody.py')
        custody = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(custody)
        target = 'gui/' + str(os.getuid()) + '/com.ods.test-retirement-' + uuid.uuid4().hex
        def call(*args):
            return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=10)
        with tempfile.TemporaryDirectory(prefix='ods-retirement-launchd-') as temporary:
            root = Path(temporary).resolve()
            source = root / 'fixture.plist'
            definition = {'Label': target.rsplit('/', 1)[1], 'RunAtLoad': True,
                'ProgramArguments': ['/usr/bin/env', '-i', '/bin/sleep', '60'],
                'WorkingDirectory': str(root), 'StandardOutPath': str(root / 'stdout'),
                'StandardErrorPath': str(root / 'stderr')}
            source.write_bytes(plistlib.dumps(definition))
            try:
                result = call('bootstrap', target.rsplit('/', 1)[0], str(source))
                self.assertEqual(result.returncode, 0, result.stderr)
                before = call('print', target)
                self.assertEqual(before.returncode, 0, before.stderr)
                custody.verify_loaded_launchd_definition(before.stdout, target, str(source), definition)
                source.rename(root / 'quarantined.plist')
                after = call('print', target)
                self.assertEqual(after.returncode, 0, after.stderr)
                custody.verify_loaded_launchd_definition(after.stdout, target, str(source), definition)
                self.assertEqual(call('bootout', target).returncode, 0)
                deadline = time.monotonic() + 5
                while call('print', target).returncode != 113:
                    self.assertLess(time.monotonic(), deadline, 'fixture job remained loaded')
                    time.sleep(0.05)
            finally:
                call('bootout', target)


if __name__ == '__main__':
    unittest.main()
