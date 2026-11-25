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
    resolve_doc_path,
)
from routers.docs import router as docs_router

# Isolated test app to avoid starting daemon background tasks
test_app = FastAPI()
test_app.include_router(docs_router)
client = TestClient(test_app)


@pytest.fixture(autouse=True)
def mock_docs_install_dir(tmp_path, monkeypatch):
    """Provide a test install directory with mock markdown docs matching DOC_REGISTRY."""
    monkeypatch.setattr(docs_registry, "INSTALL_DIR", str(tmp_path))

    # Create root README.md, QUICKSTART.md, FAQ.md
    (tmp_path / "README.md").write_text("# ODS Overview\n\n## Features\n\nContent here.\n", encoding="utf-8")
    (tmp_path / "QUICKSTART.md").write_text("# Quickstart\n\n## Installation\n\nRun install.\n", encoding="utf-8")
    (tmp_path / "FAQ.md").write_text("# FAQ\n\n## Questions\n\nAnswer.\n", encoding="utf-8")

    # Create docs/ directory
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "HARDWARE-GUIDE.md").write_text("# Hardware Guide\n\n## GPU Sizing\n\nDetails.\n", encoding="utf-8")
    (docs_dir / "SETUP-CARD.md").write_text("# Setup Card\n\n## Steps\n\nChecklist.\n", encoding="utf-8")

    return tmp_path


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
    assert "getting-started/overview" in all_slugs
    assert "getting-started/quickstart" in all_slugs


def test_resolve_doc_path_security():
    """Verify path traversal and non-markdown files are rejected."""
    # Valid relative path
    path = resolve_doc_path("README.md")
    assert path.is_file()

    # Path traversal rejected
    try:
        resolve_doc_path("../outside.md")
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "traversal" in str(exc).lower() or "outside" in str(exc).lower()

    # Non-markdown extension rejected
    try:
        resolve_doc_path("config.py")
        assert False, "Should have raised ValueError"
    except ValueError as exc:
        assert "markdown" in str(exc).lower()


def test_api_catalog_endpoint():
    """Test GET /api/docs/catalog."""
    response = client.get("/api/docs/catalog")
    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    assert data["total_documents"] >= 30
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
    """Test GET /api/docs/document for a known document."""
    response = client.get("/api/docs/document?slug=getting-started/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["slug"] == "getting-started/overview"
    assert data["title"] == "ODS Overview & Features"
    assert data["category"] == "getting-started"
    assert "content" in data
    assert len(data["content"]) > 10
    assert "headings" in data
    assert any(h["text"] == "Features" for h in data["headings"])
    assert "reading_time_minutes" in data


def test_api_document_not_found():
    """Test GET /api/docs/document with invalid slug returns 404."""
    response = client.get("/api/docs/document?slug=non-existent-doc")
    assert response.status_code == 404


def test_api_search_endpoint():
    """Test GET /api/docs/search query matches titles and metadata."""
    response = client.get("/api/docs/search?q=quickstart")
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert len(data["results"]) > 0
    slugs = [r["slug"] for r in data["results"]]
    assert "getting-started/quickstart" in slugs


def test_api_search_in_file_sections_and_live_refresh(tmp_path):
    """Test GET /api/docs/search matches in-file headings, body text, and reloads edited files."""
    # 1. Heading match inside QUICKSTART.md ("## Installation")
    resp = client.get("/api/docs/search?q=installation")
    assert resp.status_code == 200
    results = resp.json()["results"]
    qs = next((r for r in results if r["slug"] == "getting-started/quickstart"), None)
    assert qs is not None
    assert any(sec["anchor"] == "installation" and "Installation" in sec["heading"] for sec in qs["sections"])

    # 2. Body text match inside HARDWARE-GUIDE.md ("## GPU Sizing")
    (tmp_path / "docs" / "HARDWARE-GUIDE.md").write_text(
        "# Hardware Guide\n\n## GPU Sizing\n\nChoose tensor_parallelism wisely for large clusters.\n",
        encoding="utf-8",
    )
    resp2 = client.get("/api/docs/search?q=tensor_parallelism")
    assert resp2.status_code == 200
    results2 = resp2.json()["results"]
    hw = next((r for r in results2 if r["slug"] == "getting-started/hardware-guide"), None)
    assert hw is not None
    assert hw["match_type"] == "section"
    assert hw["sections"][0]["anchor"] == "gpu-sizing"
    assert "tensor_parallelism" in hw["sections"][0]["snippet"]

    # 3. Updating file on disk refreshes section cache immediately
    (tmp_path / "docs" / "HARDWARE-GUIDE.md").write_text(
        "# Hardware Guide\n\n## VRAM Budgets\n\nEstimate kv_cache_quant footprint accurately.\n",
        encoding="utf-8",
    )
    resp3 = client.get("/api/docs/search?q=kv_cache_quant")
    assert resp3.status_code == 200
    hw3 = next((r for r in resp3.json()["results"] if r["slug"] == "getting-started/hardware-guide"), None)
    assert hw3 is not None
    assert hw3["sections"][0]["anchor"] == "vram-budgets"

    # 4. Natural-language query with stop words ("how to install") still matches doc titles & sections
    resp4 = client.get("/api/docs/search?q=how+to+install")
    assert resp4.status_code == 200
    qs4 = next((r for r in resp4.json()["results"] if r["slug"] == "getting-started/quickstart"), None)
    assert qs4 is not None
    assert qs4["match_type"] == "title"
