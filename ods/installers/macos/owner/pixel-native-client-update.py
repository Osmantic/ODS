"""Prepare and publish the owner-side clients of an existing native update.

The protected updater owns Core activation. This journal owns only public
installation files, the existing host-agent job and the two Portal web images.
Preparation never changes a running service or its image tag. Publication uses
the existing Edge admission protocol after Core activation, and passes that
same binding to ingress finalization. Private owner data is never a copy input.
"""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import secrets
import shlex
import stat
import subprocess
import time

HERE = Path(__file__).resolve().parent
SERVICES = ('dashboard-api', 'dashboard')
LABEL = 'com.ods.host-agent'


def helper(name):
    directory = HERE if name == 'pixel-native-client-files' else HERE.parent / 'lib'
    spec = importlib.util.spec_from_file_location('native_clients_' + name, directory / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def private_read(path):
    path = Path(path)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600 or path.resolve(strict=True) != path
            or info.st_size > 8 * 1024 * 1024):
        raise ValueError('native-client-private-record-required')
    return json.loads(path.read_bytes())


def write(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(8))
    with temporary.open('xb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write((json.dumps(value, sort_keys=True) + '\n').encode())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def runner(docker, install_dir, endpoint):
    if not endpoint.startswith('unix:///') or not Path(endpoint[7:]).is_socket():
        raise ValueError('native-client-local-docker-required')
    environment = {key: value for key, value in os.environ.items()
        if key not in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
    environment['DOCKER_HOST'] = endpoint
    def run(*args, timeout=45, input=None):
        return subprocess.run([docker, *args], cwd=install_dir, capture_output=True,
            text=True, timeout=timeout, input=input, env=environment)
    run.environment = environment
    return run


def checked(run, *args, timeout=45, input=None):
    result = run(*args, timeout=timeout, input=input)
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise ValueError('native-client-command-failed')
    return result.stdout


def image_id(run, name):
    value = checked(run, 'image', 'inspect', '--format', '{{.Id}}', name).strip()
    if not re.fullmatch('sha256:[a-f0-9]{64}', value):
        raise ValueError('native-client-image-identity-required')
    return value


def container(run, project, service):
    values = checked(run, 'ps', '-a', '--no-trunc', '--filter',
        'label=com.docker.compose.project=' + project, '--filter',
        'label=com.docker.compose.service=' + service, '--format', '{{.ID}}').splitlines()
    if len(values) != 1 or not re.fullmatch('[a-f0-9]{64}', values[0]):
        raise ValueError('native-client-owned-container-required')
    observed = json.loads(checked(run, 'inspect', values[0]))
    if len(observed) != 1 or observed[0].get('Id') != values[0]:
        raise ValueError('native-client-container-changed')
    return observed[0]


def container_identity(observed):
    labels = observed.get('Config', {}).get('Labels') or {}
    return {'id': observed['Id'], 'image': observed['Image'],
        'tag': observed['Config']['Image'], 'configHash': labels.get('com.docker.compose.config-hash'),
        'mounts': sorted(observed['Mounts'], key=lambda item: item['Destination'])}


def host_agent(install_dir):
    path = Path.home() / 'Library/LaunchAgents' / (LABEL + '.plist')
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or info.st_mode & 0o022 or path.resolve(strict=True) != path):
        raise ValueError('native-client-owned-host-agent-required')
    body = path.read_bytes()
    value = plistlib.loads(body)
    arguments = value.get('ProgramArguments')
    if (value.get('Label') != LABEL or value.get('WorkingDirectory') != str(install_dir)
            or type(arguments) is not list or len(arguments) != 4
            or arguments[1:] != [str(install_dir / 'bin/ods-host-agent.py'), '--install-dir', str(install_dir)]
            or Path(arguments[0]).resolve(strict=True) != (install_dir / '.venv/host-agent/bin/python').resolve(strict=True)):
        raise ValueError('native-client-host-agent-binding-required')
    return {'path': str(path), 'sha256': hashlib.sha256(body).hexdigest(),
        'device': info.st_dev, 'inode': info.st_ino, 'python': arguments[0]}


def host_command(record, action):
    host_loaded(record, required=action == 'stop' and record['phase'] == 'prepared')
    target = 'gui/' + str(os.getuid()) + '/' + LABEL
    command = (['/bin/launchctl', 'bootout', target] if action == 'stop' else
        ['/bin/launchctl', 'bootstrap', 'gui/' + str(os.getuid()), record['hostAgent']['path']])
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if result.returncode:
        # An acknowledged prior stop is replayable; an unrelated live job is
        # never adopted. The exact plist is checked before every call.
        probe = subprocess.run(['/bin/launchctl', 'print', target], capture_output=True, text=True, timeout=10)
        absent = probe.returncode == 113 and 'Could not find service' in probe.stderr
        if action != 'stop' or not absent:
            raise ValueError('native-client-host-agent-' + action + '-failed')
    if action == 'start':
        subprocess.run(['/bin/launchctl', 'kickstart', '-p', target],
            capture_output=True, text=True, check=True, timeout=15)


def host_loaded(record, required=True):
    target = 'gui/' + str(os.getuid()) + '/' + LABEL
    result = subprocess.run(['/bin/launchctl', 'print', target], capture_output=True, text=True, timeout=10)
    if result.returncode:
        if not required and result.returncode == 113 and 'Could not find service' in result.stderr:
            return False
        raise ValueError('native-client-host-agent-observation-failed')
    def field(name):
        values = re.findall(r'^\s*' + name + r' = (.+)$', result.stdout, re.M)
        return values[0] if len(values) == 1 else None
    if field('path') != record['hostAgent']['path'] or field('program') != record['hostAgent']['python']:
        raise ValueError('native-client-loaded-host-agent-changed')
    blocks = re.findall(r'^\s*arguments = \{\n(.*?)^\s*\}', result.stdout, re.M | re.S)
    expected = [record['hostAgent']['python'], str(Path(record['installDir']) / 'bin/ods-host-agent.py'),
        '--install-dir', record['installDir']]
    if len(blocks) != 1 or [line.strip() for line in blocks[0].splitlines() if line.strip()] != expected:
        raise ValueError('native-client-loaded-host-agent-changed')
    return True


def compose_document(install_dir, run, project, *, staged=None):
    finalize = helper('pixel-native-finalize')
    tokens = finalize.compose_flags(install_dir, os.environ)
    relatives = helper('pixel-native-stack').resolve_files(install_dir, tokens[1::2])
    original = [install_dir / value for value in tokens[1::2]]
    resolved = [install_dir / value for value in relatives]
    helper('pixel-native-compose').validate_stack(install_dir, original)
    helper('pixel-native-compose').validate_stack(install_dir, resolved)
    selected = []
    for path in resolved:
        relative = path.relative_to(install_dir)
        candidate = staged / relative if staged is not None else path
        selected.append(candidate if candidate.is_file() else path)
    # The shipped migration is the only new storage selection. It retains
    # legacy SQLite files and migrates them only after their writers stop.
    private = Path('installers/macos/dashboard-private-state.compose.yaml')
    if staged is not None and (staged / private).is_file() and install_dir / private not in resolved:
        selected.insert(1, staged / private)
    command = ['compose', '--project-directory', str(install_dir), '--project-name', project,
        '--env-file', str(install_dir / '.env')]
    for path in selected: command.extend(['-f', str(path)])
    document = json.loads(checked(run, *command, '--profile', 'native-config-maintenance', 'config', '--format', 'json'))
    if document.get('name') != project:
        raise ValueError('native-client-compose-project-changed')
    return document


def compatible(previous, candidate, install_dir):
    """Only the shipped clients change; retain owner mounts, routes and secrets."""
    for service in SERVICES:
        old = previous['services'].get(service)
        new = candidate['services'].get(service)
        if type(old) is not dict or type(new) is not dict:
            raise ValueError('native-client-service-required')
        for definition in (old, new):
            build = definition.get('build', {})
            if (set(build) - {'context', 'dockerfile'} or build.get('dockerfile', 'Dockerfile') != 'Dockerfile'
                    or Path(build.get('context', '').replace('$$', '$')) != install_dir / 'extensions/services' / service):
                raise ValueError('native-client-custom-build-requires-review')
        for field in ('user', 'ports', 'networks', 'extra_hosts', 'entrypoint', 'command'):
            if old.get(field) != new.get(field):
                raise ValueError('native-client-transport-change-requires-review')
        before = {value['target']: value for value in old.get('volumes', [])}
        after = {value['target']: value for value in new.get('volumes', [])}
        if any(after.get(key) != value for key, value in before.items()):
            raise ValueError('native-client-existing-mount-changed')
        additions = set(after) - set(before)
        permitted = {'/data/pixel-images', '/data/pixel-chat-results'} if service == 'dashboard-api' else set()
        if additions - permitted:
            raise ValueError('native-client-new-mount-requires-review')
        old_env, new_env = old.get('environment', {}), new.get('environment', {})
        if any(new_env.get(key) != value for key, value in old_env.items()):
            raise ValueError('native-client-existing-environment-changed')
    if set(candidate.get('volumes', {})) & {'dashboard-pixel-images', 'dashboard-pixel-chat-results'}:
        helper('dashboard-private-state').selection(candidate)


def replay_document(document, observations, tags, *, verify_observed=False):
    """Keep the installed image selection and actual networks/volumes.

    Image IDs are proved separately before and after the no-build/no-pull
    operation. Replacing a build's implicit tag with a digest in Compose would
    change its service hash and break the next ordinary update's baseline.
    """
    result = {'name': document['name'], 'services': {}, 'networks': {}, 'volumes': {}}
    for service in SERVICES:
        definition = copy.deepcopy(document['services'][service])
        observed = observations[service]
        tag = definition.get('image') or document['name'] + '-' + service
        if tags[service] != tag or observed['Config']['Image'] != tag:
            raise ValueError('native-client-image-selection-changed')
        if verify_observed:
            mounts = definition.get('volumes', [])
            if len(mounts) != len(observed['Mounts']):
                raise ValueError('native-client-baseline-mount-changed')
            for mount in mounts:
                found = [value for value in observed['Mounts'] if value['Destination'] == mount['target']]
                if (len(found) != 1 or found[0]['Type'] != mount['type']
                        or found[0]['RW'] is not (not mount.get('read_only', False))):
                    raise ValueError('native-client-baseline-mount-changed')
                actual = found[0]
                if mount['type'] == 'bind':
                    if actual['Source'] != mount['source'].replace('$$', '$'):
                        raise ValueError('native-client-baseline-mount-changed')
                elif mount['type'] == 'volume':
                    expected = document['volumes'][mount['source']]['name']
                    if actual['Name'] != expected:
                        raise ValueError('native-client-baseline-volume-changed')
                else:
                    raise ValueError('native-client-baseline-mount-type-required')
            networks = {document['networks'][alias]['name'] for alias in definition.get('networks', {})}
            if networks != set(observed['NetworkSettings']['Networks']):
                raise ValueError('native-client-baseline-networks-changed')
        for field in ('depends_on', 'pull_policy'): definition.pop(field, None)
        for alias in definition.get('networks', {}):
            selected = document['networks'][alias]
            result['networks'][alias] = {'external': True, 'name': selected.get('name', document['name'] + '_' + alias)}
        for mount in definition.get('volumes', []):
            if mount['type'] == 'volume':
                alias = mount['source']
                selected = document['volumes'][alias]
                result['volumes'][alias] = {'external': True, 'name': selected.get('name', document['name'] + '_' + alias)}
        for category in ('configs', 'secrets'):
            if definition.get(category):
                raise ValueError('native-client-external-config-requires-review')
        result['services'][service] = definition
    return result


def prepare(*, install_dir, ods_source, destination, transport):
    install_dir, ods_source, destination = map(Path, (install_dir, ods_source, destination))
    destination.mkdir(mode=0o700)
    files = helper('pixel-native-client-files')
    files.stage(ods_source, install_dir, destination / 'files')
    staged = destination / 'files/candidate'
    endpoint = os.environ.get('DOCKER_HOST', '')
    run = runner(transport['docker'], install_dir, endpoint)
    previous = compose_document(install_dir, run, transport['project'])
    candidate = compose_document(install_dir, run, transport['project'], staged=staged)
    compatible(previous, candidate, install_dir)
    observations = {name: container(run, transport['project'], name) for name in SERVICES}
    before = {name: container_identity(value) for name, value in observations.items()}
    for name, value in before.items():
        if image_id(run, value['tag']) != value['image']:
            raise ValueError('native-client-selected-image-tag-changed')
        if observations[name]['State'].get('Running') is not True:
            raise ValueError('native-client-running-baseline-required')
    record = {'schemaVersion': 1, 'installDir': str(install_dir), 'transport': transport, 'dockerEndpoint': endpoint,
        'sourceDigest': files.identity(destination / 'files'), 'previous': before,
        'hostAgent': host_agent(install_dir), 'images': {}, 'phase': 'building'}
    host_loaded(record)
    write(destination / 'previous-resolved.json', previous)
    for name in SERVICES:
        hashed = checked(run, 'compose', '--project-directory', str(install_dir),
            '--project-name', transport['project'], '--env-file', '/dev/null',
            '-f', str(destination / 'previous-resolved.json'), 'config', '--hash', name).strip()
        if hashed != name + ' ' + str(before[name]['configHash']):
            raise ValueError('native-client-baseline-definition-changed')
    rollback = replay_document(previous, observations,
        {name: value['tag'] for name, value in before.items()}, verify_observed=True)
    record['previousRecipeSha256'] = digest(rollback)
    write(destination / 'journal.json', record)
    write(destination / 'previous.compose.json', rollback)
    for name in SERVICES:
        hashed = checked(run, 'compose', '--project-directory', str(install_dir),
            '--project-name', transport['project'], '--env-file', '/dev/null',
            '-f', str(destination / 'previous.compose.json'), 'config', '--hash', name).strip()
        if hashed != name + ' ' + str(before[name]['configHash']):
            raise ValueError('native-client-rollback-definition-changed')
    for name in SERVICES:
        files.verify_staged(destination / 'files')
        tag = 'ods-native-update:' + record['sourceDigest'] + '-' + name
        with (destination / (name + '-build.log')).open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            result = subprocess.run([transport['docker'], 'build', '--label',
                'org.osmantic.ods.client-source=' + record['sourceDigest'], '-t', tag,
                str(staged / 'extensions/services' / name)], cwd=install_dir,
                stdout=stream, stderr=subprocess.STDOUT, timeout=1800, check=False, env=run.environment)
        if result.returncode:
            raise ValueError('native-client-image-build-failed')
        record['images'][name] = image_id(run, tag)
        files.verify_staged(destination / 'files')
        write(destination / 'journal.json', record)
    record['candidateClients'] = {name: candidate['services'][name] for name in SERVICES}
    record['previousClients'] = {name: previous['services'][name] for name in SERVICES}
    record['phase'] = 'prepared'
    write(destination / 'journal.json', record)
    return {'sourceDigest': record['sourceDigest'], 'images': record['images']}


def load(destination):
    destination = Path(destination).resolve(strict=True)
    info = destination.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('native-client-private-directory-required')
    record = private_read(destination / 'journal.json')
    if (record.get('schemaVersion') != 1 or record.get('sourceDigest') !=
            helper('pixel-native-client-files').identity(destination / 'files')):
        raise ValueError('native-client-source-binding-changed')
    install_dir = Path(record['installDir']).resolve(strict=True)
    if destination.parent.parent != install_dir / 'data/pixel-native':
        raise ValueError('native-client-installed-preparation-required')
    if host_agent(install_dir) != record['hostAgent']:
        raise ValueError('native-client-host-agent-changed')
    installer = helper('pixel-macos-access-install')
    _, environment, _, _, _, _ = installer._source_gateway(installer._launchd.GATEWAY_PLIST,
        pwd.getpwuid(os.getuid()).pw_name, 18789)
    if environment.get('DOCKER_HOST') != record['dockerEndpoint']:
        raise ValueError('native-client-docker-selection-changed')
    return record, install_dir, runner(record['transport']['docker'], install_dir, record['dockerEndpoint'])


def require_held(record, install_dir, run):
    if record['phase'] not in ('publishing', 'published', 'applying', 'failed', 'restored', 'verified'):
        raise ValueError('native-client-held-operation-required')
    compose = helper('pixel-native-compose')
    edge = compose._managed_edge(run, record['transport']['project'])
    key = helper('pixel-macos-access-install')._env_file(install_dir / '.env')['DASHBOARD_API_KEY']
    compose._edge_transition(run, edge, key, binding=record['binding'])
    return edge


def begin(destination):
    """Acquire owner admission after Core activation, then publish public code.

    A crash keeps the exact binding and file journal. An explicit resume uses
    the same preparation; it neither starts another Core update nor acquires
    a different admission lease.
    """
    destination = Path(destination)
    record, install_dir, run = load(destination)
    files, compose = helper('pixel-native-client-files'), helper('pixel-native-compose')
    if record['phase'] == 'ready':
        verify(destination)
        return None
    if record['phase'] not in ('prepared', 'acquiring', 'publishing', 'published', 'applying', 'failed', 'restored', 'verified'):
        raise ValueError('native-client-preparation-incomplete')
    installer = helper('pixel-macos-access-install')
    owner = pwd.getpwuid(os.getuid()).pw_name
    _, environment, _, _, _, _ = installer._source_gateway(installer._launchd.GATEWAY_PLIST, owner, 18789)
    source = Path(environment['OPENCLAW_CONFIG_PATH'])
    helper('pixel-native-ingress-refresh').native_idle(source)
    key = installer._env_file(install_dir / '.env')['DASHBOARD_API_KEY']
    edge = compose._managed_edge(run, record['transport']['project'])
    pointer = install_dir / 'data/pixel-native/preparation/ingress-refresh-current.json'
    if 'binding' in record and pointer.is_file():
        name = private_read(pointer).get('directory', '')
        if not re.fullmatch('ingress-refresh-[a-f0-9]{32}', name):
            raise ValueError('native-client-ingress-pointer-invalid')
        previous = private_read(pointer.parent / name / 'refresh.json')
        if previous.get('binding') == record['binding'] and previous.get('phase') in ('ready', 'releasing'):
            verify(destination)
            if previous['phase'] == 'releasing':
                # The release may already have reached Edge. Reacquiring the
                # old revision or publishing files again would be unsafe;
                # ingress verifies the completed operation before retrying
                # only its exact, idempotent release.
                return record['binding']
            record['phase'] = 'ready'
            write(destination / 'journal.json', record)
            return None
    if 'binding' not in record:
        for name, value in record['previous'].items():
            if container_identity(container(run, record['transport']['project'], name)) != value:
                raise ValueError('native-client-baseline-changed')
        transition = compose._edge_transition(run, edge, key)
        record.update(phase='acquiring', binding={'token': secrets.token_hex(32), 'revision': transition['revision']})
        write(destination / 'journal.json', record)
    compose._edge_transition(run, edge, key, binding=record['binding'])
    record['phase'] = 'publishing'
    write(destination / 'journal.json', record)
    host_command(record, 'stop')
    try:
        files.publish(destination / 'files')
        record['phase'] = 'published'
        write(destination / 'journal.json', record)
    except BaseException:
        # Never release admission while Root's new runtime and owner clients
        # have not completed their joint finalization.
        files.restore(destination / 'files')
        host_command(record, 'start')
        record['phase'] = 'restored'
        write(destination / 'journal.json', record)
        raise
    return record['binding']


def selected_key(destination):
    record, _, _ = load(destination)
    return digest({key: record[key] for key in ('sourceDigest', 'images', 'previous', 'hostAgent')})


def validate_pre_activation(destination):
    record, install_dir, run = load(destination)
    if record['phase'] != 'prepared':
        raise ValueError('native-client-prepared-update-required')
    files = helper('pixel-native-client-files')
    files.verify_staged(Path(destination) / 'files')
    files.verify(Path(destination) / 'files', selected='previous')
    host_loaded(record)
    document = compose_document(install_dir, run, record['transport']['project'])
    if {name: document['services'][name] for name in SERVICES} != record['previousClients']:
        raise ValueError('native-client-baseline-definition-changed')
    for name, previous in record['previous'].items():
        if container_identity(container(run, record['transport']['project'], name)) != previous:
            raise ValueError('native-client-baseline-changed')
        if image_id(run, previous['tag']) != previous['image']:
            raise ValueError('native-client-selected-image-tag-changed')


def _up(run, install_dir, project, recipe):
    checked(run, 'compose', '--project-directory', str(install_dir), '--project-name', project,
        '--env-file', '/dev/null', '-f', str(recipe), 'up', '-d', '--no-build', '--pull', 'never',
        '--no-deps', '--force-recreate', '--wait', '--wait-timeout', '120', *SERVICES, timeout=180)


def verify(destination):
    record, _, run = load(destination)
    helper('pixel-native-client-files').verify(Path(destination) / 'files', selected='candidate')
    recipe = private_read(Path(destination) / 'candidate.compose.json')
    if digest(recipe) != record['candidateRecipeSha256']:
        raise ValueError('native-client-candidate-recipe-changed')
    observations = {}
    for name in SERVICES:
        current = container(run, record['transport']['project'], name)
        observations[name] = current
        if (current['Image'] != record['images'][name] or current['State'].get('Running') is not True
                or current['State'].get('Health', {}).get('Status') != 'healthy'
                or image_id(run, record['previous'][name]['tag']) != record['images'][name]
                or current['Config']['Labels'].get('com.docker.compose.config-hash') != record.get('candidateHashes', {}).get(name)):
            raise ValueError('native-client-image-not-ready')
    replay_document(recipe, observations,
        {name: record['previous'][name]['tag'] for name in SERVICES}, verify_observed=True)
    host_loaded(record)
    # The existing setup probe sends the key on stdin, never in argv. This
    # is a read-only request and does not start MCP, Google or notifications.
    probe = '''import json,os,sys,urllib.request
p=json.load(sys.stdin)
request=urllib.request.Request('http://'+p['host']+':'+p['port']+'/v1/model/status',headers={'Authorization':'Bearer '+p['key']})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=10) as response:
 if response.status != 200: raise ValueError('host-agent-not-ready')
'''
    values = helper('pixel-macos-access-install')._env_file(Path(record['installDir']) / '.env')
    payload = dict(host=values.get('ODS_AGENT_HOST') or 'host.docker.internal',
        port=values.get('ODS_AGENT_PORT') or '7710', key=values['ODS_AGENT_KEY'])
    checked(run, 'exec', '-i', 'ods-dashboard-api', 'python3', '-c', probe, input=json.dumps(payload), timeout=15)
    checked(run, 'exec', 'ods-dashboard-api', 'python3', '-c',
        'import urllib.request; r=urllib.request.build_opener(urllib.request.ProxyHandler({})).open('
        '"http://pixel-edge:9595/health",timeout=10); raise SystemExit(0 if r.status==200 else 1)', timeout=15)
    return True


def apply(destination):
    destination = Path(destination)
    record, install_dir, run = load(destination)
    require_held(record, install_dir, run)
    files = helper('pixel-native-client-files')
    files.verify(destination / 'files', selected='candidate')
    candidate = compose_document(install_dir, run, record['transport']['project'])
    if {name: candidate['services'][name] for name in SERVICES} != record['candidateClients']:
        raise ValueError('native-client-prepared-definition-changed')
    record['phase'] = 'applying'
    write(destination / 'journal.json', record)
    try:
        for name in SERVICES:
            previous = record['previous'][name]
            if image_id(run, previous['tag']) not in (previous['image'], record['images'][name]):
                raise ValueError('native-client-image-tag-drift')
            checked(run, 'tag', record['images'][name], previous['tag'])
        # Reuse the installer migration for Mac SQLite ownership. It stops
        # only the API writer and retains the old host stores and private logs.
        private = helper('dashboard-private-state')
        flags = helper('pixel-native-finalize').compose_flags(install_dir, os.environ)
        with private.locked(install_dir) as directory:
            stores = private.Stores(install_dir, shlex.join(flags), build=False)
            if stores.requires_stores:
                stores.prepare(directory, stop=True)
        observations = {name: container(run, record['transport']['project'], name) for name in SERVICES}
        recipe = replay_document(candidate, observations,
            {name: record['previous'][name]['tag'] for name in SERVICES})
        write(destination / 'candidate.compose.json', recipe)
        write(destination / 'candidate-resolved.json', candidate)
        record['candidateRecipeSha256'] = digest(recipe)
        record['candidateHashes'] = {}
        for name in SERVICES:
            result = checked(run, 'compose', '--project-directory', str(install_dir),
                '--project-name', record['transport']['project'], '--env-file', '/dev/null',
                '-f', str(destination / 'candidate.compose.json'), 'config', '--hash', name).split()
            if len(result) != 2 or result[0] != name or not re.fullmatch('[a-f0-9]{64}', result[1]):
                raise ValueError('native-client-candidate-hash-required')
            expected = checked(run, 'compose', '--project-directory', str(install_dir),
                '--project-name', record['transport']['project'], '--env-file', '/dev/null',
                '-f', str(destination / 'candidate-resolved.json'), 'config', '--hash', name).split()
            if result != expected:
                raise ValueError('native-client-candidate-definition-changed')
            record['candidateHashes'][name] = result[1]
        write(destination / 'journal.json', record)
        _up(run, install_dir, record['transport']['project'], destination / 'candidate.compose.json')
        host_command(record, 'start')
        deadline = time.monotonic() + 30
        while True:
            try:
                verify(destination)
                break
            except (ValueError, OSError, subprocess.SubprocessError):
                if time.monotonic() >= deadline: raise
                time.sleep(1)
        record['phase'] = 'verified'
        write(destination / 'journal.json', record)
    except BaseException:
        record['phase'] = 'failed'
        write(destination / 'journal.json', record)
        restore(destination)
        raise


def restore(destination):
    """Restore only owner files/web/host; keep Core and admission held."""
    destination = Path(destination)
    record, install_dir, run = load(destination)
    require_held(record, install_dir, run)
    if digest(private_read(destination / 'previous.compose.json')) != record['previousRecipeSha256']:
        raise ValueError('native-client-rollback-recipe-changed')
    host_command(record, 'stop')
    for name in SERVICES:
        previous = record['previous'][name]
        if image_id(run, previous['tag']) not in (previous['image'], record['images'][name]):
            raise ValueError('native-client-image-tag-drift')
        checked(run, 'tag', previous['image'], previous['tag'])
    _up(run, install_dir, record['transport']['project'], destination / 'previous.compose.json')
    helper('pixel-native-client-files').restore(destination / 'files')
    host_command(record, 'start')
    record['phase'] = 'restored'
    write(destination / 'journal.json', record)


def complete(destination):
    destination = Path(destination)
    verify(destination)
    record, _, _ = load(destination)
    record['phase'] = 'ready'
    write(destination / 'journal.json', record)
