"""Bound native MCP consent; credentials remain exclusively in OpenClaw.

Root selects an existing server from the installed registry. This is an internal
custodian seam, not a public enrollment endpoint or a tool grant. The journal
records intent and a callback claim, never codes, PKCE or tokens. Unknown exchange
completion must be resolved by a new explicit sign-in, never by replaying a code.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import tempfile
from urllib.parse import parse_qsl, urlsplit

from . import oauth

VERSION = '2026.6.33'
MODULE = 'dist/agent-bundle-mcp-runtime-tW2Zz-uG.js'
MODULE_SHA = 'ed2e1d92189326625e7c8a735b83ede3942e1597e547587d8ee6413a7fd23bf0'
SERVER_NAME = re.compile(r'[a-z][a-z0-9-]{1,29}\Z')
HEX = re.compile(r'[a-f0-9]{64}\Z')
PHASES = ('beginning', 'consent', 'exchanging', 'authorized', 'activating', 'settled', 'failed', 'cancelled')
BINDING_KEYS = {'ownerUid', 'home', 'configPath', 'stateDir', 'packageDir', 'nodePath', 'serverName',
                'binaryPath', 'configSha256', 'serverSha256', 'binarySha256', 'nodeSha256', 'nativeSha256', 'packageJsonSha256'}
JOURNAL_KEYS = {'kind', 'schemaVersion', 'app', 'serverName', 'bindingSha256', 'flowId',
                'phase', 'redirectUri', 'stateHash', 'createdAt', 'expiresAt', 'error'}


class NativeError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result

    def constant(_value):
        raise ValueError()

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise NativeError('invalid-native-mcp-state') from None


def _path(value):
    return (type(value) is str and value.startswith('/') and value != '/'
            and not any(ord(char) < 32 for char in value)
            and all(part not in ('', '.', '..') for part in value.split('/')[1:]))


def validate_binding(value):
    if (type(value) is not dict or set(value) != BINDING_KEYS
            or type(value['ownerUid']) is not int or value['ownerUid'] <= 0
            or type(value['serverName']) is not str or not SERVER_NAME.fullmatch(value['serverName'])
            or any(not _path(value[key]) for key in ('home', 'configPath', 'stateDir', 'packageDir', 'nodePath', 'binaryPath'))
            or any(type(value[key]) is not str or not HEX.fullmatch(value[key])
                   for key in BINDING_KEYS if key.endswith('Sha256'))):
        raise NativeError('invalid-native-mcp-binding')
    return value


def _read(path, *, owners, private=False, maximum=2 * 1024 * 1024):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        before = os.fstat(handle.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid not in owners
                or before.st_mode & (0o077 if private else 0o022) or before.st_size > maximum):
            raise NativeError('unsafe-native-mcp-state')
        data = handle.read(maximum + 1)
        after = os.fstat(handle.fileno())
        if (len(data) > maximum or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise NativeError('native-mcp-binding-changed')
        return data


def _parents(path, uid):
    """A third account must not be able to replace a bound file's ancestors."""
    for parent in Path(path).parents:
        info = parent.lstat()
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid)
                or info.st_mode & 0o022 and not sticky_root):
            raise NativeError('unsafe-native-mcp-state')


def selected_server(config, server_name):
    if type(server_name) is not str or not SERVER_NAME.fullmatch(server_name):
        raise NativeError('invalid-native-mcp-server')
    mcp = config.get('mcp') if type(config) is dict else None
    registry = mcp.get('servers') if type(mcp) is dict else None
    server = registry.get(server_name) if type(registry) is dict else None
    # Consent may prepare a disabled entry. This helper grants no tool access;
    # keeping it disabled prevents a broad preexisting MCP policy exposing it.
    if (type(server) is not dict or server.get('auth') != 'oauth'
            or server.get('transport') != 'streamable-http' or type(server.get('url')) is not str
            or 'enabled' in server and type(server['enabled']) is not bool
            or server.get('sslVerify') is False or server.get('oauth') is not None and type(server['oauth']) is not dict):
        raise NativeError('invalid-native-mcp-server')
    return copy.deepcopy(server)


