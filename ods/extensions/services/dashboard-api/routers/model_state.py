"""Read-only Model Switchboard state endpoint (PR 1, observe mode).

Registered before the dynamic ``/api/models/{model_id}`` routes. The host
agent is the only writer of ``data/model-state.json``; this endpoint is a
sanitized reader. Malformed state is reported diagnostically — it is never
promoted, repaired, or treated as a server error.
"""

from __future__ import annotations

import json
import logging
import os
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from config import DATA_DIR, INSTALL_DIR
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])

_STATE_SCHEMA = "ods.model-state.v1"


def _state_path() -> Path:
    data_dir = os.environ.get("ODS_DATA_DIR") or DATA_DIR
    return Path(data_dir) / "model-state.json"


def _schema_path() -> Path:
    override = os.environ.get("ODS_MODEL_STATE_SCHEMA_PATH")
    if override:
        return Path(override)
    return Path(INSTALL_DIR) / "config" / "model-state.schema.v1.json"


def _invalid_response(errors: list[str], *, exists: bool = True) -> dict[str, Any]:
    return {
        "exists": exists,
        "valid": False,
        "errors": errors,
        "schema": _STATE_SCHEMA,
        "seq": None,
        "routeSeq": None,
        "operation": None,
        "desired": None,
        "active": None,
        "history": [],
        "historyCount": 0,
        "historyTotal": 0,
        "availability": None,
        "capabilityImpact": {"agentViable": None},
    }


def _validate_document(doc: Any) -> list[str]:
    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
    except (OSError, ValueError, SchemaError) as exc:
        return [f"state schema unavailable or invalid: {exc}"]

    errors = []
    def sort_key(item):
        return tuple(str(part) for part in item.absolute_path)

    for error in sorted(validator.iter_errors(doc), key=sort_key):
        location = ".".join(str(part) for part in error.absolute_path) or "state"
        errors.append(f"{location}: {error.message}")
    return errors


def _summarize(doc: dict[str, Any], history_limit: int) -> dict[str, Any]:
    active = doc.get("active") if isinstance(doc.get("active"), dict) else None
    capabilities = None
    if active and isinstance(active.get("capabilities"), dict):
        capabilities = active["capabilities"]
    history = doc.get("history") if isinstance(doc.get("history"), list) else []
    return {
        "exists": True,
        "valid": True,
        "errors": [],
        "schema": doc.get("schema"),
        "seq": doc.get("seq"),
        "routeSeq": doc.get("routeSeq"),
        "operation": doc.get("operation"),
        "desired": doc.get("desired"),
        "active": active,
        "history": history[:history_limit],
        "historyCount": min(len(history), history_limit),
        "historyTotal": len(history),
        "availability": doc.get("availability"),
        "capabilityImpact": {
            "agentViable": bool(capabilities.get("agentViable")) if capabilities else None,
        },
    }


def _state_etag(raw: str, history_limit: int) -> str:
    representation = f"history_limit={history_limit}\0{raw}"
    return f'"{sha256(representation.encode("utf-8")).hexdigest()}"'


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*" or candidate == etag:
            return True
        if candidate.startswith("W/") and candidate[2:] == etag:
            return True
    return False


@router.get("/api/models/state")
async def get_model_state(
    response: Response,
    history_limit: Annotated[int, Query(ge=0, le=10)] = 10,
    if_none_match: Annotated[str | None, Header()] = None,
    api_key: str = Depends(verify_api_key),
):
    """Sanitized switchboard state summary; read-only by contract."""
    path = _state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _invalid_response([], exists=False)
    except (OSError, UnicodeError) as exc:
        logger.warning("model-state read failed: %s", exc)
        return _invalid_response([f"read failed: {exc}"])

    etag = _state_etag(raw, history_limit)
    cache_headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=cache_headers)
    response.headers.update(cache_headers)

    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return _invalid_response([f"not valid JSON: {exc}"])

    if not isinstance(doc, dict):
        return _invalid_response(["document root must be a JSON object"])

    errors = _validate_document(doc)
    if errors:
        return _invalid_response(errors)
    return _summarize(doc, history_limit)
