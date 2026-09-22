import json
from pathlib import Path

import pytest
import yaml

from extension_integration import integration_guidance


def recipe(root, *, readme='Use this API with your project.\nSee configuration.'):
    folder = root / 'demo'; folder.mkdir(parents=True)
    (folder / 'manifest.yaml').write_text(yaml.safe_dump({'service': {
        'id': 'demo', 'type': 'docker', 'container_name': 'ods-demo', 'port': 8080,
        'external_port_env': 'DEMO_PORT', 'external_port_default': 1234,
        'env_vars': [{'key': 'DEMO_PASSWORD', 'default': 'not-for-the-model'}],
    }}), encoding='utf-8')
    (folder / 'README.md').write_text(readme, encoding='utf-8')
    return folder


def test_guidance_uses_installed_recipe_and_never_environment_values(tmp_path):
    installed, library = tmp_path / 'installed', tmp_path / 'library'
    recipe(installed, readme='Installed version instructions')
    recipe(library, readme='Different library version')
    result = integration_guidance('demo', [installed, library])
    assert result['documentation'] == 'Installed version instructions'
    assert result['declaredConnection']['port'] == 8080
    assert 'not-for-the-model' not in json.dumps(result)
    assert result['connectivityVerified'] is False and result['projectIntegrationVerified'] is False
    assert result['contentTrust'] == 'untrusted-recipe-evidence'


def test_incomplete_installed_recipe_does_not_fall_back_to_other_version(tmp_path):
    installed, library = tmp_path / 'installed', tmp_path / 'library'
    (installed / 'demo').mkdir(parents=True)
    recipe(library)
    with pytest.raises(ValueError):
        integration_guidance('demo', [installed, library])


def test_documentation_is_bounded_and_missing_docs_are_not_invented(tmp_path):
    folder = recipe(tmp_path, readme='x' * 25000)
    result = integration_guidance('demo', [tmp_path])
    assert len(result['documentation']) == 24000 and result['documentationTruncated'] is True
    (folder / 'README.md').unlink()
    assert integration_guidance('demo', [tmp_path])['documentation'] is None


def test_all_catalog_definitions_project_only_bounded_declared_guidance():
    ods = Path(__file__).resolve().parents[4]
    catalog = json.loads((ods / 'config/extensions-catalog.json').read_text(encoding='utf-8'))
    rows = catalog if isinstance(catalog, list) else catalog['extensions']
    assert len(rows) == len({row['id'] for row in rows}) == 200
    count = 0
    for row in rows:
        result = integration_guidance(row['id'], [ods / 'extensions/services', ods / 'extensions/library/services'])
        if result is not None:
            count += 1
            assert result['extensionId'] == row['id']
            assert len(result['documentation'] or '') <= 24000
            assert 'env_vars' not in result['declaredConnection']
    assert count >= 169  # Built-in services without manifests expose no guessed recipe.
