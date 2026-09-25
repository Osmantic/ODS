"""Fixed isolated driver for installed native proof code, never a command API.

The parent holds the recovery flock. The child imports no checkout code, calls
no coordinator socket/lock, receives no credentials and cannot choose a path.
"""
import json

MAX_REQUEST = 16384
MAX_REPLY = 8192
MODULES = (
    'access_mode_server.py', 'unix_peer.py', 'access_mode_worker.py', 'pixel_access_mode.py',
    'access_mode_config.py', 'settings_transaction.py', 'provider_transaction.py', 'model_transaction.py',
    'pixel_access_bridge.py', 'pixel_gateway_service.py', 'pixel_access_client.py', 'pixel_access_reconcile.py',
    'pixel_model_transition.py', 'pixel_access_protocol.py', 'pixel_macos_custody.py', 'pixel_macos_process.py',
    'pixel_model_contract.py', 'pixel_model_coordinator.py', 'pixel_macos_policy.py',
    'pixel_settings/__init__.py', 'pixel_settings/contract.py', 'pixel_settings/projection.py',
    'pixel_settings/runtime.py', 'pixel_settings/coordinator.py', 'pixel_provider/__init__.py',
    'pixel_provider/config.py', 'pixel_provider/store.py', 'pixel_provider/activation_config.py',
    'pixel_provider/managed_deployment.py', 'pixel_provider/service_environment.py',
    'pixel_provider/service_activation.py', 'pixel_provider/runtime_custody.py', 'pixel_provider/coordinator.py',
)

