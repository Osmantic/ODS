"""Why a Portal turn failed: the model relay's latest call, as a cause and a next step.

The relay reports only the HTTP status of the latest model call and its age,
never a body. In the fleet's drills a down API, a 502, a refused key and a
model the key may not use all looked the same in Portal, though each needs a
different step from the owner.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends

from config import SERVICES, read_live_env_value
from security import verify_api_key

router = APIRouter(prefix="/api/pixel", tags=["pixel"])

# A failure older than this belongs to an earlier turn.
RECENT_SECONDS = 120.0


def _install_kind() -> str:
    if str(read_live_env_value("LLM_BACKEND") or "").strip().lower() == "external":
        return "api"  # the installer's API mode
    if str(read_live_env_value("ODS_MODE") or "").strip().lower() == "cloud":
        return "remote"  # a model API from Settings > Remote model, or a cloud install
    return "local"


def failure_cause(status: int, kind: str) -> dict | None:
    """Map a failed call's HTTP status and the install kind to a cause and a next step."""
    if kind == "local":
        if status == 503:
            return {"cause": "model_loading",
                    "message": "The local model is not ready yet (HTTP 503). It may still be loading: "
                               "wait a moment, then try again."}
        if status >= 500:
            return {"cause": "model_unavailable",
                    "message": f"The local model did not answer (HTTP {status}). Wait a moment, then try again."}
        return None
    if status == 401:
        step = ("Rerun the ODS installer with a current API key." if kind == "api"
                else "Connect it again with a current key in Settings > Remote model.")
        return {"cause": "key_refused", "message": f"The model API refused the key ODS uses (HTTP 401). {step}"}
    if status == 403:
        return {"cause": "access_denied",
                "message": "The model API refused access to the model ODS asked for (HTTP 403). "
                           "Check that your key may use this model, or choose a model it allows."}
    if status == 404:
        return {"cause": "model_missing",
                "message": "The model API does not serve the model ODS asked for (HTTP 404). Check the model name."}
    if status == 429:
        return {"cause": "rate_limited",
                "message": "The model API is rate-limiting this key (HTTP 429). Wait a minute, then try again."}
    if status >= 500:
        return {"cause": "api_unreachable",
                "message": f"ODS could not reach the model API, or the API failed (HTTP {status}). "
                           "Check that the API is up and this computer is online, then try again."}
    return None


@router.get("/model-failure", dependencies=[Depends(verify_api_key)])
async def pixel_model_failure() -> dict:
    """The latest model call's failure when it is recent; otherwise no cause."""
    service = SERVICES.get("pixel-model-relay")
    key = str(read_live_env_value("PIXEL_MODEL_RELAY_KEY") or "")
    if not service or not key:
        return {"cause": None}
    url = f"http://{service['host']}:{service['port']}/v1/ods/last-generation"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {key}"})
        value = response.json() if response.status_code == 200 else {}
    except (httpx.HTTPError, ValueError):
        return {"cause": None}
    status = value.get("status") if isinstance(value, dict) else None
    age = value.get("ageSeconds") if isinstance(value, dict) else None
    if (type(status) is not int or status < 400 or type(age) not in (int, float)
            or not 0 <= age <= RECENT_SECONDS):
        return {"cause": None}
    cause = failure_cause(status, _install_kind())
    return {**cause, "status": status} if cause else {"cause": None}
