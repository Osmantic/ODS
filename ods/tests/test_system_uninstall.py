"""Exercise the imported system-service uninstaller without host service access."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ODS = Path(__file__).resolve().parents[1]
UNITS = ('ods-host-agent.service', 'ods-mdns.service')
STUB = r'''#!/usr/bin/env python3
import os, pathlib, sys
args=sys.argv[1:]
root=pathlib.Path(os.environ['FIXTURE_ROOT'])
with (root/'calls').open('a') as log: log.write(' '.join(args)+'\n')
mode=os.environ.get('FAULT', '')
if args[0]=='show':
    unit=args[1]
    prop=args[2]
    if prop=='--property=FragmentPath':
        print('/other/service' if mode=='foreign_fragment' else str(root/'systemd'/unit))
    elif prop=='--property=DropInPaths':
        print('/operator/override.conf' if mode=='dropin' else '')
    elif prop=='--property=ActiveState':
        if mode=='state_error': sys.exit(1)
        print('active' if mode=='still_active' else 'inactive')
    else: sys.exit(91)
elif args[:2]==['disable','--now']:
    if mode=='stop_failure' and args[2]=='ods-mdns.service': sys.exit(23)
elif args==['daemon-reload']:
    if mode=='reload_failure': sys.exit(23)
else:
    sys.exit(92)
'''


class SystemUninstall(unittest.TestCase):
    def test_top_level_pixel_guard_precedes_system_unit_cleanup(self):
        source = (ODS / 'ods-uninstall.sh').read_text()
        pixel = source.index('ods_pixel_uninstall_managed "$INSTALL_DIR" "$HOME"')
        preflight = source.index('ODS_SYSTEM_UNINSTALL_VALIDATE_ONLY=true')
        system = source.rindex('ods_uninstall_system_units "$INSTALL_DIR" "$HOME"')
        docker = source.index('# 1. Stop and remove Docker containers')
        self.assertLess(preflight, pixel)
        self.assertLess(pixel, system)
        self.assertLess(system, docker)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods unit fixture ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root/'install'
        self.home = self.root/'home'
        self.units = self.root/'systemd'
        self.bin = self.root/'bin'
        for path in (self.install/'scripts/systemd', self.home, self.units, self.bin):
            path.mkdir(parents=True)
        (self.install/'keep').write_text('installation sentinel')
        for unit in UNITS:
            template = (ODS/'scripts/systemd'/unit).read_text()
            (self.install/'scripts/systemd'/unit).write_text(template)
            content = template.replace('__INSTALL_DIR__', str(self.install)).replace('__HOME__', str(self.home))
            content = content.replace('__INSTALL_USER__', 'fixture-user').replace('__PYTHON3__', '/usr/bin/python3')
            unit_file = self.units/unit
            unit_file.write_text(content)
            # Real system units are 0644 root-owned; the custody check rejects
            # group/other-writable files, so pin the mode instead of relying on
            # the caller's umask (fails under e.g. umask 002 shared-group dev).
            unit_file.chmod(0o644)
        stub = self.bin/'systemctl'
        stub.write_text(STUB)
        stub.chmod(0o755)
        self.env = {**os.environ, 'FIXTURE_ROOT': str(self.root),
                    'ODS_UNINSTALL_SYSTEMD_DIR': str(self.units), 'ODS_UNINSTALL_SYSTEMD_UID': str(os.getuid()),
                    'PATH': str(self.bin)+os.pathsep+os.environ['PATH']}

    def cleanup(self, fault='', validate_only=False):
        script = r'''
set -euo pipefail
source "$1"
log_error() { printf '%s\n' "$*" >&2; }
prepare_sudo_credential() { printf 'credential\n' >> "$FIXTURE_ROOT/privileged"; }
run_sudo() {
    printf '%s\n' "$*" >> "$FIXTURE_ROOT/privileged"
    if [[ "$1" == rm && "${@: -1}" != "$ODS_UNINSTALL_SYSTEMD_DIR/"* ]]; then return 99; fi
    "$@"
}
ods_uninstall_system_units "$2" "$3"
'''
        return subprocess.run(['bash', '-c', script, '_', str(ODS/'lib/system-uninstall.sh'),
                               str(self.install), str(self.home)],
                              env={**self.env, 'FAULT': fault,
                                   'ODS_SYSTEM_UNINSTALL_VALIDATE_ONLY': 'true' if validate_only else 'false'},
                              text=True, capture_output=True, timeout=15)

    def test_validation_only_retains_healthy_units_and_needs_no_privilege(self):
        result = self.cleanup(validate_only=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_retained()
        self.assertFalse((self.root/'privileged').exists())
        self.assertNotIn('disable --now', (self.root/'calls').read_text())

    def test_validation_only_rejects_foreign_unit_before_pixel_cleanup(self):
        result = self.cleanup('foreign_fragment', validate_only=True)
        self.assertNotEqual(result.returncode, 0)
        self.assert_retained()
        self.assertFalse((self.root/'privileged').exists())

    def assert_retained(self):
        self.assertTrue(all((self.units/u).exists() for u in UNITS))
        self.assertEqual((self.install/'keep').read_text(), 'installation sentinel')

    def test_disabled_but_running_units_are_stopped_and_removed_in_order(self):
        result = self.cleanup()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root/'calls').read_text()
        for unit in UNITS:
            self.assertIn('disable --now '+unit, calls)
            self.assertFalse((self.units/unit).exists())
        self.assertNotIn('is-enabled', calls)
        privileged = (self.root/'privileged').read_text()
        self.assertLess(privileged.index('disable --now ods-mdns'), privileged.index('rm -f'))
        self.assertIn('daemon-reload', calls)

    def test_absent_units_need_no_privilege_or_service_call(self):
        for unit in UNITS: (self.units/unit).unlink()
        self.assertEqual(self.cleanup().returncode, 0)
        self.assertFalse((self.root/'privileged').exists())
        self.assertFalse((self.root/'calls').exists())

    def test_foreign_or_modified_unit_is_preserved_before_any_stop(self):
        file = self.units/UNITS[1]
        original = file.read_text()
        for content in (original.replace(str(self.install), '/another-install'), original+'\n# operator change\n'):
            with self.subTest(content=content[-25:]):
                file.write_text(content)
                result = self.cleanup()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assert_retained()
                self.assertFalse((self.root/'privileged').exists())

    def test_symlinked_unit_cannot_claim_an_external_file(self):
        file = self.units/UNITS[1]
        external = self.root/'external-unit'
        file.rename(external)
        file.symlink_to(external)
        self.assertNotEqual(self.cleanup().returncode, 0)
        self.assertTrue(file.is_symlink())
        self.assertTrue(external.exists())
        self.assertFalse((self.root/'privileged').exists())

    def test_runtime_overrides_fail_before_stopping(self):
        for fault in ('dropin', 'foreign_fragment'):
            with self.subTest(fault=fault):
                self.assertNotEqual(self.cleanup(fault).returncode, 0)
                self.assert_retained()
                self.assertFalse((self.root/'privileged').exists())

    def test_stop_or_state_failure_retains_both_definitions_and_installation(self):
        for fault in ('stop_failure', 'still_active', 'state_error'):
            with self.subTest(fault=fault):
                self.assertNotEqual(self.cleanup(fault).returncode, 0)
                self.assert_retained()
                self.assertNotIn('rm -f', (self.root/'privileged').read_text())

    def test_cleanup_failure_is_not_reported_as_success(self):
        self.assertNotEqual(self.cleanup('reload_failure').returncode, 0)
        self.assertTrue((self.install/'keep').exists())

    def test_entrypoint_calls_verified_cleanup_before_broader_mutation(self):
        text = (ODS/'ods-uninstall.sh').read_text()
        self.assertLess(text.index('ods_uninstall_system_units "$INSTALL_DIR" "$HOME"'),
                        text.index('# 1. Stop and remove Docker containers'))
        self.assertNotIn('systemctl kill -s SIGKILL ods-host-agent.service', text)


if __name__ == '__main__':
    unittest.main()
