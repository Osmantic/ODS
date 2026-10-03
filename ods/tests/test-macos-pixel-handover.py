"""Opt-in real OpenClaw handover/rollback using disposable GUI jobs.

Exercises the installer's activation engine with actual launchd/process/HTTP
operations. The controller is a sleep fixture with injected readiness failure,
not the privileged access daemon. Does not qualify root custody or permissions.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_gateway_service import LaunchdGatewayService
from pixel_macos_custody import verify_loaded_launchd_definition

SPEC = importlib.util.spec_from_file_location(
    'handover_installer', ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--openclaw-entrypoint', type=Path, required=True)
    parser.add_argument('--expected-version', default='2026.6.33')
    parser.add_argument('--plugin', type=Path, help='Optional packaged Pixel plugin, read-only readiness check')
    parser.add_argument('--report', type=Path, help='New JSON report path; never overwrite an existing report')
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.getuid() == 0:
        parser.error('Run as a regular macOS login user, not root')
    node = str(args.node.resolve(strict=True))
    entrypoint = args.openclaw_entrypoint.resolve(strict=True)
    package = json.loads((entrypoint.parent / 'package.json').read_bytes())
    if package.get('name') != 'openclaw' or package.get('version') != args.expected_version:
        parser.error('Runtime package does not match the requested qualification version')
    if subprocess.check_output([node, '-p', 'process.execPath'], text=True, timeout=10).strip() != node:
        parser.error('--node must be the actual executable, not a launcher')
    if args.report and os.path.lexists(args.report):
        parser.error('--report must not already exist')
    plugin = args.plugin.resolve(strict=True) if args.plugin else None
    if plugin and json.loads((plugin / 'openclaw.plugin.json').read_bytes()).get('id') != 'pixel-ods':
        parser.error('--plugin must be the Pixel ODS integration')

    root = Path(tempfile.mkdtemp(prefix='ods-handover-', dir='/private/tmp'))
    prefix = 'com.ods.qualification.' + secrets.token_hex(8)
    domain = f'gui/{os.getuid()}'
    services = []
    completed = False
    print('Qualification directory:', root, flush=True)
    try:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        config = root / 'openclaw.json'
        token = secrets.token_urlsafe(48)
        configuration = {
            'gateway': {'mode': 'local', 'bind': 'loopback', 'port': port,
                        'auth': {'mode': 'token', 'token': token},
                        'controlUi': {'enabled': False}},
            'plugins': {'enabled': False}, 'tools': {'deny': ['*']},
            'agents': {'defaults': {'workspace': str(root / 'workspace'), 'heartbeat': {'every': '0m'}}},
        }
        if plugin:
            # Pixel creates its private admission child, but deliberately does
            # not create or trust an arbitrary parent home during registration.
            (root / '.openclaw').mkdir(mode=0o700)
            configuration['plugins'] = {'enabled': True, 'allow': ['pixel-ods'],
                'load': {'paths': [str(plugin)]},
                'entries': {'pixel-ods': {'enabled': True, 'hooks': {'allowConversationAccess': True}}}}
            configuration['agents']['list'] = [{'id': 'pixel', 'default': True,
                'workspace': str(root / 'workspace'), 'sandbox': {'mode': 'all'},
                'tools': {'exec': {'host': 'sandbox'}, 'deny': ['*']}}]
        config.write_text(json.dumps(configuration))
        config.chmod(0o600)
        environment = {'HOME': str(root), 'PATH': str(Path(node).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                       'OPENCLAW_CONFIG_PATH': str(config), 'OPENCLAW_STATE_DIR': str(root / 'state'),
                       'OPENCLAW_SKIP_CHANNELS': '1', 'TMPDIR': str(root)}

        def verify_plugin(service):
            if not plugin:
                return
            client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url = f'http://127.0.0.1:{port}/pixel-ods/access-runtime'
            try:
                client.open(url, timeout=5).close()
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise RuntimeError(f'Packaged plugin authentication boundary unconfirmed: HTTP {error.code}') from None
            else:
                raise RuntimeError('Packaged plugin route accepted unauthenticated access')
            request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
            identity = service.process_identity()
            with client.open(request, timeout=5) as response:
                value = json.load(response)
            if (value.get('available') is not True or value.get('pid') != identity[0]
                    or service.process_identity() != identity):
                raise RuntimeError('Packaged plugin native admission unavailable')

        def create(name, command, executable):
            label = prefix + '.' + name
            target = domain + '/' + label
            absent = subprocess.run(['/bin/launchctl', 'print', target], capture_output=True, timeout=10)
            if absent.returncode != 113:
                raise RuntimeError('Temporary job absence unconfirmed')
            path = root / (name + '.plist')
            document = {'Label': label, 'ProgramArguments': ['/usr/bin/env', '-i',
                        *(key + '=' + value for key, value in environment.items()), *command],
                        'EnvironmentVariables': {}, 'WorkingDirectory': str(root),
                        'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False},
                        'ThrottleInterval': 3, 'ProcessType': 'Background',
                        'StandardOutPath': str(root / (name + '.out.log')),
                        'StandardErrorPath': str(root / (name + '.err.log'))}
            expected = plistlib.dumps(document)
            path.write_bytes(expected)
            path.chmod(0o600)

            def verify_definition():
                if path.stat().st_uid != os.getuid() or path.read_bytes() != expected:
                    raise RuntimeError('Temporary service definition changed')

            def verify():
                verify_definition()
                raw = installer._command(['/bin/launchctl', 'print', target])
                verify_loaded_launchd_definition(raw, target, path, document)

            service = LaunchdGatewayService(installer._command, installer.InstallError, target, verify,
                process={'uid': os.getuid(), 'gid': os.getgid(), 'executable': executable},
                plist=path, verify_definition=verify_definition)
            services.append(service)
            return service

        command = [node, str(entrypoint), 'gateway', 'run', '--port', str(port)]
        old = create('original', command, node)
        candidate = create('candidate', command, node)
        controller = create('controller-fixture', ['/bin/sleep', '600'], '/bin/sleep')
        installer._command(['/bin/launchctl', 'bootstrap', domain, str(old.plist)])
        installer._ready_gateway(old, port, timeout=90)
        original_identity = old.transaction_identity()
        verify_plugin(old)
        print('PASS: temporary original OpenClaw ready with verified identity', flush=True)

        def reject_controller(service):
            service.process_identity()
            raise installer.InstallError('qualification-controller-readiness-failure')

        installer._ready_access = reject_controller
        plan = {'source': old.plist, 'owner': pwd.getpwuid(os.getuid()),
                'access_settings': {'gateway_port': port}}
        journal = root / 'installation.json'
        try:
            installer._activate(plan, services, journal)
        except installer.InstallError as error:
            if error.code != 'qualification-controller-readiness-failure':
                raise
        else:
            raise RuntimeError('Injected controller failure was not observed')
        if json.loads(journal.read_bytes())['phase'] != 'restored':
            raise RuntimeError('Rollback was not recorded as restored')
        restored_identity = old.transaction_identity()
        verify_plugin(old)
        if (restored_identity['boot'] != original_identity['boot']
                or restored_identity['pid'] == original_identity['pid']
                or restored_identity['started'] <= original_identity['started']):
            raise RuntimeError('Original gateway restoration identity unconfirmed')
        candidate.assert_stopped()
        controller.assert_stopped()
        if installer._job_disabled(old.target) or not all(
                installer._job_disabled(s.target) for s in (candidate, controller)):
            raise RuntimeError('Rollback launchd enablement state incorrect')
        print('PASS: candidate/controller stopped; original restored with new identity and health', flush=True)

        # Now qualify the successful lifecycle branch. This readiness fixture
        # proves only the controller process, not the real privileged protocol.
        installer._ready_access = lambda service: service.process_identity()
        installer._activate(plan, services, journal)
        old.assert_stopped()
        candidate.process_identity()
        verify_plugin(candidate)
        controller.process_identity()
        if json.loads(journal.read_bytes())['phase'] != 'active' or not installer._job_disabled(old.target):
            raise RuntimeError('Successful handover state unconfirmed')
        print('PASS: successful handover; original disabled and candidate healthy', flush=True)
        completed = True
    finally:
        failures = []
        for service in reversed(services):
            try:
                installer._stop_loaded(service)
            except Exception:
                failures.append(service.target)
        if failures:
            raise RuntimeError('Temporary cleanup unconfirmed; retained ' + str(root))
        if completed:
            shutil.rmtree(root)
            print('PASS: temporary jobs stopped and files removed', flush=True)
        else:
            print('Temporary jobs stopped; failure diagnostics retained:', root, flush=True)
    if args.report:
        with args.report.open('x') as report:
            json.dump({'passed': True, 'runtimeVersion': args.expected_version,
                       'packagedPluginAdmissionVerified': plugin is not None,
                       'rollback': True, 'handover': True, 'temporaryJobsRemoved': True,
                       'scope': 'GUI lifecycle and optional plugin admission, not root custody or permission parity'},
                      report, sort_keys=True)
            report.flush()
            os.fsync(report.fileno())


if __name__ == '__main__':
    main()
