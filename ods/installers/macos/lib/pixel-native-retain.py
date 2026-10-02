"""Prove that an existing native Pixel can be left intact by a base rerun.

This is a read-only check. Native runtime changes still use the separate,
protected update coordinator; the initial-install helper must never resume an
existing preparation directory.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import pwd
import re
import sys


HERE = Path(__file__).resolve().parent


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


def verify(install_dir, *, expected_ref=None):
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
    return {'mode': 'retained', 'runtimeDigest': digest, 'serviceDigest': services}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--expected-ref')
    args = parser.parse_args()
    try:
        verify(args.install_dir, expected_ref=args.expected_ref)
    except Exception:
        print('Native Pixel retention could not be proved. Keep its state intact and use the '
              'reviewed native update or recovery path.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
