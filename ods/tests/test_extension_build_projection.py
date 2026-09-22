"""Exercise the resolver's real build validation without starting Docker."""
import ast
import pathlib
import os
import re
import pytest
import json
import hashlib
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
    outside = tmp_path / 'private'; outside.write_text('secret')
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