def derive_binding(*, config_path, uid, home, state_dir, binary, search_path, server_name):
    """Both Root and its owner worker independently derive this exact identity.

    All paths come from the protected deployment/environment. None are accepted
    by the consent methods from a browser, model or callback.
    """
    try:
        config_path, home, state_dir = map(Path, (config_path, home, state_dir))
        for path in (config_path, home, state_dir):
            if not path.is_absolute() or path.resolve(strict=True) != path:
                raise NativeError('unsafe-native-mcp-state')
            _parents(path, uid)
        for path in (home, state_dir):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
                raise NativeError('unsafe-native-mcp-state')
        raw = _read(config_path, owners=(uid,), private=True)
        server = selected_server(decode(raw), server_name)
        launcher = Path(binary).resolve(strict=True)
        _parents(launcher, uid)
        if launcher.name != 'openclaw.mjs':
            raise NativeError('native-oauth-runtime-unsupported')
        package = launcher.parent
        metadata_raw = _read(package / 'package.json', owners=(0, uid))
        metadata = decode(metadata_raw)
        if type(metadata) is not dict or (metadata.get('name'), metadata.get('version')) != ('openclaw', VERSION):
            raise NativeError('native-oauth-runtime-unsupported')
        module_sha = hashlib.sha256(_read(package / MODULE, owners=(0, uid))).hexdigest()
        if module_sha != MODULE_SHA:
            raise NativeError('native-oauth-runtime-unsupported')
        node = shutil.which('node', path=search_path)
        if not node:
            raise NativeError('native-oauth-runtime-unsupported')
        node = Path(node).resolve(strict=True)
        _parents(node, uid)
        value = {'ownerUid': uid, 'home': str(home), 'stateDir': str(state_dir), 'configPath': str(config_path),
                 'packageDir': str(package), 'nodePath': str(node), 'binaryPath': str(launcher), 'serverName': server_name,
                 'configSha256': hashlib.sha256(raw).hexdigest(), 'serverSha256': digest(server),
                 'binarySha256': hashlib.sha256(_read(launcher, owners=(0, uid))).hexdigest(),
                 'nodeSha256': hashlib.sha256(_read(node, owners=(0, uid), maximum=512 * 1024 * 1024)).hexdigest(),
                 'nativeSha256': module_sha, 'packageJsonSha256': hashlib.sha256(metadata_raw).hexdigest()}
        return validate_binding(value)
    except (OSError, RuntimeError):
        raise NativeError('native-mcp-binding-unavailable') from None


def valid_redirect(value):
    if type(value) is not str or not re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}/', value):
        return False
    return 1024 <= int(urlsplit(value).port) <= 65535


def worker_request(value):
    if type(value) is not dict:
        raise NativeError('invalid-native-oauth-request')
    expected = {'action', 'binding', 'serverName'}
    if value.get('action') in ('begin', 'complete'):
        expected.add('redirectUri')
    if value.get('action') == 'complete':
        expected.add('authorizationCode')
    if (set(value) != expected or value['action'] not in ('begin', 'complete', 'probe', 'forget')
            or type(value['serverName']) is not str or not SERVER_NAME.fullmatch(value['serverName'])
            or value['action'] in ('begin', 'complete') and not valid_redirect(value['redirectUri'])
            or value['action'] == 'complete' and (type(value['authorizationCode']) is not str
                                                or not oauth.CODE.fullmatch(value['authorizationCode']))):
        raise NativeError('invalid-native-oauth-request')
    validate_binding(value['binding'])
    if value['serverName'] != value['binding']['serverName']:
        raise NativeError('invalid-native-mcp-binding')
    return value


