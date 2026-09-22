"""Prepare and activate a native Pixel update without replacing owner data."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def helper(name):
    spec = importlib.util.spec_from_file_location('native_update_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def activation_command(preparation, prepared, *, install_dir, ods_source, owner, transport):
    if prepared.get('status') != 'prepared' or prepared.get('installDir') != str(install_dir):
        raise ValueError('prepared-native-update-required')
    if prepared.get('runtimeDigest') == prepared.get('currentDigest'):
        raise ValueError('native-update-has-identical-runtime')
    command = ['/usr/bin/sudo', '/usr/bin/python3', str(HERE / 'pixel-macos-access-install.py'), 'migrate-native',
        '--source', str(ods_source), '--install-dir', str(install_dir), '--owner', owner,
        '--candidate', str(preparation / 'candidate'), '--runtime-bundle', str(preparation / 'runtime'),
        '--bundle-digest', prepared['runtimeDigest'], '--current-bundle-digest', prepared['currentDigest'],
        '--services-bundle', str(preparation / 'services'), '--services-digest', prepared['serviceDigest'],
        '--pixel-source-ref', prepared['pixelSourceRef'], '--gateway-port', str(prepared['gatewayPort']),
        '--access-port', str(prepared['accessPort']), '--activate']
    for flag, key in (('docker', 'docker'), ('compose-project', 'project'),
                      ('ingress-image', 'image'), ('ingress-user', 'user')):
        command.extend(['--' + flag, transport[key]])
    return command


def update(*, install_dir, ods_source, license_authorized=False, prepare_only=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    if not license_authorized:
        raise ValueError('pixel-license-authorization-required')
    install_dir, ods_source = Path(install_dir).resolve(strict=True), Path(ods_source).resolve(strict=True)
    stack = helper('pixel-native-stack')
    stack.resolve_files(install_dir, [])
    previous, _ = stack.read_selection(install_dir / 'data/pixel-native/preparation')
    installer = helper('pixel-macos-access-install')
    owner = pwd.getpwuid(os.getuid()).pw_name
    _, environment, _, _, node, _ = installer._source_gateway(installer._launchd.GATEWAY_PLIST, owner, 18789)
    if node.parent.name != previous['runtimeDigest']:
        raise ValueError('installed-native-selection-drift')
    transport = {key: environment[name] for key, name in (
        ('docker', 'PIXEL_HISTORY_DOCKER'), ('project', 'PIXEL_HISTORY_PROJECT'),
        ('image', 'PIXEL_HISTORY_IMAGE'), ('user', 'PIXEL_HISTORY_USER'))}
    installer._native_transport_environment(transport, pwd.getpwuid(os.getuid()))
    if not shutil.which(transport['docker']):
        raise ValueError('installed-docker-required')
    # Pin all subprocesses to the gateway's selected local Docker endpoint.
    endpoint = environment.get('DOCKER_HOST', '')
    if not endpoint.startswith('unix:///') or not Path(endpoint[7:]).is_socket():
        raise ValueError('installed-local-docker-socket-required')
    saved = {key: os.environ.get(key) for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
    for key in saved:
        os.environ.pop(key, None)
    os.environ['DOCKER_HOST'] = endpoint
    try:
        initial = helper('pixel-native-install')
        node, npm = initial.node_tools()
        root = install_dir / 'data/pixel-native'
        work = Path(tempfile.mkdtemp(prefix='update-', dir=root))
        print('Native update preparation: ' + str(work), flush=True)
        config = helper('pixel-native-config')
        source = config.bootstrap.acquire_source(ref=initial.DEFAULT_REF,
            destination=work / 'source', license_authorized=license_authorized)
        runtime = work / 'acquired-runtime'
        config.bootstrap.stage(source=source, ref=initial.DEFAULT_REF, destination=runtime, node=node, npm=npm)
        preparation = work / 'preparation'
        helper('pixel-native-prepare').prepare_migration(source=source, ref=initial.DEFAULT_REF,
            node=node, runtime=runtime, docker=transport['docker'], ods_source=ods_source,
            install_dir=install_dir, destination=preparation,
            license_authorized=license_authorized)
        prepared = config.private_json(preparation / 'preparation.json')
        command = activation_command(preparation, prepared, install_dir=install_dir,
            ods_source=ods_source, owner=owner, transport=transport)
        if prepare_only:
            return {'status': 'prepared', 'preparation': str(preparation)}
        subprocess.run(command, check=True, timeout=1800)
        return helper('pixel-native-finalize').finalize_update(preparation)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--ods-source', required=True)
    parser.add_argument('--license-authorized', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(update(**vars(args))))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Native update stopped (' + type(error).__name__ +
            '); retain its preparation and protected recovery journal. Do not reinstall or delete state.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
