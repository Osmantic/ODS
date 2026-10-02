"""Prove that an existing native Pixel can be left intact by a base rerun.

This is a read-only check. Native runtime changes still use the separate,
protected update coordinator; the initial-install helper must never resume an
existing preparation directory.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys


HERE = Path(__file__).resolve().parent
PROTECTED_STATE = Path('/private/var/lib/ods-pixel-access')
PENDING_JOURNALS = ('runtime-upgrade.json', 'transition.json', 'policy-activation.json')


def helper(filename):
    path = HERE / filename
    spec = importlib.util.spec_from_file_location('native_retain_' + filename.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def custody_module():
    path = HERE.parents[2] / 'bin/pixel_macos_custody.py'
    spec = importlib.util.spec_from_file_location('native_retain_custody', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def protected_clear():
    """Root-only existence check; the protected state directory is mode 0700."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('protected-native-state-check-required')
    info = PROTECTED_STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('unsafe-protected-native-state')
    if any(os.path.lexists(PROTECTED_STATE / name) for name in PENDING_JOURNALS):
        raise ValueError('native-protected-transition-pending')


def require_protected_clear(*, prompt_for_sudo=False):
    command = ['/usr/bin/sudo']
    if not prompt_for_sudo:
        command.append('-n')
    command.extend(['/usr/bin/python3', str(Path(__file__).resolve()), '--check-protected'])
    result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=None if prompt_for_sudo else subprocess.DEVNULL, timeout=120)
    if result.returncode:
        raise ValueError('native-protected-state-not-clear')


def verify_desired_services(ods_source, bundle_module, bundle, digest, services, expected_ref):
    """Bind the selected snapshot to the service bytes this rerun would copy."""
    source = Path(ods_source)
    if (not source.is_absolute() or source.is_symlink() or not source.is_dir()
            or source.resolve(strict=True) != source):
        raise ValueError('current-ods-source-required')
    config = helper('pixel-native-config.py')
    # The protected runtime embeds the approved service manifest. Owner-side
    # preparation/services may belong to an older selection after an update.
    selection = bundle_module.expected_release_selection(bundle, expected_digest=digest)
    manifest = selection.get('serviceManifest')
    if (selection.get('serviceBundleDigest') != services
            or selection.get('pixelSourceRevision') != expected_ref
            or type(manifest) is not dict):
        raise ValueError('native-service-manifest-changed')
    bundle_module.validate_service_manifest_provenance(manifest)
    records = manifest['files']
    for output, relative in config.SERVICE_SOURCES.items():
        body = config.service_snapshot(source, relative)
        record = records[output]
        if len(body) != record['bytes'] or hashlib.sha256(body).hexdigest() != record['sha256']:
            raise ValueError('native-service-source-changed')
    catalog = config.service_catalog(source)
    record = records['helpers/extension-catalog.json']
    if len(catalog) != record['bytes'] or hashlib.sha256(catalog).hexdigest() != record['sha256']:
        raise ValueError('native-service-catalog-changed')


def verify(install_dir, *, expected_ref=None, ods_source=None, prompt_for_sudo=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    root = Path(install_dir)
    if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
            or root.resolve(strict=True) != root or root.stat().st_uid != os.getuid()):
        raise ValueError('owned-native-installation-required')
    native = root / 'data/pixel-native'
    if native.is_symlink() or not native.is_dir() or native.stat().st_uid != os.getuid():
        raise ValueError('existing-native-pixel-needs-review')
    initial = helper('pixel-native-install.py')
    expected_ref = expected_ref or initial.DEFAULT_REF
    if not re.fullmatch('[a-f0-9]{40}', expected_ref):
        raise ValueError('exact-pixel-source-ref-required')
    require_protected_clear(prompt_for_sudo=prompt_for_sudo)
    preparation = native / 'preparation'
    if preparation.is_symlink() or not preparation.is_dir():
        raise ValueError('native-installation-needs-recovery')
    if not (os.path.lexists(preparation / 'activation.json')
            or os.path.lexists(preparation / 'selection-update.json')):
        raise ValueError('native-installation-needs-recovery')
    stack = helper('pixel-native-stack.py')
    stack.resolve_files(root, [])
    selected, active = stack.read_selection(preparation)
    digest = selected.get('runtimeDigest')
    services = selected.get('serviceDigest')
    if (selected.get('pixelSourceRef') != expected_ref
            or active.get('runtimeDigest') != digest
            or active.get('serviceDigest') != services
            or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
            or not isinstance(services, str) or not re.fullmatch('[a-f0-9]{64}', services)):
        raise ValueError('native-selection-changed')
    access = helper('pixel-macos-access-install.py')
    owner = pwd.getpwuid(os.getuid())
    document, environment, _, _, node, entrypoint = access._source_gateway(
        access._launchd.GATEWAY_PLIST, owner.pw_name, 18789)
    bundle = access._bundle.INSTALL_ROOT / digest
    if (document.get('UserName') != owner.pw_name
            or node != bundle / 'node'
            or entrypoint != bundle / 'runtime/openclaw.mjs'
            or not access._source_runtime_config(environment.get('OPENCLAW_CONFIG_PATH'),
                access.RUNTIME_CONFIG_ROOT / str(owner.pw_uid), digest)):
        raise ValueError('installed-native-selection-drift')
    target = access._launchd.GATEWAY_TARGET
    loaded = access._command(['/bin/launchctl', 'print', target])
    custody_module().verify_loaded_launchd_definition(
        loaded, target, access._launchd.GATEWAY_PLIST, document)
    states = re.findall(r'^\tstate = ([^\n]+)$', loaded, re.MULTILINE)
    pids = re.findall(r'^\tpid = ([^\n]+)$', loaded, re.MULTILINE)
    if (states != ['running'] or len(pids) != 1 or not pids[0].isdecimal()
            or int(pids[0]) <= 0):
        raise ValueError('native-gateway-not-running')
    access._bundle.verify(bundle, expected_digest=digest)
    # Current native installs bind the protected runtime to its service bundle.
    # Older unbound bundles need explicit migration and cannot enter this path.
    if not os.path.lexists(bundle / 'ods-service-binding.json'):
        raise ValueError('native-service-binding-required')
    access._bundle.verify_service_binding(bundle, services)
    verify_desired_services(ods_source, access._bundle, bundle, digest, services, expected_ref)
    return {'mode': 'retained', 'runtimeDigest': digest, 'serviceDigest': services}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir')
    parser.add_argument('--expected-ref')
    parser.add_argument('--ods-source')
    parser.add_argument('--prompt-for-sudo', action='store_true')
    parser.add_argument('--check-protected', action='store_true')
    args = parser.parse_args()
    try:
        if args.check_protected:
            protected_clear()
        else:
            if not args.install_dir or not args.ods_source:
                raise ValueError('native-retention-input-required')
            verify(args.install_dir, expected_ref=args.expected_ref, ods_source=args.ods_source,
                   prompt_for_sudo=args.prompt_for_sudo)
    except Exception:
        print('Native Pixel retention could not be proved. Keep its state intact and use the '
              'reviewed native update or recovery path.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
