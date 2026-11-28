"""Tests for documentation registry and endpoints."""

import sys
from pathlib import Path

# Add dashboard-api root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import docs_registry
from docs_registry import (
    DOC_CATEGORIES,
    DOC_REGISTRY,
    get_all_slugs,
    get_docs_catalog,
    get_path_to_slug_map,
    resolve_doc_path,
)
from routers import docs as docs_router_module
from routers.docs import _extract_headings, _parse_doc_sections, router as docs_router

# Real ODS repository root (ods/) so catalog, routing, and registry checks validate shipped files.
REAL_ODS_ROOT = Path(__file__).resolve().parents[4]

# Isolated test app to avoid starting daemon background tasks
test_app = FastAPI()
test_app.include_router(docs_router)
client = TestClient(test_app)


@pytest.fixture(autouse=True)
def real_docs_install_dir(monkeypatch):
    """Point docs_registry at the real ODS source tree rather than mock fixtures."""
    monkeypatch.setattr(docs_registry, "INSTALL_DIR", str(REAL_ODS_ROOT))
    docs_router_module._DOC_SECTION_CACHE.clear()
    yield REAL_ODS_ROOT
    docs_router_module._DOC_SECTION_CACHE.clear()


def test_docs_registry_integrity():
    """Verify registry categories and entries are properly structured."""
    catalog = get_docs_catalog()
    assert len(catalog) == len(DOC_CATEGORIES)
    for cat in catalog:
        assert "id" in cat
        assert "title" in cat
        assert "documents" in cat

    all_slugs = get_all_slugs()
    assert len(all_slugs) == len(DOC_REGISTRY)
    assert len(set(all_slugs)) == len(DOC_REGISTRY)
    assert "getting-started/overview" in all_slugs
    assert "getting-started/quickstart" in all_slugs


def test_all_registered_docs_exist_in_source_tree():
    """Every entry in DOC_REGISTRY and path_map must resolve to a real shipped markdown document."""
    all_slugs = set(get_all_slugs())
    missing: list[tuple[str, str]] = []

    for doc in DOC_REGISTRY:
        resolved = resolve_doc_path(doc["path"])
        if not resolved.is_file():
            missing.append((doc["slug"], doc["path"]))
            continue
        content = resolved.read_text(encoding="utf-8")
        assert content.strip(), f"Registered document {doc['path']} is empty"

    assert missing == [], f"Registered docs missing from source tree: {missing}"

    for alias, slug in get_path_to_slug_map().items():
        assert slug in all_slugs, f"Path alias {alias!r} points to unknown slug {slug!r}"


def test_resolve_doc_path_security():
    """Verify path traversal and non-markdown files are rejected."""
    # Valid relative path against real source tree
    path = resolve_doc_path("README.md")
    assert path.is_file()

    # Path traversal rejected
    with pytest.raises(ValueError, match="traversal|outside"):
        resolve_doc_path("../outside.md")

    # Non-markdown extension rejected
    with pytest.raises(ValueError, match="markdown"):
        resolve_doc_path("config.py")


