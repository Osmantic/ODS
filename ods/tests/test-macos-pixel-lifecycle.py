"""Opt-in native OpenClaw lifecycle qualification with disposable state.

Requires a previously acquired, version-qualified runtime. Does not install
packages, enable tools, touch an existing gateway, or prove root custody.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_gateway_service import LaunchdGatewayService
from pixel_access_bridge import AccessError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node', type=Path, required=True)
    parser.add_argument('--openclaw-entrypoint', type=Path, required=True)
    parser.add_argument('--expected-version', default='2026.6.33')
    args = parser.parse_args()
    if sys.platform != 'darwin' or os.getuid() == 0:
        parser.error('Run as a regular macOS login user, not root')
    node = str(args.node.resolve(strict=True))
    entrypoint = args.openclaw_entrypoint.resolve(strict=True)
    package = json.loads((entrypoint.parent / 'package.json').read_text())
    if package.get('name') != 'openclaw' or package.get('version') != args.expected_version:
        parser.error('The runtime package does not match the requested qualification version')
    node_path = subprocess.check_output([node, '-p', 'process.execPath'], text=True, timeout=10).strip()
    if node_path != node:
        parser.error('--node must be the actual runtime executable, not a launcher')

    with tempfile.TemporaryDirectory(prefix='ods-launchd-', dir='/private/tmp') as directory:
        root = Path(directory)
        profile = 'ods-qualification-' + secrets.token_hex(6)
        label = 'ai.openclaw.' + profile
        target = f'gui/{os.getuid()}/{label}'
        absent = subprocess.run(['/bin/launchctl', 'print', target], capture_output=True, timeout=10)
        if absent.returncode == 0:
            raise RuntimeError('Refusing to replace an existing service')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        config = root / 'openclaw.json'
        config.write_text(json.dumps({
            'gateway': {'mode':'local', 'bind':'loopback', 'port':port,
                'auth':{'mode':'token', 'token':secrets.token_urlsafe(48)},
                'controlUi':{'enabled':False}},
            'plugins':{'enabled':False}, 'tools':{'deny':['*']},
            'agents':{'defaults':{'workspace':str(root / 'workspace'),
                                  'heartbeat':{'every':'0m'}}}}))
        config.chmod(0o600)
        # Pin Node through OpenClaw's wrapper API; its installer otherwise
        # prefers a system Node even when invoked with another runtime.
        wrapper = root / 'openclaw-pinned'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([node, str(entrypoint)]) + ' "$@"\n')
        wrapper.chmod(0o700)
        env = {'HOME':str(root), 'PATH':str(Path(node).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
            'OPENCLAW_PROFILE':profile, 'OPENCLAW_STATE_DIR':str(root / 'state'),
            'OPENCLAW_CONFIG_PATH':str(config), 'OPENCLAW_SKIP_CHANNELS':'1',
            'OPENCLAW_WRAPPER':str(wrapper)}
        http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def health():
            for _ in range(60):
                try:
                    with http.open(f'http://127.0.0.1:{port}/healthz', timeout=1) as response:
                        if response.status == 200:
                            return
                except OSError:
                    pass
                time.sleep(1)
            raise RuntimeError('Temporary gateway health deadline exceeded')

        try:
            result = subprocess.run([node, str(entrypoint), 'gateway', 'install',
                '--port', str(port), '--runtime', 'node', '--json'],
                cwd=root, env=env, capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError('Temporary OpenClaw service installation failed')
            plist = root / 'Library/LaunchAgents' / (label + '.plist')
            expected = plist.read_bytes()
            if plistlib.loads(expected).get('Label') != label:
                raise RuntimeError('Unexpected service label')

            def verify_fixture():
                if plist.stat().st_uid != os.getuid() or plist.read_bytes() != expected:
                    raise RuntimeError('Temporary service definition changed')

            def command(argv, timeout=20):
                try:
                    return subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL, timeout=timeout)
                except subprocess.CalledProcessError as error:
                    raise AccessError('host-command-failed', returncode=error.returncode) from None

            service = LaunchdGatewayService(command, AccessError, target, verify_fixture,
                process={'uid':os.getuid(), 'gid':os.getgid(), 'executable':node},
                plist=plist, verify_definition=verify_fixture)
            health()
            before = service.process_identity()
            before_transaction = service.transaction_identity()
            if before_transaction['pid'] != before[0]:
                raise RuntimeError('Process changed during initial qualification')
            print('PASS: native health and exact process identity', flush=True)
            service.restart(timeout=20)
            health()
            if service.process_identity() == before:
                raise RuntimeError('Gateway identity did not change after restart')
            after_transaction = service.transaction_identity()
            if (before_transaction['boot'] != after_transaction['boot']
                    or before_transaction['pid'] == after_transaction['pid']
                    or before_transaction['started'] >= after_transaction['started']):
                raise RuntimeError('Restart transaction identity unconfirmed')
            print('PASS: native restart and new process identity', flush=True)
            service.stop(timeout=20)
            service.assert_stopped()
            service.assert_stopped()
            print('PASS: unloaded job and captured process identities exited; repeatable check', flush=True)
            service.reload()
            health()
            reloaded = service.transaction_identity()
            if (reloaded['boot'] != after_transaction['boot']
                    or reloaded['pid'] == after_transaction['pid']
                    or reloaded['started'] <= after_transaction['started']):
                raise RuntimeError('Reload transaction identity unconfirmed')
            print('PASS: native bootstrap after stop and healthy new process', flush=True)
        finally:
            subprocess.run(['/bin/launchctl', 'bootout', target], capture_output=True, timeout=30)
            for _ in range(30):
                state = subprocess.run(['/bin/launchctl', 'print', target], capture_output=True, timeout=5)
                if state.returncode:
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Temporary job removal unconfirmed')
            print('PASS: temporary launchd job removed (not a descendant-empty proof)', flush=True)


if __name__ == '__main__':
    main()