PROGRAM = 'MODULES = ' + repr(MODULES) + '\n' + r'''
import hashlib, json, os, re, stat, sys, types
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path('/usr/local/libexec/ods-pixel-access')
CONFIG = Path('/private/etc/ods/pixel-access.json')
KEY = Path('/private/etc/ods/pixel-access-relay.key')
STATE = Path('/private/var/lib/ods-pixel-access')
AFTER = '90daa9ec87c571ed9f979a243df18c739a9708b7283f058be44b94af109cb29d'
def deny():
    raise ValueError('controller-held-proof-child-denied')
def hex64(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None
def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result: deny()
        result[key] = value
    return result
def decode(raw):
    if type(raw) is not bytes or len(raw) > 16384: deny()
    value = json.loads(raw, object_pairs_hook=unique)
    if (type(value) is not dict or set(value) != {'action','owner','settingsSha256','modules','token','mode','gatewayIdentity'}
            or value['action'] not in ('snapshot','acquire','probe','release')
            or not hex64(value['token']) or not hex64(value['settingsSha256'])
            or value['mode'] not in ('sandboxed','full-access')
            or type(value['modules']) is not dict or set(value['modules']) != set(MODULES)
            or any(not hex64(item) for item in value['modules'].values())
            or value['modules']['pixel_access_bridge.py'] != AFTER): deny()
    owner, identity = value['owner'], value['gatewayIdentity']
    if (type(owner) is not dict or set(owner) != {'name','uid','gid'}
            or type(owner['name']) is not str or not re.fullmatch('[A-Za-z_][A-Za-z0-9_-]{0,127}', owner['name'])
            or type(owner['uid']) is not int or owner['uid'] <= 0 or type(owner['gid']) is not int or owner['gid'] < 0
            or type(identity) is not list or len(identity) != 10
            or any(type(item) is not int or item < 0 for item in identity[:9])
            or identity[0] <= 0 or identity[1] <= 0 or identity[2] >= 1000000
            or type(identity[9]) is not str or not identity[9].startswith('/')
            or identity[3:9] != [owner['uid'], owner['gid']] * 3): deny()
    return value
def private_metadata(path, mode, uid=0, gid=0):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_gid != gid
            or stat.S_IMODE(info.st_mode) != mode or info.st_nlink != 1): deny()
def bootstrap_custody(request):
    # Bootstrap only the exact approved custody module bytes. Execute those
    # captured bytes, not a second path lookup; all later imports use its full
    # dirfd/ACL custody checks. No stdin field supplies Python source or paths.
    path = ROOT / 'pixel_macos_custody.py'
    for entry in (ROOT, *ROOT.parents):
        info = entry.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022: deny()
    private_metadata(path, 0o644)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        body = stream.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024 or hashlib.sha256(body).hexdigest() != request['modules']['pixel_macos_custody.py']: deny()
    module = types.ModuleType('pixel_macos_custody'); module.__file__ = str(path)
    sys.modules[module.__name__] = module
    exec(compile(body, str(path), 'exec'), module.__dict__)
    return module
def authority(custody, request):
    custody.protected_tree_metadata(ROOT)
    for name in MODULES:
        path = ROOT / name
        body = custody.protected_bytes(path, limit=8 * 1024 * 1024)
        private_metadata(path, 0o644)
        if hashlib.sha256(body).hexdigest() != request['modules'][name]: deny()
    body = custody.protected_bytes(CONFIG, limit=8192)
    private_metadata(CONFIG, 0o600)
    if hashlib.sha256(body).hexdigest() != request['settingsSha256']: deny()
    settings = json.loads(body, object_pairs_hook=unique)
    if (settings.get('owner') != request['owner']['name']
            or settings.get('state_dir') != str(STATE)
            or settings.get('gateway_target') != 'system/com.ods.pixel-native-gateway'
            or settings.get('gateway_plist') != '/Library/LaunchDaemons/com.ods.pixel-native-gateway.plist'
            or settings.get('openclaw_bin') != str(ROOT / 'openclaw-gateway-launcher')
            or not hex64(settings.get('edge_owner_key_sha256'))): deny()
    if any(os.path.lexists(STATE / name) for name in ('runtime-upgrade.json','transition.json','policy-activation.json')): deny()
    return settings
def key_bytes(settings, owner):
    private_metadata(KEY, 0o600, owner.pw_uid, owner.pw_gid)
    fd = os.open(KEY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner.pw_uid or info.st_gid != owner.pw_gid
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1): deny()
        body = stream.read(4097)
    if len(body) > 4096 or hashlib.sha256(body).hexdigest() != settings['edge_owner_key_sha256']: deny()
    return body.decode('ascii')
def ready(status, mode):
    return (status.get('available') is True and status.get('scope') == 'owner-host'
        and status.get('surface') == 'darwin' and status.get('configured_mode') == mode
        and status.get('effective_mode') == mode and status.get('runtime_verified') is True
        and status.get('busy') is False and status.get('pending') is False and status.get('reason') is None)
def project(native):
    keys = ('available','phase','active','pid','revision','proof','probe_failure','runtime_version')
    if (native.get('runtime_version') != '2026.6.33' or native.get('available') is not True
            or native.get('phase') not in ('idle','held') or type(native.get('active')) is not int or native['active'] != 0): deny()
    return {key: native.get(key) for key in keys}
def run(request):
    import pwd
    if sys.platform != 'darwin' or os.geteuid() != 0: deny()
    custody = bootstrap_custody(request)
    settings = authority(custody, request)
    owner = pwd.getpwnam(request['owner']['name'])
    if (owner.pw_uid, owner.pw_gid) != (request['owner']['uid'], request['owner']['gid']): deny()
    key = key_bytes(settings, owner)
    sys.path.insert(0, str(ROOT))
    from pixel_access_bridge import LaunchdAccessBridge
    bridge = LaunchdAccessBridge(settings['install_dir'], key, gateway_target=settings['gateway_target'],
        gateway_plist=settings['gateway_plist'], gateway_process=settings['gateway_process'],
        state=STATE, gateway_binding=settings['gateway_binding'], installed_binary=settings['openclaw_bin'],
        gateway_policy=settings['gateway_policy'], gateway_owner=owner.pw_name,
        settings_data_dir=settings.get('settings_data_dir'), gateway_port=settings.get('gateway_port'))
    with bridge.bounded(140):
        bridge.discover()
        def identity():
            if list(bridge.gateway_service.process_identity()) != request['gatewayIdentity']: deny()
        identity()
        if bridge._policy_state()['activeMode'] != request['mode']: deny()
        if request['action'] == 'probe': bridge.verify_held_mode(request['token'], request['mode'])
        elif request['action'] != 'snapshot': bridge.native(request['action'], request['token'])
        # Finish potentially slow custody checks before the final process/proof
        # observation. The parent can use this sample at its release boundary.
        if authority(custody, request) != settings: deny()
        if key_bytes(settings, owner) != key: deny()
        before = project(bridge.native(timeout=10))
        status = bridge.status()
        after = project(bridge.native(timeout=10))
        if before != after or after['pid'] != request['gatewayIdentity'][0]: deny()
        owned = bridge.owns_native_hold(after, request['token'])
        identity()
        return {'native': after, 'owned': owned, 'ready': ready(status, request['mode'])}
def main():
    try:
        request = decode(sys.stdin.buffer.read(16385))
        result = run(request)
        body = json.dumps(result, separators=(',', ':'), allow_nan=False)
        if len(body.encode()) > 8192: deny()
        print(body)
        return 0
    except BaseException:
        # Neither credentials, paths nor arbitrary subprocess errors are public.
        print('{"error":"controller-held-proof-child-failed"}')
        return 1
if __name__ == '__main__':
    raise SystemExit(main())
'''


def request_bytes(value):
    namespace = {'__name__': 'controller_proof_protocol'}
    exec(compile(PROGRAM, '<fixed-installed-proof-driver>', 'exec'), namespace)
    body = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    namespace['decode'](body)
    return body


def command():
    return ['/usr/bin/python3', '-I', '-B', '-c', PROGRAM]
