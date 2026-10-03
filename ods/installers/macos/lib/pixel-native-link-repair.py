"""Repair unreadable metadata on the proved active legacy native Pixel bundle.

This root-only entrypoint is called only after the owner-side retained check
identifies a private symlink. It does not update Pixel or change its selection.
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
    spec = importlib.util.spec_from_file_location(
        'native_link_repair_' + filename.replace('-', '_'), HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def custody_module():
    path = HERE.parents[2] / 'bin/pixel_macos_custody.py'
    spec = importlib.util.spec_from_file_location('native_link_repair_custody', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def repair(install_dir, owner_uid):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('root-macos-link-repair-required')
    if type(owner_uid) is not int or owner_uid <= 0:
        raise ValueError('native-gateway-owner-invalid')
    owner = pwd.getpwuid(owner_uid)
    root = Path(install_dir)
    if (not root.is_absolute() or root.is_symlink() or not root.is_dir()
            or root.resolve(strict=True) != root or root.stat().st_uid != owner_uid):
        raise ValueError('owned-native-installation-required')
    native = root / 'data/pixel-native'
    preparation = native / 'preparation'
    if (native.is_symlink() or not native.is_dir() or native.stat().st_uid != owner_uid
            or preparation.is_symlink() or not preparation.is_dir()
            or preparation.stat().st_uid != owner_uid):
        raise ValueError('existing-native-pixel-needs-review')
    helper('pixel-native-retain.py').protected_clear()
    stack = helper('pixel-native-stack.py')
    stack.resolve_files(root, [])
    selected, active = stack.read_selection(preparation)
    digest = selected.get('runtimeDigest')
    services = selected.get('serviceDigest')
    if (type(digest) is not str or not re.fullmatch('[a-f0-9]{64}', digest)
            or type(services) is not str or not re.fullmatch('[a-f0-9]{64}', services)
            or active.get('runtimeDigest') != digest
            or active.get('serviceDigest') != services):
        raise ValueError('native-selection-changed')
    access = helper('pixel-macos-access-install.py')
    document, environment, _, _, node, entrypoint = access._source_gateway(
        access._launchd.GATEWAY_PLIST, owner.pw_name, 18789)
    bundle = access._bundle.INSTALL_ROOT / digest
    if (document.get('UserName') != owner.pw_name
            or node != bundle / 'node'
            or entrypoint != bundle / 'runtime/openclaw.mjs'
            or not access._source_runtime_config(
                environment.get('OPENCLAW_CONFIG_PATH'),
                access.RUNTIME_CONFIG_ROOT / str(owner_uid), digest)):
        raise ValueError('installed-native-selection-drift')
    loaded = access._command(['/bin/launchctl', 'print', access._launchd.GATEWAY_TARGET])
    custody_module().verify_loaded_launchd_definition(
        loaded, access._launchd.GATEWAY_TARGET, access._launchd.GATEWAY_PLIST, document)
    states = re.findall(r'^\tstate = ([^\n]+)$', loaded, re.MULTILINE)
    pids = re.findall(r'^\tpid = ([^\n]+)$', loaded, re.MULTILINE)
    if (states != ['running'] or len(pids) != 1 or not pids[0].isdecimal()
            or int(pids[0]) <= 0):
        raise ValueError('native-gateway-not-running')
    access._bundle.verify_service_binding(bundle, services)
    return access._bundle.repair_legacy_link_modes(digest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--owner-uid', required=True, type=int)
    args = parser.parse_args()
    try:
        repair(args.install_dir, args.owner_uid)
    except Exception:
        print('Native Pixel legacy link repair could not prove the active protected bundle. '
              'Keep its state intact for reviewed recovery.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
