"""Exercise the resolver's real build validation without starting Docker."""
import ast
import pathlib
import os
import re
import pytest
import json
import hashlib
import shlex
import shutil
import subprocess
import sys
import yaml

SCRIPT = pathlib.Path(__file__).parents[1] / 'scripts/resolve-compose-stack.sh'

def validator(tmp_path):
    source = SCRIPT.read_text(encoding='utf-8')
    start = source.index('def _extension_build_context(')
    end = source.index('def _scan_user_compose_content(', start)
    namespace = {'script_dir': tmp_path, 'pathlib': pathlib, 're': re, 'os': os, 'json': json, 'yaml': yaml}
    exec(compile(ast.parse(source[start:end]), str(SCRIPT), 'exec'), namespace)
    extension = tmp_path / 'data/user-extensions/distribution'
    extension.mkdir(parents=True)
    (extension / 'Dockerfile').write_text('FROM scratch')
    return namespace['_extension_build_context'], extension

@pytest.mark.parametrize('context', ['.', '/data/user-extensions/distribution'])
def test_container_context_projects_to_host(tmp_path, context):
    check, extension = validator(tmp_path)
    assert check(extension / 'compose.yaml', {'context': context}) == str(extension.resolve())

@pytest.mark.parametrize('build', [
    {'context': '../other'}, {'context': '/etc'},
    {'context': '.', 'dockerfile': '../../Dockerfile'},
    {'context': '.', 'network': 'host'}, {'context': '.', 'privileged': True},
    {'context': '.', 'secrets': ['host-secret']}, {'context': '${HOME}'},
    {'context': '.', 'args': ['HOST_SECRET']},
    {'context': '/data/user-extensions/distribution-evil'},
])
def test_rejects_external_or_privileged_builds(tmp_path, build):
    check, extension = validator(tmp_path)
    with pytest.raises(ValueError): check(extension / 'compose.yaml', build)

def test_rejects_symlink_escape(tmp_path):
    check, extension = validator(tmp_path)
    outside = tmp_path / 'private'
    outside.write_text('secret')
    try: (extension / 'leak').symlink_to(outside)
    except OSError: pytest.skip('symlink privilege unavailable')
    with pytest.raises(ValueError): check(extension / 'compose.yaml', {'context': '.'})


@pytest.mark.parametrize('inline', [False, True])
def test_commit_bound_github_context_survives_host_projection(tmp_path, inline):
    check, extension = validator(tmp_path)
    commit = 'a' * 40
    context = f'https://github.com/owner/project.git#{commit}:app'
    build = {'context': context}
    receipt = {'service': 'distribution', 'path': 'app/Dockerfile', 'blob': 'b' * 40}
    if inline:
        build['dockerfile_inline'] = 'FROM scratch\nCOPY . /app\n'
        receipt = {'service': 'distribution', 'kind': 'proposed-dockerfile',
                   'sha256': hashlib.sha256(build['dockerfile_inline'].encode()).hexdigest()}
    candidate = {'repository': 'https://github.com/owner/project', 'commit': commit,
                 'manifest': {'service': {'id': 'distribution'}},
                 'compose': {'services': {'distribution': {'build': build,
                     'image': 'ods-source-distribution:' + commit, 'pull_policy': 'never'}}}}
    digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    (extension / 'manifest.yaml').write_text(yaml.safe_dump(candidate['manifest']))
    (extension / 'compose.yaml').write_text(yaml.safe_dump(candidate['compose']))
    (extension / 'upstream.json').write_text(json.dumps({'origin': 'github-proposal',
        'repository': candidate['repository'], 'commit': commit, 'recipeDigest': digest, 'sourceFiles': [receipt]}))
    assert check(extension / 'compose.yaml', build) == context
    candidate['compose']['services']['distribution']['build']['context'] = context.replace(commit, 'main')
    (extension / 'compose.yaml').write_text(yaml.safe_dump(candidate['compose']))
    with pytest.raises(ValueError):
        check(extension / 'compose.yaml', build)


