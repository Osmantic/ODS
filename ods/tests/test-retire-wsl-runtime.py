"""Windows retirement uses mocked controllers and disposable files only."""

import importlib.util
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
# The bridge gets the round-F key and its Lemonade-era alias (one release).
ENV = {'LEMONADE_HOST_TRANSPORT': 'model-router', 'ODS_HOST_LLM_TRANSPORT': 'model-router',
       'ODS_WINDOWS_SYSTEM_DIRECTORY': r'C:\Windows\System32'}
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
        self.disable_startup.assert_called_once_with(self.root, ENV, validate_only=True, retire_relay=True)
        self.status.assert_called_once_with(self.root, ENV)

    def test_custom_state_root_is_loaded_and_forwarded_to_both_startup_checks(self):
        state_root = r'D:\Private ODS $state'
        with (self.root / '.env').open('a') as stream:
            stream.write("ODS_WSL_STATE_ROOT='" + state_root + "'\n")
        expected = {**ENV, 'ODS_WSL_STATE_ROOT': state_root}
        helper.retire(self.root, validate_only=True)
        self.disable_startup.assert_called_once_with(self.root, expected, validate_only=True, retire_relay=True)
        self.stop.assert_not_called()
        self.disable_startup.reset_mock()
        helper.retire(self.root)
        self.assertEqual(self.disable_startup.call_count, 2)
        self.assertTrue(self.disable_startup.call_args_list[0].kwargs['validate_only'])
        self.assertEqual(self.disable_startup.call_args_list[0].args, (self.root, expected))
        self.assertTrue(self.disable_startup.call_args_list[0].kwargs['retire_relay'])
        self.disable_startup.assert_called_with(self.root, expected, retire_relay=True)

    def test_state_root_alone_is_a_management_contract_even_without_model_metadata(self):
        (self.root / '.env').write_text("ODS_WSL_STATE_ROOT='D:\\ODS state'\n")
        self.candidate.return_value = False
        helper.retire(self.root, validate_only=True)
        self._owner.assert_called_once_with(self.root)
        self.disable_startup.assert_called_once_with(self.root, {'ODS_WSL_STATE_ROOT': r'D:\ODS state'}, validate_only=True, retire_relay=True)
        self.status.assert_not_called()
        self.stop.assert_not_called()

    def test_apply_disables_and_settles_startup_before_stopping_lemonade(self):
        operations = []
        self.status.side_effect = lambda *_: operations.append('status') or {'managed': True, 'planDigest': 'a' * 64}
        self.disable_startup.side_effect = lambda *_, **kw: operations.append('check' if kw.get('validate_only') else 'disable') or {'state': 'disabled'}
        self.stop.side_effect = lambda *_: operations.append('stop')
        self.assertEqual(helper.retire(self.root)['state'], 'retired')
        self.assertEqual(operations, ['status', 'check', 'disable', 'stop'])
        self.stop.assert_called_once_with(self.root, ENV, 'a' * 64)

    def test_migrated_environment_reaches_the_bridge_under_both_names(self):
        (self.root / '.env').write_text('ODS_HOST_LLM_TRANSPORT=model-router\n'
                                      'NATIVE_LLM_BASE_URL=http://localhost:8080\n'
                                      'NATIVE_LLM_CONTAINER_BASE_URL=http://host.docker.internal:8080\n'
                                      'ODS_WINDOWS_SYSTEM_DIRECTORY="C:\\Windows\\System32"\n')
        helper.retire(self.root, validate_only=True)
        self.status.assert_called_once_with(self.root, {
            'ODS_HOST_LLM_TRANSPORT': 'model-router', 'LEMONADE_HOST_TRANSPORT': 'model-router',
            'NATIVE_LLM_BASE_URL': 'http://localhost:8080', 'LEMONADE_BASE_URL': 'http://localhost:8080',
            'NATIVE_LLM_CONTAINER_BASE_URL': 'http://host.docker.internal:8080',
            'LEMONADE_CONTAINER_BASE_URL': 'http://host.docker.internal:8080',
            'ODS_WINDOWS_SYSTEM_DIRECTORY': r'C:\Windows\System32'})

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

    def test_registered_runtime_is_verified_after_routing_changes(self):
        # An API or cloud route leaves the owned Windows task registered.
        # Custody is proven from the task with a control-only environment;
        # startup checks still get the installation's own values.
        (self.root / 'data').mkdir()
        (self.root / 'data/wsl-lemonade-runtime.json').write_text('{}')
        self.candidate.return_value = False
        for key in ('ODS_HOST_LLM_TRANSPORT', 'LEMONADE_HOST_TRANSPORT'):
            for transport in ('direct', 'cloud', ''):
                content = (key + '=' + transport + '\n'
                           'ODS_WINDOWS_SYSTEM_DIRECTORY="C:\\Windows\\System32"\n'
                           'NATIVE_LLM_BASE_URL=\nNATIVE_LLM_CONTAINER_BASE_URL=https://example.com/api\n'
                           'AMD_INFERENCE_PORT=\n')
                (self.root / '.env').write_text(content)
                with self.subTest(key=key, transport=transport):
                    self.assertEqual(helper.retire(self.root, validate_only=True)['state'], 'validated')
                    self.status.assert_called_with(self.root, ENV)
                    self.stop.assert_not_called()
                    self.assertEqual(helper.retire(self.root)['state'], 'retired')
                    self.stop.assert_called_once_with(self.root, ENV, 'a' * 64)
                    self.assertEqual(self.disable_startup.call_args.args[1][key], transport)
                    self.assertEqual((self.root / '.env').read_text(), content)
                    self.stop.reset_mock()

    def test_changed_routing_does_not_allow_unowned_registered_runtime(self):
        (self.root / 'data').mkdir()
        (self.root / 'data/wsl-lemonade-runtime.json').write_text('{}')
        (self.root / '.env').write_text('ODS_HOST_LLM_TRANSPORT=direct\n')
        self.candidate.return_value = False
        for result in ({'managed': False}, OSError('foreign Windows task')):
            self.status.side_effect = result if isinstance(result, Exception) else None
            self.status.return_value = result
            with self.subTest(result=result), self.assertRaises((ValueError, OSError)):
                helper.retire(self.root)
        self.status.assert_called_with(self.root, {'ODS_HOST_LLM_TRANSPORT': 'model-router',
                                                   'LEMONADE_HOST_TRANSPORT': 'model-router'})
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


