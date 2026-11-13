"""Fixed native OAuth under the existing owner apply lock, never as root."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile

import pixel_access_mode as controller
from pixel_connections import native_mcp as native
from pixel_connections import native_apps


def _invoke(binding, payload):
    """Private child I/O, under the caller's existing owner apply lock."""
    environment = dict(os.environ, OPENCLAW_STATE_DIR=binding['stateDir'],
                       OPENCLAW_CONFIG_PATH=binding['configPath'])
    try:
        # EOF on this private guardian socket stops Node if its owner worker
        # dies. A surviving child must not overwrite a restarted sign-in.
        guardian, watched = socket.socketpair()
        with guardian, watched, tempfile.TemporaryFile() as reply:
            environment['ODS_NATIVE_OAUTH_GUARD_FD'] = str(watched.fileno())
            # SDK diagnostics must not corrupt or become the protocol reply.
            environment['ODS_NATIVE_OAUTH_REPLY_FD'] = str(reply.fileno())
            result = subprocess.run([binding['nodePath'], str(Path(__file__).with_name('native_mcp_oauth.mjs'))],
                input=json.dumps(payload), text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=environment, cwd=binding['home'], timeout=45, pass_fds=(watched.fileno(), reply.fileno()))
            reply.seek(0)
            raw_reply = reply.read(8193)
        if len(raw_reply) > 8192:
            raise native.NativeError('invalid-native-oauth-result')
        value = native.decode(raw_reply)
        if result.returncode or type(value) is not dict or set(value) != {'ok', 'result'} or value['ok'] is not True:
            # No remote message, callback code, path or token crosses this seam.
            raise native.NativeError('native-oauth-failed')
        return value['result']
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise native.NativeError('native-oauth-attempt-unconfirmed') from None


def check_staged_native_apps(path, *, openclaw, chosen):
    """Qualify a fixed app candidate BEFORE writing, inside the transaction lock.

    This internal call accepts no public request. It does not acquire the lock
    again, contact a provider or access credentials. Readiness after restart is
    still required; static policy eligibility does not mean an app is connected.
    """
    chosen = native_apps.choices(chosen)
    if not any(chosen.values()):
        return
    if os.geteuid() == 0:
        raise native.NativeError('native-mcp-owner-mismatch')
    raw = native._read(path, owners=(os.geteuid(),), private=True)
    config = native.decode(raw)
    for app_id, enabled in chosen.items():
        if not enabled:
            continue
        entry = native_apps.app(app_id)
        server = native.selected_server(config, entry['serverName'])
        if server != native_apps.server(app_id, True):
            raise native.NativeError('native-app-contract-changed')

        def current():
            return native.derive_binding(config_path=path, uid=os.geteuid(), home=os.environ['HOME'],
                state_dir=os.environ.get('OPENCLAW_STATE_DIR', str(Path(os.environ['HOME']) / '.openclaw')),
                binary=openclaw, search_path=os.environ['PATH'], server_name=entry['serverName'])

        binding = current()
        if binding['configSha256'] != hashlib.sha256(raw).hexdigest():
            raise native.NativeError('native-mcp-binding-changed')
        value = _invoke(binding, {'operation': 'preflight', 'packageDir': binding['packageDir'],
            'serverName': entry['serverName'], 'server': server,
            'configPath': binding['configPath'], 'configSha256': binding['configSha256']})
        if current() != binding:
            raise native.NativeError('native-mcp-binding-changed')
        if (type(value) is not dict or set(value) != {'eligible', 'reason', 'scope'}
                or type(value['eligible']) is not bool or value['scope'] != 'pixel-owner-default'
                or value['reason'] not in ('eligible', 'disabled', 'namespace-conflict', 'policy-blocked')
                or value['eligible'] != (value['reason'] == 'eligible')):
            raise native.NativeError('invalid-native-oauth-result')
        if not value['eligible']:
            raise native.NativeError('native-app-' + value['reason'])


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
        if request['action'] == 'forget' and server.get('enabled') is not False:
            raise native.NativeError('native-app-withdrawal-required')
        if request['action'] in ('begin', 'complete'):
            server['oauth'] = dict(server.get('oauth') or {}, redirectUrl=request['redirectUri'])
        payload = {'operation': request['action'], 'packageDir': binding['packageDir'],
                   'serverName': request['serverName'], 'server': server}
        if request['action'] == 'complete':
            payload['authorizationCode'] = request['authorizationCode']
        elif request['action'] == 'probe':
            payload.update(configPath=binding['configPath'], configSha256=binding['configSha256'])
        value = _invoke(binding, payload)
        if current() != binding:
            raise native.NativeError('native-mcp-binding-changed')
        return native.worker_result(value, request['action'])
