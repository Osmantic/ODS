import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('POSIX descriptor traversal required')

import hashlib
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'bin'))
import pixel_macos_custody as custody


class LoadedDefinitionTests(unittest.TestCase):
    def setUp(self):
        self.target = 'system/com.ods.pixel'
        self.path = '/Library/LaunchDaemons/com.ods.pixel.plist'
        self.expected = {'Label': 'com.ods.pixel',
                         'ProgramArguments': ['/usr/local/bin/node', '/opt/ODS Native/gateway.mjs'],
                         'WorkingDirectory': '/opt/ODS Native',
                         'StandardOutPath': '/var/log/pixel.log',
                         'StandardErrorPath': '/var/log/pixel.log',
                         'EnvironmentVariables': {'HOME': '/var/empty', 'PATH': '/usr/bin:/bin'}}
        self.raw = ('system/com.ods.pixel = {\n'
                    '\tpath = /Library/LaunchDaemons/com.ods.pixel.plist\n'
                    '\tprogram = /usr/local/bin/node\n'
                    '\targuments = {\n\t\t/usr/local/bin/node\n'
                    '\t\t/opt/ODS Native/gateway.mjs\n\t}\n'
                    '\tworking directory = /opt/ODS Native\n'
                    '\tstdout path = /var/log/pixel.log\n'
                    '\tstderr path = /var/log/pixel.log\n'
                    '\tenvironment = {\n\t\tHOME => /var/empty\n'
                    '\t\tPATH => /usr/bin:/bin\n\t\tOSLogRateLimit => 64\n'
                    '\t\tXPC_SERVICE_NAME => com.ods.pixel\n\t}\n}\n')

    def verify(self, raw):
        custody.verify_loaded_launchd_definition(raw, self.target, self.path, self.expected)

    def test_expected_loaded_definition(self):
        self.verify(self.raw)

    def test_stale_paths_arguments_environment_and_ambiguity_rejected(self):
        changes = [
            ('\tprogram = /usr/local/bin/node', '\tprogram = /tmp/node'),
            ('\t\t/opt/ODS Native/gateway.mjs', '\t\t/opt/Old/gateway.mjs'),
            ('\t\tHOME => /var/empty', '\t\tHOME => /tmp'),
            ('\t\tHOME => /var/empty', '\t\tHOME => /var/empty\n\t\tHOME => /tmp'),
            ('\tenvironment = {', '\tinherited environment = {\n\t\tSSH_AUTH_SOCK => /tmp/agent\n\t}\n\tenvironment = {'),
            ('\t\tPATH => /usr/bin:/bin', '\t\tPATH => /usr/bin:/bin\n\t\tNODE_OPTIONS => --require=/tmp/inject.js'),
            ('\tprogram = /usr/local/bin/node', '\tprogram = /usr/local/bin/node\n\tprogram = /tmp/node'),
            ('system/com.ods.pixel = {', 'system/com.other = {'),
            ('\tworking directory = /opt/ODS Native', '\t\tworking directory = /opt/ODS Native'),
        ]
        for before, after in changes:
            with self.subTest(after=after), self.assertRaises(custody.CustodyError):
                self.verify(self.raw.replace(before, after))

    def test_empty_inherited_environment_allowed(self):
        self.verify(self.raw.replace('\tenvironment = {',
                                    '\tinherited environment = {\n\t}\n\tenvironment = {'))

    def test_explicit_clean_environment_matches_loaded_job(self):
        from pixel_launchd_environment import clean_gateway_document
        self.expected['EnvironmentVariables'].update({
            'OPENCLAW_STATE_DIR': '/opt/state', 'OPENCLAW_CONFIG_PATH': '/opt/config.json'})
        self.expected = clean_gateway_document(self.expected)
        raw = self.raw.replace('\tprogram = /usr/local/bin/node', '\tprogram = /usr/bin/env')
        old_args = '\targuments = {\n\t\t/usr/local/bin/node\n\t\t/opt/ODS Native/gateway.mjs\n\t}'
        new_args = '\targuments = {\n' + ''.join('\t\t' + arg + '\n' for arg in self.expected['ProgramArguments']) + '\t}'
        raw = raw.replace(old_args, new_args)
        raw = raw.replace('\t\tHOME => /var/empty\n', '').replace('\t\tPATH => /usr/bin:/bin\n', '')
        raw = raw.replace('\tenvironment = {', '\tinherited environment = {\n\t\tSSH_AUTH_SOCK => /tmp/session\n\t}\n\tenvironment = {')
        self.verify(raw)


