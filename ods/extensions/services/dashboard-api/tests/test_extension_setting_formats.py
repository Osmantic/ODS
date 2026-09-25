"""Declared formats for extension settings, and the manifests that use them.

On tower1 (2026-09-25) the library Shlink recipe was installed from the
dashboard with settings that were not 64 hexadecimal characters. Its start
script rejected them, and the install ended after 90 seconds with only
"state=restarting". A declaration's format lets the dialog and the configure
endpoint refuse such a value before it is saved, naming the setting and the
expected format but never the value. A value that is already saved is only
reported (by name) as a warning: ODS never replaces a saved setting.
"""

import asyncio
import json
import re
import secrets
from pathlib import Path
from unittest.mock import AsyncMock

import jsonschema
import pytest
import yaml

from extension_install_plan import InstallPlanError, build_install_plan, configuration_fields, declares_setup_hook
from extension_recipe_validation import validate_recipe
from extension_setting_formats import (
    GENERATORS, NAMED_FORMATS, UNPORTABLE_PATTERN_RULE, SettingFormatError, conforms, distinct_conflicts,
    format_problem, parse_setting_format, setting_problems,
)
from routers import extensions
from test_extension_install_settings import _definition, _post, host  # noqa: F401  (fixture)

ODS = Path(__file__).resolve().parents[4]
SCHEMA = json.loads((ODS / 'extensions/schema/service-manifest.v1.json').read_text(encoding='utf-8'))
MANIFESTS = sorted([*(ODS / 'extensions/library/services').glob('*/manifest.yaml'),
                    *(ODS / 'extensions/services').glob('*/manifest.yaml')])
ALPHANUMERIC = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
HEX64 = 'c0ffee' + '0' * 58
HEX64_HINT = '64 hexadecimal characters (0-9, a-f)'
# Owner-chosen values with no format a check could enforce without rejecting
# values the service accepts (LightRAG falls back on its own defaults).
FREE_FORM = {'lightrag:LIGHTRAG_EMBEDDING_DIM', 'lightrag:LIGHTRAG_EMBEDDING_TOKEN_LIMIT',
             'lightrag:LIGHTRAG_MAX_TOTAL_TOKENS'}
# The recipes whose own start-up check only accepts lowercase hex.
LOWERCASE_HEX = {'nifi:NIFI_ADMIN_PASSWORD', 'nifi:NIFI_SENSITIVE_PROPS_KEY', 'orthanc:ORTHANC_PASSWORD',
                 'rest-server:REST_SERVER_PASSWORD', 'solr:SOLR_ADMIN_PASSWORD',
                 'pixel-edge:PIXEL_OPENWEBUI_KEY', 'pixel-model-relay:PIXEL_MODEL_RELAY_KEY'}


