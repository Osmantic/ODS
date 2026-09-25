"""Exercise the resolver's real build validation without starting Docker."""
import ast
import pathlib
import os
import re
import pytest
import json
import hashlib
import shlex
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
