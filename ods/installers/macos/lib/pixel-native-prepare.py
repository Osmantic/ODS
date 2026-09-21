"""Prepare a new native installation without activating any service."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import subprocess
import sys


def helper(name):
    filename = 'pixel-macos-access-install.py' if name == 'access-install' else 'pixel-native-' + name + '.py'
    spec = importlib.util.spec_from_file_location('native_prepare_' + name,
        Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_record(destination, record):
    temporary = destination / '.preparation.json.tmp'
    with temporary.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write((json.dumps(record, sort_keys=True) + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination / 'preparation.json')
    directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def stage_legacy_storage(*, preparation, docker, project):
    """Record existing Docker volumes without stopping or changing any service."""
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    preparation = Path(preparation).resolve(strict=True)
    environment, compose = helper('env'), helper('compose')
    receipt_path = preparation / 'preparation.json'
    before = environment.snapshot(receipt_path)
    receipt = json.loads(before[0])
    if (receipt.get('kind') != 'legacy-native' or receipt.get('status') != 'prepared'
            or receipt.get('phase') != 'awaiting-joint-activation'
            or os.path.lexists(preparation / 'activation.json') or 'storageDigest' in receipt):
        raise ValueError('unactivated-legacy-preparation-required')
    def run(*args, timeout=45):
        return subprocess.run([str(docker), *args], capture_output=True, text=True, timeout=timeout)
    documents = {}
    for key, name in (('ingress', compose.INGRESS_NAME),
            ('preview', 'ods-pixel-workspace-preview'), ('edge', 'ods-pixel-edge')):
        identity = compose._named_ingress(run, name)
        if identity is None:
            raise ValueError('existing-legacy-storage-container-required')
        documents[key] = compose._inspect_ingress(run, identity)
    # Bind workspace selection to the preserved configuration, not a new default.
    config_snapshot = environment.snapshot(receipt['previousConfig'])
    old = json.loads(config_snapshot[0])
    agents = old.get('agents', {}).get('list', [])
    if len(agents) != 1 or agents[0].get('id') != 'pixel':
        raise ValueError('legacy-native-pixel-agent-required')
    workspace = agents[0].get('workspace') or old['agents'].get('defaults', {}).get('workspace')
    storage = compose.legacy_storage_override(**documents, project=project, workspace=workspace)
    body = json.dumps(storage, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(body).hexdigest()
    if (environment.snapshot(receipt_path) != before
            or environment.snapshot(receipt['previousConfig']) != config_snapshot):
        raise ValueError('native-migration-source-changed-during-preparation')
    path = preparation / 'storage.compose.json'
    with path.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    receipt.update(storageDigest=digest, legacyStorage={
        'project': project, 'containers': {key: value['Id'] for key, value in documents.items()}})
    write_record(preparation, receipt)
    return {'storageDigest': digest, 'volumes': len(storage['volumes']), 'servicesChanged': False}


def stage_legacy_rollback(*, preparation, docker, project):
    """Snapshot the running Compose definitions and immutable recovery images."""
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    preparation = Path(preparation).resolve(strict=True)
    environment, compose = helper('env'), helper('compose')
    before = environment.snapshot(preparation / 'preparation.json')
    receipt = json.loads(before[0])
    if (receipt.get('kind') != 'legacy-native' or receipt.get('status') != 'prepared'
            or receipt.get('phase') != 'awaiting-joint-activation'
            or receipt.get('legacyStorage', {}).get('project') != project
            or os.path.lexists(preparation / 'activation.json') or 'rollbackDigest' in receipt):
        raise ValueError('unactivated-legacy-storage-preparation-required')
    install_dir = Path(receipt['installDir']).resolve(strict=True)
    def run(*args, timeout=45):
        return subprocess.run([str(docker), *args], capture_output=True, text=True, timeout=timeout)
    rollback = {'name': project, 'services': {}, 'volumes': {}, 'networks': {}}
    images = {}
    for key, service in (('edge', 'pixel-edge'), ('preview', 'pixel-workspace-preview')):
        identity = receipt['legacyStorage']['containers'][key]
        if compose._named_ingress(run, 'ods-' + service) != identity:
            raise ValueError('legacy-rollback-container-changed')
        observed = compose._inspect_ingress(run, identity)
        labels = observed['Config'].get('Labels') or {}
        if (labels.get('com.docker.compose.project') != project
                or labels.get('com.docker.compose.service') != service
                or not re.fullmatch('sha256:[a-f0-9]{64}', observed.get('Image', ''))):
            raise ValueError('qualified-legacy-rollback-container-required')
        paths = [Path(value) for value in labels.get('com.docker.compose.project.config_files', '').split(',')]
        if not paths or any(not path.is_absolute() or not path.is_file()
                or install_dir not in path.resolve(strict=True).parents for path in paths):
            raise ValueError('installed-legacy-compose-files-required')
        command = ['compose', '--project-name', project, '--project-directory', str(install_dir),
            '--env-file', str(install_dir / '.env')]
        for path in paths: command.extend(['-f', str(path)])
        expected_hash = service + ' ' + labels.get('com.docker.compose.config-hash', '')
        if compose._docker(run, *command, 'config', '--hash', service).strip() != expected_hash:
            raise ValueError('legacy-compose-definition-changed')
        document = json.loads(compose._docker(run, *command, 'config', '--format', 'json'))
        definition = copy.deepcopy(document['services'][service])
        # Pin the already-running image; never rebuild old code during recovery.
        for field in ('build', 'depends_on', 'pull_policy'): definition.pop(field, None)
        definition['image'] = observed['Image']
        images[service] = observed['Image']
        for mount in definition.get('volumes', []):
            found = [value for value in observed['Mounts'] if value['Destination'] == mount['target']]
            if len(found) != 1 or found[0]['Type'] != mount['type']:
                raise ValueError('legacy-rollback-mount-changed')
            actual = found[0]
            if mount['type'] == 'volume':
                source = mount['source']
                if document['volumes'][source]['name'] != actual['Name']:
                    raise ValueError('legacy-rollback-volume-changed')
                selected = {'external': True, 'name': actual['Name']}
                if source in rollback['volumes'] and rollback['volumes'][source] != selected:
                    raise ValueError('legacy-rollback-volume-conflict')
                rollback['volumes'][source] = selected
            elif mount['type'] == 'bind' and mount['source'] != actual['Source']:
                raise ValueError('legacy-rollback-bind-changed')
        networks = {}
        for network, options in definition.get('networks', {}).items():
            name = document['networks'][network]['name']
            if name not in observed['NetworkSettings']['Networks']:
                raise ValueError('legacy-rollback-network-changed')
            selected = {'external': True, 'name': name}
            # Independently installed legacy services can call different Docker
            # networks "default". Preserve each actual network, not that alias.
            alias = 'legacy-' + hashlib.sha256(name.encode()).hexdigest()[:16]
            if alias in rollback['networks'] and rollback['networks'][alias] != selected:
                raise ValueError('legacy-rollback-network-conflict')
            rollback['networks'][alias] = selected
            networks[alias] = options
        definition['networks'] = networks
        rollback['services'][service] = definition
        # Rendering must not race a changed definition or replacement container.
        if (compose._docker(run, *command, 'config', '--hash', service).strip() != expected_hash
                or compose._named_ingress(run, 'ods-' + service) != identity):
            raise ValueError('legacy-compose-definition-changed')
    if environment.snapshot(preparation / 'preparation.json') != before:
        raise ValueError('native-migration-source-changed-during-preparation')
    # Compose's rendered JSON already escapes literal dollars for replay.
    body = json.dumps(rollback, sort_keys=True, separators=(',', ':')).encode()
    digest = hashlib.sha256(body).hexdigest()
    with (preparation / 'rollback.compose.json').open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    receipt.update(rollbackDigest=digest, rollbackImages=images)
    write_record(preparation, receipt)
    return {'rollbackDigest': digest, 'services': len(images), 'servicesChanged': False}


def configure_legacy_environment(*, preparation, docker, project):
    """Add approved native bindings without restarting any service."""
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    preparation = Path(preparation).resolve(strict=True)
    environment, compose = helper('env'), helper('compose')
    receipt = json.loads(environment.snapshot(preparation / 'preparation.json')[0])
    if (receipt.get('kind') != 'legacy-native' or receipt.get('status') != 'prepared'
            or receipt.get('phase') != 'awaiting-joint-activation'
            or receipt.get('legacyStorage', {}).get('project') != project
            or os.path.lexists(preparation / 'activation.json') or 'environmentStatus' in receipt):
        raise ValueError('unactivated-legacy-preparation-required')
    for filename, field in (('storage.compose.json', 'storageDigest'), ('rollback.compose.json', 'rollbackDigest')):
        document = json.loads(environment.snapshot(preparation / filename)[0])
        digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if digest != receipt.get(field): raise ValueError('legacy-preparation-artifact-changed')
    docker = Path(docker).resolve(strict=True)
    def run(*args, timeout=45):
        return subprocess.run([str(docker), *args], capture_output=True, text=True, timeout=timeout)
    identity = receipt['legacyStorage']['containers']['ingress']
    if compose._named_ingress(run, compose.INGRESS_NAME) != identity:
        raise ValueError('legacy-ingress-changed')
    ingress = compose._inspect_ingress(run, identity)
    user, image = ingress['Config']['User'], ingress['Image']
    if (not re.fullmatch(str(os.getuid()) + r':[0-9]+', user)
            or not re.fullmatch('sha256:[a-f0-9]{64}', image)):
        raise ValueError('qualified-legacy-ingress-owner-required')
    config_mounts = [value for value in ingress['Mounts'] if value['Destination'] == '/run/gateway.json']
    if len(config_mounts) != 1 or config_mounts[0]['Type'] != 'bind' or config_mounts[0]['RW'] is not False:
        raise ValueError('qualified-legacy-gateway-config-required')
    install_dir = Path(receipt['installDir']).resolve(strict=True)
    config_path = Path(config_mounts[0]['Source']).resolve(strict=True)
    if install_dir not in config_path.parents:
        raise ValueError('installed-legacy-gateway-config-required')
    mounted_snapshot = environment.snapshot(config_path)
    previous = json.loads(environment.snapshot(receipt['previousConfig'])[0])
    mounted = json.loads(mounted_snapshot[0])
    if mounted['gateway']['auth'] != previous['gateway']['auth']:
        raise ValueError('legacy-gateway-credential-mismatch')
    agents = previous.get('agents', {}).get('list', [])
    if len(agents) != 1 or agents[0].get('id') != 'pixel':
        raise ValueError('legacy-native-pixel-agent-required')
    workspace = agents[0].get('workspace') or previous['agents'].get('defaults', {}).get('workspace')
    bindings = {'PIXEL_NATIVE_UID': str(os.getuid()), 'PIXEL_INGRESS_GID': user.split(':')[1],
        'PIXEL_NATIVE_INGRESS_IMAGE': image, 'PIXEL_NATIVE_CONFIG_PATH': str(config_path),
        'PIXEL_NATIVE_WORKSPACE': workspace, 'PIXEL_NATIVE_GATEWAY_PORT': str(receipt['gatewayPort']),
        'PIXEL_NATIVE_ACCESS_PORT': str(receipt['accessPort']),
        'PIXEL_INGRESS_RUNTIME_DIR': str(install_dir / 'data/pixel-native/unused-docker-ingress'),
        'PIXEL_PREVIEW_RUNTIME_DIR': str(install_dir / 'data/pixel-native/unused-docker-preview')}
    receipt['nativeTransport'] = {'docker': str(docker), 'project': project, 'image': image, 'user': user}
    receipt['environmentBindings'] = bindings
    def before_write(body):
        if environment.snapshot(config_path) != mounted_snapshot:
            raise ValueError('legacy-gateway-config-changed')
        with (preparation / 'environment.after.env').open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        receipt.update(environmentStatus='applying', environmentAfterSha256=hashlib.sha256(body).hexdigest())
        write_record(preparation, receipt)
    written = environment.persist_migration(install_dir / '.env', bindings,
        previous_config=receipt['previousConfig'], backup=preparation / 'environment.before.env',
        before_write=before_write)
    receipt['environmentStatus'] = 'configured'
    write_record(preparation, receipt)
    return {'environment': 'configured', 'changed': written is not None, 'servicesChanged': False}


def prepare_migration(*, source, ref, node, runtime, docker, ods_source, install_dir,
                      destination, license_authorized=False, gateway_port=18789, access_port=18790):
    """Stage a legacy migration without changing credentials, state or services."""
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    if license_authorized is not True:
        raise ValueError('pixel-license-authorization-required')
    config, environment = helper('config'), helper('env')
    installer = helper('access-install')
    owner = pwd.getpwuid(os.getuid())
    document, env, _, _, active_node, entrypoint = installer._source_gateway(
        installer._launchd.GATEWAY_PLIST, owner.pw_name, gateway_port)
    current = active_node.parent.name
    if (not re.fullmatch('[a-f0-9]{64}', current)
            or active_node != installer._bundle.INSTALL_ROOT / current / 'node'
            or entrypoint != active_node.parent / 'runtime/openclaw.mjs'
            or document.get('UserName') != owner.pw_name):
        raise ValueError('active-protected-native-gateway-required')
    installer._bundle.verify(active_node.parent, expected_digest=current)
    previous = Path(env['OPENCLAW_CONFIG_PATH'])
    parent = installer.RUNTIME_CONFIG_ROOT / str(owner.pw_uid)
    if not installer._source_runtime_config(previous, parent, current):
        raise ValueError('active-protected-native-config-required')
    previous_snapshot = environment.snapshot(previous)
    source_definition = installer._launchd.GATEWAY_PLIST.read_bytes()
    if plistlib.loads(source_definition) != document:
        raise ValueError('native-migration-source-changed-during-preparation')
    install_dir = Path(install_dir).resolve(strict=True)
    env_snapshot = environment.snapshot(install_dir / '.env')
    old = json.loads(previous_snapshot[0])
    agents = old.get('agents', {}).get('list', [])
    if len(agents) != 1 or agents[0].get('id') != 'pixel':
        raise ValueError('legacy-native-pixel-agent-required')
    workspace = agents[0].get('workspace') or old['agents'].get('defaults', {}).get('workspace')
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise ValueError('existing-native-workspace-required')
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise ValueError('new-native-migration-preparation-required')
    destination = destination.parent.resolve(strict=True) / destination.name
    destination.mkdir(mode=0o700)
    record = {'schemaVersion': 1, 'kind': 'legacy-native', 'status': 'preparing',
        'phase': 'onboarding', 'pixelSourceRef': ref, 'currentDigest': current,
        'previousConfig': str(previous), 'previousState': env['OPENCLAW_STATE_DIR'],
        'installDir': str(install_dir), 'requiresActivation': True}
    def checkpoint(phase):
        record['phase'] = phase
        write_record(destination, record)
    try:
        checkpoint('onboarding')
        answers = helper('onboarding').write(source=source, ref=ref, ods_source=ods_source,
            install_dir=install_dir, home=Path(env['HOME']), runtime=runtime, node=node,
            destination=destination / 'onboarding.json', workspace=workspace, previous_config=previous)
        checkpoint('sandbox-qualification')
        sandbox = config.bootstrap.prepare_sandbox(source=source, ref=ref, docker=docker)
        checkpoint('configuration')
        candidate = config.prepare(source=source, ref=ref, answers=answers, node=node, runtime=runtime,
            sandbox_image=sandbox['imageId'], destination=destination / 'candidate',
            previous_config=previous, previous_state_dir=env['OPENCLAW_STATE_DIR'])
        checkpoint('services')
        record['serviceDigest'] = config.stage_services(source=source, ref=ref, ods_source=ods_source,
            candidate=candidate, destination=destination / 'services')
        checkpoint('runtime')
        record['runtimeDigest'] = config.stage_bundle(source=source, ref=ref, candidate=candidate,
            node=node, runtime=runtime, destination=destination / 'runtime')
        checkpoint('joint-plan')
        plan = installer.make_migration_plan(install_dir=install_dir, owner_name=owner.pw_name,
            openclaw_bin=installer.GATEWAY_LAUNCHER, gateway_port=gateway_port, access_port=access_port,
            candidate=candidate, runtime_bundle=destination / 'runtime', bundle_digest=record['runtimeDigest'],
            current_digest=current, services_bundle=destination / 'services',
            services_digest=record['serviceDigest'], source_ref=ref)
        if (environment.snapshot(previous) != previous_snapshot
                or environment.snapshot(install_dir / '.env') != env_snapshot
                or plan['source_bytes'] != source_definition):
            raise ValueError('native-migration-source-changed-during-preparation')
        record.update(status='prepared', gatewayPort=gateway_port, accessPort=access_port)
        checkpoint('awaiting-joint-activation')
    except BaseException:
        record['status'] = 'error'
        write_record(destination, record)
        raise
    return destination / 'preparation.json'


def prepare(*, source=None, ref, answers=None, node, runtime=None, sandbox_image=None, destination,
            docker, docker_socket, ods_source, ingress_image, compose_project,
            ingress_gid, research_port=3004, npm=None, license_authorized=False,
            install_dir=None, native_home=None):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    if (source is None or runtime is None or sandbox_image is None) and license_authorized is not True:
        raise ValueError('pixel-license-authorization-required')
    if runtime is None and not npm:
        raise ValueError('native-runtime-acquisition-requires-npm')
    config = helper('config')
    if answers is None:
        if not install_dir or not native_home:
            raise ValueError('installed-ods-and-native-home-required')
        configured_home = Path(native_home) / '.openclaw'
    else:
        if install_dir is not None or native_home is not None:
            raise ValueError('ambiguous-native-onboarding-input')
        contract = config.private_answers(answers)
        configured_home = Path(contract['openclawHome'])
    if not configured_home.is_absolute() or configured_home.name != '.openclaw':
        raise ValueError('native-ods-home-required')
    home = configured_home.parent
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination) or os.path.lexists(home):
        raise ValueError('new-native-preparation-and-home-required')
    home = home.parent.resolve(strict=True) / home.name
    destination = destination.parent.resolve(strict=True) / destination.name
    if destination == home or destination in home.parents or home in destination.parents:
        raise ValueError('native-preparation-home-overlap')
    destination.mkdir(mode=0o700)
    record = {'schemaVersion': 1, 'status': 'preparing', 'phase': 'configuration',
        'pixelSourceRef': ref, 'home': str(home), 'requiresActivation': True}
    credential_environment = None
    def checkpoint():
        write_record(destination, record)
    try:
        checkpoint()
        if source is None:
            record['phase'] = 'source-acquisition'
            checkpoint()
            source = config.bootstrap.acquire_source(ref=ref, destination=destination / 'source',
                license_authorized=license_authorized)
        if answers is None:
            record['phase'] = 'credentials'
            checkpoint()
            environment = helper('env')
            credential_environment = environment.ensure_credentials(Path(install_dir) / '.env',
                backup=destination / 'environment-before-credentials.env', return_bytes=True)
            record['phase'] = 'onboarding'
            checkpoint()
            answers = helper('onboarding').write(source=source, ref=ref, ods_source=ods_source,
                install_dir=install_dir, home=home, runtime=runtime or destination / 'acquired-runtime',
                node=node, destination=destination / 'onboarding.json')
        if runtime is None:
            record['phase'] = 'runtime-acquisition'
            checkpoint()
            runtime = destination / 'acquired-runtime'
            config.bootstrap.stage(source=source, ref=ref, destination=runtime, node=node, npm=npm)
        if sandbox_image is None:
            record['phase'] = 'sandbox-qualification'
            checkpoint()
            sandbox = config.bootstrap.prepare_sandbox(source=source, ref=ref, docker=docker)
            sandbox_image = sandbox['imageId']
            record['sandbox'] = sandbox
        record['phase'] = 'configuration'
        checkpoint()
        candidate = config.prepare(source=source, ref=ref, answers=answers, node=node,
            runtime=runtime, sandbox_image=sandbox_image, destination=destination / 'candidate',
            research_port=research_port)
        record['phase'] = 'services'
        checkpoint()
        record['serviceDigest'] = config.stage_services(source=source, ref=ref, ods_source=ods_source,
            candidate=candidate, destination=destination / 'services')
        record['phase'] = 'runtime'
        checkpoint()
        record['runtimeDigest'] = config.stage_bundle(source=source, ref=ref, candidate=candidate,
            node=node, runtime=runtime, destination=destination / 'runtime')
        record['phase'] = 'layout'
        checkpoint()
        template = helper('layout').prepare(candidate=candidate, home=home, node=node, runtime=runtime,
            docker=docker, docker_socket=docker_socket, ods_source=ods_source,
            ingress_image=ingress_image, compose_project=compose_project, ingress_gid=ingress_gid)
        record.update(status='prepared', phase='awaiting-protected-activation', template=str(template))
        checkpoint()
    except Exception:
        record['status'] = 'error'
        if credential_environment is not None:
            try:
                environment.restore(Path(install_dir) / '.env',
                    backup=destination / 'environment-before-credentials.env', expected=credential_environment)
                record['credentialRecovery'] = 'restored'
            except Exception:
                record['credentialRecovery'] = 'review-required'
        checkpoint()
        raise
    return destination / 'preparation.json'


def migration_main(argv):
    parser = argparse.ArgumentParser(description='Prepare a legacy native migration without activating it.')
    for name in ('source', 'ref', 'node', 'runtime', 'docker', 'ods-source', 'install-dir', 'destination'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--license-authorized', action='store_true')
    parser.add_argument('--gateway-port', type=int, default=18789)
    parser.add_argument('--access-port', type=int, default=18790)
    args = parser.parse_args(argv)
    try:
        receipt = prepare_migration(**vars(args))
    except (ValueError, OSError, KeyError, TypeError):
        print('Native migration preparation failed; retain its phase journal. Active services were not replaced.', file=sys.stderr)
        return 1
    print(json.dumps({'preparation': str(receipt), 'status': 'awaiting-joint-activation'}))
    return 0


def main():
    if sys.argv[1:2] in (['storage'], ['rollback'], ['environment']):
        parser = argparse.ArgumentParser(description='Prepare legacy Docker storage, recovery or environment without service activation.')
        for name in ('preparation', 'docker', 'project'):
            parser.add_argument('--' + name, required=True)
        try:
            operation = {'storage': stage_legacy_storage, 'rollback': stage_legacy_rollback,
                'environment': configure_legacy_environment}[sys.argv[1]]
            print(json.dumps(operation(**vars(parser.parse_args(sys.argv[2:])))))
        except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
            print('Legacy Docker preparation failed; keep the private receipts, backups and volumes intact.', file=sys.stderr)
            return 1
        return 0
    if sys.argv[1:2] == ['migrate']:
        return migration_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('ref', 'node', 'destination',
                 'docker', 'docker-socket', 'ods-source', 'ingress-image', 'compose-project'):
        parser.add_argument('--' + name, required=True)
    onboarding = parser.add_mutually_exclusive_group(required=True)
    onboarding.add_argument('--answers', help='Existing private onboarding contract')
    onboarding.add_argument('--install-dir', help='Generate onboarding from this installed ODS environment')
    parser.add_argument('--native-home', help='New native home for generated onboarding; parent must exist')
    parser.add_argument('--source', help='Existing exact checkout; otherwise acquire the official Pixel source')
    parser.add_argument('--runtime')
    parser.add_argument('--sandbox-image')
    parser.add_argument('--npm')
    parser.add_argument('--license-authorized', action='store_true')
    parser.add_argument('--ingress-gid', required=True, type=int)
    parser.add_argument('--research-port', default=3004, type=int)
    args = parser.parse_args()
    try:
        receipt = prepare(**vars(args))
    except (ValueError, OSError, KeyError, RuntimeError):
        print('Native preparation failed; inspect its private phase receipt. No services were activated.', file=sys.stderr)
        return 1
    print(json.dumps({'preparation': str(receipt), 'requiresActivation': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
