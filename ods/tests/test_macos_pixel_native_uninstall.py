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

    @staticmethod
    def quarantine_names(nonce='5e9bd1134ba942c08ea766f23d29df75', leaf='broker.py', device=16777232, inode=9187483):
        """Names built exactly as recover-runtime --quarantine-empty-source-stage builds them."""
        stem = ('.ods-quarantined-source-file-' + nonce + '-' + hashlib.sha256(leaf.encode()).hexdigest()[:16]
                + '-' + format(device, 'x') + '-' + format(inode, 'x'))
        return {'kit-receipt': '.ods-empty-source-stage-' + nonce + '.json',
                'kit': '.ods-quarantined-source-kit-' + nonce,
                'file': stem + '.file', 'file-receipt': stem + '.json'}

    def test_quarantined_source_stage_is_an_archived_deployment_record(self):
        names = self.quarantine_names()
        # The fleet Mac's Core names after a first-kit quarantine and a later
        # code-only update (Source names are inventoried separately); digests
        # replaced. Before this record family it refused as unknown state.
        old, new = 'a' * 64, 'b' * 64
        roles = ('access', 'gateway', 'manager', 'operations', 'promoter', 'relay')
        mac = {'installation-edge.json', 'installation.json', 'integrations-preservation.json', 'lock',
               'ops-identity.json', 'ops-identity.lock', 'service-baseline.json', 'service-installation.json',
               'verified.json', names['kit-receipt'], names['kit'],
               *('runtime-upgrade-' + digest + '.completed.json' for digest in (old, new)),
               *('runtime-upgrade-' + kind + '-' + digest + '.json' for kind in ('context', 'edge') for digest in (old, new)),
               *('runtime-upgrade-stop-' + old + '-previous-' + role + '.json' for role in roles),
               *('runtime-upgrade-stop-' + new + '-previous-' + role + '.json' for role in (*roles, 'integrations'))}
        retirement.validate_state_names(mac)
        retirement.validate_state_names(mac | {names['file'], names['file-receipt']})
        for form, name in names.items():
            with self.subTest(form=form):
                self.assertTrue(retirement.state_name_allowed(name))
                self.assertTrue(retirement.source_quarantine_record(name))
        self.assertTrue(retirement.source_quarantine_record(self.quarantine_names(device=0, inode=1)['file']))
        # An unfinished quarantine still holds its recovery journal.
        with self.assertRaisesRegex(ValueError, 'native-retirement-transition-pending'):
            retirement.validate_state_names(mac | {'runtime-upgrade.json'})

    def test_quarantined_source_stage_look_alikes_remain_unknown_state(self):
        nonce = '5e9bd1134ba942c08ea766f23d29df75'
        names = self.quarantine_names(nonce)
        stem = names['file'][:-len('.file')]
        head = '.ods-quarantined-source-file-' + nonce + '-' + 'a' * 16
        for name in (
                # The writer's own temporaries are an unfinished seal, not a record.
                '.ods-empty-source-stage-tmp-' + 'c' * 32, '.ods-empty-source-file-tmp-' + 'c' * 32,
                names['kit-receipt'][:-len('.json')], names['kit-receipt'] + '.tmp', names['kit'] + '.json',
                names['kit'] + '/foreign', '../' + names['kit'], names['kit'] + '\n', names['kit'][1:],
                names['kit'][:-1], names['kit'] + 'a', '.ods-empty-source-stage-' + nonce.upper() + '.json',
                '.ods-quarantined-source-' + nonce, '.ods-quarantined-source-kit-' + '\u0661' * 32,
                stem, stem + '.tmp', stem + '.file.json', head + '-01-1.file', head + '-1-0.file',
                head + '-1-01.json', head + '-' + '1' * 17 + '-1.file', head + '-1.file', head + '--1-1.file',
                '.ods-quarantined-source-file-' + nonce + '-' + 'a' * 15 + '-1-1.file',
                '.ods-quarantined-source-file-' + nonce + '-' + 'A' * 16 + '-1-1.json'):
            with self.subTest(name=name):
                self.assertFalse(retirement.source_quarantine_record(name))
                with self.assertRaisesRegex(ValueError, 'native-retirement-unknown-protected-state'):
                    retirement.validate_state_names({'installation.json', names['kit'], names['kit-receipt'], name})

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
        with patch.object(self.recovery, 'command', return_value=SimpleNamespace(returncode=1)), \
                patch.object(self.module.time, 'monotonic', side_effect=[0, 31]):
            with self.assertRaisesRegex(ValueError, 'recovery-stop-failed'): self.prepare()
        self.assertEqual(self.recovery.document['phase'], 'staging')
        self.assertTrue(all((self.recovery.archive / path.name).exists() for path in self.plists))
        self.recovery = self.load()
        self.prepare()
        self.verify()

    def test_delayed_launchd_removal_waits_without_repeating_bootout(self):
        def stop_later(args):
            self.calls.append(args)
            return SimpleNamespace(returncode=0)
        def finish_stop(_delay):
            self.loaded.discard(self.calls[-1][2])
        with patch.object(self.recovery, 'command', side_effect=stop_later), \
                patch.object(self.module.time, 'sleep', side_effect=finish_stop) as sleep:
            self.prepare()
        self.assertEqual(sleep.call_count, 6)
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(self.recovery.document['phase'], 'awaiting-reboot')
        self.verify()

    def test_job_reappearing_during_later_stops_keeps_staging(self):
        def stop(args):
            result = self.command(args)
            if args[2] == self.targets[-1]:
                self.loaded.add(self.targets[0])
            return result
        with patch.object(self.recovery, 'command', side_effect=stop):
            with self.assertRaisesRegex(ValueError, 'job-reappeared'): self.prepare()
        self.assertEqual(self.recovery.document['phase'], 'staging')

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
            if value.startswith(('/private/var/lib/', '/private/var/run/', '/private/var/log/',
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
            if label in ('native-manager', 'native-promoter', 'native-operations'):
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
            protected_inspection_bytes=lambda path, **kw: path.read_bytes() if os.path.lexists(path) else None,
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
        if args == ['/bin/launchctl', 'print-disabled', 'system']:
            return SimpleNamespace(returncode=0, stdout='disabled services = {\n}\n')
        if args[:2] == ['/bin/launchctl', 'bootout']:
            del self.loaded[args[2]]
            return SimpleNamespace(returncode=0, stdout='')
        self.fail('unexpected host command')

    def run_retire(self, **kwargs):
        return retirement.retire(str(self.install), 'owner', **kwargs)

    def test_new_seventh_service_reboot_retirement_preserves_owner_state_and_private_authority(self):
        import base64
        path = self.path('/Library/LaunchDaemons/com.ods.pixel-native-integrations.plist')
        document = dict(Label=path.stem, UserName='owner', ProgramArguments=['/usr/bin/env', '-i', '/fixture/python'])
        path.write_bytes(plistlib.dumps(document))
        self.plists.append(path)
        self.loaded['system/' + path.stem] = 1007
        journal = self.state / 'service-installation.json'
        value = json.loads(journal.read_text())
        value['recovery']['definitions']['integrations'] = dict(path=str(path), body=base64.b64encode(path.read_bytes()).decode())
        journal.write_text(json.dumps(value))
        for name in retirement.INTEGRATION_DIRECTORIES: self.path(name).mkdir(parents=True)
        auth = self.path('/private/var/lib/ods-pixel-integrations-auth/501')
        auth.mkdir()
        (auth / 'owner.key').write_bytes(b'private retained authority')
        owner_state = self.root / 'home/.config/ods/pixel-integrations'
        owner_state.mkdir(parents=True)
        (owner_state / 'config.json').write_bytes(b'owner settings remain')
        previous_helper = retirement.helper
        validations = []
        with patch.object(retirement, 'helper', side_effect=lambda name:
                SimpleNamespace(validate_retirement=lambda owner: validations.append(owner))
                if name == 'pixel-native-integrations-service' else previous_helper(name)):
            prepared = self.run_retire(prepare_stopped_recovery=True)
            self.assertEqual(prepared['status'], 'reboot-required')
            self.assertEqual(self.loaded, {})
            self.boot = '22222222-2222-4222-8222-222222222222'
            self.assertEqual(self.run_retire(validate_only=True)['status'], 'validated')
            result = self.run_retire(resume_stopped_recovery=True)
        receipt = json.loads((Path(result['archive']) / 'receipt.json').read_text())
        for name in retirement.INTEGRATION_DIRECTORIES:
            self.assertTrue(any(move['source'] == str(self.path(name)) for move in receipt['moves']))
        auth_move = next(move for move in receipt['moves'] if move['source'] == str(auth.parent))
        self.assertEqual((Path(auth_move['archive']) / '501/owner.key').read_bytes(), b'private retained authority')
        self.assertEqual((owner_state / 'config.json').read_bytes(), b'owner settings remain')
        self.assertEqual(validations, ['owner'] * 4)

    def test_restored_legacy_can_retire_retained_partial_private_preparation_without_reinstall(self):
        preservation = retirement.helper('pixel-native-integrations-preservation')
        owner = retirement.pwd.getpwnam('owner')
        common = b'VERIFIED_COMMON = True\n'
        value = preservation.make(owner=owner, install_dir=self.install,
            settings=self.settings.read_bytes(), services=(self.state / 'service-installation.json').read_bytes(),
            installation=(self.state / 'installation.json').read_bytes(), current_digest='a' * 64,
            candidate_digest='b' * 64, common_body=common)
        marker = self.state / preservation.NAME
        marker.write_bytes(preservation._body(value))
        marker.chmod(0o600)
        # A failure between fixed provisioning steps may leave auth/root only.
        auth = self.path('/private/var/lib/ods-pixel-integrations-auth')
        auth.mkdir(parents=True)
        (auth / '501').mkdir()
        owner_state = self.root / 'home/.config/ods/pixel-integrations'
        owner_state.mkdir(parents=True)
        (owner_state / 'notes-kept.txt').write_bytes(b'owner data preserved')
        previous_helper = retirement.helper
        checks = []
        def verify(owner_name, **kwargs):
            self.assertEqual(owner_name, 'owner')
            self.assertEqual(kwargs, {'common_body': common, 'allow_partial': True})
            checks.append(True)
        with patch.object(retirement, 'helper', side_effect=lambda name:
                SimpleNamespace(validate_preserved=verify) if name == 'pixel-native-integrations-service' else previous_helper(name)):
            self.assertEqual(self.run_retire(prepare_stopped_recovery=True)['status'], 'reboot-required')
            self.boot = '22222222-2222-4222-8222-222222222222'
            result = self.run_retire(resume_stopped_recovery=True)
        receipt = json.loads((Path(result['archive']) / 'receipt.json').read_text())
        auth_move = next(move for move in receipt['moves'] if move['source'] == str(auth))
        self.assertTrue((Path(auth_move['archive']) / '501').is_dir())
        marker_move = next(move for move in receipt['moves'] if move['source'] == str(marker))
        self.assertEqual(Path(marker_move['archive']).read_bytes(), preservation._body(value))
        self.assertEqual((owner_state / 'notes-kept.txt').read_bytes(), b'owner data preserved')
        self.assertEqual(checks, [True] * 3)

    def test_interrupted_preservation_publication_is_archived_without_becoming_authority(self):
        self.loaded['system/' + self.plists[0].stem] = 1000
        pending = self.state / '.integrations-preservation.pending'
        pending.write_bytes(b'{"schemaVersion":')
        pending.chmod(0o600)
        result = self.run_retire()
        receipt = json.loads((Path(result['archive']) / 'receipt.json').read_text())
        moved = next(move for move in receipt['moves'] if move['source'] == str(pending))
        self.assertEqual(Path(moved['archive']).read_bytes(), b'{"schemaVersion":')

    def quarantined_source_stage(self):
        """What a finished empty-stage quarantine leaves: kit directory, one file, sealed receipts."""
        nonce = '5e9bd1134ba942c08ea766f23d29df75'
        kit = self.state / ('.ods-quarantined-source-kit-' + nonce)
        kit.mkdir(mode=0o700)
        orphan = self.state / 'unrecorded-empty-file'
        orphan.write_bytes(b'')
        orphan.chmod(0o600)
        info = orphan.stat()  # The writer names the file by its own retained inode.
        stem = ('.ods-quarantined-source-file-' + nonce + '-' + hashlib.sha256(b'broker.py').hexdigest()[:16]
                + '-' + format(info.st_dev, 'x') + '-' + format(info.st_ino, 'x'))
        empty = self.state / (stem + '.file')
        os.rename(orphan, empty)
        records = {'kit': kit, 'file': empty,
                   'kit-receipt': self.state / ('.ods-empty-source-stage-' + nonce + '.json'),
                   'file-receipt': self.state / (stem + '.json')}
        for form in ('kit-receipt', 'file-receipt'):
            records[form].write_bytes(b'{"sealed":"' + form.encode() + b'"}\n')
            records[form].chmod(0o600)
        return records

    def test_quarantined_source_stage_is_archived_by_rename_and_never_read(self):
        records = self.quarantined_source_stage()
        identity = {form: (path.stat().st_dev, path.stat().st_ino) for form, path in records.items()}
        receipts = {form: records[form].read_bytes() for form in ('kit-receipt', 'file-receipt')}
        self.loaded['system/' + self.plists[0].stem] = 1000
        custody = sys.modules['pixel_macos_custody']
        read, reads = custody.protected_bytes, []
        def recorded(path, **kwargs):
            reads.append(Path(path).name)
            return read(path, **kwargs)
        with patch.object(custody, 'protected_bytes', side_effect=recorded):
            self.assertEqual(self.run_retire(validate_only=True)['status'], 'validated')
            self.assertTrue(all(path.exists() for path in records.values()))
            result = self.run_retire()
        self.assertEqual(result['status'], 'retired')
        # Never authority: no protected read (a directory cannot be one) and no witness binding.
        self.assertTrue(reads)
        self.assertFalse({path.name for path in records.values()} & set(reads))
        receipt = json.loads((Path(result['archive']) / 'receipt.json').read_text())
        self.assertEqual(receipt['status'], 'retired')
        moved = {Path(move['source']).name: Path(move['archive']) for move in receipt['moves'] if move['done']}
        for form, path in records.items():
            with self.subTest(form=form):
                self.assertFalse(os.path.lexists(path))
                saved = moved[path.name]
                self.assertEqual((saved.stat().st_dev, saved.stat().st_ino), identity[form])
        self.assertTrue(moved[records['kit'].name].is_dir())
        self.assertEqual(list(moved[records['kit'].name].iterdir()), [])
        self.assertEqual(moved[records['file'].name].read_bytes(), b'')
        for form, body in receipts.items():
            self.assertEqual(moved[records[form].name].read_bytes(), body)
        witness = json.loads(moved['retirement.json'].read_text())
        self.assertTrue(witness['hashes'])
        self.assertFalse({Path(path).name for path in witness['hashes']} & {path.name for path in records.values()})
        self.assertEqual({path.name for path in self.state.iterdir()}, {'ops-identity.json'})

    def test_quarantined_source_stage_look_alike_refuses_before_any_stop(self):
        records = self.quarantined_source_stage()
        # An interrupted seal leaves the writer's temporary; it is not a record.
        temporary = self.state / ('.ods-empty-source-stage-tmp-' + 'c' * 32)
        temporary.write_bytes(b'{"schemaVersion":')
        temporary.chmod(0o600)
        self.loaded['system/' + self.plists[0].stem] = 1000
        for mode in ({'validate_only': True}, {}, {'prepare_stopped_recovery': True}):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'native-retirement-unknown-protected-state'):
                self.run_retire(**mode)
        self.assertEqual(self.calls, [])
        self.assertFalse((self.state / 'retirement.json').exists())
        self.assertFalse(self.path('/private/var/lib/ods-pixel-retired').exists())
        self.assertTrue(all(path.exists() for path in [temporary, *records.values()]))
        self.assertTrue(all(path.exists() for path in self.plists))

    def test_preservation_never_adopts_a_different_owner_or_unknown_private_residue(self):
        preservation = retirement.helper('pixel-native-integrations-preservation')
        value = preservation.make(owner=retirement.pwd.getpwnam('owner'), install_dir=self.install,
            settings=self.settings.read_bytes(), services=(self.state / 'service-installation.json').read_bytes(),
            installation=(self.state / 'installation.json').read_bytes(), current_digest='a' * 64,
            candidate_digest='b' * 64, common_body=b'VERIFIED = True\n')
        value['owner'] = 502
        marker = self.state / preservation.NAME
        marker.write_bytes(preservation._body(value))
        marker.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'authority-mismatch'):
            self.run_retire(prepare_stopped_recovery=True)
        self.assertFalse(any(args[1] == 'bootout' for args in self.calls))

    def test_restored_legacy_private_quarantine_can_resume_without_losing_prior_archive(self):
        preservation = retirement.helper('pixel-native-integrations-preservation')
        value = preservation.make(owner=retirement.pwd.getpwnam('owner'), install_dir=self.install,
            settings=self.settings.read_bytes(), services=(self.state / 'service-installation.json').read_bytes(),
            installation=(self.state / 'installation.json').read_bytes(), current_digest='a' * 64,
            candidate_digest='b' * 64, common_body=b'VERIFIED = True\n')
        marker = self.state / preservation.NAME
        marker.write_bytes(preservation._body(value))
        marker.chmod(0o600)
        roots = [self.path(name) for name in retirement.INTEGRATION_DIRECTORIES]
        for path in roots: path.mkdir(parents=True)
        self.loaded['system/' + self.plists[0].stem] = 1000
        previous_helper, real_rename = retirement.helper, os.rename
        def fail_auth(source, target):
            if source == roots[1]: raise OSError('interrupted private quarantine')
            return real_rename(source, target)
        with patch.object(retirement, 'helper', side_effect=lambda name:
                SimpleNamespace(validate_preserved=lambda *a, **k: True)
                if name == 'pixel-native-integrations-service' else previous_helper(name)):
            with patch.object(retirement.os, 'rename', side_effect=fail_auth):
                with self.assertRaisesRegex(OSError, 'private quarantine'): self.run_retire()
            self.assertTrue(self.settings.exists())
            self.assertTrue((self.state / 'service-installation.json').exists())
            self.assertFalse(roots[0].exists())
            first = list(self.path('/private/var/lib/ods-pixel-retired').iterdir())
            self.assertEqual(len(first), 1)
            prior = json.loads((first[0] / 'receipt.json').read_text())
            self.assertEqual(prior['status'], 'retiring')
            moved = next(item for item in prior['moves'] if item['source'] == str(roots[0]))
            self.assertTrue(Path(moved['archive']).is_dir())
            result = self.run_retire()
        self.assertEqual(result['status'], 'retired')
        self.assertTrue(first[0].exists())
        self.assertTrue(Path(moved['archive']).is_dir())

    def test_legacy_retirement_refuses_unbound_new_integrations_before_stopping(self):
        self.loaded['system/com.ods.pixel-native-integrations'] = 1007
        with self.assertRaisesRegex(ValueError, 'unbound-integrations'):
            self.run_retire(prepare_stopped_recovery=True)
        self.assertFalse(any(args[1] == 'bootout' for args in self.calls))

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
