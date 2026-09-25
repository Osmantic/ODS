"""Fooocus crosses the bundled GPU boundary, never the uploaded-recipe boundary."""
import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from routers import extensions


ODS = Path(__file__).resolve().parents[4]
RECIPE = ODS / 'extensions/services/fooocus'


def test_fooocus_is_one_disabled_builtin_with_valid_manifest():
    manifest = yaml.safe_load((RECIPE / 'manifest.yaml').read_text(encoding='utf-8'))
    schema = json.loads((ODS / 'extensions/schema/service-manifest.v1.json').read_text(encoding='utf-8'))
    jsonschema.validate(manifest, schema)
    catalog = json.loads((ODS / 'config/extensions-catalog.json').read_text(encoding='utf-8'))['extensions']
    entries = [entry for entry in catalog if entry['id'] == 'fooocus']
    assert len(entries) == 1 and entries[0]['catalog_source'] == 'builtin'
    assert not (ODS / 'extensions/library/services/fooocus').exists()
    assert not (RECIPE / 'compose.yaml').exists()  # never enabled by adding a catalog entry


def test_fooocus_gpu_request_passes_only_existing_builtin_scanner_boundary():
    compose = RECIPE / 'compose.yaml.disabled'
    extensions._scan_compose_content(compose, skip_name_collision=True,
                                    skip_gpu_passthrough_check=True, skip_root_user_check=True)
    with pytest.raises(extensions.HTTPException) as error:
        extensions._scan_compose_content(compose, trusted=True)
    assert 'GPU passthrough' in error.value.detail
