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
_CODE_FENCE_REGEX = re.compile(r"```[^\n]*\n?")
_MD_LINK_REGEX = re.compile(r"!?\[([^\]]*)\]\([^)]+\)")
_WHITESPACE_REGEX = re.compile(r"\s+")

# Per-file cache keyed by resolved path -> (mtime_ns, size, parsed sections).
_DOC_SECTION_CACHE: dict[Path, tuple[int, int, list[dict[str, str]]]] = {}


def _clean_heading_text(raw_title: str) -> str:
    """Strip inline markdown syntax from a heading title."""
    clean_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", raw_title.strip())
    return re.sub(r"[*_`]", "", clean_text).strip()


def _slugify(text: str) -> str:
    """Generate a clean URL anchor from heading text."""
    clean = _SLUGIFY_CLEAN_REGEX.sub("", text).strip().lower()
    return re.sub(r"[-\s]+", "-", clean)


def _strip_markdown(text: str) -> str:
    """Convert raw markdown body text into compact plain text for search & snippets."""
    cleaned = _CODE_FENCE_REGEX.sub(" ", text)
    cleaned = _MD_LINK_REGEX.sub(r"\1", cleaned)
    cleaned = re.sub(r"^>+\s*(?:\[![A-Z]+\])?", "", cleaned, flags=re.MULTILINE)
    # Strip markdown emphasis/table punctuation without breaking snake_case identifiers.
    cleaned = re.sub(r"[`*~|#]+", " ", cleaned)
    cleaned = re.sub(r"(?<!\w)_+|_+(?!\w)", " ", cleaned)
    return _WHITESPACE_REGEX.sub(" ", cleaned).strip()


def _parse_doc_sections(markdown_text: str) -> list[dict[str, str]]:
    """Split markdown into searchable sections keyed by h1-h3 headings."""
    sections: list[dict[str, str]] = []
    seen_ids: dict[str, int] = {}
    current_heading = ""
    current_anchor = ""
    buffer: list[str] = []

    def flush() -> None:
        body = _strip_markdown("\n".join(buffer))
        if current_heading or body:
            sections.append({
                "heading": current_heading,
                "anchor": current_anchor,
                "text": body,
            })

    for line in markdown_text.splitlines():
        match = re.match(r"^(#{1,3})\s+(.+)$", line)
        if match:
            flush()
            buffer = []
            clean_text = _clean_heading_text(match.group(2))
            anchor = _slugify(clean_text) or f"heading-{len(sections) + 1}"
            if anchor in seen_ids:
                seen_ids[anchor] += 1
                anchor = f"{anchor}-{seen_ids[anchor]}"
            else:
                seen_ids[anchor] = 0
            current_heading = clean_text
            current_anchor = anchor
        else:
            buffer.append(line)

    flush()
    return sections


def _get_doc_sections(rel_path: str) -> list[dict[str, str]]:
    """Return cached parsed sections for a doc path, refreshing when mtime/size changes."""
    try:
        file_path = resolve_doc_path(rel_path)
        stat = file_path.stat()
    except (ValueError, OSError):
        return []

    cached = _DOC_SECTION_CACHE.get(file_path)
    sig = (stat.st_mtime_ns, stat.st_size)
    if cached is not None and cached[0] == sig[0] and cached[1] == sig[1]:
        return cached[2]

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    sections = _parse_doc_sections(content)
    _DOC_SECTION_CACHE[file_path] = (sig[0], sig[1], sections)
    return sections