def generated(kind):
    """The dashboard's generators (ExtensionInstallSettings.jsx), in Python."""
    if kind.startswith('hex'):
        return secrets.token_hex(int(kind[3:]) // 2)
    return ''.join(secrets.choice(ALPHANUMERIC) for _ in range({'password': 24, 'token': 48}[kind]))


def generate_conforming(spec):
    # The dashboard redraws up to 64 times; every declaration must succeed
    # far more reliably than that.
    for _ in range(64):
        value = generated(spec['generate'])
        if conforms(value, spec):
            return value
    return None


# --- The format module --------------------------------------------------------


def test_named_formats_are_exact_and_hex_accepts_either_case():
    spec = parse_setting_format({'key': 'X_KEY', 'format': 'hex64', 'generate': 'hex64'})
    assert spec == {'name': 'hex64', 'patterns': ['^[0-9a-fA-F]{64}$'], 'minLength': None, 'maxLength': None,
                    'generate': 'hex64', 'distinctFrom': [], 'hint': HEX64_HINT}
    # Every recipe with a hex setting accepts upper case unless it adds a pattern.
    assert conforms(HEX64, spec) and conforms(HEX64.upper(), spec)
    for value in (HEX64[:-1], HEX64 + '0', HEX64[:-1] + 'g', HEX64 + '\n', ' ' + HEX64, ''):
        assert not conforms(value, spec)
    lowercase = parse_setting_format({'format': 'hex64', 'pattern': '^[0-9a-f]+$',
                                      'format_description': 'lowercase only'})
    assert lowercase['hint'] == HEX64_HINT + ', lowercase only'
    assert conforms(HEX64, lowercase) and not conforms(HEX64.upper(), lowercase)
    email = parse_setting_format({'format': 'email'})
    assert conforms('owner@example.com', email) and conforms('first.last+ods@mail.example.org', email)
    # PHP's FILTER_VALIDATE_EMAIL (wallabag) rejects these; so does ODS.
    for value in ('owner@localhost', 'a..b@example.com', '.a@example.com', 'a@-x.com', 'a@x_y.com',
                  'a,b@example.com', 'ü@example.com', 'a' * 65 + '@example.com', 'a@' + 'b' * 64 + '.com'):
        assert not conforms(value, email), value
    url = parse_setting_format({'format': 'url'})
    assert conforms('http://localhost:11148', url) and conforms('https://example.com/health?x=1', url)
    assert not conforms('http://user:pass@example.com', url) and not conforms('ftp://example.com', url)
    integer = parse_setting_format({'format': 'integer'})
    assert conforms('1024', integer)
    # Group IDs start at 1; the installer rejects leading zeros.
    for value in ('0', '-1', '007', '1.5', '1e3', ''):
        assert not conforms(value, integer), value


def test_lengths_count_characters_for_the_minimum_and_bytes_for_the_maximum():
    spec = parse_setting_format({'min_length': 12, 'max_length': 72})
    assert spec['hint'] == 'at least 12 characters, no more than 72 bytes'
    assert conforms('a' * 12, spec) and conforms('a' * 72, spec)
    assert not conforms('a' * 11, spec) and not conforms('a' * 73, spec)
    # Six two-byte characters are 12 bytes but only 6 characters.
    assert not conforms('é' * 6, spec)
    # 40 characters, 80 bytes: over a 72-byte limit such as bcrypt's.
    assert not conforms('é' * 40, spec)
    # A lone surrogate (valid JSON, not UTF-8) is non-conforming, not an error.
    assert not conforms('a' * 12 + '\ud800', spec)


@pytest.mark.parametrize('pattern,portable', [
    ('^[0-9]+$', True), ('^(?=.*[A-Z])(?=.*[0-9]).*$', True), ('^(?:a|b)$', True), ('^(?!-h)x$', True),
    ('^\\d+$', False), ('^\\w+$', False), ('^\\bx$', False), ('^\\p{L}+$', False), ('^\\P{L}+$', False),
    ('^(?P<x>a)$', False), ('^(?<x>a)$', False), ('^(?<=a)b$', False), ('^(?i)a$', False), ('^(?#c)a$', False),
])
def test_the_api_and_the_schema_reject_the_same_unportable_patterns(pattern, portable):
    declaration = {'key': 'X_KEY', 'pattern': pattern, 'format_description': 'x'}
    if portable:
        assert parse_setting_format(declaration)['patterns'] == [pattern]
    else:
        with pytest.raises(SettingFormatError):
            parse_setting_format(declaration)
    items = SCHEMA['properties']['service']['properties']['env_vars']['items']['properties']
    assert items['pattern']['pattern'] == UNPORTABLE_PATTERN_RULE
    assert bool(re.search(UNPORTABLE_PATTERN_RULE, pattern)) is portable


def test_a_pattern_is_explained_and_must_run_the_same_in_the_dashboard():
    spec = parse_setting_format({'pattern': '^[A-Za-z0-9]+$', 'format_description': 'letters and digits only.',
                                 'min_length': 32, 'generate': 'token'})
    assert spec['hint'] == 'letters and digits only, at least 32 characters'
    assert format_problem('APP_SECRET', spec) == 'APP_SECRET must be letters and digits only, at least 32 characters.'
    invalid = [
        {'pattern': '^[0-9]+$'},  # unexplained
        {'format_description': 'digits'},  # explains nothing
        {'pattern': '[0-9]+', 'format_description': 'digits'},  # unanchored
        {'pattern': '^[$', 'format_description': 'broken'},
        {'format': 'hex65'},
        {'generate': 'uuid'},
        {'min_length': 0}, {'min_length': True}, {'max_length': 4097},
        {'min_length': 10, 'max_length': 9},
        {'key': 'A_KEY', 'distinct_from': ['A_KEY']}, {'distinct_from': ['lower']}, {'distinct_from': 'B_KEY'},
        {'distinct_from': ['B_KEY', 'B_KEY']},
    ]
    for item in invalid:
        with pytest.raises(SettingFormatError):
            parse_setting_format(item)
    assert parse_setting_format({'key': 'X', 'required': True, 'secret': True, 'description': 'x'}) is None
    assert parse_setting_format({'key': 'X', 'generate': 'token'})['hint'] == ''
    assert conforms('anything', None) and not conforms(None, None)


def test_install_plan_fields_carry_the_format_and_reject_unusable_declarations():
    fields = configuration_fields('app', {'env_vars': [
        {'key': 'APP_KEY', 'required': True, 'secret': True, 'format': 'hex32', 'generate': 'hex32'},
        {'key': 'APP_NAME'}]}, lambda key: False)
    assert fields[0]['format']['hint'] == '32 hexadecimal characters (0-9, a-f)'
    assert fields[1]['format'] is None
    with pytest.raises(InstallPlanError):
        configuration_fields('app', {'env_vars': [{'key': 'APP_KEY', 'generate': 'uuid'}]}, lambda key: False)
    with pytest.raises(InstallPlanError):
        configuration_fields('app', {'env_vars': [{'key': 'APP_KEY', 'distinct_from': ['APP_OTHER']}]},
                             lambda key: False)
    # Presence alone never depends on a format.
    [field] = configuration_fields('app', {'env_vars': [{'key': 'APP_KEY', 'required': True, 'generate': 'uuid'}]},
                                   lambda key: False, formats=False)
    assert field['required'] is True and field['configured'] is False and field['format'] is None


def test_distinct_settings_report_the_pair_but_never_the_value():
    fields = configuration_fields('app', {'env_vars': [
        {'key': 'APP_DB_PASSWORD', 'format': 'hex64'},
        {'key': 'APP_API_KEY', 'format': 'hex64', 'distinct_from': ['APP_DB_PASSWORD']}]}, lambda key: False)
    assert fields[1]['format']['distinctFrom'] == ['APP_DB_PASSWORD']
    same = {'APP_DB_PASSWORD': HEX64, 'APP_API_KEY': HEX64}
    problems = setting_problems(fields, same.get, {'APP_API_KEY'})
    assert problems == [{'key': 'APP_API_KEY', 'expected': 'a value different from APP_DB_PASSWORD',
                         'message': 'APP_API_KEY must differ from APP_DB_PASSWORD.'}]
    # Either side of the pair being submitted is enough to check it.
    assert setting_problems(fields, same.get, {'APP_DB_PASSWORD'}) == problems
    assert setting_problems(fields, {'APP_DB_PASSWORD': HEX64, 'APP_API_KEY': 'f' * 64}.get, set(same)) == []
    assert setting_problems(fields, {'APP_API_KEY': ''}.get, {'APP_API_KEY'}) != []  # empty is not hex64
    assert distinct_conflicts(fields, {}.get) == []
    assert HEX64 not in json.dumps(problems)


def test_model_proposed_recipes_must_declare_enforceable_formats():
    schema = json.loads((ODS / 'extensions/schema/service-manifest.v1.json').read_text(encoding='utf-8'))
    manifest = {'schema_version': 'ods.services.v1', 'service': {
        'id': 'demo', 'name': 'Demo', 'port': 8080, 'health': '/health', 'type': 'docker', 'category': 'optional',
        'compose_file': 'compose.yaml', 'gpu_backends': ['cpu'],
        'env_vars': [{'key': 'DEMO_TOKEN', 'required': True, 'secret': True, 'distinct_from': ['DEMO_OTHER']}]}}
    candidate = {'repository': 'https://github.com/owner/demo', 'commit': 'a' * 40, 'manifest': manifest,
                 'compose': {'services': {'demo': {'image': 'example/demo:1'}}}}
    result = validate_recipe(candidate, schema, set(), lambda compose: [])
    codes = {issue['code'] for issue in result['errors']} if isinstance(result, dict) else set()
    assert 'configuration-declaration-invalid' in codes, result


# --- The install and plan endpoints -------------------------------------------

SHLINK_COMPOSE = (
    "services:\n  shlink:\n    image: ods/shlink:5.1.6-local-v1\n    environment:\n"
    "      DB_PASSWORD: ${SHLINK_DB_PASSWORD:?Set a 64-hex database password}\n")
SHLINK_FIELD = {"key": "SHLINK_DB_PASSWORD", "required": True, "secret": True,
                "description": "64-hex PostgreSQL password.", "format": "hex64", "generate": "hex64"}


def _saved(monkeypatch, value):
    import config
    monkeypatch.setattr(config, "_read_env_value", lambda key: value if key == "SHLINK_DB_PASSWORD" else "")


def _plan_roots(tmp_path, monkeypatch, status="not_installed"):
    roots = [tmp_path / name for name in ('user', 'builtin', 'library')]
    for key, root in zip(('USER_EXTENSIONS_DIR', 'EXTENSIONS_DIR', 'EXTENSIONS_LIBRARY_DIR'), roots):
        root.mkdir()
        monkeypatch.setattr(extensions, key, root)
    monkeypatch.setattr(extensions, 'extensions_catalog', AsyncMock(return_value={'extensions': [
        {'id': 'shlink', 'status': status, 'installable': True}]}))
    return roots


def test_reinstall_with_a_saved_nonconforming_value_warns_but_proceeds(test_client, host, monkeypatch):  # noqa: F811
    # An uninstall keeps .env values (and data volumes), and a library secret
    # cannot be cleared from the environment editor: never strand a reinstall.
    _definition(host.library, "shlink", SHLINK_COMPOSE, [SHLINK_FIELD], name="Shlink")
    _saved(monkeypatch, "private-typed-value")

    response = _post(test_client, "/api/extensions/shlink/install")

    assert response.status_code == 200
    assert host.installs == ["shlink"]
    assert "private-typed-value" not in response.text


def test_plan_warns_by_name_about_a_saved_nonconforming_value(tmp_path, monkeypatch):
    roots = _plan_roots(tmp_path, monkeypatch)
    _definition(roots[2], 'shlink', SHLINK_COMPOSE, [SHLINK_FIELD], name='Shlink')
    _saved(monkeypatch, 'private-typed-value')

    plan = asyncio.run(extensions.extension_install_plan('shlink', api_key='test'))

    step = plan['steps'][0]
    assert plan['blocked'] is False and step['action'] == 'install'
    assert step['savedConfigurationWarnings'] == ['SHLINK_DB_PASSWORD']
    assert step['configuration'][0]['format']['generate'] == 'hex64'
    assert 'private-typed-value' not in json.dumps(plan)
    # Upper-case hex is what Shlink accepts too: no warning.
    _saved(monkeypatch, HEX64.upper())
    assert asyncio.run(extensions.extension_install_plan('shlink', api_key='test'))['steps'][0][
        'savedConfigurationWarnings'] == []


def test_plan_warnings_cover_equal_saved_values_but_only_fresh_installs():
    service = {'id': 'app', 'env_vars': [
        {'key': 'APP_DB_PASSWORD', 'required': True, 'secret': True, 'format': 'hex64'},
        {'key': 'APP_API_KEY', 'required': True, 'secret': True, 'format': 'hex64',
         'distinct_from': ['APP_DB_PASSWORD']}]}
    saved = {'APP_DB_PASSWORD': HEX64, 'APP_API_KEY': HEX64}

    def step(status, **extra):
        return build_install_plan(
            'app', [{'id': 'app', 'status': status, 'installable': True}], {'app': {**service, **extra}}.__getitem__,
            lambda key: True, saved_nonconforming=lambda fields: [
                problem['key'] for problem in setting_problems(fields, saved.get, set(saved))])['steps'][0]

    fresh = step('not_installed')
    assert fresh['action'] == 'install' and fresh['savedConfigurationWarnings'] == ['APP_API_KEY']
    # An installed definition keeps its saved value; a setup hook owns its settings.
    assert step('disabled')['savedConfigurationWarnings'] == []
    assert step('not_installed', setup_hook='setup.sh')['savedConfigurationWarnings'] == []


def test_missing_settings_refusal_carries_the_format_for_the_dialog(test_client, host):  # noqa: F811
    _definition(host.library, "shlink", SHLINK_COMPOSE, [SHLINK_FIELD], name="Shlink")

    response = _post(test_client, "/api/extensions/shlink/install")

    assert response.status_code == 400
    [field] = response.json()["detail"]["configuration"]
    assert field["format"]["generate"] == "hex64"
    assert field["format"]["patterns"] == ["^[0-9a-fA-F]{64}$"]


@pytest.mark.parametrize("broken", [{"pattern": "^\\d+$", "format_description": "digits"},
                                    {"generate": "uuid"}, {"distinct_from": ["SHLINK_OTHER"]}])
def test_an_unusable_format_never_hides_a_missing_required_setting(test_client, host, broken):  # noqa: F811
    _definition(host.library, "shlink", SHLINK_COMPOSE, [{**SHLINK_FIELD, **broken}], name="Shlink")

    response = _post(test_client, "/api/extensions/shlink/install")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["missing_configuration"] == ["SHLINK_DB_PASSWORD"]
    assert detail["configuration"][0]["format"] is None
    assert host.installs == []

    extension = _definition(host.users, "shlink", SHLINK_COMPOSE, [{**SHLINK_FIELD, **broken}], enabled=False,
                            name="Shlink")
    host.progress("shlink", "error")
    assert _post(test_client, "/api/extensions/shlink/enable").status_code == 400
    assert (extension / "compose.yaml.disabled").is_file() and host.starts == []


def test_enabling_an_installed_definition_does_not_second_guess_its_saved_value(test_client, host, monkeypatch):  # noqa: F811
    # Its data may already depend on the saved value; ODS never rotates it.
    extension = _definition(host.users, "shlink", SHLINK_COMPOSE, [SHLINK_FIELD], enabled=False, name="Shlink")
    _saved(monkeypatch, "private-typed-value")

    assert _post(test_client, "/api/extensions/shlink/enable").status_code == 200
    assert (extension / "compose.yaml").is_file()


# --- The shipped manifests ----------------------------------------------------


def _service(path):
    return yaml.safe_load(path.read_text(encoding='utf-8'))['service']


@pytest.mark.parametrize('path', MANIFESTS, ids=lambda path: path.parent.name)
def test_every_manifest_declaration_is_enforceable_and_its_generator_conforms(path):
    service = _service(path)
    for field in configuration_fields(service['id'], service, lambda key: False):
        spec = field['format']
        if spec and spec['generate']:
            assert generate_conforming(spec), f"{field['key']}: generate {spec['generate']} never conforms"
        for pattern in (spec or {}).get('patterns', []):
            re.compile(pattern)


def test_every_setting_the_dialog_can_ask_for_declares_a_format_or_generator():
    """Each required setting an owner may be asked for says what it accepts.

    Setup hooks generate their own settings, so the dialog never asks for
    those. Everything else declares a format, a generator, or both, apart
    from owner-chosen values no check could describe (FREE_FORM).
    """
    unannotated, asked, generatable = set(), 0, 0
    for path in MANIFESTS:
        service = _service(path)
        if declares_setup_hook(service):
            continue
        for field in configuration_fields(service['id'], service, lambda key: False):
            if not field['required']:
                continue
            asked += 1
            generatable += bool(field['format'] and field['format']['generate'])
            if field['format'] is None:
                unannotated.add(f"{service['id']}:{field['key']}")
    assert unannotated == FREE_FORM
    assert asked >= 139 and generatable >= 116


def test_hex_descriptions_match_their_declared_length_and_case():
    for path in MANIFESTS:
        service = _service(path)
        for item in service.get('env_vars', []):
            described = re.search(r'(?i)\b(32|64|128)[- ](?:random )?hex|(32|64|128) (?:random )?hexadecimal',
                                  item.get('description', ''))
            if described and not declares_setup_hook(service):
                length = described.group(1) or described.group(2)
                name = f"{service['id']}:{item['key']}"
                assert item.get('format') == f'hex{length}', name
                assert (item.get('pattern') == '^[0-9a-f]+$') is (name in LOWERCASE_HEX), name


def test_lowercase_only_hex_matches_the_recipes_own_checks():
    for path, check in [('nifi/start.sh', '^[a-f0-9]{64}$'), ('orthanc/start.sh', '^[a-f0-9]{64}$'),
                        ('rest-server/start.sh', "'^[a-f0-9]{64}$'"), ('solr/security.py', "'[a-f0-9]{64}'")]:
        assert check in (ODS / 'extensions/library/services' / path).read_text(encoding='utf-8'), path


def test_shlink_settings_are_generatable_64_hex():
    service = _service(ODS / 'extensions/library/services/shlink/manifest.yaml')
    fields = {field['key']: field for field in configuration_fields('shlink', service, lambda key: False)}
    for key in ('SHLINK_DB_PASSWORD', 'SHLINK_API_KEY'):
        assert fields[key]['format']['name'] == 'hex64'
        assert fields[key]['format']['generate'] == 'hex64'
    assert fields['SHLINK_API_KEY']['format']['distinctFrom'] == ['SHLINK_DB_PASSWORD']
    # The start script accepts 64 hex characters in either case, and exits
    # when both secrets are equal.
    script = (ODS / 'extensions/library/services/shlink/start.sh').read_text(encoding='utf-8')
    assert "*[!0-9a-fA-F]*" in script and '"${#value}" -eq 64' in script
    assert '[ "$DB_PASSWORD" != "$INITIAL_API_KEY" ]' in script


@pytest.mark.parametrize('declaration,valid', [
    ({'key': 'X_KEY', 'format': 'hex64', 'generate': 'hex64', 'min_length': 64, 'max_length': 64}, True),
    ({'key': 'X_KEY', 'pattern': '^[a-z]+$', 'format_description': 'lowercase letters'}, True),
    ({'key': 'X_KEY', 'pattern': '^(?=.*[A-Z]).+$', 'format_description': 'with an uppercase letter'}, True),
    ({'key': 'X_KEY', 'format': 'hex63'}, False),
    ({'key': 'X_KEY', 'generate': 'uuid'}, False),
    ({'key': 'X_KEY', 'pattern': '[a-z]+', 'format_description': 'letters'}, False),
    ({'key': 'X_KEY', 'pattern': '^[a-z]+$'}, False),
    ({'key': 'X_KEY', 'pattern': '^\\d+$', 'format_description': 'digits'}, False),
    ({'key': 'X_KEY', 'pattern': '^\\p{L}+$', 'format_description': 'letters'}, False),
    ({'key': 'X_KEY', 'pattern': '^(?i)[a-z]+$', 'format_description': 'letters'}, False),
    ({'key': 'X_KEY', 'min_length': 0}, False),
    ({'key': 'X_KEY', 'max_length': '72'}, False),
])
def test_schema_accepts_only_the_declared_format_vocabulary(declaration, valid):
    manifest = {'schema_version': 'ods.services.v1', 'service': {
        'id': 'x', 'name': 'X', 'port': 8080, 'health': '/health', 'type': 'docker', 'category': 'optional',
        'gpu_backends': ['cpu'], 'env_vars': [declaration]}}
    errors = list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(manifest))
    assert (not errors) is valid, errors


def test_schema_vocabulary_matches_the_api():
    items = SCHEMA['properties']['service']['properties']['env_vars']['items']['properties']
    assert set(items['format']['enum']) == set(NAMED_FORMATS)
    assert set(items['generate']['enum']) == set(GENERATORS)
