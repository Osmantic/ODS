"""Stage a native candidate through Pixel's pinned configure and renderer.

No service is activated. The generated broker policies still require native
transport provisioning and verification before this candidate can be used.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

SPEC = importlib.util.spec_from_file_location('native_bootstrap',
    Path(__file__).with_name('pixel-native-bootstrap.py'))
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)
BUNDLE_SPEC = importlib.util.spec_from_file_location('native_config_bundle',
    Path(__file__).with_name('pixel-runtime-bundle.py'))
bundle = importlib.util.module_from_spec(BUNDLE_SPEC)
BUNDLE_SPEC.loader.exec_module(bundle)
NODE_SPEC = importlib.util.spec_from_file_location('native_config_node',
    Path(__file__).with_name('pixel-native-node.py'))
native_node = importlib.util.module_from_spec(NODE_SPEC)
NODE_SPEC.loader.exec_module(native_node)


def private_json(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 2 * 1024 * 1024):
            raise ValueError('unsafe-onboarding-contract')
        body = stream.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise ValueError('oversized-onboarding-contract')
    return json.loads(body)


def private_answers(path):
    value = private_json(path)
    if type(value) is not dict:
        raise ValueError('invalid-onboarding-contract')
    if value.get('agentId') != 'pixel' or value.get('deploymentName') != 'ods-default':
        raise ValueError('not-ods-pixel-onboarding')
    return value


SERVICE_SOURCES = {
    'manager/extension_manager.py': 'extensions/services/pixel-agent/host/extension_manager.py',
    'manager/unix_peer.py': 'extensions/services/pixel-agent/host/unix_peer.py',
    'promoter/artifact_promoter.py': 'extensions/services/pixel-agent/host/artifact_promoter.py',
    'promoter/unix_peer.py': 'extensions/services/pixel-agent/host/unix_peer.py',
    'promoter/pixel_macos_custody.py': 'bin/pixel_macos_custody.py',
    'helpers/extension_search.py': 'extensions/services/pixel-agent/host/extension_search.py',
    'helpers/system_observe.py': 'extensions/services/pixel-agent/host/system_observe.py',
}


def service_catalog(ods_source):
    """Use the shared owner-side catalog generator, without shell interpolation."""
    with tempfile.TemporaryDirectory(prefix='pixel-catalog-') as temporary:
        destination = Path(temporary) / 'catalog.json'
        command = ('source "$1"\n'
            'ods_pixel_run_as_owner() { shift 2; "$@"; }\n'
            'INSTALL_DIR="$2"\n'
            '_ods_pixel_write_extension_catalog unused "$3" "$4"\n')
        bootstrap.command(['/bin/bash', '-c', command, 'native-catalog',
            str(ods_source / 'installers/lib/pixel-host-install.sh'), str(ods_source),
            temporary, str(destination)], cwd=ods_source)
        return service_snapshot(Path(temporary), 'catalog.json', private=True)


def service_snapshot(root, relative, *, private=False):
    with os.fdopen(bundle._open_file(root, relative), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o7000 or before.st_size > 2 * 1024 * 1024
                or private and (before.st_uid != os.getuid() or before.st_mode & 0o077)):
            raise ValueError('unsafe-native-service-source')
        body = stream.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024 or bundle._signature(before) != bundle._signature(os.fstat(stream.fileno())):
            raise ValueError('native-service-source-changed')
    return body


def stage_services(*, source, ref, ods_source, candidate, destination):
    """Prepare all three service snapshots together, without privileges/jobs.

    The bundle manifest is an integrity record, not root authorization. The
    privileged installer must bind its approval to the returned manifest digest.
    """
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    source, ods_source, candidate = [Path(path).resolve(strict=True) for path in (source, ods_source, candidate)]
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise ValueError('new-native-service-destination-required')
    destination = destination.parent.resolve(strict=True) / destination.name
    bootstrap.selected_release(source, ref)
    receipt = json.loads(service_snapshot(candidate, 'candidate.json', private=True))
    if (type(receipt) is not dict or receipt.get('pixelSourceRef') != ref or receipt.get('status') != 'staged'
            or receipt.get('requiresServiceQualification') is not True):
        raise ValueError('native-service-candidate-ref-mismatch')
    broker = service_snapshot(source, 'deploy/ops-broker/broker.py')
    committed = subprocess.run(['git', '--no-pager', 'show', ref + ':deploy/ops-broker/broker.py'],
        cwd=source, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    if committed.returncode or committed.stdout != broker:
        raise ValueError('native-service-broker-ref-mismatch')
    files = {'operations/broker.py': broker,
             'operations/policy.json': service_snapshot(candidate, 'operations-policy.json', private=True),
             'helpers/extension-catalog.json': service_catalog(ods_source)}
    policy = json.loads(files['operations/policy.json'])
    if (type(policy) is not dict or type(policy.get('schemaVersion')) is not int
            or policy['schemaVersion'] not in (1, 2)):
        raise ValueError('native-service-policy-required')
    snapshots = {}
    for output, relative in SERVICE_SOURCES.items():
        if relative not in snapshots:
            snapshots[relative] = service_snapshot(ods_source, relative)
        files[output] = snapshots[relative]
    for name, body in files.items():
        if name.endswith('.py'): compile(body, name, 'exec')
    config_digest = hashlib.sha256(service_snapshot(candidate, 'openclaw.json', private=True)).hexdigest()
    manifest = {'schemaVersion': 1, 'status': 'staged', 'requiresServiceQualification': True,
        'pixelSourceRef': ref, 'candidateConfigSha256': config_digest,
        'files': {name: {'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
                  for name, body in sorted(files.items())}}
    manifest_body = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    with tempfile.TemporaryDirectory(prefix='.pixel-services-', dir=destination.parent) as temporary:
        output = Path(temporary) / 'services'
        output.mkdir(mode=0o700)
        for name, body in {**files, 'services.json': manifest_body}.items():
            path = output / name
            path.parent.mkdir(mode=0o700, exist_ok=True)
            with path.open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(body)
        if os.path.lexists(destination):
            raise ValueError('native-service-destination-appeared')
        os.rename(output, destination)
    return hashlib.sha256(manifest_body).hexdigest()


def verified_services(root, *, expected_digest, expected_ref, expected_config_digest):
    """Read approved snapshots once; never execute candidate code or start jobs.

    All expected values come from the installer's approved transaction, not
    from this directory. Returned bytes can be handed directly to publishers.
    """
    for value, length in ((expected_digest, 64), (expected_ref, 40), (expected_config_digest, 64)):
        if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{' + str(length) + '}', value):
            raise ValueError('approved-service-identity-required')
    body = service_snapshot(root, 'services.json')
    if hashlib.sha256(body).hexdigest() != expected_digest:
        raise ValueError('native-service-manifest-digest-mismatch')
    manifest = json.loads(body)
    names = set(SERVICE_SOURCES) | {'operations/broker.py', 'operations/policy.json', 'helpers/extension-catalog.json'}
    if (type(manifest) is not dict or type(manifest.get('schemaVersion')) is not int
            or manifest['schemaVersion'] != 1 or manifest.get('status') != 'staged'
            or manifest.get('requiresServiceQualification') is not True
            or manifest.get('pixelSourceRef') != expected_ref
            or manifest.get('candidateConfigSha256') != expected_config_digest
            or type(manifest.get('files')) is not dict or set(manifest['files']) != names):
        raise ValueError('native-service-manifest-contract-mismatch')
    snapshots = {}
    for name in sorted(names):
        record = manifest['files'][name]
        if (type(record) is not dict or type(record.get('bytes')) is not int
                or not 0 < record['bytes'] <= 2 * 1024 * 1024
                or not isinstance(record.get('sha256'), str)
                or not re.fullmatch('[a-f0-9]{64}', record['sha256'])):
            raise ValueError('native-service-file-record-invalid')
        snapshot = service_snapshot(root, name)
        if len(snapshot) != record['bytes'] or hashlib.sha256(snapshot).hexdigest() != record['sha256']:
            raise ValueError('native-service-file-digest-mismatch')
        if name.endswith('.py'): compile(snapshot, name, 'exec')
        snapshots[name] = snapshot
    policy = json.loads(snapshots['operations/policy.json'])
    if (type(policy) is not dict or type(policy.get('schemaVersion')) is not int
            or policy['schemaVersion'] not in (1, 2)):
        raise ValueError('native-service-policy-required')
    return snapshots


def canonical_operations_policy(policy):
    """Canonicalize new local policy roots before Pixel hashes approval plans.

    Do not change SSH paths or executable arguments, and never reinterpret an
    existing live policy. macOS /var aliases otherwise fail the broker's real
    cwd containment check even when the configured directory is legitimate.
    """
    result = copy.deepcopy(policy)
    def canonical(value):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError('absolute-native-operations-path-required')
        return str(Path(value).resolve())
    targets = result.get('targets', {})
    local = {name for name, target in targets.items() if target.get('backend') == 'local'}
    for name in local:
        target = targets[name]
        if 'defaultCwd' in target:
            target['defaultCwd'] = canonical(target['defaultCwd'])
        for key in ('allowedRoots', 'writableRoots'):
            if key in target:
                target[key] = [canonical(path) for path in target[key]]
    for action in result.get('actions', {}).values():
        selected = set(action.get('targets', []))
        if '*' in selected:
            selected = set(targets)
        if selected & local and 'cwd' in action:
            resolved = canonical(action['cwd'])
            if selected - local and resolved != action['cwd']:
                raise ValueError('mixed-local-remote-cwd-requires-distinct-actions')
            action['cwd'] = resolved
    if 'stagingRoot' in result.get('download', {}):
        result['download']['stagingRoot'] = canonical(result['download']['stagingRoot'])
    return result


def generated_environment(path):
    """Parse the single-quoted assignments emitted by Pixel configure, never eval."""
    values = {}
    for token in shlex.split(path.read_text(), comments=True, posix=True):
        key, separator, value = token.partition('=')
        if not separator or not re.fullmatch('[A-Z][A-Z0-9_]*', key) or key in values:
            raise ValueError('invalid-pixel-generated-environment')
        values[key] = value
    return values


def verified_parallel_plugin(path, release):
    spec = importlib.util.spec_from_file_location('native_config_search',
        Path(__file__).resolve().parents[3] / 'extensions/services/pixel-agent/host/native_search.py')
    search = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(search)
    path = Path(path)
    if (not path.is_absolute() or path != path.resolve(strict=True)
            or path.name != 'parallel-' + search.VERSION or release['openclaw'] != search.VERSION):
        raise ValueError('native-parallel-plugin-identity-mismatch')
    archive = search.read_private(path.parent / ('parallel-' + search.VERSION + '.tgz'))
    search.verify_archive(archive)
    search.verify_tree(path, search.archive_files(archive))
    return str(path)


def runtime_plugins(runtime, release):
    """Bind plugin paths to the selected staged runtime, not ambient installs."""
    runtime = Path(runtime).resolve(strict=True)
    metadata = json.loads((runtime / 'node_modules/openclaw/package.json').read_text())
    if metadata.get('name') != 'openclaw' or metadata.get('version') != release['openclaw']:
        raise ValueError('native-config-runtime-version-mismatch')
    paths = {}
    for _, plugin_id, name in bootstrap.PLUGINS:
        paths[plugin_id] = (runtime / 'node_modules' / name, release['openclawPlugins'][name])
    for directory, plugin_id in bootstrap.PIXEL_PLUGINS:
        paths[plugin_id] = (runtime / 'pixel-plugins' / directory, release['pixel'])
    for plugin_id, (path, version) in paths.items():
        if runtime not in path.resolve(strict=True).parents:
            raise ValueError('native-plugin-path-escapes-runtime')
        metadata = json.loads((path / 'package.json').read_text())
        manifest = json.loads((path / 'openclaw.plugin.json').read_text())
        if metadata.get('version') != version or manifest.get('id') != plugin_id:
            raise ValueError('native-config-plugin-version-mismatch')
    return {key: str(value[0]) for key, value in paths.items()}


def apply_runtime_budget(config, *, answers, openclaw_home, research_port, env):
    writer = Path(__file__).resolve().parents[2] / 'lib/pixel-runtime-budget.py'
    result = bootstrap.command([sys.executable, str(writer), str(config),
                                str(research_port), str(answers), str(openclaw_home)],
                               cwd=config.parent, env=env).strip()
    if result == 'unchanged':
        return
    staged = Path(result)
    if (not staged.is_absolute() or staged.parent != config.parent
            or not staged.name.startswith('.ods-pixel-runtime-budget.')):
        raise ValueError('invalid-native-runtime-overlay-output')
    private_json(staged)
    # Both files are inside this run's private unpublished candidate directory.
    # The complete result still passes OpenClaw schema/plugin validation below.
    os.replace(staged, config)


def validate_candidate(config, *, node, entrypoint, expected, env, cwd):
    validation_env = {**env, 'OPENCLAW_CONFIG_PATH': str(config),
                      'OPENCLAW_STATE_DIR': str(Path(env['HOME']) / 'validation-state'),
                      'OPENCLAW_SKIP_CHANNELS': '1'}
    bootstrap.command([str(node), str(entrypoint), 'config', 'validate'],
                      cwd=cwd, env=validation_env)
    loaded = json.loads(bootstrap.command([str(node), str(entrypoint), 'plugins', 'list', '--json'],
                                          cwd=cwd, env=validation_env))
    for plugin_id, path in expected.items():
        matches = [item for item in loaded.get('plugins', []) if item.get('id') == plugin_id]
        if (len(matches) != 1 or matches[0].get('status') != 'loaded'
                or matches[0].get('rootDir') != path):
            raise ValueError('native-config-plugin-not-loaded')


def stage_bundle(*, source, ref, candidate, node, runtime, destination, services_digest=None):
    """Package exactly the configured plugins and prove their relocated loader.

    The candidate configuration is not edited. Protected publication still uses
    the installer's content-checked mapping to its final runtime destination.
    """
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    source = Path(source).resolve(strict=True)
    release = bootstrap.selected_release(source, ref)
    runtime = Path(runtime).resolve(strict=True)
    known = runtime_plugins(runtime, release)
    candidate = Path(candidate).resolve(strict=True)
    receipt = private_json(candidate / 'candidate.json')
    config = private_json(candidate / 'openclaw.json')
    if (type(receipt) is not dict or type(config) is not dict
            or receipt.get('status') != 'staged' or receipt.get('pixelSourceRef') != ref
            or receipt.get('requiresServiceQualification') is not True):
        raise ValueError('native-staged-candidate-required')
    plugins = config.get('plugins', {})
    if type(plugins) is not dict or type(plugins.get('load', {})) is not dict:
        raise ValueError('native-bundle-plugin-mapping-invalid')
    paths = plugins.get('load', {}).get('paths', [])
    allowed = plugins.get('allow', [])
    if (type(paths) is not list or not paths or type(allowed) is not list
            or not all(isinstance(item, str) for item in paths + allowed)
            or len(set(paths)) != len(paths) or len(set(allowed)) != len(allowed)
            or 'pixel-ods' not in allowed or plugins.get('installs')):
        raise ValueError('native-bundle-plugin-mapping-invalid')
    expected = {}
    for index, path in enumerate(paths):
        if not Path(path).is_absolute():
            raise ValueError('native-bundle-plugin-path-invalid')
        manifest = json.loads((Path(path) / 'openclaw.plugin.json').read_text())
        plugin_id = manifest.get('id')
        if plugin_id == 'parallel' and config.get('tools', {}).get('web', {}).get('search', {}).get('provider') == 'parallel-free':
            known['parallel'] = verified_parallel_plugin(path, release)
        if (plugin_id not in allowed or plugin_id in expected
                or plugin_id != 'pixel-ods' and known.get(plugin_id) != path):
            raise ValueError('native-bundle-plugin-source-mismatch')
        expected[plugin_id] = 'plugins/' + str(index)
    if set(expected) != set(allowed):
        raise ValueError('native-bundle-plugin-mapping-incomplete')
    destination = Path(destination)
    if not destination.is_absolute() or os.path.lexists(destination):
        raise ValueError('new-native-bundle-destination-required')
    destination = destination.parent.resolve(strict=True) / destination.name
    # Never place the output inside one of its own input trees.
    roots = [runtime, candidate, *(Path(path).resolve(strict=True) for path in paths)]
    if any(root == destination or root in destination.parents for root in roots):
        raise ValueError('native-bundle-destination-inside-source')
    with tempfile.TemporaryDirectory(prefix='.pixel-package-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        staged = temporary / 'bundle'
        protected_node = native_node.acquire(temporary, minimum_major=int(release['node'][2:]))
        digest = bundle.build(node=protected_node, runtime=runtime / 'node_modules/openclaw',
            destination=staged, plugins=paths, expected_version=release['openclaw'],
            stream_progress_fix=True, shared_runtime_repairs=True, services_digest=services_digest,
            exec_wrapper=Path(__file__).resolve().parents[3] / 'extensions/services/pixel-agent/host/cancellable-exec.sh')
        relocated = copy.deepcopy(config)
        relocated['plugins']['load']['paths'] = [str(staged / ('plugins/' + str(i)))
                                                for i in range(len(paths))]
        validation_config = temporary / 'openclaw.json'
        validation_config.write_text(json.dumps(relocated) + '\n')
        validation_config.chmod(0o600)
        home = temporary / 'home'
        home.mkdir(mode=0o700)
        validate_candidate(validation_config, node=staged / 'node',
            entrypoint=staged / 'runtime/openclaw.mjs',
            expected={key: str(staged / value) for key, value in expected.items()},
            env={'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
                 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}, cwd=temporary)
        bundle.verify(staged, expected_digest=digest)
        if private_json(candidate / 'openclaw.json') != config:
            raise ValueError('native-bundle-candidate-changed')
        if os.path.lexists(destination):
            raise ValueError('native-bundle-destination-appeared')
        os.rename(staged, destination)
    return digest


def prepare(*, source, ref, answers, node, sandbox_image, destination, runtime,
            research_port=3004, previous_config=None, previous_state_dir=None):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    if type(research_port) is not int or not 1 <= research_port <= 65535:
        raise ValueError('invalid-native-research-port')
    if (previous_config is None) != (previous_state_dir is None):
        raise ValueError('complete-native-migration-selection-required')
    previous = private_json(previous_config) if previous_config is not None else None
    if previous_config is not None and not isinstance(previous, dict):
        raise ValueError('native-migration-configuration-required')
    source, node = Path(source).resolve(strict=True), Path(node).resolve(strict=True)
    release = bootstrap.selected_release(source, ref)
    paths = runtime_plugins(runtime, release)
    entrypoint = Path(runtime).resolve(strict=True) / 'node_modules/openclaw/openclaw.mjs'
    contract = private_answers(answers)
    destination = Path(destination)
    if (not destination.is_absolute() or os.path.lexists(destination)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', sandbox_image)):
        raise ValueError('new-native-candidate-and-qualified-image-required')
    home = Path(contract['openclawHome'])
    # Migration renders in isolation and preserves the selected existing state.
    # Only an initial installation requires an unconfigured home.
    if not home.is_absolute() or (previous is None and os.path.lexists(home / 'openclaw.json')):
        raise ValueError('initial-native-config-requires-unconfigured-home')
    destination = destination.parent.resolve(strict=True) / destination.name
    with tempfile.TemporaryDirectory(prefix='.pixel-config-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        checkout = temporary / 'source'
        bootstrap.command(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout',
                           str(source), str(checkout)], cwd=temporary)
        bootstrap.command(['git', 'checkout', '--quiet', '--detach', ref], cwd=checkout)
        bootstrap.selected_release(checkout, ref)
        policy = canonical_operations_policy(private_json(contract['operationsPolicyFile']))
        policy_snapshot = temporary / 'operations-policy.json'
        policy_snapshot.write_text(json.dumps(policy))
        policy_snapshot.chmod(0o600)
        contract['operationsPolicyFile'] = str(policy_snapshot)
        snapshot = temporary / 'answers.json'
        snapshot.write_text(json.dumps(contract))
        snapshot.chmod(0o600)
        isolated_home = temporary / 'home'
        isolated_home.mkdir(mode=0o700)
        env = {'HOME': str(isolated_home), 'XDG_CONFIG_HOME': str(isolated_home / '.config'),
               'PATH': str(node.parent) + ':/usr/bin:/bin:/usr/sbin:/sbin'}
        bootstrap.command([str(node), str(checkout / 'scripts/configure.mjs'),
                           '--answers', str(snapshot)], cwd=checkout, env=env)
        values = generated_environment(checkout / '.env')
        values.update(OPENCLAW_HOME=str(isolated_home / 'empty-openclaw'),
                      PIXEL_SANDBOX_IMAGE=sandbox_image,
                      PIXEL_PLUGIN_PATH=paths['pixel-source-broker'],
                      PIXEL_OPS_PLUGIN_PATH=paths['pixel-operations-broker'],
                      PIXEL_FRONTIER_PLUGIN_PATH=paths['pixel-frontier-broker'])
        output = temporary / 'candidate'
        output.mkdir(mode=0o700)
        config = output / 'openclaw.json'
        bootstrap.command([str(node), str(checkout / 'scripts/render-config.mjs'), str(config)],
                          cwd=checkout, env={**env, **values})
        document = json.loads(config.read_text())
        for plugin_id in document['plugins']['allow']:
            if plugin_id in paths and paths[plugin_id] not in document['plugins']['load']['paths']:
                document['plugins']['load']['paths'].append(paths[plugin_id])
        # This is the same loopback chat endpoint enabled by the Linux caller.
        document['gateway']['port'] = contract['gatewayPort']
        document['gateway']['http']['endpoints']['chatCompletions'] = {'enabled': True}
        config.write_text(json.dumps(document, indent=2) + '\n')
        config.chmod(0o600)
        apply_runtime_budget(config, answers=snapshot, openclaw_home=home,
                             research_port=research_port, env=env)
        # The broker lives in Docker Desktop; Linux's default Unix socket is
        # not present in a native macOS gateway process.
        document = private_json(config)
        document['plugins'].setdefault('entries', {}).setdefault('pixel-ods', {}).setdefault(
            'config', {})['workspacePreviewTransport'] = 'docker-desktop'
        config.write_text(json.dumps(document, indent=2) + '\n')
        if previous is not None:
            migration_spec = importlib.util.spec_from_file_location('native_migration',
                Path(__file__).with_name('pixel-native-migration.py'))
            migration = importlib.util.module_from_spec(migration_spec)
            migration_spec.loader.exec_module(migration)
            migrated, migration_contract = migration.preserve_state(private_json(config), previous,
                state_dir=previous_state_dir)
            migration_contract['previousConfigSha256'] = hashlib.sha256(
                json.dumps(previous, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            config.write_text(json.dumps(migrated, indent=2) + '\n')
            # Recompute context limits from the retained live model metadata,
            # which can differ from the newly rendered onboarding defaults.
            apply_runtime_budget(config, answers=snapshot, openclaw_home=home,
                                 research_port=research_port, env=env)
            rebalanced = private_json(config)
            # The shared overlay also sets provider timeout defaults. Migration
            # retains the owner's complete provider settings, not just its key.
            rebalanced['models'] = migrated['models']
            config.write_text(json.dumps(rebalanced, indent=2) + '\n')
            migration_contract['candidateConfigSha256'] = hashlib.sha256(config.read_bytes()).hexdigest()
            migration.verify_state_preservation(previous, config.read_bytes(), migration_contract,
                state_dir=previous_state_dir)
            migration_record = output / 'migration.json'
            migration_record.write_text(json.dumps(migration_contract, sort_keys=True) + '\n')
            migration_record.chmod(0o600)
        expected = {item['id']: item['path'] for item in contract['gatewayExtensions'] if 'path' in item}
        expected.update({key: value for key, value in paths.items() if key in document['plugins']['allow']})
        validate_candidate(config, node=node, entrypoint=entrypoint, expected=expected,
                           env=env, cwd=temporary)
        shutil.copytree(checkout / '.generated/workspace', output / 'workspace')
        shutil.copyfile(checkout / '.generated/ops-policy.json', output / 'operations-policy.json')
        (output / 'operations-policy.json').chmod(0o600)
        # Do not export generated systemd units or a deployment record claiming
        # an active hardened service. This is an unactivated candidate only.
        (output / 'candidate.json').write_text(json.dumps({
            'schemaVersion': 1, 'pixelSourceRef': ref, 'status': 'staged',
            'sandboxImage': sandbox_image, 'requiresServiceQualification': True}) + '\n')
        (output / 'candidate.json').chmod(0o600)
        if os.path.lexists(destination):
            raise ValueError('native-candidate-destination-appeared')
        if previous is not None and private_json(previous_config) != previous:
            raise ValueError('native-migration-source-config-changed')
        os.rename(output, destination)
    return destination


def main():
    if sys.argv[1:2] == ['services']:
        return services_main(sys.argv[2:])
    if sys.argv[1:2] == ['bundle']:
        return bundle_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'source-ref', 'answers', 'node', 'sandbox-image', 'destination', 'runtime'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--research-port', type=int, default=3004)
    args = parser.parse_args()
    try:
        path = prepare(source=args.source, ref=args.source_ref, answers=args.answers,
                       node=args.node, sandbox_image=args.sandbox_image, destination=args.destination,
                       runtime=args.runtime, research_port=args.research_port)
    except (OSError, ValueError, KeyError, TypeError, bootstrap.subprocess.SubprocessError):
        print('error: native-pixel-config-staging-failed', file=sys.stderr)
        return 1
    print(json.dumps({'candidate': str(path), 'status': 'staged'}))
    return 0


def bundle_main(argv):
    parser = argparse.ArgumentParser(description='Stage and qualify a native candidate runtime bundle')
    for name in ('source', 'source-ref', 'candidate', 'node', 'runtime', 'destination'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        digest = stage_bundle(source=args.source, ref=args.source_ref, candidate=args.candidate,
                              node=args.node, runtime=args.runtime, destination=args.destination)
    except (OSError, ValueError, KeyError, TypeError, bootstrap.subprocess.SubprocessError):
        print('error: native-pixel-bundle-staging-failed', file=sys.stderr)
        return 1
    print(json.dumps({'runtimeBundle': args.destination, 'digest': digest, 'status': 'staged',
                      'requiresServiceQualification': True}))
    return 0


def services_main(argv):
    parser = argparse.ArgumentParser(description='Prepare a coherent native broker/manager/promoter source set')
    for name in ('source', 'source-ref', 'ods-source', 'candidate', 'destination'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        digest = stage_services(source=args.source, ref=args.source_ref, ods_source=args.ods_source,
                                candidate=args.candidate, destination=args.destination)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, subprocess.SubprocessError):
        print('error: native-service-staging-failed', file=sys.stderr)
        return 1
    print(json.dumps({'serviceBundle': args.destination, 'digest': digest, 'status': 'staged',
                      'requiresServiceQualification': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