def worker_result(value, action=None):
    if action == 'probe':
        if (type(value) is not dict or set(value) != {'reachable', 'permitted', 'reason', 'scope'}
                or type(value['reachable']) is not bool or type(value['permitted']) is not bool
                or value['scope'] != 'pixel-owner-default'
                or value['reason'] not in ('ready', 'disabled', 'sign-in-required', 'namespace-conflict',
                                           'unreachable', 'tools-unavailable', 'policy-blocked')
                or value['permitted'] != (value['reason'] == 'ready')
                or value['reachable'] != (value['reason'] in ('ready', 'tools-unavailable', 'policy-blocked'))):
            raise NativeError('invalid-native-oauth-result')
        return value
    if action == 'forget':
        if type(value) is not dict or value != {'forgotten': True} or type(value['forgotten']) is not bool:
            raise NativeError('invalid-native-oauth-result')
        return value
    if (type(value) is not dict or set(value) != {'outcome', 'authUrl'}
            or value['outcome'] not in ('redirect', 'authorized')
            or value['outcome'] == 'authorized' and value['authUrl'] is not None
            or value['outcome'] == 'redirect' and (type(value['authUrl']) is not str
                                                  or not 1 <= len(value['authUrl'].encode()) <= 7000)):
        raise NativeError('invalid-native-oauth-result')
    return value


def bridge_binding(bridge, server_name):
    """Derive the installed owner/runtime identity under the caller's Root lock."""
    from pixel_access_bridge import runtime_config_path
    bridge.discover()
    if bridge.pending():
        raise NativeError('transition-recovery-required')
    environment = bridge.worker_environment()
    return derive_binding(config_path=runtime_config_path(bridge), uid=bridge.owner.pw_uid,
                          home=bridge.home, state_dir=environment.get('OPENCLAW_STATE_DIR', bridge.home / '.openclaw'),
                          binary=bridge.binary, search_path=environment['PATH'], server_name=server_name)


def _verified_runtime(bridge, binding):
    snapshot = bridge.inspect()
    if (snapshot.get('runtime_verified') is not True or snapshot.get('pending')
            or snapshot.get('configured_mode') not in ('sandboxed', 'full-access')
            or snapshot.get('effective_mode') != snapshot.get('configured_mode')
            or snapshot.get('_config', {}).get('config_sha256') != binding['configSha256']
            or type(snapshot.get('revision')) is not str or not HEX.fullmatch(snapshot['revision'])):
        raise NativeError('native-app-runtime-unverified')
    return snapshot['revision']


def check_access(bridge, server_name):
    """Check the selected native tools in the verified default Portal context.

    The worker uses native effective policy, without executing a content tool.
    A journal/token alone cannot supply this proof. Nothing enrolls an app here.
    """
    with bridge.bounded(70), bridge.locked():
        return _check_access_locked(bridge, server_name)


def _check_access_locked(bridge, server_name):
    """Internal controller seam; the caller already holds Root's access lock."""
    binding = bridge_binding(bridge, server_name)
    revision = _verified_runtime(bridge, binding)
    request = worker_request({'action': 'probe', 'binding': binding, 'serverName': server_name})
    result = worker_result(bridge.worker('native-mcp-oauth', config_hash=binding['configSha256'],
                                        native_oauth_request=request), 'probe')
    if bridge_binding(bridge, server_name) != binding or _verified_runtime(bridge, binding) != revision:
        raise NativeError('native-mcp-binding-changed')
    return dict(result, configSha256=binding['configSha256'], runtimeRevision=revision)


def forget_disabled(bridge, server_name):
    """Forget locally only after the disabled config has verified runtime proof.

    The owner independently checks the disabled entry and its apply lock. The
    Root proof also prevents an edit without a verified gateway restart from
    being mistaken for withdrawal of credentials cached by the active gateway.
    Provider-side authorization is not revoked by this operation.
    """
    with bridge.bounded(70), bridge.locked():
        return _forget_disabled_locked(bridge, server_name)


def _forget_disabled_locked(bridge, server_name):
    """Internal controller seam; withdrawal and Root's lock are already held."""
    binding = bridge_binding(bridge, server_name)
    revision = _verified_runtime(bridge, binding)
    raw = _read(Path(binding['configPath']), owners=(binding['ownerUid'],), private=True)
    if hashlib.sha256(raw).hexdigest() != binding['configSha256']:
        raise NativeError('native-mcp-binding-changed')
    if selected_server(decode(raw), server_name).get('enabled') is not False:
        raise NativeError('native-app-withdrawal-required')
    request = worker_request({'action': 'forget', 'binding': binding, 'serverName': server_name})
    result = worker_result(bridge.worker('native-mcp-oauth', config_hash=binding['configSha256'],
                                        native_oauth_request=request), 'forget')
    if bridge_binding(bridge, server_name) != binding or _verified_runtime(bridge, binding) != revision:
        raise NativeError('native-mcp-binding-changed')
    return dict(result, providerRevoked=False)