class WindowsKeptSummaryTests(unittest.TestCase):
    """M25: what stays on Windows after uninstall, each line only when it applies."""

    STORE = r'C:\Users\owner\AppData\Local\ODS\lemonade\models'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.ods = Path(temporary.name).resolve() / 'mnt/c/Users/owner/AppData/Local/ODS'
        self.store = self.ods / 'lemonade/models'
        self.store.mkdir(parents=True)
        self.managed = {'managed': True, 'planDigest': 'a' * 64, 'modelStoreWindowsPath': self.STORE}
        patcher = patch.object(helper.wsl_lemonade, 'model_store', return_value=self.store)
        self.model_store = patcher.start()
        self.addCleanup(patcher.stop)

    def summary(self):
        return helper.windows_kept_summary(Path('/home/owner/ods'), ENV, self.managed)

    def test_nothing_on_windows_gives_no_lines(self):
        self.assertEqual(self.summary(), [])
        self.model_store.assert_called_once_with(Path('/home/owner/ods'), ENV, self.managed)

    def test_model_store_and_llama_cpp_with_their_sizes(self):
        (self.store / 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf').write_bytes(b'x' * 1500)
        llama = self.ods / 'llama.cpp/b11429-win-cuda-13.4-arm64'
        llama.mkdir(parents=True)
        (llama / 'llama-server.exe').write_bytes(b'x' * 500)
        lines = self.summary()
        self.assertEqual(lines, [
            "Windows keeps ODS's model files (" + self.STORE + ", 0.0 GB) and llama.cpp "
            "(C:\\Users\\owner\\AppData\\Local\\ODS\\llama.cpp, 0.0 GB) so a reinstall needs no download; delete the folders to free the space."])

    def test_sizes_are_in_gigabytes(self):
        with patch.object(helper, '_tree_bytes', side_effect=[22_134_528_992, 298_533_031]):
            (self.ods / 'llama.cpp').mkdir()
            lines = self.summary()
        self.assertIn('lemonade\\models, 22.1 GB) and llama.cpp', lines[0])
        self.assertIn('ODS\\llama.cpp, 0.3 GB)', lines[0])

    def test_page_file_line_only_when_ods_set_it(self):
        settings = self.ods / 'windows-settings.json'
        settings.write_text(json.dumps({'schemaVersion': 1, 'PageFile': {'Original': [], 'OriginalAutomatic': True, 'SetGB': None}}))
        self.assertEqual(self.summary(), [])
        settings.write_text(json.dumps({'schemaVersion': 1, 'PageFile': {
            'Original': ['C:\\pagefile.sys 16384 16384'], 'OriginalAutomatic': False, 'SetGB': 32, 'SetAt': '2026-10-17T00:00:00Z'}}))
        self.assertEqual(self.summary(), [
            'ODS set the page file to 32 GB (it was a fixed 16384 MB page file on C:); to change it, see System Properties > '
            'Advanced > Performance > Settings > Advanced > Virtual memory.'])
        settings.write_text(json.dumps({'PageFile': {'Original': [], 'OriginalAutomatic': True, 'SetGB': 16}}))
        self.assertIn('(it was managed automatically by Windows)', self.summary()[0])

    def test_wsl_line_only_with_the_record_and_its_restore_script(self):
        settings = self.ods / 'windows-settings.json'
        settings.write_text(json.dumps({'WslConfig': {'Value': 16, 'HadFile': False, 'Backup': '', 'WrittenSha256': 'b' * 64}}))
        self.assertEqual(self.summary(), [])
        (self.ods / 'restore-windows-settings.ps1').write_text('# restore')
        self.assertEqual(self.summary(), [
            'ODS limited WSL memory; to undo that, run in PowerShell: '
            '& "C:\\Users\\owner\\AppData\\Local\\ODS\\restore-windows-settings.ps1"'])

    def test_unreadable_settings_and_unmounted_drives_print_nothing(self):
        (self.ods / 'windows-settings.json').write_bytes(b'\xff{not json')
        self.assertEqual(self.summary(), [])
        self.model_store.side_effect = helper.wsl_lemonade.BridgeError('The owned Windows path is not available as a canonical WSL path')
        self.assertEqual(self.summary(), [])

    def test_only_an_ods_owned_store_is_measured(self):
        self.managed['modelStoreWindowsPath'] = r'D:\Models'
        self.assertEqual(self.summary(), [])
        self.model_store.assert_not_called()
        self.assertEqual(helper.windows_kept_summary(Path('/home/owner/ods'), ENV, {'managed': False}), [])


class KeptSummaryFlagTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / '.env').write_text('ODS_HOST_LLM_TRANSPORT=model-router\n'
                                      'ODS_WINDOWS_SYSTEM_DIRECTORY="C:\\Windows\\System32"\n')
        managed = {'managed': True, 'planDigest': 'a' * 64, 'modelStoreWindowsPath': WindowsKeptSummaryTests.STORE}
        for target, name, value in ((helper.platform, 'system', 'Linux'), (helper.platform, 'release', 'microsoft-WSL2'),
                                    (helper, '_owner', None), (helper.wsl_lemonade, 'candidate', True),
                                    (helper.wsl_lemonade, 'status', managed), (helper.wsl_lemonade, 'stop', {'managed': True}),
                                    (helper.wsl_lemonade, 'disable_startup', {'state': 'disabled'}),
                                    (helper, 'windows_kept_summary', ['line one', 'line two'])):
            patcher = patch.object(target, name, return_value=value)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_retire_reports_the_summary_only_when_asked(self):
        self.assertNotIn('windowsKept', helper.retire(self.root))
        self.windows_kept_summary.assert_not_called()
        self.assertEqual(helper.retire(self.root, kept_summary=True)['windowsKept'], ['line one', 'line two'])
        self.assertEqual(helper.retire(self.root, validate_only=True, kept_summary=True), {'state': 'validated', 'startup': 'disabled'})

    def test_main_writes_the_lines_and_keeps_the_json_line_unchanged(self):
        target = self.root / 'kept.txt'
        argv = ['retire-wsl-runtime.py', '--install-dir', str(self.root), '--kept-summary', str(target)]
        with patch.object(helper.sys, 'argv', argv), patch('builtins.print') as printed:
            self.assertEqual(helper.main(), 0)
        self.assertEqual(target.read_text(encoding='utf-8'), 'line one\nline two\n')
        self.assertEqual(json.loads(printed.call_args.args[0]), {'state': 'retired', 'startup': 'disabled'})


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
        pixel_check = script.index('if ! ODS_PIXEL_UNINSTALL_VALIDATE_ONLY=true ods_pixel_uninstall_managed')
        upgrade_stop = script.index("# Stop this installation's background full-model upgrade")
        pixel = script.index('if ! ods_pixel_uninstall_managed')
        apply = script.index('if ! python3 "$_ods_wsl_retire_helper" --install-dir "$INSTALL_DIR" --kept-summary')
        host = script.index('if ! ods_uninstall_system_units')
        # A Pixel refusal must come before anything changes, Windows startup included.
        self.assertLess(precheck, pixel_check)
        self.assertLess(pixel_check, upgrade_stop)
        self.assertLess(pixel_check, apply)
        self.assertLess(precheck, apply)
        self.assertLess(apply, pixel)
        self.assertLess(pixel, host)
        self.assertIn('Windows startup changes already applied for this uninstall remain in effect.', script)

    def test_windows_summary_is_collected_at_retirement_and_printed_last(self):
        script = (SOURCE / 'ods-uninstall.sh').read_text(encoding='utf-8')
        collect = script.index('ODS_WINDOWS_KEPT_SUMMARY="$(cat "$_ods_windows_kept")"')
        removal = script.index('log_info "Removing installation directory..."')
        banner = script.index('ODS has been uninstalled.')
        printed = script.index('echo "On Windows:"')
        # Collected while the helper and the Windows paths still exist; shown last.
        self.assertLess(collect, removal)
        self.assertLess(banner, printed)


if __name__ == '__main__':
    unittest.main()
