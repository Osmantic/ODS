"""Exercise native retained selection without Bash, Docker or an installation."""
import importlib.util
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ODS = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('retained', ODS / 'scripts/windows-retained-extensions.py')
retained = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(retained)


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / 'retained install λ'
    (root / 'scripts').mkdir(parents=True)
    for name in ('windows-retained-extensions.py', 'resolve-compose-stack.sh', 'compose-cache-policy.py'):
        shutil.copyfile(ODS / 'scripts' / name, root / 'scripts' / name)
    (root / 'docker-compose.base.yml').write_text('services:\n  dashboard: {image: dashboard}\n')
    (root / 'windows-overlay.yml').write_text('services:\n  dashboard: {restart: unless-stopped}\n')
    recipe = root / 'data/user-extensions/actual-budget'
    shutil.copytree(ODS / 'extensions/library/services/actual-budget', recipe)
    return root


FLAGS = ['--env-file', '.env', '-f', 'docker-compose.base.yml', '-f', 'windows-overlay.yml']
ACTUAL = 'data/user-extensions/actual-budget/compose.yaml'


@pytest.mark.parametrize('backend,mode', [('nvidia', 'local'), ('amd', 'local'), ('none', 'cloud'), ('cpu', 'local')])
def test_actual_budget_retained_without_reselecting_windows_stack(installed, backend, mode):
    # A bundled optional recipe must not override the installer's feature plan.
    shutil.copytree(installed / 'data/user-extensions/actual-budget', installed / 'extensions/services/unselected')
    (installed / 'docker-compose.override.yml').write_text('services: {owner: {image: owner}}')
    before = {p: p.read_bytes() for p in (installed / 'data/user-extensions').rglob('*') if p.is_file()}
    assert retained.resolve(installed, FLAGS, backend=backend, mode=mode) == FLAGS + ['-f', ACTUAL]
    assert all(path.read_bytes() == data for path, data in before.items())


def test_disabled_recipe_and_overlay_stay_disabled(installed):
    recipe = installed / 'data/user-extensions/actual-budget'
    (recipe / 'compose.yaml').rename(recipe / 'compose.yaml.disabled')
    (recipe / 'compose.nvidia.yaml').write_text('services: {actual-budget: {privileged: true}}')
    assert retained.resolve(installed, FLAGS, backend='nvidia') == FLAGS
    assert (recipe / 'compose.yaml.disabled').is_file()
    assert not (recipe / 'compose.yaml').exists()


def test_manifest_custom_compose_and_compatible_overlay(installed):
    recipe = installed / 'data/user-extensions/actual-budget'
    (recipe / 'compose.yaml').rename(recipe / 'custom.yaml')
    manifest = recipe / 'manifest.yaml'
    manifest.write_text(manifest.read_text().replace('compose_file: compose.yaml', 'compose_file: custom.yaml'))
    (recipe / 'compose.nvidia.yaml').write_text('services: {actual-budget: {environment: {EXAMPLE: yes}}}')
    assert retained.resolve(installed, FLAGS, backend='nvidia') == FLAGS + [
        '-f', ACTUAL.replace('compose.yaml', 'custom.yaml'), '-f', ACTUAL.replace('compose.yaml', 'compose.nvidia.yaml')]


@pytest.mark.parametrize('compose', [
    'services: {actual-budget: {image: app, privileged: true}}',
    'services: {actual-budget: {image: app, volumes: ["/var/run/docker.sock:/socket"]}}',
    'services: {dashboard: {image: hostile}}',
    'services: {actual-budget: {image: app, depends_on: [unselected]}}',
])
def test_unsafe_or_unresolvable_active_recipe_aborts(installed, compose):
    (installed / ACTUAL).write_text(compose)
    with pytest.raises(ValueError):
        retained.resolve(installed, FLAGS)


@pytest.mark.parametrize('manifest', [
    'schema_version: invalid\nservice: {compose_file: compose.yaml}',
    'schema_version: ods.services.v1\nservice: scalar',
    'schema_version: ods.services.v1\nservice: {compose_file: ../../outside.yml}',
    'schema_version: ods.services.v1\nservice: {compose_file: []}',
    '[invalid',
])
def test_invalid_active_manifest_aborts(installed, manifest):
    (installed / 'data/user-extensions/actual-budget/manifest.yaml').write_text(manifest)
    with pytest.raises(ValueError):
        retained.resolve(installed, FLAGS)


@pytest.mark.parametrize('target', ['compose.yaml', 'manifest.yaml'])
def test_recipe_symlink_cannot_borrow_other_source(installed, target):
    path = installed / 'data/user-extensions/actual-budget' / target
    outside = installed / ('outside-' + target)
    path.rename(outside)
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip('Windows symlink privilege unavailable')
    with pytest.raises(ValueError):
        retained.resolve(installed, FLAGS)


def test_missing_pyyaml_aborts_native_entrypoint(installed):
    run = subprocess.run([sys.executable, '-S', str(installed / 'scripts/windows-retained-extensions.py'),
                          '--install-dir', str(installed)], input=json.dumps(FLAGS), capture_output=True, text=True)
    assert run.returncode == 1
    assert 'yaml' in run.stderr
    assert not run.stdout


def test_ambiguous_embedded_source_is_rejected(installed):
    resolver = installed / 'scripts/resolve-compose-stack.sh'
    resolver.write_text(resolver.read_text() + "\n# <<'PY'\n")
    with pytest.raises(ValueError, match='ambiguous'):
        retained.resolve(installed, FLAGS)


def test_json_protocol_supports_bom_and_unicode(installed):
    run = subprocess.run([sys.executable, '-X', 'utf8', str(installed / 'scripts/windows-retained-extensions.py'),
                          '--install-dir', str(installed)], input=('\ufeff' + json.dumps(FLAGS)).encode(), capture_output=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == FLAGS + ['-f', ACTUAL]


@pytest.mark.parametrize('unsafe', [False, True])
def test_existing_shell_resolver_mode_keeps_its_behavior(installed, monkeypatch, unsafe):
    if unsafe:
        (installed / ACTUAL).write_text('services: {actual-budget: {image: app, privileged: true}}')
    source = (installed / 'scripts/resolve-compose-stack.sh').read_text()
    body = source.split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    monkeypatch.setattr(sys, 'argv', ['resolver', str(installed), '1', 'cpu', '', 'false',
                                    'false', '1', 'local', '', str(installed), 'false'])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(compile(body, 'resolver', 'exec'), {'__name__': '__main__'})
    assert output.getvalue().startswith('-f docker-compose.base.yml')
    assert ('actual-budget' in output.getvalue()) is (not unsafe)