def _instant(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        raise ValueError()
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def validate_journal(value):
    try:
        if (type(value) is not dict or not JOURNAL_KEYS <= set(value) or set(value) - JOURNAL_KEYS - {'activationId'}
                or value['kind'] != 'native-mcp-consent'
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or any(type(value[k]) is not str or not SERVER_NAME.fullmatch(value[k]) for k in ('app', 'serverName'))
                or type(value['bindingSha256']) is not str or not HEX.fullmatch(value['bindingSha256'])
                or type(value['flowId']) is not str or not oauth.FLOW_ID.fullmatch(value['flowId'])
                or value['phase'] not in PHASES or not valid_redirect(value['redirectUri'])
                or value['stateHash'] is not None and (type(value['stateHash']) is not str or not HEX.fullmatch(value['stateHash']))
                or value['phase'] in ('consent', 'exchanging') and value['stateHash'] is None
                or value['error'] is not None and (type(value['error']) is not str
                                                  or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}', value['error']))
                or value['phase'] == 'failed' and value['error'] is None):
            raise ValueError()
        if value['phase'] in ('activating', 'settled'):
            if type(value.get('activationId')) is not str or not HEX.fullmatch(value['activationId']):
                raise ValueError()
        elif 'activationId' in value:
            raise ValueError()
        duration = (_instant(value['expiresAt']) - _instant(value['createdAt'])).total_seconds()
        if duration != oauth.FLOW_SECONDS:
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise NativeError('invalid-native-mcp-journal') from None


class Consent:
    """Root's consent transition under its existing access lock and deadline.

    The caller selects app/server identifiers. Until authenticated enrollment is
    wired, no public socket operation constructs this object. No method grants
    tools, edits the registry, restarts the gateway or forgets existing tokens.
    """

    def __init__(self, bridge, *, app, server_name, now=None):
        if any(type(name) is not str or not SERVER_NAME.fullmatch(name) for name in (app, server_name)):
            raise NativeError('invalid-native-mcp-server')
        self.bridge, self.app, self.server_name = bridge, app, server_name
        self.path = Path(bridge.state) / 'native-mcp-consent.json'
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _parent(self):
        info = self.path.parent.lstat()
        if (self.path.parent.resolve() != self.path.parent or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise NativeError('unsafe-native-mcp-journal')

    def _read(self):
        self._parent()
        try:
            value = validate_journal(decode(_read(self.path, owners=(os.geteuid(),), private=True, maximum=8192)))
        except FileNotFoundError:
            if self.path.is_symlink():
                raise NativeError('unsafe-native-mcp-journal') from None
            return None
        if (value['app'], value['serverName']) != (self.app, self.server_name):
            raise NativeError('another-native-sign-in-pending')
        return value

    def _write(self, value):
        self._parent()
        raw = encoded(validate_journal(value)) + b'\n'
        # Share the existing narrowly recognized crash-temp prefix, so an
        # interrupted private write does not strand verified uninstall.
        fd, temporary = tempfile.mkstemp(prefix='.transition-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'wb') as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            fd = os.open(self.path.parent, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)

    def _binding(self):
        return bridge_binding(self.bridge, self.server_name)

    def _run(self, action, binding, redirect, **values):
        request = worker_request(dict(action=action, binding=binding, serverName=self.server_name,
                                      redirectUri=redirect, **values))
        return worker_result(self.bridge.worker('native-mcp-oauth', config_hash=binding['configSha256'],
                                                native_oauth_request=request), action)

    def begin(self, *, redirect_port):
        with self.bridge.bounded(70), self.bridge.locked():
            return self._begin_locked(redirect_port=redirect_port)

    def _begin_locked(self, *, redirect_port, flow_id=None):
        if flow_id is None:
            flow_id = secrets.token_hex(16)
        if type(flow_id) is not str or not oauth.FLOW_ID.fullmatch(flow_id):
            raise NativeError('native-flow-mismatch')
        if type(redirect_port) is not int or not 1024 <= redirect_port <= 65535:
            raise NativeError('invalid-native-oauth-redirect')
        old = self._read()
        if old and old['phase'] not in ('failed', 'cancelled', 'settled'):
            raise NativeError('native-sign-in-in-progress')
        binding = self._binding()
        now = self.now()
        journal = dict(kind='native-mcp-consent', schemaVersion=1, app=self.app, serverName=self.server_name,
                       bindingSha256=digest(binding), flowId=flow_id, phase='beginning',
                       redirectUri=f'http://127.0.0.1:{redirect_port}/', stateHash=None,
                       createdAt=_iso(now), expiresAt=_iso(now + timedelta(seconds=oauth.FLOW_SECONDS)), error=None)
        self._write(journal)  # Durable before the first native side effect.
        result = self._run('begin', binding, journal['redirectUri'])
        if digest(self._binding()) != journal['bindingSha256']:
            raise NativeError('native-mcp-binding-changed')
        if result['outcome'] == 'redirect':
            try:
                url = urlsplit(result['authUrl'])
                fields = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True, max_num_fields=32)
                values = dict(fields)
                if (len(values) != len(fields) or url.username or url.password or url.fragment
                        or url.scheme not in ('https', 'http') or not url.hostname
                        or url.scheme == 'http' and url.hostname != '127.0.0.1'
                        or values.get('redirect_uri') != journal['redirectUri']
                        or not oauth.STATE.fullmatch(values.get('state', ''))
                        or values.get('response_type') != 'code' or values.get('code_challenge_method') != 'S256'):
                    raise ValueError()
            except ValueError:
                raise NativeError('invalid-native-oauth-result') from None
            journal.update(phase='consent', stateHash=oauth.state_hash(values['state']))
        else:
            journal['phase'] = 'authorized'
        self._write(journal)
        return {'flowId': journal['flowId'], 'phase': journal['phase'], 'authUrl': result['authUrl'],
                'expiresAt': journal['expiresAt'], 'portalAccessVerified': False}

    def complete(self, *, flow_id, callback_url):
        with self.bridge.bounded(70), self.bridge.locked():
            return self._complete_locked(flow_id=flow_id, callback_url=callback_url)

    def _complete_locked(self, *, flow_id, callback_url):
        journal = self._flow(flow_id)
        if journal['phase'] != 'consent':
            raise NativeError('native-callback-already-claimed')
        if self.now() >= _instant(journal['expiresAt']):
            raise NativeError('native-flow-expired')
        if (type(callback_url) is not str or len(callback_url) > 8192
                or not callback_url.startswith(journal['redirectUri'] + '?')
                or urlsplit(callback_url).fragment):
            raise NativeError('native-callback-redirect-mismatch')
        try:
            fields = oauth.parse_callback(callback_url.partition('?')[2], expected_state_hash=journal['stateHash'])
        except oauth.OAuthError as error:
            raise NativeError(error.code) from None
        if 'error' in fields:
            self._write(dict(journal, phase='failed', error=fields['error']))
            return {'phase': 'failed', 'error': fields['error'], 'portalAccessVerified': False}
        binding = self._binding()
        if not hmac.compare_digest(digest(binding), journal['bindingSha256']):
            raise NativeError('native-mcp-binding-changed')
        journal['phase'] = 'exchanging'
        self._write(journal)  # Claim before exchanging, even if the process dies next.
        result = self._run('complete', binding, journal['redirectUri'], authorizationCode=fields['code'])
        if result['outcome'] != 'authorized' or digest(self._binding()) != journal['bindingSha256']:
            raise NativeError('native-oauth-exchange-unconfirmed')
        self._write(dict(journal, phase='authorized'))
        return {'phase': 'authorized', 'error': None, 'portalAccessVerified': False}

    def _flow(self, flow_id):
        journal = self._read()
        if (type(flow_id) is not str or not oauth.FLOW_ID.fullmatch(flow_id)
                or journal is None or not hmac.compare_digest(flow_id, journal['flowId'])):
            raise NativeError('native-flow-mismatch')
        return journal

    def _claim_authorization_locked(self, *, flow_id, activation_id):
        """Reserve a verified authorization for one Root plan, under its lock.

        The enrollment controller must persist that plan before calling this,
        and settle the configuration transaction before finishing the claim.
        This method changes no configuration and certifies no Portal access.
        """
        if type(activation_id) is not str or not HEX.fullmatch(activation_id):
            raise NativeError('invalid-native-activation-id')
        journal = self._flow(flow_id)
        if journal['phase'] != 'authorized':
            raise NativeError('native-authorization-already-claimed')
        if self.now() >= _instant(journal['expiresAt']):
            raise NativeError('native-flow-expired')
        binding = self._binding()
        if not hmac.compare_digest(digest(binding), journal['bindingSha256']):
            raise NativeError('native-mcp-binding-changed')
        raw = _read(Path(binding['configPath']), owners=(binding['ownerUid'],), private=True)
        if hashlib.sha256(raw).hexdigest() != binding['configSha256']:
            raise NativeError('native-mcp-binding-changed')
        if selected_server(decode(raw), self.server_name).get('enabled') is not False:
            raise NativeError('native-app-preparation-required')
        journal = dict(journal, phase='activating', activationId=activation_id)
        self._write(journal)  # Durable consumption before the caller can grant tools.
        return {key: journal[key] for key in ('flowId', 'activationId', 'bindingSha256', 'phase')}

    def claim_authorization(self, *, flow_id, activation_id):
        with self.bridge.bounded(30), self.bridge.locked():
            return self._claim_authorization_locked(flow_id=flow_id, activation_id=activation_id)

    def _finish_authorization_locked(self, *, flow_id, activation_id):
        """Close the same Root reservation; this alone never means app-ready."""
        journal = self._flow(flow_id)
        if (type(activation_id) is not str or not HEX.fullmatch(activation_id)
                or journal['phase'] not in ('activating', 'settled')
                or not hmac.compare_digest(activation_id, journal['activationId'])):
            raise NativeError('native-activation-claim-mismatch')
        if journal['phase'] == 'activating':
            self._write(dict(journal, phase='settled'))
        return {'phase': 'settled', 'portalAccessVerified': False}

    def finish_authorization(self, *, flow_id, activation_id):
        with self.bridge.bounded(20), self.bridge.locked():
            return self._finish_authorization_locked(flow_id=flow_id, activation_id=activation_id)

    def recover(self, *, flow_id):
        with self.bridge.bounded(20), self.bridge.locked():
            return self._recover_locked(flow_id=flow_id)

    def _recover_locked(self, *, flow_id):
        """Record an unknown attempt as failed; never infer success from token presence."""
        journal = self._flow(flow_id)
        if journal['phase'] in ('beginning', 'exchanging'):
            journal = dict(journal, phase='failed', error='native-oauth-attempt-unconfirmed')
            self._write(journal)
        elif journal['phase'] in ('consent', 'authorized') and self.now() >= _instant(journal['expiresAt']):
            journal = dict(journal, phase='failed', error='native-flow-expired')
            self._write(journal)
        return self._public(journal)

    def cancel(self, *, flow_id):
        with self.bridge.bounded(20), self.bridge.locked():
            return self._cancel_locked(flow_id=flow_id)

    def _cancel_locked(self, *, flow_id):
        journal = self._flow(flow_id)
        if journal['phase'] in ('activating', 'settled'):
            raise NativeError('native-activation-settlement-required')
        self._write(dict(journal, phase='cancelled', error=None))
        # Existing valid native tokens survive cancellation. No active
        # gateway permissions are withdrawn or credentials forgotten here.
        return {'phase': 'cancelled', 'portalAccessVerified': False}

    @staticmethod
    def _public(journal):
        if journal is None:
            return None
        return {key: journal[key] for key in ('app', 'phase', 'expiresAt', 'error')} | {'portalAccessVerified': False}

    def status(self):
        with self.bridge.bounded(20), self.bridge.locked():
            return self._public(self._read())
