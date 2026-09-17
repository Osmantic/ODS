"""Contract tests for host/extension_search.py.

The Pixel agent's extension search is a deliberately read-only projection of
the operator-owned catalog: the file must be root-owned 0640, entries are
schema-locked, and the query surface is a bounded regex so nothing the agent
asks for can read arbitrary paths or exhaust the host. These tests pin the
custody, schema, and ranking contract.
"""
import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "host" / "extension_search.py"
SPEC = importlib.util.spec_from_file_location("extension_search", MODULE)
search = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(search)

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0, reason="catalog custody checks require uid 0 files")

CATALOG_KEYS = {
    "schemaVersion": 1,
    "kind": "ods-pixel-extension-catalog",
    "sourceSha256": "a" * 64,
}


def entry(ext_id="fah", name="Fah TTS", **kw):
    doc = {
        "id": ext_id,
        "name": name,
        "description": "Local text to speech",
        "category": "voice",
        "gpuBackends": ["amd"],
        "dependsOn": [],
        "requiredConfiguration": ["FAH_MODEL"],
        "optionalConfiguration": [],
        "tags": ["tts", "voice"],
        "featureNames": ["speech"],
    }
    doc.update(kw)
    return doc


def write_catalog(tmp_path, extensions=None, mode=0o640, **kw):
    doc = dict(CATALOG_KEYS, extensions=extensions if extensions is not None
               else [entry()])
    doc.update(kw)
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(doc))
    os.chmod(path, mode)
    return path


class TestLoadCatalogCustody:
    def test_valid_catalog_loads(self, tmp_path):
        path = write_catalog(tmp_path)
        entries = search._load_catalog(path)
        assert entries[0]["id"] == "fah"
        assert entries[0]["catalogSource"] == "library"
        assert entries[0]["configurationScope"] == (
            "declared-environment-keys")

    def test_missing_catalog(self, tmp_path):
        with pytest.raises(search.CatalogError, match="unavailable"):
            search._load_catalog(tmp_path / "absent.json")

    @pytest.mark.parametrize("mode", [0o600, 0o644, 0o660, 0o777])
    def test_mode_must_be_exactly_0640(self, tmp_path, mode):
        path = write_catalog(tmp_path, mode=mode)
        with pytest.raises(search.CatalogError, match="custody"):
            search._load_catalog(path)

    def test_symlink_rejected(self, tmp_path):
        real = write_catalog(tmp_path)
        link = tmp_path / "link.json"
        link.symlink_to(real)
        with pytest.raises(search.CatalogError, match="custody"):
            search._load_catalog(link)

    def test_directory_rejected(self, tmp_path):
        with pytest.raises(search.CatalogError, match="custody"):
            search._load_catalog(tmp_path)

    def test_oversized_rejected(self, tmp_path):
        path = tmp_path / "catalog.json"
        path.write_text(" " * (search.MAX_CATALOG_BYTES + 1))
        os.chmod(path, 0o640)
        with pytest.raises(search.CatalogError, match="custody"):
            search._load_catalog(path)


class TestLoadCatalogSchema:
    @pytest.mark.parametrize("mutation", [
        {"schemaVersion": 2},
        {"kind": "other"},
        {"sourceSha256": "x" * 64},      # must be hex
        {"sourceSha256": "A" * 64},
        {"extra": "key"},
    ])
    def test_root_schema(self, tmp_path, mutation):
        with pytest.raises(search.CatalogError):
            search._load_catalog(write_catalog(tmp_path, **mutation))

    def test_missing_root_key(self, tmp_path):
        doc = dict(CATALOG_KEYS, extensions=[entry()])
        del doc["kind"]
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps(doc))
        os.chmod(path, 0o640)
        with pytest.raises(search.CatalogError, match="schema"):
            search._load_catalog(path)

    @pytest.mark.parametrize("extensions", [[], "x", 5])
    def test_extensions_shape(self, tmp_path, extensions):
        with pytest.raises(search.CatalogError):
            search._load_catalog(write_catalog(tmp_path, extensions=extensions))

    def test_extensions_none_rejected(self, tmp_path):
        doc = dict(CATALOG_KEYS, extensions=None)
        path = tmp_path / "catalog.json"
        path.write_text(json.dumps(doc))
        os.chmod(path, 0o640)
        with pytest.raises(search.CatalogError):
            search._load_catalog(path)

    def test_entry_scope_enforced(self, tmp_path):
        # Both optional scope keys must be present to reach the value check.
        with pytest.raises(search.CatalogError, match="scope"):
            search._load_catalog(write_catalog(tmp_path, [entry(
                catalogSource="third-party",
                configurationScope="declared-environment-keys")]))
        with pytest.raises(search.CatalogError, match="scope"):
            search._load_catalog(write_catalog(tmp_path, [entry(
                catalogSource="library",
                configurationScope="arbitrary-env")]))

    def test_partial_scope_keys_rejected_as_schema(self, tmp_path):
        with pytest.raises(search.CatalogError, match="entry schema"):
            search._load_catalog(write_catalog(
                tmp_path, [entry(catalogSource="library")]))

    @pytest.mark.parametrize("ext_id", [
        "Bad-Case", " spaces ", "x" * 65, "a b", "", "-lead",
    ])
    def test_extension_id_charset(self, tmp_path, ext_id):
        with pytest.raises(search.CatalogError, match="extension id"):
            search._load_catalog(write_catalog(
                tmp_path, [entry(ext_id=ext_id)]))

    def test_duplicate_id_rejected(self, tmp_path):
        with pytest.raises(search.CatalogError, match="extension id"):
            search._load_catalog(write_catalog(
                tmp_path, [entry(), entry()]))

    def test_entry_extra_key_rejected(self, tmp_path):
        bad = dict(entry(), secretEnv="KEY")
        with pytest.raises(search.CatalogError, match="entry schema"):
            search._load_catalog(write_catalog(tmp_path, [bad]))

    @pytest.mark.parametrize("field", ["name", "description", "category"])
    def test_entry_string_bounds(self, tmp_path, field):
        with pytest.raises(search.CatalogError):
            search._load_catalog(write_catalog(
                tmp_path, [entry(**{field: "x" * 2000})]))
        with pytest.raises(search.CatalogError):
            search._load_catalog(write_catalog(
                tmp_path, [entry(**{field: "line\nbreak"})]))

    def test_string_list_duplicate_items_rejected(self, tmp_path):
        with pytest.raises(search.CatalogError, match="duplicate"):
            search._load_catalog(write_catalog(
                tmp_path, [entry(tags=["tts", "tts"])]))


