"""Windows retirement uses mocked controllers and disposable files only."""

import importlib.util
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
ENV = {'LEMONADE_HOST_TRANSPORT': 'model-router', 'ODS_WINDOWS_SYSTEM_DIRECTORY': r'C:\Windows\System32'}
spec = importlib.util.spec_from_file_location('retire_wsl_runtime', SOURCE / 'scripts/retire-wsl-runtime.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class RetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / '.env').write_text('LEMONADE_HOST_TRANSPORT=model-router\n'
                                      'ODS_WINDOWS_SYSTEM_DIRECTORY="C:\\Windows\\System32"\nHF_TOKEN=private\n')
        for target, name, value in ((helper.platform, 'system', 'Linux'),
                                    (helper.platform, 'release', 'microsoft-WSL2'),
                                    (helper, '_owner', None),
                                    (helper.wsl_lemonade, 'candidate', True),
                                    (helper.wsl_lemonade, 'status', {'managed': True, 'planDigest': 'a' * 64}),
                                    (helper.wsl_lemonade, 'stop', {'managed': True, 'running': False}),
                                    (helper.wsl_lemonade, 'disable_startup', {'state': 'validated'})):
            patcher = patch.object(target, name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_precheck_reads_binding_without_mutating(self):
        self.assertEqual(helper.retire(self.root, validate_only=True)['state'], 'validated')
        self.stop.assert_not_called()
        self.disable_startup.assert_called_once_with(self.root, ENV, validate_only=True)
        self.status.assert_called_once_with(self.root, ENV)

    def test_custom_state_root_is_loaded_and_forwarded_to_both_startup_checks(self):
        state_root = r'D:\Private ODS $state'
        with (self.root / '.env').open('a') as stream:
            stream.write("ODS_WSL_STATE_ROOT='" + state_root + "'\n")
        expected = {**ENV, 'ODS_WSL_STATE_ROOT': state_root}
        helper.retire(self.root, validate_only=True)
        self.disable_startup.assert_called_once_with(self.root, expected, validate_only=True)
        self.stop.assert_not_called()
        self.disable_startup.reset_mock()
        helper.retire(self.root)
        self.assertEqual(self.disable_startup.call_count, 2)
        self.assertTrue(self.disable_startup.call_args_list[0].kwargs['validate_only'])
        self.assertEqual(self.disable_startup.call_args_list[0].args, (self.root, expected))
        self.disable_startup.assert_called_with(self.root, expected)

    def test_state_root_alone_is_a_management_contract_even_without_model_metadata(self):
        (self.root / '.env').write_text("ODS_WSL_STATE_ROOT='D:\\ODS state'\n")
        self.candidate.return_value = False
        helper.retire(self.root, validate_only=True)
        self._owner.assert_called_once_with(self.root)
        self.disable_startup.assert_called_once_with(self.root, {'ODS_WSL_STATE_ROOT': r'D:\ODS state'}, validate_only=True)
        self.status.assert_not_called()
        self.stop.assert_not_called()

    def test_apply_disables_and_settles_startup_before_stopping_lemonade(self):
        operations = []
        self.status.side_effect = lambda *_: operations.append('status') or {'managed': True, 'planDigest': 'a' * 64}
        self.disable_startup.side_effect = lambda *_, **kw: operations.append('check' if kw else 'disable') or {'state': 'disabled'}
        self.stop.side_effect = lambda *_: operations.append('stop')
        self.assertEqual(helper.retire(self.root)['state'], 'retired')
        self.assertEqual(operations, ['status', 'check', 'disable', 'stop'])
        self.stop.assert_called_once_with(self.root, ENV, 'a' * 64)

    def test_owner_failure_precedes_any_windows_probe(self):
        self._owner.side_effect = ValueError('wrong Linux owner')
        with self.assertRaises(ValueError):
            helper.retire(self.root, validate_only=True)
        self.status.assert_not_called()
        self.disable_startup.assert_not_called()
        self.stop.assert_not_called()

    def test_precheck_binding_failure_never_stops_windows(self):
        self.disable_startup.side_effect = OSError('foreign startup task')
        with self.assertRaises(OSError):
            helper.retire(self.root)
        self.stop.assert_not_called()

    def test_failed_stop_keeps_install_recoverable_and_does_not_replay(self):
        self.stop.side_effect = OSError('uncertain stop')
        with self.assertRaises(OSError):
            helper.retire(self.root)
        self.assertEqual(self.stop.call_count, 1)
        self.assertEqual(self.disable_startup.call_count, 2)
        self.assertTrue((self.root / '.env').exists())

    def test_startup_that_does_not_settle_blocks_lemonade_teardown(self):
        self.disable_startup.side_effect = [{'state': 'validated'}, OSError('startup is still active')]
        with self.assertRaises(OSError):
            helper.retire(self.root)
        self.stop.assert_not_called()
        self.assertTrue((self.root / '.env').exists())

    def test_generic_external_server_is_never_stopped(self):
        self.status.return_value = {'managed': False}
        helper.retire(self.root)
        self.stop.assert_not_called()

    def test_lost_registered_binding_refuses_even_precheck(self):
        (self.root / 'data').mkdir()
        (self.root / 'data/wsl-lemonade-runtime.json').write_text('{}')
        self.status.return_value = {'managed': False}
        with self.assertRaises(ValueError):
            helper.retire(self.root, validate_only=True)
        self.stop.assert_not_called()
        self.disable_startup.assert_not_called()

    def test_other_wsl_backends_only_retire_their_startup_task(self):
        self.candidate.return_value = False
        helper.retire(self.root)
        self.status.assert_not_called()
        self.stop.assert_not_called()
        self.assertEqual(self.disable_startup.call_count, 2)

    def test_registered_runtime_cannot_be_hidden_by_a_changed_transport(self):
        (self.root / 'data').mkdir()
        (self.root / 'data/wsl-lemonade-runtime.json').write_text('{}')
        self.candidate.return_value = False
        with self.assertRaises(ValueError):
            helper.retire(self.root, validate_only=True)
        self.disable_startup.assert_not_called()
        self.stop.assert_not_called()

    def test_non_wsl_backends_do_not_even_read_configuration(self):
        for system, release in (('Darwin', '25'), ('Windows', '11'), ('Linux', '6.8')):
            with self.subTest(system=system), patch.object(helper.platform, 'system', return_value=system), \
                    patch.object(helper.platform, 'release', return_value=release):
                self.assertEqual(helper.retire(self.root), {'state': 'not_wsl'})
        self._owner.assert_not_called()
        self.status.assert_not_called()
        self.stop.assert_not_called()
        self.disable_startup.assert_not_called()

    def test_plain_or_legacy_wsl_without_windows_contract_needs_no_owner_or_interop(self):
        for config in ('GPU_BACKEND=nvidia\n', 'GPU_BACKEND=cpu\n', 'LEMONADE_HOST_TRANSPORT=model-router\n'):
            (self.root / '.env').write_text(config)
            with self.subTest(config=config):
                self.assertEqual(helper.retire(self.root, validate_only=True),
                                 {'state': 'unmanaged_legacy', 'reason': 'no_windows_management_contract'})
                self.assertEqual(helper.retire(self.root)['state'], 'unmanaged_legacy')
        self._owner.assert_not_called()
        self.status.assert_not_called()
        self.stop.assert_not_called()
        self.disable_startup.assert_not_called()

    def test_empty_registered_system_directory_is_not_silently_legacy(self):
        (self.root / '.env').write_text('ODS_WINDOWS_SYSTEM_DIRECTORY=\n')
        with self.assertRaises(ValueError):
            helper.retire(self.root, validate_only=True)
        self._owner.assert_called_once()
        self.disable_startup.assert_not_called()

    def test_registered_runtime_without_system_key_still_requires_ownership(self):
        (self.root / '.env').write_text('LEMONADE_HOST_TRANSPORT=model-router\n')
        (self.root / 'data').mkdir()
        (self.root / 'data/wsl-lemonade-runtime.json').write_text('{}')
        self._owner.side_effect = ValueError('wrong owner')
        with self.assertRaises(ValueError):
            helper.retire(self.root, validate_only=True)
        self.status.assert_not_called()

    def test_redirected_env_cannot_hide_a_managed_contract(self):
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)):
            with self.assertRaises(ValueError):
                helper.retire(self.root, validate_only=True)
        self.disable_startup.assert_not_called()

    def test_environment_detection_is_bounded_and_rejects_replaced_files(self):
        (self.root / '.env').write_bytes(b'x' * 65537)
        with self.assertRaisesRegex(ValueError, 'bounded'):
            helper.retire(self.root, validate_only=True)
        (self.root / '.env').write_text('GPU_BACKEND=nvidia\n')
        with patch.object(helper.os, 'fstat', return_value=SimpleNamespace(st_dev=-1, st_ino=-1)):
            with self.assertRaisesRegex(ValueError, 'changed'):
                helper.retire(self.root, validate_only=True)
        self.disable_startup.assert_not_called()


