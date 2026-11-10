"""API endpoints for the ODS Documentation Portal (/docs)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from docs_registry import (
    DOC_CATEGORIES,
    DOC_REGISTRY,
    find_doc_by_slug,
    get_all_slugs,
    get_docs_catalog,
    get_path_to_slug_map,
    resolve_doc_path,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/docs", tags=["documentation"])

_HEADING_REGEX = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_SLUGIFY_CLEAN_REGEX = re.compile(r"[^\w\s-]", re.UNICODE)


def _slugify(text: str) -> str:
    """Generate a clean URL anchor from heading text."""
    clean = _SLUGIFY_CLEAN_REGEX.sub("", text).strip().lower()
    return re.sub(r"[-\s]+", "-", clean)


def _extract_headings(markdown_text: str) -> list[dict[str, Any]]:
    """Extract h1, h2, h3 headings from markdown text for table of contents."""
    headings: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}

    for match in _HEADING_REGEX.finditer(markdown_text):
        hashes, title = match.groups()
        level = len(hashes)
        if level > 3:
            continue
        text = title.strip()
        # Remove markdown inline links or bolding from heading text
        clean_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        clean_text = re.sub(r"[*_`]", "", clean_text)

        anchor = _slugify(clean_text)
        if not anchor:
            anchor = f"heading-{len(headings) + 1}"

        if anchor in seen_ids:
            seen_ids[anchor] += 1
            anchor = f"{anchor}-{seen_ids[anchor]}"
        else:
            seen_ids[anchor] = 0

        headings.append({
            "level": level,
            "text": clean_text,
            "id": anchor,
        })

    return headings


def _get_adjacent_docs(slug: str) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """Return prev and next doc metadata for bottom pagination."""
    all_slugs = get_all_slugs()
    try:
        idx = all_slugs.index(slug)
    except ValueError:
        return None, None

    prev_doc = None
    next_doc = None

    if idx > 0:
        prev_meta = find_doc_by_slug(all_slugs[idx - 1])
        if prev_meta:
            prev_doc = {"slug": prev_meta["slug"], "title": prev_meta["title"]}

    if idx < len(all_slugs) - 1:
        next_meta = find_doc_by_slug(all_slugs[idx + 1])
        if next_meta:
            next_doc = {"slug": next_meta["slug"], "title": next_meta["title"]}

    return prev_doc, next_doc


@router.get("/catalog")
def get_catalog() -> dict[str, Any]:
    """Return documentation categories, list of all docs, path-to-slug mapping, and ordering."""
    return {
        "categories": get_docs_catalog(),
        "total_documents": len(DOC_REGISTRY),
        "path_map": get_path_to_slug_map(),
    }


@router.get("/document")
def get_document(slug: str = Query(..., description="Unique slug for the document")) -> dict[str, Any]:
    """Retrieve full markdown content, headings, and metadata for a document."""
    meta = find_doc_by_slug(slug)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Document with slug '{slug}' not found")

    try:
        file_path = resolve_doc_path(meta["path"])
    except ValueError as exc:
        logger.warning("Rejected invalid doc path for %s: %s", slug, exc)
        raise HTTPException(status_code=400, detail="Invalid document path") from exc

    if not file_path.is_file():
        logger.error("Documentation file not found at %s for slug %s", file_path, slug)
        raise HTTPException(
            status_code=404,
            detail=f"Documentation file '{meta['path']}' is not available on the server",
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.exception("Failed to read documentation file %s", file_path)
        raise HTTPException(status_code=500, detail="Failed to read documentation file") from exc

    headings = _extract_headings(content)
    prev_doc, next_doc = _get_adjacent_docs(meta["slug"])

    category_info = next((c for c in DOC_CATEGORIES if c["id"] == meta["category"]), None)
    category_title = category_info["title"] if category_info else meta["category"]

    # Calculate word count and estimated reading time (~200 wpm)
    word_count = len(content.split())
    reading_time_mins = max(1, round(word_count / 200))

    return {
        "slug": meta["slug"],
        "title": meta["title"],
        "description": meta.get("description", ""),
        "category": meta["category"],
        "category_title": category_title,
        "path": meta["path"],
        "content": content,
        "headings": headings,
        "word_count": word_count,
        "reading_time_minutes": reading_time_mins,
        "prev": prev_doc,
        "next": next_doc,
    }


@router.get("/search")
def search_documentation(q: str = Query(..., min_length=2, description="Search query string")) -> dict[str, Any]:
    """Search document titles, descriptions, and categories."""
    query = q.lower().strip()
    results: list[dict[str, Any]] = []

    for doc in DOC_REGISTRY:
        score = 0
        title_lower = doc["title"].lower()
        desc_lower = doc.get("description", "").lower()
        slug_lower = doc["slug"].lower()

        if query in title_lower:
            score += 10
        if query in slug_lower:
            score += 5
        if query in desc_lower:
            score += 3

        if score > 0:
            category_info = next((c for c in DOC_CATEGORIES if c["id"] == doc["category"]), None)
            results.append({
                "slug": doc["slug"],
                "title": doc["title"],
                "description": doc.get("description", ""),
                "category": doc["category"],
                "category_title": category_info["title"] if category_info else doc["category"],
                "score": score,
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return {"query": q, "results": results[:20]}