class TestMatches:
    ENTRIES = [
        entry("fah", "Fah TTS", tags=["tts"]),
        entry("comfyui", "ComfyUI", description="Image generation",
              category="image", tags=["sdxl"], featureNames=["image"]),
        entry("searxng", "SearXNG", category="search", tags=["meta"]),
    ]

    def test_all_returns_sorted_by_name(self):
        result = search._matches(self.ENTRIES, "all")
        assert [e["id"] for e in result] == ["comfyui", "fah", "searxng"]

    def test_exact_id_beats_prefix(self):
        result = search._matches(self.ENTRIES, "fah")
        assert result[0]["id"] == "fah"

    def test_exact_name_scores_below_id(self):
        entries = [entry("other", "Fah"), entry("fah", "Other")]
        result = search._matches(entries, "fah")
        assert result[0]["id"] == "fah"  # id exact (100) beats name (95)

    def test_prefix_scores_above_partial(self):
        entries = [entry("voicebox", "VoiceBox", tags=["fah"]),
                   entry("fahad", "Fahad")]
        result = search._matches(entries, "fah")
        assert result[0]["id"] == "fahad"

    def test_multi_term_requires_all(self):
        result = search._matches(self.ENTRIES, "image generation")
        assert [e["id"] for e in result] == ["comfyui"]
        assert search._matches(self.ENTRIES, "image nonexistent") == []

    def test_casefold_and_whitespace(self):
        assert search._matches(self.ENTRIES, "  FAH  ")[0]["id"] == "fah"

    def test_no_match_returns_empty(self):
        assert search._matches(self.ENTRIES, "zzz") == []

    def test_tags_and_features_searched(self):
        assert search._matches(self.ENTRIES, "sdxl")[0]["id"] == "comfyui"
        assert search._matches(self.ENTRIES, "speech")[0]["id"] == "fah"


class TestMain:
    def test_usage(self):
        with pytest.raises(search.CatalogError, match="usage"):
            search.main([])

    def test_catalog_path_locked(self):
        with pytest.raises(search.CatalogError, match="not permitted"):
            search.main(["/etc/passwd", "all"])
        with pytest.raises(search.CatalogError, match="not permitted"):
            search.main(["relative/catalog.json", "all"])
        with pytest.raises(search.CatalogError, match="not permitted"):
            search.main(
                ["/opt/pixel-ops-broker/../other.json", "all"])

    @pytest.mark.parametrize("query", [
        "", "x" * 81, "a;b", "a|b", "$(x)", "a\nb",
    ])
    def test_query_charset(self, query):
        with pytest.raises(search.CatalogError, match="invalid"):
            search.main(
                ["/opt/pixel-ops-broker/ods-extension-catalog.json", query])

    def test_traversal_looking_query_is_inert(self):
        # '../../etc' satisfies the query charset — safety comes from the
        # fixed catalog path, so it reaches catalog loading, not the fs.
        with pytest.raises(search.CatalogError, match="unavailable"):
            search.main(
                ["/opt/pixel-ops-broker/ods-extension-catalog.json",
                 "../../etc"])