class OwnerTests(unittest.TestCase):
    def test_root_cannot_borrow_install_owner_windows_authority(self):
        with patch.object(helper.os, 'getuid', return_value=0, create=True):
            with self.assertRaisesRegex(ValueError, 'without sudo'):
                helper._owner(Path('/home/other/ods'))

    def test_regular_owner_and_private_mode_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / '.env').write_text('')
            for uid, mode, links in ((1000, 0o600, 1), (1001, 0o600, 1), (1000, 0o666, 1), (1000, 0o600, 2)):
                def entry(path, **_):
                    return SimpleNamespace(st_uid=uid, st_mode=(stat.S_IFDIR | 0o700) if path == root else stat.S_IFREG | mode,
                                           st_nlink=1 if path == root else links)
                with self.subTest(uid=uid, mode=mode, links=links), \
                        patch.object(helper.os, 'getuid', return_value=1000, create=True), patch.object(Path, 'lstat', entry):
                    if (uid, mode, links) == (1000, 0o600, 1):
                        helper._owner(root)
                    else:
                        with self.assertRaises(ValueError):
                            helper._owner(root)


class HookOrderTests(unittest.TestCase):
    def test_windows_precheck_and_apply_precede_pixel_then_host_agent_removal(self):
        script = (SOURCE / 'ods-uninstall.sh').read_text(encoding='utf-8')
        precheck = script.index('! python3 "$_ods_wsl_retire_helper" --install-dir "$INSTALL_DIR" --validate-only')
        pixel = script.index('if ! ods_pixel_uninstall_managed')
        apply = script.index('if ! python3 "$_ods_wsl_retire_helper" --install-dir "$INSTALL_DIR";')
        host = script.index('if ! ods_uninstall_system_units')
        self.assertLess(precheck, apply)
        self.assertLess(apply, pixel)
        self.assertLess(pixel, host)
        self.assertIn('Windows startup changes already applied for this uninstall remain in effect.', script)


if __name__ == '__main__':
    unittest.main()