def _make_snippet(text: str, query: str, tokens: list[str], max_len: int = 150) -> str:
    """Extract a readable context snippet centered around the earliest match."""
    if not text:
        return ""
    lower = text.lower()
    idx = lower.find(query)
    if idx == -1:
        for token in tokens:
            pos = lower.find(token)
            if pos != -1:
                idx = pos
                break
    if idx == -1:
        return text[:max_len].rstrip() + ("..." if len(text) > max_len else "")

    lead = 45
    start = max(0, idx - lead)
    end = min(len(text), start + max_len)
    if end - start < max_len:
        start = max(0, end - max_len)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def _extract_headings(markdown_text: str) -> list[dict[str, Any]]:
    """Extract h1, h2, h3 headings from markdown text for table of contents."""
    headings: list[dict[str, Any]] = []
    seen_ids: dict[str, int] = {}

    for match in _HEADING_REGEX.finditer(markdown_text):
        hashes, title = match.groups()
        level = len(hashes)
        if level > 3:
            continue
        clean_text = _clean_heading_text(title)

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


_SEARCH_STOP_WORDS = frozenset({
    "a", "an", "the", "to", "of", "in", "on", "for", "and", "or",
    "is", "are", "how", "what", "with", "from", "by", "at", "be", "do", "it", "as",
})


@router.get("/search")
def search_documentation(q: str = Query(..., min_length=2, description="Search query string")) -> dict[str, Any]:
    """Search document metadata (title/slug/description) and in-file markdown sections."""
    query = q.lower().strip()
    raw_tokens = [t for t in re.split(r"\s+", query) if len(t) >= 2]
    tokens = [t for t in raw_tokens if t not in _SEARCH_STOP_WORDS] or raw_tokens
    multi_word = len(raw_tokens) > 1
    results: list[dict[str, Any]] = []
    category_titles = {c["id"]: c["title"] for c in DOC_CATEGORIES}

    for doc in DOC_REGISTRY:
        meta_score = 0
        title_lower = doc["title"].lower()
        desc_lower = doc.get("description", "").lower()
        slug_lower = doc["slug"].lower()

        if query in title_lower:
            meta_score += 30
            if title_lower.startswith(query):
                meta_score += 10
        if query in slug_lower:
            meta_score += 15
        if query in desc_lower:
            meta_score += 8

        if meta_score == 0 and tokens and (multi_word or len(tokens) > 1):
            meta_blob = f"{title_lower} {slug_lower} {desc_lower}"
            if all(t in meta_blob for t in tokens):
                meta_score += sum(18 for t in tokens if t in title_lower)
                meta_score += sum(10 for t in tokens if t in slug_lower)
                meta_score += sum(5 for t in tokens if t in desc_lower)

        section_hits: list[tuple[int, dict[str, str]]] = []
        for sec in _get_doc_sections(doc["path"]):
            heading_lower = sec["heading"].lower()
            text_lower = sec["text"].lower()
            sec_score = 0
            if query in heading_lower:
                sec_score += 22
            if query in text_lower:
                sec_score += 10
            if sec_score == 0 and tokens and (multi_word or len(tokens) > 1):
                sec_blob = f"{heading_lower} {text_lower}"
                if all(t in sec_blob for t in tokens):
                    sec_score += sum(12 for t in tokens if t in heading_lower)
                    sec_score += sum(5 for t in tokens if t in text_lower)

            if sec_score > 0:
                section_hits.append((
                    sec_score,
                    {
                        "heading": sec["heading"] or doc["title"],
                        "anchor": sec["anchor"],
                        "snippet": _make_snippet(sec["text"], query, tokens),
                    },
                ))

        section_hits.sort(key=lambda item: item[0], reverse=True)
        matched_sections = [hit[1] for hit in section_hits[:3]]
        top_section_score = section_hits[0][0] if section_hits else 0

        total_score = meta_score + top_section_score
        if total_score > 0:
            results.append({
                "slug": doc["slug"],
                "title": doc["title"],
                "description": doc.get("description", ""),
                "category": doc["category"],
                "category_title": category_titles.get(doc["category"], doc["category"]),
                "match_type": "title" if meta_score > 0 else "section",
                "sections": matched_sections,
                "score": total_score,
            })

    results.sort(key=lambda r: (-r["score"], r["title"].lower()))
    return {"query": q, "total": len(results), "results": results[:20]}