class MetadataTests(unittest.TestCase):
    def test_rejects_nonroot_writable_hardlinked_and_wrong_kind(self):
        valid = dict(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_nlink=1)
        for change in ({'st_uid': 501}, {'st_mode': stat.S_IFREG | 0o664},
                       {'st_nlink': 2}, {'st_mode': stat.S_IFIFO | 0o600}):
            with self.subTest(change=change), \
                    patch.object(custody.os, 'fstat', return_value=types.SimpleNamespace(**(valid | change))), \
                    patch.object(custody, '_require_no_acl') as acl:
                with self.assertRaises(custody.CustodyError):
                    custody._verify_fd(123, directory=False)
                acl.assert_not_called()

    def test_directory_links_allowed_but_acl_must_be_checked(self):
        info = types.SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_nlink=4)
        with patch.object(custody.os, 'fstat', return_value=info), \
                patch.object(custody, '_require_no_acl') as acl:
            self.assertIs(custody._verify_fd(123, directory=True), info)
            acl.assert_called_once_with(123)

    @unittest.skipUnless(sys.platform == 'darwin', 'native macOS ACL API required')
    def test_real_macos_empty_and_extended_acl(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / 'fixture'
            filename.write_bytes(b'fixture')
            fd = os.open(filename, os.O_RDONLY)
            try:
                custody._require_no_acl(fd)
                subprocess.run(['/bin/chmod', '+a', 'everyone allow write', str(filename)], check=True)
                with self.assertRaisesRegex(custody.CustodyError, 'acl-present'):
                    custody._require_no_acl(fd)
            finally:
                os.close(fd)
                subprocess.run(['/bin/chmod', '-N', str(filename)], check=True)

    @unittest.skipUnless(sys.platform == 'darwin', 'native macOS filesystem required')
    def test_real_launchdaemon_parent_custody(self):
        for path in ('/', '/Library', '/Library/LaunchDaemons'):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                custody._verify_fd(fd, directory=True)
            finally:
                os.close(fd)

    @unittest.skipUnless(sys.platform == 'darwin', 'native macOS ACL API required')
    def test_invalid_descriptor_is_not_treated_as_absent_acl(self):
        with self.assertRaisesRegex(custody.CustodyError, 'acl-unavailable'):
            custody._require_no_acl(-1)

    def test_other_platform_refuses_native_custody(self):
        with patch.object(custody.sys, 'platform', 'linux'):
            with self.assertRaisesRegex(custody.CustodyError, 'platform-required'):
                custody._require_no_acl(123)


class TraversalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.file = self.root / 'gateway.plist'
        self.file.write_bytes(b'test')
        # Temp ancestry is intentionally writable and not deployment custody.
        # Exercise real descriptor walking separately from metadata/ACL tests.
        verifier = patch.object(custody, '_verify_fd', side_effect=lambda fd, **kw: os.fstat(fd))
        self.verify = verifier.start()
        self.addCleanup(verifier.stop)

    def test_read_verifies_every_component_and_rechecks_file(self):
        self.assertEqual(custody.protected_bytes(self.file), b'test')
        self.assertEqual(self.verify.call_count, len(self.file.parts) + 1)
        self.assertFalse(self.verify.call_args.kwargs['directory'])

    def test_protected_directory_creates_new_paths_with_stable_modes(self):
        previous = os.umask(0o077)
        try:
            path = self.root / 'new' / 'child'
            with custody.protected_directory(path, create=True) as fd:
                self.assertEqual(stat.S_IMODE(os.fstat(fd).st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o755)
            self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        finally:
            os.umask(previous)

    def test_protected_directory_never_follows_symlink_during_creation(self):
        link = self.root / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            with custody.protected_directory(link / 'never-created', create=True):
                self.fail('symlink accepted')
        self.assertFalse((self.root / 'never-created').exists())

    def test_protected_directory_rejects_parent_before_creating_child(self):
        path = self.root / 'never-created'
        self.verify.side_effect = custody.CustodyError('unsafe parent')
        with self.assertRaises(custody.CustodyError):
            with custody.protected_directory(path, create=True):
                self.fail('unsafe parent accepted')
        self.assertFalse(path.exists())

    def test_tree_metadata_checks_every_regular_entry(self):
        child = self.root / 'nested'
        child.mkdir()
        (child / 'file').write_text('fixture')
        custody.protected_tree_metadata(self.root)
        file_checks = [call for call in self.verify.call_args_list if not call.kwargs['directory']]
        self.assertEqual(len(file_checks), 2)

    @unittest.skipUnless(sys.platform == 'darwin', 'real macOS ACL check required')
    def test_tree_refuses_real_acl_on_nested_file(self):
        def check_acl(fd, **_):
            custody._require_no_acl(fd)
            return os.fstat(fd)
        self.verify.side_effect = check_acl
        subprocess.run(['/bin/chmod', '+a', 'everyone allow write', str(self.file)], check=True)
        try:
            with self.assertRaisesRegex(custody.CustodyError, 'acl-present'):
                custody.protected_tree_metadata(self.root)
        finally:
            subprocess.run(['/bin/chmod', '-N', str(self.file)], check=True)

    def test_rejects_ambiguous_paths_before_open(self):
        for path in ('relative', '/', '//Library/file', '/Library/../file', '/Library/./file', '/Library/file/', '/a\0b'):
            with self.subTest(path=path), patch.object(custody.os, 'open') as opened:
                with self.assertRaises(custody.CustodyError):
                    custody.protected_bytes(path)
                opened.assert_not_called()

    def test_rejects_file_and_directory_symlinks(self):
        link = self.root / 'link'
        link.symlink_to(self.file)
        parent = self.root / 'parent'
        parent.symlink_to(self.root, target_is_directory=True)
        for path in (link, parent / self.file.name):
            with self.subTest(path=path), self.assertRaises(custody.CustodyError):
                custody.protected_bytes(path)

    def test_size_limit_and_missing_file(self):
        self.assertEqual(custody.protected_bytes(self.file, limit=4), b'test')
        with self.assertRaisesRegex(custody.CustodyError, 'too-large'):
            custody.protected_bytes(self.file, limit=3)
        with self.assertRaises(custody.CustodyError):
            custody.protected_bytes(self.root / 'missing')

    def test_change_during_read_rejected(self):
        original = os.read
        def mutate(fd, size):
            result = original(fd, size)
            self.file.write_bytes(b'changed-length')
            return result
        with patch.object(custody.os, 'read', side_effect=mutate):
            with self.assertRaisesRegex(custody.CustodyError, 'file-changed'):
                custody.protected_bytes(self.file)

    def test_all_opened_descriptors_closed_after_failure(self):
        self.verify.side_effect = custody.CustodyError('fixture')
        original = os.close
        with patch.object(custody.os, 'close', wraps=original) as close:
            with self.assertRaises(custody.CustodyError):
                custody.protected_bytes(self.file)
            close.assert_called_once()


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.expected = {'Label': 'com.ods.pixel.gateway', 'UserName': 'fixture',
                         'ProgramArguments': ['/opt/ods/node', '/opt/ods/gateway.mjs'],
                         'EnvironmentVariables': {'HOME': '/var/lib/ods-pixel'},
                         'RunAtLoad': True}

    def test_pins_complete_document_without_returning_environment(self):
        body = plistlib.dumps(self.expected)
        with patch.object(custody, 'protected_bytes', return_value=body):
            result = custody.launchd_document_binding('/Library/LaunchDaemons/fixture.plist', self.expected)
        self.assertEqual(result['sha256'], hashlib.sha256(body).hexdigest())
        self.assertNotIn('HOME', str(result))

    def test_rejects_extra_keys_changed_identity_and_boolean_integer_confusion(self):
        for changes in ({'UserName': 'root'}, {'Program': '/bin/sh'},
                        {'EnvironmentVariables': {'NODE_OPTIONS': '--import=/tmp/evil.mjs'}},
                        {'RunAtLoad': 1}):
            body = plistlib.dumps(self.expected | changes)
            with self.subTest(changes=changes), patch.object(custody, 'protected_bytes', return_value=body):
                with self.assertRaisesRegex(custody.CustodyError, 'document-changed'):
                    custody.launchd_document_binding('/fixture', self.expected)

    def test_rejects_malformed_document_and_missing_specification(self):
        for body in (b'not a plist', b'<?xml version="1.0"?><plist><dict>'):
            with patch.object(custody, 'protected_bytes', return_value=body):
                with self.assertRaises(custody.CustodyError):
                    custody.launchd_document_binding('/fixture', self.expected)
                with self.assertRaises(custody.CustodyError):
                    custody.launchd_document_binding('/fixture', {})

    def test_rejects_duplicate_keys_even_if_python_keeps_expected_value(self):
        body = plistlib.dumps(self.expected).replace(b'<dict>', b'<dict><key>UserName</key><string>root</string>', 1)
        with patch.object(custody, 'protected_bytes', return_value=body):
            with self.assertRaisesRegex(custody.CustodyError, 'noncanonical'):
                custody.launchd_document_binding('/fixture', self.expected)

    def test_accepts_generated_binary_plist(self):
        body = plistlib.dumps(self.expected, fmt=plistlib.FMT_BINARY)
        with patch.object(custody, 'protected_bytes', return_value=body):
            self.assertEqual(custody.launchd_document_binding('/fixture', self.expected)['sha256'],
                             hashlib.sha256(body).hexdigest())


if __name__ == '__main__':
    unittest.main()
