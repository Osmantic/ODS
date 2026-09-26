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


def _owner_workspace(candidate):
    document = helper('pixel-native-config').private_json(candidate / 'openclaw.json')
    agents = document['agents']['list']
    if len(agents) != 1 or agents[0].get('id') != 'pixel':
        raise ValueError('native-portal-profile-agent-required')
    workspace = Path(agents[0]['workspace'])
    if not workspace.is_absolute() or not workspace.is_dir() or workspace.resolve(strict=True) != workspace:
        raise ValueError('native-portal-profile-workspace-required')
    return workspace


def migrate_public_identity(*, source, preparation, node):
    """Refresh only unmodified legacy profiles after a proved native update.

    This owner-side profile repair is intentionally outside the protected runtime
    activation transaction: failure leaves the activated runtime and owner data
    untouched, and is reported for manual review instead of claiming rollback.
    """
    try:
        candidate = preparation / 'candidate'
        workspace = _owner_workspace(candidate)
        generated = candidate / 'workspace'
        script = source / 'scripts/migrate-portal-identity.mjs'
        if not generated.is_dir() or not script.is_file():
            raise ValueError('native-portal-profile-candidate-required')
        result = subprocess.run([str(node), str(script), str(workspace), str(generated)],
            capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise ValueError('native-portal-profile-migration-failed')
        return {'status': 'checked', 'detail': result.stdout.strip()}
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        return {'status': 'manual-review-required'}


def remove_retired_workspace_text(*, source, preparation, node, state_dir):
    """Remove exact retired template text that an earlier install copied.

    Like the profile repair, this runs after activation and outside its
    transaction. The script changes only byte-exact shipped blocks and keeps a
    private backup of each changed file under the OpenClaw state directory,
    outside the workspace. Anything it could not remove is reported for review.
    """
    try:
        workspace = _owner_workspace(preparation / 'candidate')
        state = Path(state_dir)
        if not state.is_absolute() or not state.is_dir():
            raise ValueError('native-openclaw-state-required')
        script = source / 'scripts/migrate-retired-workspace-text.mjs'
        if not script.is_file():
            raise ValueError('native-retired-text-script-required')
        result = subprocess.run([str(node), str(script), str(workspace), str(state / 'backups/retired-workspace-text')],
            capture_output=True, text=True, timeout=30, check=False)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return _retired_text_review('Retired workspace text: not checked (' + type(error).__name__ + ')')
    detail = result.stdout.strip()
    if result.returncode:
        # A script that stopped before reporting any file prints nothing to stdout.
        return _retired_text_review(detail or 'Retired workspace text: not checked (exit ' + str(result.returncode) + ')')
    return {'status': 'checked', 'detail': detail}


def _retired_text_review(detail):
    print('Warning: review the Pixel workspace; retired template text was not fully removed.\n' + detail,
        file=sys.stderr)
    return {'status': 'manual-review-required', 'detail': detail}


def update(*, install_dir, ods_source, prepare_only=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
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
            destination=work / 'source',
            source_url=str(ods_source / 'vendor/pixel.bundle'))
        runtime = work / 'acquired-runtime'
        config.bootstrap.stage(source=source, ref=initial.DEFAULT_REF, destination=runtime, node=node, npm=npm)
        preparation = work / 'preparation'
        helper('pixel-native-prepare').prepare_migration(source=source, ref=initial.DEFAULT_REF,
            node=node, runtime=runtime, docker=transport['docker'], ods_source=ods_source,
            install_dir=install_dir, destination=preparation)
        prepared = config.private_json(preparation / 'preparation.json')
        command = activation_command(preparation, prepared, install_dir=install_dir,
            ods_source=ods_source, owner=owner, transport=transport)
        if prepare_only:
            return {'status': 'prepared', 'preparation': str(preparation)}
        subprocess.run(command, check=True, timeout=1800)
        outcome = helper('pixel-native-finalize').finalize_update(preparation)
        outcome['portalIdentityMigration'] = migrate_public_identity(
            source=source, preparation=preparation, node=node)
        outcome['retiredWorkspaceText'] = remove_retired_workspace_text(
            source=source, preparation=preparation, node=node, state_dir=environment.get('OPENCLAW_STATE_DIR'))
        return outcome
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
