"""Maintainer-supplied Google application custody; no real registration/account."""
import copy
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_connections import catalog, google_app, oauth  # noqa: E402

CLIENT = {'installed': {'client_id': '100000000000-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.apps.googleusercontent.com',
    'client_secret': 'synthetic-deployment-client-only', 'project_id': 'ods-fixture-only',
    'auth_uri': oauth.GOOGLE_AUTH_URI, 'token_uri': oauth.GOOGLE_TOKEN_URI}}


def envelope():
    return {'kind': google_app.KIND, 'schemaVersion': 1, 'client': copy.deepcopy(CLIENT)}


@pytest.fixture
def deployment(tmp_path):
    if os.name != 'posix':
        pytest.skip('POSIX Root configuration custody')
    tmp_path.chmod(0o700)
    parent = tmp_path / 'etc-ods'
    parent.mkdir(mode=0o700)
    path = parent / 'portal-google-app.json'
    path.write_text(json.dumps(envelope()))
    path.chmod(0o600)
    return path


def test_deployment_client_uses_existing_fixed_google_desktop_contract():
    assert google_app.validate(envelope()) == oauth.parse_client(CLIENT)
    value = envelope()
    value['client']['installed']['auth_uri'] = 'https://untrusted.example/authorize'
    with pytest.raises(google_app.ManagedAppError, match='google-app-config-invalid'):
        google_app.validate(value)


@pytest.mark.parametrize('field,value', [('schemaVersion', True), ('schemaVersion', 2),
    ('kind', 'personal-owner-client'), ('verifiedByGoogle', True), ('client', {'web': {}})])
def test_local_configuration_cannot_assert_google_verification_or_another_client_type(field, value):
    data = envelope()
    data[field] = value
    with pytest.raises(google_app.ManagedAppError, match='google-app-config-invalid'):
        google_app.validate(data)


def test_protected_config_load_and_public_projection_never_expose_client_material(deployment):
    owners = (os.getuid(),)
    assert google_app.load(path=deployment, owners=owners) == oauth.parse_client(CLIENT)
    projected = google_app.public_status(path=deployment, owners=owners)
    assert projected == {'mode': 'managed', 'state': 'configured', 'advancedAvailable': True}
    assert google_app.validate_public(projected) == projected
    assert not any(value in json.dumps(projected) for value in oauth.parse_client(CLIENT).values())


def test_missing_deployment_does_not_use_an_owner_client_or_modify_the_parent(deployment):
    deployment.unlink()
    before = list(deployment.parent.iterdir())
    with pytest.raises(google_app.ManagedAppError, match='google-app-not-configured'):
        google_app.load(path=deployment, owners=(os.getuid(),))
    assert google_app.public_status(path=deployment, owners=(os.getuid(),)) == {
        'mode': 'managed', 'state': 'not-configured', 'advancedAvailable': True}
    assert list(deployment.parent.iterdir()) == before


@pytest.mark.parametrize('change', ['public-file', 'writable-parent', 'symlink', 'hardlink', 'foreign-owner', 'oversized'])
def test_unsafe_configuration_cannot_be_selected_or_advertised_as_configured(deployment, change):
    owners = (os.getuid(),)
    if change == 'public-file':
        deployment.chmod(0o644)
    elif change == 'writable-parent':
        deployment.parent.chmod(0o777)
    elif change == 'symlink':
        target = deployment.with_name('untrusted.json')
        deployment.rename(target)
        deployment.symlink_to(target)
    elif change == 'hardlink':
        os.link(deployment, deployment.with_name('alias.json'))
    elif change == 'foreign-owner':
        owners = (os.getuid() + 100000,)
    else:
        deployment.write_text(json.dumps({'synthetic-private': 'x' * google_app.MAX_BYTES}))
    with pytest.raises(google_app.ManagedAppError) as caught:
        google_app.load(path=deployment, owners=owners)
    assert caught.value.code in ('google-app-config-unsafe', 'google-app-config-unavailable')
    assert google_app.public_status(path=deployment, owners=owners)['state'] == 'unavailable'
    assert CLIENT['installed']['client_secret'] not in str(caught.value)


@pytest.mark.parametrize('raw', [
    json.dumps(envelope()).replace('"schemaVersion": 1', '"schemaVersion": 2, "schemaVersion": 1'),
    '{broken', 'NaN', 'null',
])
def test_invalid_or_ambiguous_json_has_only_a_fixed_error(deployment, raw):
    deployment.write_text(raw)
    with pytest.raises(google_app.ManagedAppError, match='google-app-config-invalid') as caught:
        google_app.load(path=deployment, owners=(os.getuid(),))
    assert raw not in str(caught.value)


@pytest.mark.parametrize('extra', [{'client': CLIENT}, {'clientSecret': 'private'}, {'verified': True}])
def test_public_configuration_status_rejects_extra_credential_or_verification_fields(extra):
    with pytest.raises(google_app.ManagedAppError, match='google-app-status-invalid'):
        google_app.validate_public({'mode': 'managed', 'state': 'configured', 'advancedAvailable': True, **extra})


@pytest.mark.parametrize('state', google_app.PUBLIC_STATES)
def test_managed_catalog_state_is_explicit_but_contains_no_client_file(state):
    projected = {'mode': 'managed', 'state': state, 'advancedAvailable': True}
    value = catalog.public_catalog(google_auth=projected)
    assert value[0]['auth'] == {'type': 'oauth2-pkce', 'client': 'ods-managed-desktop-app',
                              'redirects': ['loopback', 'paste'], **projected}
    assert 'client_secret' not in json.dumps(value) and 'verified' not in json.dumps(value)
    assert 'mode' not in catalog.public_catalog()[0]['auth'], 'The legacy transport shape remains compatible'
    value[0]['auth']['state'] = 'changed'
    assert catalog.public_catalog(google_auth=projected)[0]['auth']['state'] == state


def test_managed_catalog_refuses_an_untrusted_projection_with_credentials():
    with pytest.raises(google_app.ManagedAppError, match='google-app-status-invalid'):
        catalog.public_catalog(google_auth={'mode': 'managed', 'state': 'configured',
                                           'advancedAvailable': True, 'client': CLIENT})
