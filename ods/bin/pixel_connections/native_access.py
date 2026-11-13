"""Private observations of existing reviewed-app access, never enrollment.

Check runs the existing native readiness probe. Status only joins its bounded
cache to current protected files/runtime; it never executes Node or a provider.
No observation can grant tools, sign in, or create an enrollment/Consent flow.
"""
import ast
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat

from pixel_access_bridge import atomic_json
from . import coordinator as c, native_apps as apps, native_mcp as native
from .native_enrollment import TERMINAL


NAME = 'native-app-access.json'
MAX_BYTES = 8192
TTL = 300
KIND = 'native-app-access-observation'
ADAPTER = Path(__file__).resolve().parents[1] / 'native_mcp_oauth.mjs'
CONTEXT_KEYS = {'ownerUid', 'ownerGid', 'recordSha256', 'configSha256', 'runtimeRevision',
                'runtimeIdentitySha256', 'nativeBindingSha256', 'gatewayBindingSha256', 'policySdkSha256'}
CACHE_BINDINGS = CONTEXT_KEYS - {'runtimeRevision'}


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def policy_fingerprint(binding):
    """Read the canonical protected adapter's literal list, without JS execution.

    These pins remain defined once in native_mcp_oauth.mjs. A new expression or
    list shape fails closed instead of inventing a second policy/pin registry.
    """
    try:
        raw = native._read(ADAPTER, owners=(0, os.geteuid()), maximum=128 * 1024)
        match = re.search(r'^const POLICY_MODULES = (\{.*?^\});$', raw.decode('utf-8'), re.MULTILINE | re.DOTALL)
        if match is None:
            raise ValueError()
        literal = re.sub(r'(?m)^(\s*)([a-z]+):', r"\1'\2':", match[1])
        value = ast.literal_eval(literal)
        if type(value) is not dict or set(value) != {'materialize', 'effective', 'sandbox', 'names'}:
            raise ValueError()
        hashes = {}
        for item in value.values():
            if (type(item) is not list or len(item) != 2 or type(item[0]) is not str
                    or re.fullmatch(r'dist/[A-Za-z0-9_-]+\.js', item[0]) is None
                    or not native.HEX.fullmatch(item[1]) or '..' in PurePosixPath(item[0]).parts):
                raise ValueError()
            content = native._read(Path(binding['packageDir']) / item[0], owners=(0, binding['ownerUid']),
                                   maximum=16 * 1024 * 1024)
            actual = _hash(content)
            if actual != item[1]:
                raise ValueError()
            hashes[item[0]] = actual
        return native.digest({'adapterSha256': _hash(raw), 'modules': hashes})
    except (OSError, ValueError, SyntaxError, TypeError, KeyError, UnicodeError):
        raise native.NativeError('native-app-source-unavailable') from None


def validate(value):
    keys = CONTEXT_KEYS | {'kind', 'schemaVersion', 'app', 'checkedAt', 'proof'}
    try:
        if (type(value) is not dict or set(value) != keys or value['kind'] != KIND
                or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
                or value['app'] != 'notion'
                or any(type(value[key]) is not int or not 0 <= value[key] <= 2**32 - 1 for key in ('ownerUid', 'ownerGid'))
                or any(type(value[key]) is not str or not native.HEX.fullmatch(value[key])
                       for key in CONTEXT_KEYS - {'ownerUid', 'ownerGid'})):
            raise ValueError()
        native._instant(value['checkedAt'])
        native.worker_result(value['proof'], 'probe')
        return value
    except (ValueError, TypeError, KeyError):
        raise native.NativeError('invalid-native-app-observation') from None


