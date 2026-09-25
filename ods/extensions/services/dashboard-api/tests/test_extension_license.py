import asyncio
import base64
import copy
import hashlib
import json

import httpx
import pytest

from extension_github import inspect_repository
from extension_license import spdx_identifiers, verified_expression_evidence
from extension_recipe_package import LicenseEvidenceError, publish_package
from test_extension_recipe_drafts import evidence as validation
from test_extension_recipe_validation import candidate


def blob(content):
    raw = content.encode()
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\x00' + raw).hexdigest()


APACHE = ('Apache License\nVersion 2.0, January 2004\n'
          'TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n'
          '2. Grant of Copyright License. ' + 'The license applies to the work. ' * 8)
BSD = ('Copyright (c) contributors.\nRedistribution and use in source and binary forms, '
       'with or without modification, are permitted provided that the following conditions are met:\n'
       '1. Redistributions of source code must retain this notice.\n'
       '2. Redistributions in binary form must reproduce this notice.\n'
       'THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS". '
       + 'No warranties are granted. ' * 8)
PYPROJECT = '[project]\nname = "example"\nlicense = "Apache-2.0 OR BSD-2-Clause"\n'


def pinned_file(path, content):
    return {'type': 'file', 'path': path, 'encoding': 'base64',
            'content': base64.b64encode(content.encode()).decode(), 'sha': blob(content)}


def composite_evidence():
    return {'metadata': {'path': 'pyproject.toml', 'blob': blob(PYPROJECT), 'content': PYPROJECT},
            'documents': [
                {'path': 'LICENSE.APACHE', 'blob': blob(APACHE), 'content': APACHE},
                {'path': 'LICENSE.BSD', 'blob': blob(BSD), 'content': BSD},
            ]}


def test_spdx_expression_accepts_allowlisted_ids_and_rejects_unknown_terms():
    assert spdx_identifiers('(Apache-2.0 OR BSD-2-Clause) AND MIT') == {
        'Apache-2.0', 'BSD-2-Clause', 'MIT'}
    for expression in ('Apache-2.0 OR Proprietary', 'Apache-2.0 WITH LLVM-exception',
                       'Apache-2.0 OR', 'Apache-2.0 OR OR MIT', '(MIT OR BSD-2-Clause',
                       'MIT; OR BSD-2-Clause'):
        with pytest.raises(ValueError):
            spdx_identifiers(expression)


def test_inspection_uses_commit_scoped_pyproject_and_both_license_texts(tmp_path):
    commit = 'a' * 40
    files = {'pyproject.toml': PYPROJECT, 'LICENSE.APACHE': APACHE, 'LICENSE.BSD': BSD}
    calls = []

    def handler(request):
        calls.append(str(request.url))
        path = request.url.path
        if path == '/repos/owner/repo':
            return httpx.Response(200, json={'full_name': 'owner/repo', 'private': False,
                                              'default_branch': 'main'})
        if path.endswith('/commits/main'):
            return httpx.Response(200, json={'sha': commit})
        assert request.url.params['ref'] == commit
        if path.endswith('/readme'):
            return httpx.Response(404)
        if path.endswith('/license'):
            return httpx.Response(200, json={
                **pinned_file('LICENSE', 'See LICENSE.APACHE and LICENSE.BSD'),
                'license': {'spdx_id': 'NOASSERTION'}})
        if path.endswith('/contents'):
            return httpx.Response(200, json=[{'name': name, 'path': name, 'type': 'file'}
                                             for name in files])
        name = path.rsplit('/', 1)[1]
        return httpx.Response(200, json=pinned_file(name, files[name]))

    result = asyncio.run(inspect_repository('https://github.com/owner/repo', tmp_path,
                                            httpx.MockTransport(handler)))
    assert result['licenseIdentifier'] == 'Apache-2.0 OR BSD-2-Clause'
    assert verified_expression_evidence(result['licenseExpressionEvidence']) == result['licenseIdentifier']
    assert len(calls) == 8
    assert all('ref=' + commit in call for call in calls[2:])


def test_composite_license_can_prepare_and_keeps_file_identity(tmp_path):
    proposal = candidate()
    upstream = {'repository': proposal['repository'], 'commit': proposal['commit'],
                'existingExtensionIds': [], 'evidenceScope': 'repository-documents-at-commit',
                'licenseIdentifier': 'Apache-2.0 OR BSD-2-Clause',
                'licenseText': 'See LICENSE.APACHE and LICENSE.BSD',
                'licenseExpressionEvidence': composite_evidence()}
    receipt = publish_package(tmp_path, proposal, validation(proposal), upstream)
    assert receipt['state'] == 'available'
    provenance = json.loads((tmp_path / 'apache-answer/upstream.json').read_text())
    assert provenance['licenseIdentifier'] == upstream['licenseIdentifier']
    assert provenance['licenseMetadataBlob'] == blob(PYPROJECT)
    assert {item['path'] for item in provenance['licenseDocuments']} == {'LICENSE.APACHE', 'LICENSE.BSD'}


@pytest.mark.parametrize('damage', [
    lambda proof: proof['documents'].pop(),
    lambda proof: proof['documents'][0].update(content='See LICENSE.APACHE'),
    lambda proof: proof['documents'][0].update(blob='0' * 40),
    lambda proof: proof['metadata'].update(content='[project]\nlicense = "MIT OR BSD-2-Clause"'),
    lambda proof: proof['metadata'].update(content='[project]\nlicense = "Apache-2.0 OR Private"'),
    lambda proof: proof['documents'][1].update(content='Unrelated text. ' * 30,
                                                blob=blob('Unrelated text. ' * 30)),
])
def test_missing_conflicting_or_unverified_license_evidence_requires_review(tmp_path, damage):
    proposal = candidate()
    proof = copy.deepcopy(composite_evidence())
    damage(proof)
    upstream = {'repository': proposal['repository'], 'commit': proposal['commit'],
                'existingExtensionIds': [], 'evidenceScope': 'repository-documents-at-commit',
                'licenseIdentifier': 'Apache-2.0 OR BSD-2-Clause',
                'licenseExpressionEvidence': proof}
    with pytest.raises(LicenseEvidenceError, match='Repository license requires review'):
        publish_package(tmp_path, proposal, validation(proposal), upstream)
    assert list(tmp_path.iterdir()) == []


def test_readme_or_metadata_string_alone_cannot_license_a_recipe(tmp_path):
    proposal = candidate()
    upstream = {'repository': proposal['repository'], 'commit': proposal['commit'],
                'existingExtensionIds': [], 'evidenceScope': 'repository-documents-at-commit',
                'licenseIdentifier': 'Apache-2.0 OR BSD-2-Clause',
                'readme': 'Apache-2.0 OR BSD-2-Clause',
                'licenseText': 'See LICENSE.APACHE and LICENSE.BSD'}
    with pytest.raises(LicenseEvidenceError):
        publish_package(tmp_path, proposal, validation(proposal), upstream)
    assert list(tmp_path.iterdir()) == []
