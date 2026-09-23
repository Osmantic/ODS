"""Connect the macOS installer's resolved base stack to initial native Pixel setup."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys


HERE = Path(__file__).resolve().parent
DEFAULT_REF = '817214d5ec3d8aa583fe50c1dc7561f3c1a16dff'
INGRESS_IMAGE = 'node:24-bookworm-slim'
FRAGMENTS = ('extensions/services/pixel-model-relay/compose.yaml.disabled',
    'extensions/services/pixel-edge/compose.yaml.disabled',
    'installers/macos/pixel-native.compose.yaml.disabled')
ERROR_GUIDANCE = {
    'native-apple-silicon-owner-required': 'Run as the signed-in owner on Apple Silicon, not with sudo.',
    'existing-native-pixel-requires-migration-or-recovery':
        'Existing native Pixel state was found. This initial-install path cannot migrate or resume it; leave it intact.',
    'native-node-or-homebrew-required': 'Install native Node.js 22+ with npm, or Homebrew for automatic Node setup.',
    'local-docker-socket-required': 'Select a local Docker Desktop Unix-socket context.',
}


def helper(name):
    spec = importlib.util.spec_from_file_location('native_install_' + name,
        HERE / ('pixel-native-' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(args, *, env=None, timeout=60):
    result = subprocess.run([str(arg) for arg in args], env=env,
        capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise ValueError('native-installer-command-failed')
    return result.stdout.strip()


def preflight(install_dir, *, license_authorized=None):
    if sys.platform != 'darwin' or platform.machine() != 'arm64' or os.geteuid() == 0:
        raise ValueError('native-apple-silicon-owner-required')
    install_dir = Path(install_dir)
    if not install_dir.is_absolute():
        raise ValueError('absolute-ods-installation-required')
    # The initial path cannot safely infer migration or resume from partial state.
    for path in (install_dir / 'data/pixel-native',
                 Path('/private/etc/ods/pixel-access.json'),
                 Path('/private/var/lib/ods-pixel-access')):
        if os.path.lexists(path):
            raise ValueError('existing-native-pixel-requires-migration-or-recovery')
    return install_dir


def node_tools():
    node, npm = shutil.which('node'), shutil.which('npm')
    def usable(node, npm):
        if not node or not npm:
            return False
        try:
            data = json.loads(command([node, '-p',
                'JSON.stringify({platform:process.platform,arch:process.arch,major:Number(process.versions.node.split(".")[0]),execPath:process.execPath})']))
            if data['platform'] != 'darwin' or data['arch'] != 'arm64' or data['major'] < 22:
                return False
            actual_node = Path(data['execPath']).resolve(strict=True)
            actual_npm = Path(npm).resolve(strict=True)
            if actual_npm.name == 'volta-shim':
                volta = shutil.which('volta')
                if not volta:
                    return False
                actual_npm = Path(command([volta, 'which', 'npm'])).resolve(strict=True)
            env = {**os.environ, 'PATH': str(actual_node.parent) + ':/usr/bin:/bin:/usr/sbin:/sbin'}
            if not re.fullmatch(r'\d+\.\d+\.\d+', command([actual_npm, '--version'], env=env)):
                return False
            return actual_node, actual_npm
        except (ValueError, OSError, KeyError, subprocess.SubprocessError):
            return False
    selected = usable(node, npm)
    if not selected:
        brew = shutil.which('brew')
        if not brew:
            raise ValueError('native-node-or-homebrew-required')
        command([brew, 'install', 'node@24'], timeout=1800)
        prefix = Path(command([brew, '--prefix', 'node@24']))
        node, npm = prefix / 'bin/node', prefix / 'bin/npm'
        selected = usable(node, npm)
        if not selected:
            raise ValueError('native-node-qualification-failed')
    return selected


def install(*, install_dir, ods_source, compose_files, license_authorized=False, ref=DEFAULT_REF):
    install_dir = preflight(install_dir, license_authorized=license_authorized).resolve(strict=True)
    if not re.fullmatch('[a-f0-9]{40}', ref):
        raise ValueError('exact-pixel-source-ref-required')
    paths = [Path(path).resolve(strict=True) for path in compose_files]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError('resolved-base-compose-stack-required')
    for path in paths:
        if not path.is_file() or install_dir not in path.parents:
            raise ValueError('installed-compose-file-required')
    fragments = [install_dir / fragment for fragment in FRAGMENTS]
    if any(not path.is_file() or path in paths for path in fragments):
        raise ValueError('new-native-compose-fragments-required')
    docker = shutil.which('docker')
    if not docker:
        raise ValueError('docker-required')
    docker = str(Path(docker).resolve(strict=True))
    endpoint = os.environ.get('DOCKER_HOST')
    if not endpoint:
        context = json.loads(command([docker, 'context', 'inspect']))
        endpoint = context[0]['Endpoints']['docker']['Host']
    if not endpoint.startswith('unix:///'):
        raise ValueError('local-docker-socket-required')
    socket = Path(endpoint[len('unix://'):]).resolve(strict=True)
    env = {**os.environ, 'DOCKER_HOST': 'unix://' + str(socket)}
    # DOCKER_CONTEXT takes precedence over DOCKER_HOST. Once the local socket
    # is selected, inherited context/TLS overrides must not redirect commands.
    for key in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    base = [docker, 'compose', '--project-directory', str(install_dir), '--env-file', str(install_dir / '.env')]
    for path in paths:
        base.extend(['-f', str(path)])
    stack = json.loads(command([*base, 'config', '--format', 'json'], env=env))
    project = stack.get('name', '')
    if not re.fullmatch('[a-z0-9][a-z0-9_-]{0,127}', project):
        raise ValueError('resolved-compose-project-required')
    if not {'dashboard-api', 'model-router', 'open-webui'} <= set(stack.get('services', {})):
        raise ValueError('native-base-services-required')
    node, npm = node_tools()
    command([docker, 'pull', '--platform', 'linux/arm64', INGRESS_IMAGE], env=env, timeout=600)
    images = json.loads(command([docker, 'image', 'inspect', INGRESS_IMAGE], env=env))
    if len(images) != 1 or images[0].get('Architecture') != 'arm64' or images[0].get('Os') != 'linux':
        raise ValueError('native-ingress-image-platform-mismatch')
    image = images[0]['Id']
    if not re.fullmatch('sha256:[a-f0-9]{64}', image):
        raise ValueError('native-ingress-image-id-required')
    command([docker, 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges:true', '--user', str(os.getuid()) + ':' + str(os.getgid()),
        '--entrypoint', 'node', image, '-e',
        'if(process.platform!=="linux"||process.arch!=="arm64"||Number(process.versions.node.split(".")[0])<22)process.exit(1)'], env=env)
    root = install_dir / 'data/pixel-native'
    root.mkdir(mode=0o700)
    prepared = root / 'preparation'
    helper('prepare').prepare(ref=ref, node=node, npm=npm, destination=prepared,
        docker=docker, docker_socket=socket, ods_source=ods_source, ingress_image=image,
        compose_project=project, ingress_gid=os.getgid(), license_authorized=True,
        install_dir=install_dir, native_home=root / 'home')
    helper('activate').activate(preparation=prepared, install_dir=install_dir, ods_source=ods_source,
        compose_files=[*paths, *fragments], configure_stack=True)
    return prepared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--ods-source')
    parser.add_argument('--compose-file', action='append', default=[])
    parser.add_argument('--license-authorized', action='store_true')
    parser.add_argument('--ref', default=DEFAULT_REF)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    try:
        if args.preflight_only:
            preflight(args.install_dir, license_authorized=args.license_authorized)
        else:
            if not args.ods_source:
                raise ValueError('ods-source-required')
            install(install_dir=args.install_dir, ods_source=args.ods_source, compose_files=args.compose_file,
                license_authorized=args.license_authorized, ref=args.ref)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        # Error codes contain no captured subprocess output, environment or keys.
        guidance = ERROR_GUIDANCE.get(str(error),
            'Check prerequisites and private preparation/activation receipts; do not reset them.')
        print('Native Pixel installation stopped (' + type(error).__name__ + '). ' + guidance, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