@pytest.fixture(params=['generic', 'generated-python'])
def resolver_recipe(tmp_path, request):
    _, extension = validator(tmp_path)
    (tmp_path / 'docker-compose.base.yml').write_text('services: {}\n')
    if request.param == 'generic':
        candidate = {
            'manifest': {'schema_version': 'ods.services.v1', 'service': {
                'id': 'distribution', 'name': 'Distribution', 'type': 'docker',
                'category': 'optional', 'port': 0, 'health': '',
                'compose_file': 'compose.yaml', 'startup_check': False}},
            'compose': {'services': {'distribution': {'image': 'example:fixture'}}},
        }
    else:
        compiler = SCRIPT.parents[1] / 'extensions/services/pixel-agent/plugin/extension-source-recipe.mjs'
        source = {'repository': 'https://github.com/owner/project', 'commit': 'a' * 40,
                  'serviceId': 'distribution', 'name': 'Distribution', 'port': 0,
                  'cliOnly': True, 'pythonVersion': '3.12', 'pythonImports': ['example']}
        result = subprocess.run(['node', '--input-type=module', '-e',
            'const {compileSourceRecipe} = await import(process.argv[1]); '
            'console.log(JSON.stringify(compileSourceRecipe(JSON.parse(process.argv[2]))));',
            compiler.resolve().as_uri(), json.dumps(source)],
            check=True, capture_output=True, text=True, timeout=15)
        candidate = json.loads(result.stdout)
    assert 'gpu_backends' not in candidate['manifest']['service']
    return tmp_path, extension, candidate