def _usable_gates(snapshot):
    """Observe canonical admission state without releasing either gate.

    Native's live contract has no stopped flag; its stopped recovery shape
    explicitly sets it. Edge always publishes admission_blocked, including
    while shutdown preserves an otherwise idle durable phase.
    """
    runtime, edge = snapshot.get('_native'), snapshot.get('_edge')
    if (type(runtime) is not dict or type(edge) is not dict
            or runtime.get('available') is not True or runtime.get('phase') not in ('idle', 'busy')
            or type(runtime.get('active')) is not int or runtime['active'] < 0
            or type(runtime.get('revision')) is not str or not native.HEX.fullmatch(runtime['revision'])
            or 'stopped' in runtime and runtime['stopped'] is not False
            or edge.get('capability') != 'available' or edge.get('phase') not in ('idle', 'busy')
            or edge.get('admission_blocked') is not False
            or type(edge.get('streams')) is not int or edge['streams'] < 0
            or type(edge.get('revision')) is not str or not native.HEX.fullmatch(edge['revision'])):
        raise native.NativeError('native-app-runtime-unverified')
    return runtime


class Observation:
    def __init__(self, bridge, controller):
        self.bridge, self.controller = bridge, controller
        self.path = Path(bridge.state) / NAME

    def _raw(self):
        info = self.path.parent.lstat()
        if (self.path.parent.resolve() != self.path.parent or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise native.NativeError('unsafe-native-app-observation')
        try:
            return native._read(self.path, owners=(os.geteuid(),), private=True, maximum=MAX_BYTES)
        except FileNotFoundError:
            if self.path.is_symlink():
                raise native.NativeError('unsafe-native-app-observation') from None
            return None
        except OSError:
            raise native.NativeError('unsafe-native-app-observation') from None

    def _read(self):
        raw = self._raw()
        if raw is None:
            return None
        try:
            return validate(native.decode(raw))
        except native.NativeError:
            return None  # Derived data only; never proof until freshly checked.

    def _choices(self):
        record = c._record()
        config, config_sha = self.controller._config()
        return record, config_sha, state_choice(record), apps.configured_choices(config).get(self.controller.app)

    def _context(self, binding=None):
        record, config_sha, chosen, configured = self._choices()
        if chosen is not True or configured is not True:
            raise native.NativeError('native-app-state-changed')
        current = native.bridge_binding(self.bridge, self.controller.entry['serverName'])
        if binding is not None and current != binding:
            raise native.NativeError('native-mcp-binding-changed')
        identity = c._identity(self.bridge)
        snapshot = self.bridge.inspect()
        revision = native._verified_runtime(self.bridge, current, snapshot=snapshot)
        runtime = _usable_gates(snapshot)
        proof = runtime.get('proof')
        if (runtime.get('pid') != identity['pid'] or type(proof) is not dict
                or proof.get('executed') is not True or proof.get('pid') != identity['pid']
                or proof.get('mode') != snapshot['effective_mode']):
            raise native.NativeError('native-app-runtime-unverified')
        runtime_identity = native.digest({'identity': identity, 'configuredMode': snapshot['configured_mode'],
            'effectiveMode': snapshot['effective_mode'], 'proof': proof, 'boundary': self.bridge.unit_boundary()})
        if c._identity(self.bridge) != identity:
            raise native.NativeError('native-mcp-binding-changed')
        return {'ownerUid': self.bridge.owner.pw_uid, 'ownerGid': self.bridge.owner.pw_gid,
                'recordSha256': native.digest(record), 'configSha256': config_sha, 'runtimeRevision': revision,
                'runtimeIdentitySha256': runtime_identity,
                'nativeBindingSha256': native.digest(current),
                'gatewayBindingSha256': native.digest(self.bridge.gateway_installation_binding(require_running=True)),
                'policySdkSha256': policy_fingerprint(current)}

    def _idle(self):
        # All callers already hold Root's lock. Pending enrollment, Google,
        # owner and Source admission have precedence over an observed cache.
        self.controller._other_transitions()
        self.controller._fresh_source()
        journal = self.controller._read()
        if journal and journal['phase'] not in TERMINAL:
            raise native.NativeError('native-activation-settlement-required')

    def _put(self, context, proof):
        # Validate custody BEFORE provider work at check(), and again before
        # replacement. Corrupt content is not authority and may be recomputed;
        # symlink/owner/mode/size problems are never silently reset.
        self._raw()
        value = dict(context, kind=KIND, schemaVersion=1, app=self.controller.app,
            checkedAt=native._iso(self.controller.now()), proof=native.worker_result(proof, 'probe'))
        validate(value)
        if len(native.encoded(value)) > MAX_BYTES:
            raise native.NativeError('invalid-native-app-observation')
        try:
            atomic_json(self.path, value)
        except OSError:
            raise native.NativeError('native-app-observation-unavailable') from None
        return value

    def _invalidate(self):
        # A new explicit observation supersedes any prior positive result,
        # including when the new SDK reply is lost. This derived cache grants
        # nothing; authoritative admission/configuration remain untouched.
        self._raw()
        try:
            self.path.unlink(missing_ok=True)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise native.NativeError('native-app-observation-unavailable') from None

    def record_enrollment(self, proof, binding):
        """Trusted post-SDK hook, inside the existing enrollment Root lock."""
        journal = self.controller._read()
        if journal is None or journal['phase'] != 'verifying' or journal['intent'] != 'connect':
            raise native.NativeError('native-app-transition-unconfirmed')
        self.controller._require_stable(journal)
        self.controller._other_transitions()
        self.controller._fresh_source()
        context = self._context(binding)
        if (context['configSha256'] != proof['configSha256'] or context['runtimeRevision'] != proof['runtimeRevision']
                or context['recordSha256'] != journal['stable']['recordSha256']):
            raise native.NativeError('native-app-state-changed')
        self._put(context, {key: proof[key] for key in ('reachable', 'permitted', 'reason', 'scope')})
        if self._context(binding) != context:
            raise native.NativeError('native-app-state-changed')

    def check(self):
        with self.bridge.bounded(70), self.bridge.locked():
            self.bridge.discover()
            self._idle()
            self._invalidate()
            before = self._context()
            proof = native._check_access_locked(self.bridge, self.controller.entry['serverName'])
            after = self._context()
            if (before != after or proof['configSha256'] != after['configSha256']
                    or proof['runtimeRevision'] != after['runtimeRevision']):
                raise native.NativeError('native-app-state-changed')
            observed = self._put(after, {key: proof[key] for key in ('reachable', 'permitted', 'reason', 'scope')})
            if self._context() != after:
                raise native.NativeError('native-app-state-changed')
            return self._public(observed)

    def _public(self, observed=None, *, error=None):
        journal = self.controller._read()
        ready = observed is not None and observed['proof']['permitted']
        result = {'app': self.controller.app, 'phase': 'ready' if ready else 'configured',
                  'flowId': journal['flowId'] if journal and journal['phase'] == 'ready' else None,
                  'error': None if ready else error or ('native-app-' + observed['proof']['reason'] if observed else 'native-app-verification-required'),
                  'portalAccessVerified': bool(ready), 'scope': 'pixel-owner-default'}
        if ready:
            result['checkedAt'] = observed['checkedAt']
        return result

    def status(self):
        with self.bridge.bounded(20), self.bridge.locked():
            self.bridge.discover()
            journal = self.controller._read()
            if journal and journal['phase'] not in TERMINAL:
                return self.controller._public(journal)
            _record, config_sha, chosen, configured = self._choices()
            if chosen is True and configured is True:
                try:
                    self._idle()
                    observed = self._read()
                    if observed is not None:
                        age = (self.controller.now() - native._instant(observed['checkedAt'])).total_seconds()
                        current = self._context()
                        # Chat/direct/cron admission epochs change on each
                        # ordinary turn. They remain strict inside SDK probes,
                        # while TTL retention binds the same proved process,
                        # boot/config/mode/boundary and reviewed source bytes.
                        if 0 <= age <= TTL and all(observed[key] == current[key] for key in CACHE_BINDINGS):
                            return self._public(observed)
                    return self._public()
                except (native.NativeError, OSError):
                    return self._public(error='native-app-verification-required')
            if chosen is True or configured is True:
                return self._public(error='native-app-state-changed')
            if journal and journal['phase'] == 'disconnected':
                try:
                    self._idle()
                    native._verified_runtime(self.bridge, {'configSha256': config_sha})
                except (native.NativeError, OSError):
                    return self._public(error='native-app-runtime-unverified')
            if journal and journal['phase'] == 'ready':
                return self._public(error='native-app-state-changed')
            return self.controller._public(journal)


def state_choice(record):
    from .state import native_app_choices
    return native_app_choices(record).get('notion')
