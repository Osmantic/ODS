"""Fixed native OAuth under the existing owner apply lock, never as root."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess

import pixel_access_mode as controller
from pixel_connections import native_mcp as native


def operate(path, state_dir, *, openclaw, request, expected_config_sha256):
    request = native.worker_request(request)
    binding = request['binding']
    if os.geteuid() == 0 or os.geteuid() != binding['ownerUid']:
        raise native.NativeError('native-mcp-owner-mismatch')
    sd = controller._prepare_state_dir(state_dir)
    with controller._Lock(sd):
        # Share the actual owner's access/configuration lock. Never start OAuth
        # while a previous owner-side transaction still needs recovery.
        for name in ('connection-journal.json', 'model-journal.json', 'settings-journal.json',
                     'provider-journal.json', 'access-release-journal.json'):
            if os.path.lexists(os.path.join(sd, name)):
                raise native.NativeError('transition-recovery-required')
        receipt = controller._load_receipt(sd)
        if receipt is not None and receipt.get('status') == controller.STATUS_PENDING:
            raise native.NativeError('transition-recovery-required')

        def current():
            return native.derive_binding(config_path=path, uid=os.geteuid(), home=os.environ['HOME'],
                state_dir=os.environ.get('OPENCLAW_STATE_DIR', str(Path(os.environ['HOME']) / '.openclaw')),
                binary=openclaw, search_path=os.environ['PATH'], server_name=request['serverName'])

        if current() != binding or binding['configSha256'] != expected_config_sha256:
            raise native.NativeError('native-mcp-binding-changed')
        raw = native._read(path, owners=(os.geteuid(),), private=True)
        if hashlib.sha256(raw).hexdigest() != binding['configSha256']:
            raise native.NativeError('native-mcp-binding-changed')
        server = native.selected_server(native.decode(raw), request['serverName'])
        if native.digest(server) != binding['serverSha256']:
            raise native.NativeError('native-mcp-binding-changed')
        server['oauth'] = dict(server.get('oauth') or {}, redirectUrl=request['redirectUri'])
        payload = {'operation': request['action'], 'packageDir': binding['packageDir'],
                   'serverName': request['serverName'], 'server': server}
        if request['action'] == 'complete':
            payload['authorizationCode'] = request['authorizationCode']
        environment = dict(os.environ, OPENCLAW_STATE_DIR=binding['stateDir'],
                           OPENCLAW_CONFIG_PATH=binding['configPath'])
        try:
            # EOF on this private guardian socket stops Node if its owner worker
            # dies. A surviving child must not overwrite a restarted sign-in.
            guardian, watched = socket.socketpair()
            with guardian, watched:
                environment['ODS_NATIVE_OAUTH_GUARD_FD'] = str(watched.fileno())
                result = subprocess.run([binding['nodePath'], str(Path(__file__).with_name('native_mcp_oauth.mjs'))],
                    input=json.dumps(payload), text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=environment, cwd=binding['home'], timeout=45, pass_fds=(watched.fileno(),))
            if len(result.stdout.encode()) > 8192:
                raise native.NativeError('invalid-native-oauth-result')
            value = native.decode(result.stdout)
            if result.returncode or type(value) is not dict or set(value) != {'ok', 'result'} or value['ok'] is not True:
                # No native remote message, callback code, path or token is
                # reflected in the worker's result or exception.
                raise native.NativeError('native-oauth-failed')
            if current() != binding:
                raise native.NativeError('native-mcp-binding-changed')
            return native.worker_result(value['result'])
        except (OSError, subprocess.SubprocessError, UnicodeError):
            raise native.NativeError('native-oauth-attempt-unconfirmed') from None