def resolve_recipe(fixture, backend, *, restriction=None, disabled=False, tampered=False):
    root, extension, candidate = fixture
    if restriction is not None:
        candidate['manifest']['service']['gpu_backends'] = restriction
    (extension / 'manifest.yaml').write_text(yaml.safe_dump(candidate['manifest']))
    compose = extension / ('compose.yaml.disabled' if disabled else 'compose.yaml')
    compose.write_text(yaml.safe_dump(candidate['compose']))
    if 'repository' in candidate:
        inline = candidate['compose']['services']['distribution']['build']['dockerfile_inline']
        digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        (extension / 'upstream.json').write_text(json.dumps({
            'origin': 'github-proposal', 'repository': candidate['repository'],
            'commit': candidate['commit'], 'recipeDigest': '0' * 64 if tampered else digest,
            'sourceFiles': [{'service': 'distribution', 'kind': 'proposed-dockerfile',
                             'sha256': hashlib.sha256(inline.encode()).hexdigest()}]}))
    # Supply only fixture state. Never inherit an installed ODS model selection.
    env = {'PATH': str(pathlib.Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
           'HOME': str(root), 'ODS_MODE': 'local'}
    result = subprocess.run(['bash', str(SCRIPT), '--script-dir', str(root),
                             '--gpu-backend', backend, '--tier', '1'],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    flags = shlex.split(result.stdout)
    assert flags[::2] == ['-f'] * (len(flags) // 2)
    return flags[1::2], result.stderr


@pytest.mark.parametrize('backend', ['apple', 'cpu', 'none', 'nvidia', 'amd'])
def test_resolver_keeps_omitted_backend_recipe(resolver_recipe, backend):
    files, _ = resolve_recipe(resolver_recipe, backend)
    assert 'data/user-extensions/distribution/compose.yaml' in files
    if 'repository' in resolver_recipe[2]:
        projection = 'data/user-extensions/distribution/.ods-build-context-compose.yaml.json'
        assert projection in files
        projected = json.loads((resolver_recipe[0] / projection).read_text())
        assert projected['services']['distribution']['build']['context'] == (
            resolver_recipe[2]['compose']['services']['distribution']['build']['context'])


@pytest.mark.parametrize('backend', ['apple', 'cpu', 'nvidia'])
def test_resolver_honors_explicit_backend_restriction(resolver_recipe, backend):
    files, _ = resolve_recipe(resolver_recipe, backend, restriction=['nvidia'])
    assert ('data/user-extensions/distribution/compose.yaml' in files) is (backend == 'nvidia')


@pytest.mark.parametrize('backend', ['apple', 'cpu', 'none', 'nvidia', 'amd'])
def test_resolver_keeps_disabled_recipe_out(resolver_recipe, backend):
    files, _ = resolve_recipe(resolver_recipe, backend, disabled=True)
    assert not any('user-extensions/distribution/' in path for path in files)


@pytest.mark.parametrize('backend', ['apple', 'cpu', 'none', 'nvidia', 'amd'])
@pytest.mark.parametrize('resolver_recipe', ['generated-python'], indirect=True)
def test_resolver_rejects_tampered_recipe_provenance(resolver_recipe, backend):
    files, diagnostics = resolve_recipe(resolver_recipe, backend, tampered=True)
    assert not any('user-extensions/distribution/' in path for path in files)
    assert 'installed source recipe changed' in diagnostics


HOST_GATEWAY = 'host.docker.internal:host-gateway'


def scanner(tmp_path):
    """The resolver's user-extension compose scan and library trust decision."""
    source = SCRIPT.read_text(encoding='utf-8')
    start = source.index('_LOOPBACK_VAR_DEFAULT_RE = re.compile(')
    end = source.index('def _extension_base_path(', start)
    namespace = {'script_dir': tmp_path, 'pathlib': pathlib, 're': re, 'os': os, 'json': json, 'yaml': yaml}
    exec(compile(ast.parse(source[start:end]), str(SCRIPT), 'exec'), namespace)
    extension = tmp_path / 'data/user-extensions/gaia'
    extension.mkdir(parents=True)
    compose = extension / 'compose.yaml'
    return namespace['_scan_user_compose_content'], namespace['_library_recipe_trusted'], extension, compose


def write_extra_hosts(compose, extra_hosts):
    compose.write_text(yaml.safe_dump({'services': {'gaia': {'image': 'example:fixture',
                                                             'extra_hosts': extra_hosts}}}))


@pytest.mark.parametrize('upstream, trusted', [
    (None, True),  # curated recipe without provenance file (gaia)
    ({'repository': 'https://github.com/amd/gaia', 'license': 'MIT'}, True),
    ({'origin': 'github-proposal', 'repository': 'https://github.com/owner/project'}, False),
    ('{not json', False),
])
def test_resolver_library_trust_mirrors_dashboard_install(tmp_path, upstream, trusted):
    scan, library_trusted, extension, compose = scanner(tmp_path)
    if upstream is not None:
        (extension / 'upstream.json').write_text(upstream if isinstance(upstream, str) else json.dumps(upstream))
    assert library_trusted(extension) is trusted
    write_extra_hosts(compose, [HOST_GATEWAY])
    ok, warnings = scan(compose, library_trusted(extension))
    assert ok is trusted and bool(warnings) is not trusted, warnings


def test_resolver_linked_provenance_is_untrusted(tmp_path):
    _, library_trusted, extension, _ = scanner(tmp_path)
    (tmp_path / 'elsewhere.json').write_text('{}')
    try:
        (extension / 'upstream.json').symlink_to(tmp_path / 'elsewhere.json')
    except OSError:
        pytest.skip('symlink privilege unavailable')
    assert library_trusted(extension) is False


@pytest.mark.parametrize('extra_hosts', [
    ['metadata.internal:169.254.169.254'],
    [HOST_GATEWAY, 'registry.example:10.0.0.1'],
    ['host.docker.internal=host-gateway'],
    {'host.docker.internal': 'host-gateway'},
])
def test_resolver_trusted_library_allows_only_the_host_gateway_entry(tmp_path, extra_hosts):
    scan, _, _, compose = scanner(tmp_path)
    write_extra_hosts(compose, extra_hosts)
    ok, warnings = scan(compose, True)
    assert not ok and any('unsupported extra_hosts' in item for item in warnings), warnings


def test_resolver_untrusted_compose_keeps_rejecting_extra_hosts(tmp_path):
    """The override file and imported recipes never get the exemption."""
    scan, _, _, compose = scanner(tmp_path)
    write_extra_hosts(compose, [HOST_GATEWAY])
    ok, warnings = scan(compose)
    assert not ok and any('declares extra_hosts' in item for item in warnings), warnings


NVIDIA_GPU = {'deploy': {'resources': {'reservations': {'devices': [
    {'driver': 'nvidia', 'count': 1, 'capabilities': ['gpu']}]}}}}
AMD_GPU = {'devices': ['/dev/dri:/dev/dri', '/dev/kfd:/dev/kfd'],
           'group_add': ['${VIDEO_GID:-44}', '${RENDER_GID:-992}']}


def gpu_recipe_root(tmp_path, *, upstream=None, nvidia=NVIDIA_GPU, amd=AMD_GPU, override=None, base=None):
    """An install root holding one GPU library recipe as the dashboard installs it."""
    (tmp_path / 'docker-compose.base.yml').write_text('services: {}\n')
    extension = tmp_path / 'data/user-extensions/gpu-recipe'
    extension.mkdir(parents=True)
    (extension / 'manifest.yaml').write_text(yaml.safe_dump({'schema_version': 'ods.services.v1', 'service': {
        'id': 'gpu-recipe', 'name': 'GPU Recipe', 'compose_file': 'compose.yaml',
        'gpu_backends': ['nvidia', 'amd']}}))
    (extension / 'compose.yaml').write_text(yaml.safe_dump(
        {'services': {'gpu-recipe': {'image': 'example:fixture', **(base or {})}}}))
    (extension / 'compose.nvidia.yaml').write_text(yaml.safe_dump({'services': {'gpu-recipe': nvidia}}))
    (extension / 'compose.amd.yaml').write_text(yaml.safe_dump({'services': {'gpu-recipe': amd}}))
    if upstream is not None:
        (extension / 'upstream.json').write_text(json.dumps(upstream))
    if override is not None:
        (tmp_path / 'docker-compose.override.yml').write_text(yaml.safe_dump({'services': {'base': override}}))
    return tmp_path


def resolve_root(root, backend):
    env = {'PATH': str(pathlib.Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
           'HOME': str(root), 'ODS_MODE': 'local'}
    result = subprocess.run(['bash', str(SCRIPT), '--script-dir', str(root),
                             '--gpu-backend', backend, '--tier', '1'],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return shlex.split(result.stdout)[1::2], result.stderr


@pytest.mark.parametrize('backend', ['nvidia', 'amd'])
def test_resolver_keeps_curated_gpu_overlay_on_its_backend(tmp_path, backend):
    files, diagnostics = resolve_root(gpu_recipe_root(tmp_path), backend)
    recipe = 'data/user-extensions/gpu-recipe/'
    assert recipe + 'compose.yaml' in files
    assert recipe + f'compose.{backend}.yaml' in files, diagnostics
    assert recipe + ('compose.amd.yaml' if backend == 'nvidia' else 'compose.nvidia.yaml') not in files
    assert 'WARNING' not in diagnostics


@pytest.mark.parametrize('backend, overlay, reason', [
    ('amd', {'devices': ['/dev/dri:/dev/dri', '/dev/mem:/dev/mem']}, 'declares unsupported devices'),
    ('amd', {'devices': ['/dev/sda:/dev/sda']}, 'declares unsupported devices'),
    ('amd', {**AMD_GPU, 'privileged': True}, 'uses privileged mode'),
    ('amd', {**AMD_GPU, 'cap_add': ['SYS_ADMIN']}, 'adds dangerous capability'),
    ('amd', NVIDIA_GPU, 'requests GPU passthrough'),
    ('nvidia', {'deploy': {'resources': {'reservations': {'devices': [
        {'driver': 'nvidia', 'count': 1, 'capabilities': ['gpu', 'utility', 'compute']}]}}}},
     'unsupported GPU reservation'),
    ('nvidia', {**NVIDIA_GPU, 'network_mode': 'host'}, 'uses host network mode'),
    ('nvidia', AMD_GPU, 'declares devices'),
    ('nvidia', {'gpus': 'all'}, 'requests GPUs via gpus'),
    ('nvidia', {**NVIDIA_GPU, 'runtime': 'nvidia'}, 'sets a container runtime'),
    ('amd', {**AMD_GPU, 'gpus': 'all'}, 'requests GPUs via gpus'),
])
def test_resolver_drops_curated_overlay_outside_the_accelerator_policy(tmp_path, backend, overlay, reason):
    root = gpu_recipe_root(tmp_path, **{backend: overlay})
    files, diagnostics = resolve_root(root, backend)
    assert 'data/user-extensions/gpu-recipe/compose.yaml' in files
    assert f'data/user-extensions/gpu-recipe/compose.{backend}.yaml' not in files
    assert reason in diagnostics


@pytest.mark.parametrize('backend', ['nvidia', 'amd'])
def test_resolver_never_grants_an_imported_recipe_an_accelerator(tmp_path, backend):
    """A github-proposal recipe is untrusted even in the exact ODS shape."""
    root = gpu_recipe_root(tmp_path, upstream={'origin': 'github-proposal',
                                               'repository': 'https://github.com/owner/project'})
    files, diagnostics = resolve_root(root, backend)
    assert 'data/user-extensions/gpu-recipe/compose.yaml' in files
    assert f'data/user-extensions/gpu-recipe/compose.{backend}.yaml' not in files
    assert ('declares devices' if backend == 'amd' else 'requests GPU passthrough') in diagnostics


@pytest.mark.parametrize('backend, override, reason', [
    ('amd', AMD_GPU, 'declares devices'),
    ('nvidia', NVIDIA_GPU, 'requests GPU passthrough'),
    ('nvidia', {'gpus': 'all'}, 'requests GPUs via gpus'),
    ('nvidia', {'runtime': 'nvidia'}, 'sets a container runtime'),
])
def test_resolver_never_grants_the_override_file_an_accelerator(tmp_path, backend, override, reason):
    files, diagnostics = resolve_root(gpu_recipe_root(tmp_path, override=override), backend)
    assert 'docker-compose.override.yml' not in files
    assert f'docker-compose.override.yml: service \'base\' {reason}' in diagnostics


@pytest.mark.parametrize('upstream', [None, {'origin': 'github-proposal',
                                             'repository': 'https://github.com/owner/project'}],
                         ids=['curated', 'imported'])
@pytest.mark.parametrize('base, reason', [
    ({'gpus': 'all'}, 'requests GPUs via gpus'),
    ({'runtime': 'nvidia'}, 'sets a container runtime'),
], ids=['gpus', 'runtime'])
def test_resolver_rejects_gpus_and_runtime_for_every_recipe(tmp_path, upstream, base, reason):
    """Another route to a GPU drops the whole recipe, curated or imported."""
    root = gpu_recipe_root(tmp_path, upstream=upstream, base=base)
    files, diagnostics = resolve_root(root, 'nvidia')
    assert not any('user-extensions/gpu-recipe/' in path for path in files)
    assert reason in diagnostics


IMPORTED = {'origin': 'github-proposal', 'repository': 'https://github.com/owner/project'}


@pytest.mark.parametrize('upstream', [None, IMPORTED], ids=['curated', 'imported'])
@pytest.mark.parametrize('base, reason', [
    ({'privileged': 'true'}, 'uses privileged mode'),
    ({'network_mode': '${RECIPE_NET:-host}'}, 'network_mode must be a literal string'),
    ({'cap_add': ['CAP_SYS_ADMIN']}, 'adds dangerous capability: CAP_SYS_ADMIN'),
    ({'security_opt': ['systempaths=unconfined']}, "dangerous security_opt 'systempaths=unconfined'"),
    ({'group_add': ['docker']}, 'adds supplementary groups'),
    ({'device_cgroup_rules': ['c 1:1 rwm']}, 'declares device_cgroup_rules'),
    ({'extends': {'file': 'base.yml', 'service': 'base'}}, 'extends another service definition'),
    ({'volumes': ['./.env:/secrets/.env:ro']}, 'bind-mounts the ODS install directory'),
], ids=['string-privileged', 'interpolated-host-network', 'cap-prefix', 'systempaths', 'group-add',
        'device-cgroup-rules', 'extends', 'install-env'])
def test_resolver_applies_the_shared_policy_to_every_recipe(tmp_path, upstream, base, reason):
    """The shared compose policy drops the whole recipe, curated or imported."""
    root = gpu_recipe_root(tmp_path, upstream=upstream, base=base)
    files, diagnostics = resolve_root(root, 'nvidia')
    assert not any('user-extensions/gpu-recipe/' in path for path in files)
    assert reason in diagnostics


@pytest.mark.parametrize('upstream, volume, kept', [
    (IMPORTED, './data/gpu-recipe/state:/state', True),
    (IMPORTED, './config/gpu-recipe/app.yaml:/etc/app.yaml:ro', True),
    (IMPORTED, './scripts:/host-scripts', False),
    (IMPORTED, './data/n8n:/n8n', False),
    (IMPORTED, './upload:/upload', False),
    (None, './upload:/upload', True),
], ids=['imported-own-data', 'imported-own-config', 'imported-ods-scripts', 'imported-core-data',
        'imported-install-root', 'curated-reviewed-bind'])
def test_resolver_keeps_imported_binds_in_their_own_namespace(tmp_path, upstream, volume, kept):
    """Relative binds resolve against the install directory; an imported recipe
    may reach only its own ./data/<id> and ./config/<id>."""
    root = gpu_recipe_root(tmp_path, upstream=upstream, base={'volumes': [volume]})
    files, diagnostics = resolve_root(root, 'nvidia')
    assert ('data/user-extensions/gpu-recipe/compose.yaml' in files) is kept, diagnostics
    if not kept:
        assert 'outside its own ./data/gpu-recipe and ./config/gpu-recipe' in diagnostics


def test_resolver_rejects_an_unparseable_user_extension_without_failing_the_stack(tmp_path):
    """Invalid UTF-8 used to raise out of the scan and abort every `ods` command."""
    root = gpu_recipe_root(tmp_path)
    (root / 'data/user-extensions/gpu-recipe/compose.yaml').write_bytes(
        b'services:\n  gpu-recipe:\n    image: \xff\xfe\n')
    files, diagnostics = resolve_root(root, 'nvidia')  # exits 0: the stack still resolves
    assert not any('user-extensions/gpu-recipe/' in path for path in files)
    assert 'invalid compose file' in diagnostics


def user_extension(root, identifier, service, *, upstream=None, extra=None):
    """One enabled user extension (ods.services.v1 manifest, gpu_backends all)."""
    extension = root / 'data/user-extensions' / identifier
    extension.mkdir(parents=True)
    (extension / 'manifest.yaml').write_text(yaml.safe_dump({'schema_version': 'ods.services.v1', 'service': {
        'id': identifier, 'name': identifier, 'compose_file': 'compose.yaml', 'gpu_backends': ['all']}}))
    services = {identifier: {'image': 'busybox:1.36', **service}, **(extra or {})}
    (extension / 'compose.yaml').write_text(yaml.safe_dump({'services': services}))
    if upstream is not None:
        (extension / 'upstream.json').write_text(json.dumps(upstream))


def compose_loads(root, files):
    """`docker compose config` of the resolved stack (never `up`)."""
    if subprocess.run(['docker', 'compose', 'version'], capture_output=True).returncode != 0:
        pytest.skip('Docker Compose v2 is unavailable')
    env = {key: value for key, value in os.environ.items()
           if key in {'PATH', 'HOME', 'DOCKER_CONFIG', 'DOCKER_HOST', 'DOCKER_CONTEXT'}}
    flags = [argument for path in files for argument in ('-f', path)]
    result = subprocess.run(['docker', 'compose', '-p', 'ods-cascade-fixture', '--project-directory', str(root),
                             *flags, 'config', '-q'], cwd=root, env=env, capture_output=True, text=True,
                            timeout=120)
    return result.returncode == 0, result.stderr


def cascade_root(tmp_path):
    """A refused provider, a dependent chain on it, and an unrelated healthy pair."""
    root = tmp_path
    (root / 'docker-compose.base.yml').write_text('services:\n  core-svc:\n    image: busybox:1.36\n')
    (root / 'docker-compose.nvidia.yml').write_text('services: {}\n')
    user_extension(root, 'fx-env-file', {'env_file': ['./data/fx-env-file/app.env']})
    user_extension(root, 'fx-dependent', {'depends_on': ['fx-env-file']})
    user_extension(root, 'fx-chain', {'depends_on': {'fx-dependent': {'condition': 'service_started'}}})
    user_extension(root, 'fx-link', {'links': ['fx-env-file:legacy-alias']})
    user_extension(root, 'fx-provider', {})
    user_extension(root, 'fx-consumer', {'depends_on': ['fx-provider', 'core-svc']})
    return root


def test_resolver_drops_the_dependents_of_a_refused_extension(tmp_path):
    """Compose refuses the whole project when a kept service depends on a
    refused one; every `ods` command, host-agent start/stop and the installer
    would fail. The dependents go too (transitively), naming the chain."""
    root = cascade_root(tmp_path)
    files, diagnostics = resolve_root(root, 'nvidia')
    for dropped in ('fx-env-file', 'fx-dependent', 'fx-chain', 'fx-link'):
        assert not any(f'user-extensions/{dropped}/' in path for path in files), (dropped, files)
    for kept in ('fx-provider', 'fx-consumer'):
        assert f'data/user-extensions/{kept}/compose.yaml' in files, (kept, diagnostics)
    refused = "which fx-env-file/compose.yaml declares but was refused: service 'fx-env-file' reads an env_file"
    assert f"WARNING: fx-dependent: skipped because service 'fx-dependent' needs 'fx-env-file', {refused}" \
        in diagnostics
    assert f"WARNING: fx-link: skipped because service 'fx-link' needs 'fx-env-file', {refused}" in diagnostics
    assert ("WARNING: fx-chain: skipped because service 'fx-chain' needs 'fx-dependent', which fx-dependent "
            "declares but was skipped because service 'fx-dependent' needs 'fx-env-file'") in diagnostics
    assert 'fx-consumer' not in diagnostics and 'fx-provider' not in diagnostics


def test_resolver_drops_a_dependent_of_a_service_nothing_declares(tmp_path):
    (tmp_path / 'docker-compose.base.yml').write_text('services: {}\n')
    user_extension(tmp_path, 'fx-orphan', {'depends_on': ['not-installed']})
    files, diagnostics = resolve_root(tmp_path, 'nvidia')
    assert not any('user-extensions/fx-orphan/' in path for path in files)
    assert ("fx-orphan: skipped because service 'fx-orphan' needs 'not-installed', which no enabled "
            "extension or ODS service declares") in diagnostics


@pytest.mark.skipif(shutil.which('docker') is None, reason='needs the Docker CLI')
def test_resolved_stack_still_loads_after_a_refusal(tmp_path):
    root = cascade_root(tmp_path)
    (root / 'data/fx-env-file').mkdir(parents=True)
    (root / 'data/fx-env-file/app.env').write_text('')
    files, _ = resolve_root(root, 'nvidia')
    ok, error = compose_loads(root, files)
    assert ok, error


def test_resolver_drops_an_alias_bomb_without_failing_the_stack(tmp_path):
    """The expansion bound refuses the one extension instead of exhausting memory."""
    root = gpu_recipe_root(tmp_path)
    bomb = ('x-0: &x0 [a, a, a, a, a, a, a, a, a, a]\n'
            + ''.join(f"x-{n}: &x{n} [{', '.join([f'*x{n - 1}'] * 10)}]\n" for n in range(1, 9))
            + 'services:\n  gpu-recipe:\n    image: busybox:1.36\n    labels: *x8\n')
    (root / 'data/user-extensions/gpu-recipe/compose.yaml').write_text(bomb)
    files, diagnostics = resolve_root(root, 'nvidia')
    assert not any('user-extensions/gpu-recipe/' in path for path in files)
    assert 'excessive aliasing' in diagnostics
