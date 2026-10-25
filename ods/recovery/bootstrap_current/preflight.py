"""Read-only installed compatibility and original custody preflight."""
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess

from custody import Directory, Refused, decode, digest, encode, read

BUNDLE = Path('/usr/local/libexec/ods-pixel-access')
SETTINGS = Path('/etc/ods/pixel-access.json')
STATE = Path('/var/lib/ods-pixel-access')
UNIT = 'ods-pixel-access.service'
UNIT_PATH = Path('/etc/systemd/system') / UNIT
RELEASE = ('release-intent.json', 'release-baseline.json', 'release-prepared.json', 'release-completed.json')


def capture_bundle(compatibility):
    expected = compatibility['bundle']
    if len(expected) != 32:
        raise Refused('unsupported-bundle')
    found = {}
    def visit(path, prefix=''):
        with Directory(path) as directory:
            for name in sorted(os.listdir(directory.fd)):
                info = directory.stat(name)
                relative = prefix + name
                if stat.S_ISDIR(info.st_mode):
                    visit(path / name, relative + '/')
                else:
                    found[relative] = directory.read(name, maximum=2*1024*1024)
    visit(BUNDLE)
    if set(found) != set(expected) or any(digest(raw) != expected[name] for name, raw in found.items()):
        raise Refused('unsupported-bundle')
    return found


def properties(unit, names):
    if unit not in (UNIT, 'openclaw-gateway.service'):
        raise Refused('fixed-unit-required')
    value = subprocess.run(['/usr/bin/systemctl', 'show', unit, '--property=' + ','.join(names)],
                           check=True, capture_output=True, text=True, timeout=10).stdout
    return dict(line.split('=', 1) for line in value.splitlines() if '=' in line)


def snapshot(compatibility, transaction=None):
    if transaction is not None and re.fullmatch('[a-f0-9]{64}', transaction) is None:
        raise Refused('invalid-transaction')
    capture_bundle(compatibility)
    unit = read(UNIT_PATH)
    if digest(unit) != compatibility['unitSha256']:
        raise Refused('unsupported-unit')
    service = properties(UNIT, ('LoadState', 'FragmentPath', 'DropInPaths', 'User', 'Group', 'Type',
                               'Restart', 'PrivateTmp', 'RuntimeDirectory', 'MainPID', 'ActiveState'))
    expected = dict(LoadState='loaded', FragmentPath=str(UNIT_PATH), DropInPaths='', User='root', Group='root',
                    Type='simple', Restart='on-failure', PrivateTmp='yes', RuntimeDirectory='ods-pixel-access')
    if any(service.get(key) != value for key, value in expected.items()):
        raise Refused('coordinator-unit-changed')
    settings_raw = read(SETTINGS, maximum=8192)
    settings = decode(settings_raw)
    if not isinstance(settings.get('owner'), str) or not re.fullmatch('[a-z_][a-z0-9_-]*', settings['owner']):
        raise Refused('owner-unavailable')
    owner = pwd.getpwnam(settings['owner'])
    if owner.pw_uid == 0:
        raise Refused('nonroot-owner-required')
    install, home = Path(settings['install_dir']), Path(owner.pw_dir)
    if not install.is_absolute() or install.resolve() != install or home.resolve() != home:
        raise Refused('installed-identity-changed')
    gateway = properties('openclaw-gateway.service', ('LoadState', 'User', 'MainPID', 'ActiveState', 'Restart'))
    if gateway.get('LoadState') != 'loaded' or gateway.get('User') != owner.pw_name:
        raise Refused('gateway-owner-changed')
    from pixel_access_bridge import SystemdAccessBridge
    from pixel_source_upgrade import SourceUpgrade
    # This constructor and journal parser perform no discovery, worker launch,
    # credential load, probe, gate acquisition, service action, or write.
    bridge = SystemdAccessBridge(install, '', gateway_owner=owner.pw_name, installed_binary=settings['openclaw_bin'])
    pending = bridge.pending()
    if pending is not None:
        pending = bridge.model_journal(transaction)
        if pending['phase'] not in ('held', 'error'):
            raise Refused('unsupported-transition-phase')
    elif transaction is not None:
        raise Refused('transaction-not-pending')
    with Directory(STATE) as directory:
        if any(name in os.listdir(directory.fd) for name in RELEASE):
            raise Refused('retained-release-custody')
    manager = SourceUpgrade(STATE / 'source-upgrade', install, owner.pw_uid)
    plan = manager.journal()
    if (plan is None or plan['phase'] != 'complete' or plan['outcome'] != 'applied'
            or pending is not None and plan['hold'] == pending['transaction_id']):
        raise Refused('source-custody-conflict')
    manager.verify_mirror()  # original operation; never bypass or replace it
    with Directory(STATE / 'source-upgrade') as directory:
        source = {name: digest(directory.read(name)) for name in ('source-upgrade.json', manager._mirror_name())}
    native = read(home / '.openclaw/openclaw.json', uid=owner.pw_uid, maximum=2*1024*1024)
    decode(native)
    value = dict(version=1, bundle=compatibility['bundle'], unitSha256=digest(unit), settingsSha256=digest(settings_raw),
                 source=source, owner=dict(name=owner.pw_name, uid=owner.pw_uid, gid=owner.pw_gid, home=str(home)),
                 install=str(install), gateway=gateway, nativeSha256=digest(native), original=pending)
    # Private binding stays inside the root process/claim. Public projection
    # excludes tokens, config values, credentials, source plans and paths.
    return value, settings


def inspect(compatibility, transaction=None):
    first, _ = snapshot(compatibility, transaction)
    from adapters import source_inputs
    sources = source_inputs(first)
    second, _ = snapshot(compatibility, transaction)
    if first != second or sources != source_inputs(second):
        raise Refused('preflight-changed')
    return dict(ok=True, bundleFiles=32, sourceMirrorVerified=True, unitVerified=True,
                ownerVerified=True, pending=first['original'] is not None,
                eligible=first['original'] is not None, bindingSha256=digest(encode(first)),
                ownerSourceFiles=len(sources), ownerSourceSha256=digest(encode(sources)),
                physicalRecoveryAccepted=False, mutationPerformed=False)
