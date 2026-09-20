import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "generate-extensions-catalog.py"


@pytest.fixture()
def generator():
    spec = importlib.util.spec_from_file_location("generate_extensions_catalog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_invalid_manifest_fails_with_manifest_path(generator, tmp_path):
    library = tmp_path / "services"
    bad = library / "broken"
    bad.mkdir(parents=True)
    (bad / "manifest.yaml").write_text(
        "schema_version: ods.services.v1\nservice: not-a-mapping\n",
        encoding="utf-8",
    )

    with pytest.raises(generator.CatalogGenerationError, match="broken.manifest.yaml"):
        generator.generate_catalog(library)


def test_valid_manifests_keep_sorted_catalog_entries(generator, tmp_path):
    library = tmp_path / "services"
    for service_id in ("zulu", "alpha"):
        service = library / service_id
        service.mkdir(parents=True)
        (service / "manifest.yaml").write_text(
            f"schema_version: ods.services.v1\nservice:\n  id: {service_id}\n",
            encoding="utf-8",
        )

    entries = generator.generate_catalog(library)
    assert [entry["id"] for entry in entries] == ["alpha", "zulu"]
