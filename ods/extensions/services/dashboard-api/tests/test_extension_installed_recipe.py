"""Installed GitHub source recipes keep strict provenance through lifecycle changes."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest
import yaml

from extension_recipe_package import publish_package, verify_package, verify_installed_package
from extension_source_build import inspect_source_builds
from routers import extensions
from test_extension_recipe_drafts import evidence
from test_extension_recipe_package import upstream
from test_extension_recipe_validation import ODS


@pytest.fixture()
def installed_recipe(tmp_path, monkeypatch):
    compiler = ODS / 'extensions/services/pixel-agent/plugin/extension-source-recipe.mjs'
    source = dict(repository='https://github.com/python-humanize/humanize', commit='a' * 40,
        serviceId='humanize', name='Humanize', port=0, cliOnly=True,
        pythonVersion='3.12', pythonImports=['humanize'])
    compiled = subprocess.run(['node', '--input-type=module', '-e',
        'const {compileSourceRecipe}=await import(process.argv[1]); '
        'console.log(JSON.stringify(compileSourceRecipe(JSON.parse(process.argv[2]))));',
        compiler.as_uri(), json.dumps(source)], capture_output=True, text=True, check=True, timeout=15)
    candidate = json.loads(compiled.stdout)
    library = tmp_path / 'library'
    library.mkdir()
    user = tmp_path / 'data/user-extensions'
    monkeypatch.setattr(extensions, 'EXTENSIONS_LIBRARY_DIR', library)
    monkeypatch.setattr(extensions, 'USER_EXTENSIONS_DIR', user)
    monkeypatch.setattr(extensions, 'EXTENSIONS_DIR', ODS / 'extensions/services')
    monkeypatch.setattr(extensions, 'DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setattr(extensions, '_extensions_lock_path', lambda: tmp_path / '.lock')
    receipts = asyncio.run(inspect_source_builds(candidate, Mock(side_effect=AssertionError('no fetch'))))
    publish_package(library, candidate, evidence(candidate), {**upstream(candidate), 'sourceFiles': receipts})
    with extensions._extensions_lock():
        extensions._install_from_library('humanize')
    directory = user / 'humanize'
    projection = {'services': {'humanize': {'build': {
        'context': candidate['compose']['services']['humanize']['build']['context']}}}}
    (directory / '.ods-build-context-compose.yaml.json').write_text(json.dumps(projection))
    return tmp_path, directory, library / 'humanize', candidate, projection


def test_generated_python_enable_disable_enable_preserves_recipe(installed_recipe, monkeypatch):
    root, directory, library, candidate, projection = installed_recipe
    agent, hook, invalidate = Mock(return_value=True), Mock(return_value=True), Mock(return_value=True)
    monkeypatch.setattr(extensions, '_call_agent', agent)
    monkeypatch.setattr(extensions, '_call_agent_hook', hook)
    monkeypatch.setattr(extensions, '_call_agent_invalidate_compose_cache', invalidate)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    (root / 'docker-compose.base.yml').write_text('services: {}\n')
    # Generate the real projection from reviewed source before lifecycle scans.
    result = subprocess.run(['/bin/bash', str(ODS / 'scripts/resolve-compose-stack.sh'),
        '--script-dir', str(root), '--tier', '1', '--gpu-backend', 'apple',
        '--gpu-count', '1', '--ods-mode', 'local'],
        env={'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
             'HOME': str(root)}, capture_output=True, text=True, check=True, timeout=30)
    assert '.ods-build-context-compose.yaml.json' in result.stdout
    assert json.loads((directory / '.ods-build-context-compose.yaml.json').read_text()) == projection
    for _ in range(2):
        assert extensions.enable_extension('humanize', auto_enable_deps=False, api_key='owner')['restart_required'] is False
        assert extensions.disable_extension('humanize', include_data_info=False, api_key='owner')['action'] == 'disabled'
        assert (directory / '.ods-build-context-compose.yaml.json').exists()
        verify_installed_package(directory, candidate, compose_name='compose.yaml.disabled')
    assert extensions.enable_extension('humanize', auto_enable_deps=False, api_key='owner')['action'] == 'enabled'
    assert [call.args for call in agent.call_args_list] == [
        ('start', 'humanize'), ('stop', 'humanize'), ('start', 'humanize'),
        ('stop', 'humanize'), ('start', 'humanize')]
    for name in ('manifest.yaml', 'compose.yaml', 'upstream.json', '.ods-library-receipt.json'):
        assert (directory / name).read_bytes() == before[name]
    verify_package(library, candidate)
    with pytest.raises(ValueError, match='unexpected files'):
        verify_package(directory, candidate)


@pytest.mark.parametrize('fault', ['unknown', 'other-overlay', 'projection-context',
    'projection-extra-service', 'projection-command', 'bad-receipt', 'provenance',
    'source-receipt', 'compose', 'privileged', 'symlink-receipt', 'symlink-projection',
    'symlink-compose', 'symlink-upstream'])
def test_installed_recipe_rejects_tampering_without_rename_or_start(installed_recipe, monkeypatch, fault):
    _, directory, _, candidate, projection = installed_recipe
    disabled = directory / 'compose.yaml.disabled'
    (directory / 'compose.yaml').rename(disabled)
    if fault == 'unknown': (directory / 'setup.sh').write_text('unreviewed')
    if fault == 'other-overlay': (directory / '.ods-build-context-compose.yaml.disabled.json').write_text('{}')
    if fault.startswith('projection-'):
        if fault == 'projection-context': projection['services']['humanize']['build']['context'] = '/private'
        if fault == 'projection-extra-service': projection['services']['other'] = {}
        if fault == 'projection-command': projection['services']['humanize']['command'] = ['sh']
        (directory / '.ods-build-context-compose.yaml.json').write_text(json.dumps(projection))
    if fault == 'bad-receipt': (directory / '.ods-library-receipt.json').write_text('{}')
    if fault in ('provenance', 'source-receipt'):
        path = directory / 'upstream.json'
        value = json.loads(path.read_text())
        if fault == 'provenance': value['recipeDigest'] = '0' * 64
        else: value['sourceFiles'][0]['sha256'] = '0' * 64
        path.write_text(json.dumps(value))
    if fault in ('compose', 'privileged'):
        value = yaml.safe_load(disabled.read_text())
        if fault == 'compose': value['services']['humanize']['build']['context'] = '.'
        else: value['services']['humanize']['privileged'] = True
        disabled.write_text(yaml.safe_dump(value))
    if fault.startswith('symlink-'):
        name = {'symlink-receipt': '.ods-library-receipt.json',
            'symlink-projection': '.ods-build-context-compose.yaml.json',
            'symlink-compose': disabled.name, 'symlink-upstream': 'upstream.json'}[fault]
        path = directory / name
        target = directory.parent / ('target-' + name)
        target.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(target)
    agent = Mock(side_effect=AssertionError('must not start'))
    monkeypatch.setattr(extensions, '_call_agent', agent)
    with pytest.raises(extensions.HTTPException) as failure:
        extensions.enable_extension('humanize', auto_enable_deps=False, api_key='owner')
    assert failure.value.status_code == 400
    assert disabled.exists() and not (directory / 'compose.yaml').exists()
    agent.assert_not_called()


@pytest.mark.parametrize('artifact', ['.ods-library-receipt.json', '.ods-build-context-compose.yaml.json'])
def test_publisher_and_staging_remain_exact(installed_recipe, artifact):
    _, directory, library, candidate, _ = installed_recipe
    (library / artifact).write_bytes((directory / artifact).read_bytes())
    with pytest.raises(ValueError, match='unexpected files'):
        verify_package(library, candidate)
    with pytest.raises(ValueError, match='unexpected files'):
        publish_package(library.parent, candidate, evidence(candidate), {
            **upstream(candidate), 'sourceFiles': json.loads((library / 'upstream.json').read_text())['sourceFiles']})
    with pytest.raises(extensions.HTTPException):
        extensions._scan_compose_content(library / 'compose.yaml')