def test_api_catalog_endpoint():
    """Test GET /api/docs/catalog."""
    response = client.get("/api/docs/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    assert data["total_documents"] == len(DOC_REGISTRY)
    assert len(data["categories"]) == 8
    assert "path_map" in data
    assert "readme.md" in data["path_map"]
    assert data["path_map"]["readme.md"] == "getting-started/overview"


def test_api_document_alias_resolution():
    """Test GET /api/docs/document resolves relative markdown paths and aliases."""
    response = client.get("/api/docs/document?slug=README.md")
    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "getting-started/overview"

    # Nested path alias e.g. getting-started/docs/HARDWARE-GUIDE.md
    response2 = client.get("/api/docs/document?slug=getting-started/docs/HARDWARE-GUIDE.md")
    assert response2.status_code == 200
    data2 = response2.json()
    assert data2["slug"] == "getting-started/hardware-guide"


def test_api_document_endpoint():
    """Test GET /api/docs/document for a known real document."""
    response = client.get("/api/docs/document?slug=getting-started/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "getting-started/overview"
    assert data["title"] == "ODS Overview & Features"
    assert data["category"] == "getting-started"
    assert "content" in data
    assert len(data["content"]) > 100
    assert "headings" in data
    assert len(data["headings"]) > 0
    assert "reading_time_minutes" in data


def test_api_document_all_registered_slugs_serve_200():
    """Every slug advertised in DOC_REGISTRY must return HTTP 200 without 404s."""
    for slug in get_all_slugs():
        response = client.get(f"/api/docs/document?slug={slug}")
        assert response.status_code == 200, f"Slug {slug!r} returned {response.status_code}"


def test_real_mode_switch_headings_deduped_and_fences_excluded():
    """Real docs/MODE-SWITCH.md deduplicates repeated headings and ignores bash fence comments."""
    response = client.get("/api/docs/document?slug=architecture/mode-switch")
    assert response.status_code == 200
    headings = response.json()["headings"]
    ids = [h["id"] for h in headings]

    # Repeated "Cloud Mode" and "Hybrid Mode" headings get matching -1 suffixes
    assert "cloud-mode" in ids
    assert "cloud-mode-1" in ids
    assert "hybrid-mode" in ids
    assert "hybrid-mode-1" in ids

    # Comments inside ```bash code blocks (e.g. "# Check current mode") must not appear as headings
    texts = [h["text"] for h in headings]
    assert not any("Check current mode" in t for t in texts)
    assert len(ids) == len(set(ids))


def test_heading_and_section_anchor_parity():
    """_extract_headings and _parse_doc_sections agree on deduplication and ignore code fences."""
    sample = (
        "# Title\n\n"
        "Intro before first section.\n\n"
        "## Overview\n\n"
        "First overview.\n\n"
        "```bash\n"
        "# Check current mode\n"
        "## Not a section heading\n"
        "echo ok\n"
        "```\n\n"
        "## Overview\n\n"
        "Second overview.\n\n"
        "~~~python\n"
        "# Tilde fence comment\n"
        "~~~\n\n"
        "## Overview\n\n"
        "Third overview.\n"
    )
    headings = _extract_headings(sample)
    sections = _parse_doc_sections(sample)

    assert [(h["text"], h["id"]) for h in headings] == [
        ("Title", "title"),
        ("Overview", "overview"),
        ("Overview", "overview-1"),
        ("Overview", "overview-2"),
    ]
    assert [(s["heading"], s["anchor"]) for s in sections if s["heading"]] == [
        ("Title", "title"),
        ("Overview", "overview"),
        ("Overview", "overview-1"),
        ("Overview", "overview-2"),
    ]


def test_nested_readme_and_unregistered_docs_never_misroute():
    """Nested READMEs resolve strictly by full path and unregistered files return 404 (never root README/LICENSING)."""
    # 1. Registered nested README resolves to its own Open WebUI entry, not root README.md
    ow_resp = client.get("/api/docs/document?slug=extensions/services/open-webui/README.md")
    assert ow_resp.status_code == 200
    ow_data = ow_resp.json()
    assert ow_data["slug"] == "extensions/open-webui"
    assert ow_data["path"] == "extensions/services/open-webui/README.md"
    assert "Open WebUI" in ow_data["content"]

    # 2. Unregistered nested READMEs must 404 instead of falling back to root README.md
    for unregistered_readme in (
        "extensions/schema/README.md",
        "extensions/library/services/aider/README.md",
        "docs/README.md",
        "extensions/README.md",
    ):
        resp = client.get(f"/api/docs/document?slug={unregistered_readme}")
        assert resp.status_code == 404, f"{unregistered_readme} unexpectedly resolved to {resp.json().get('slug')!r}"

    # 3. Unregistered files (e.g. vendor notices/licenses) must 404 instead of falling back to LICENSING.md
    for unregistered_doc in (
        "vendor/pixel/THIRD_PARTY_NOTICES.md",
        "vendor/pixel/LICENSE.md",
        "docs/FAQ.md",
    ):
        resp = client.get(f"/api/docs/document?slug={unregistered_doc}")
        assert resp.status_code == 404, f"{unregistered_doc} unexpectedly resolved to {resp.json().get('slug')!r}"


def test_all_relative_doc_links_never_misroute_to_wrong_file():
    """Relative links across all shipped docs never resolve via find_doc_by_slug to a different file."""
    import posixpath
    import re
    from routers.docs import _iter_markdown_lines

    link_re = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
    checked = 0

    for doc in DOC_REGISTRY:
        src_path = doc["path"]
        src_dir = posixpath.dirname(src_path)
        content = (REAL_ODS_ROOT / src_path).read_text(encoding="utf-8")
        for line, in_fence in _iter_markdown_lines(content):
            if in_fence:
                continue
            for href in link_re.findall(line):
                if not href or href.startswith("#") or re.match(r"^(https?:|//|mailto:|tel:)", href, re.I):
                    continue
                target, _, _ = href.partition("#")
                if not target or target.startswith("/"):
                    continue
                rel = posixpath.normpath(posixpath.join(src_dir, target))
                if rel.startswith(".."):
                    continue
                checked += 1
                matched = docs_registry.find_doc_by_slug(rel)
                if matched is not None:
                    assert matched["path"].lower() == rel.lower(), (
                        f"Link {href!r} in {src_path} resolved {rel!r} to wrong file {matched['path']!r}"
                    )

    assert checked >= 400


def test_api_document_not_found():
    """Test GET /api/docs/document with invalid slug returns 404."""
    response = client.get("/api/docs/document?slug=non-existent-doc")
    assert response.status_code == 404


def test_api_search_endpoint():
    """Test GET /api/docs/search matches real documentation titles and in-file sections."""
    response = client.get("/api/docs/search?q=quickstart")
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert len(data["results"]) > 0
    slugs = [r["slug"] for r in data["results"]]
    assert "getting-started/quickstart" in slugs

    # Natural-language query with stop words still matches real quickstart doc
    resp_nl = client.get("/api/docs/search?q=how+to+install")
    assert resp_nl.status_code == 200
    qs_nl = next((r for r in resp_nl.json()["results"] if r["slug"] == "getting-started/quickstart"), None)
    assert qs_nl is not None
    assert qs_nl["match_type"] == "title"


def test_api_search_section_cache_refresh_on_disk_change(tmp_path, monkeypatch):
    """Test _DOC_SECTION_CACHE refreshes immediately when a file is updated on disk."""
    # Copy real docs minimally only for simulating an isolated atomic file edit without mutating the repo.
    monkeypatch.setattr(docs_registry, "INSTALL_DIR", str(tmp_path))
    docs_router_module._DOC_SECTION_CACHE.clear()
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    hw_file = docs_dir / "HARDWARE-GUIDE.md"
    hw_file.write_text(
        "# Hardware Guide\n\n## GPU Sizing\n\nChoose tensor_parallelism wisely.\n",
        encoding="utf-8",
    )

    resp1 = client.get("/api/docs/search?q=tensor_parallelism")
    assert resp1.status_code == 200
    hw1 = next((r for r in resp1.json()["results"] if r["slug"] == "getting-started/hardware-guide"), None)
    assert hw1 is not None
    assert hw1["match_type"] == "section"
    assert hw1["sections"][0]["anchor"] == "gpu-sizing"

    hw_file.write_text(
        "# Hardware Guide\n\n## VRAM Budgets\n\nEstimate kv_cache_quant footprint.\n",
        encoding="utf-8",
    )
    resp2 = client.get("/api/docs/search?q=kv_cache_quant")
    assert resp2.status_code == 200
    hw2 = next((r for r in resp2.json()["results"] if r["slug"] == "getting-started/hardware-guide"), None)
    assert hw2 is not None
    assert hw2["sections"][0]["anchor"] == "vram-budgets"
