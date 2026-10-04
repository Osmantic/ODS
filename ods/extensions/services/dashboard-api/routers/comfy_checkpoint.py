"""Authenticated Dashboard proxy for the explicit ComfyUI checkpoint action."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from host_agent_client import AgentClientError, AgentHTTPError, request_json
from security import verify_api_key

router = APIRouter(tags=["extensions"])


class DownloadConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str
    acknowledge_size_bytes: int


def _host(method: str, path: str, payload=None) -> dict:
    try:
        return request_json(method, path, payload=payload, timeout=10)
    except AgentHTTPError as exc:
        status = exc.status_code if exc.status_code in (400, 409, 502, 503) else 502
        raise HTTPException(status_code=status, detail=exc.detail) from exc
    except AgentClientError as exc:
        raise HTTPException(status_code=503, detail="Host agent is unavailable") from exc


@router.get("/api/extensions/comfyui/checkpoint")
def checkpoint_status(api_key: str = Depends(verify_api_key)):
    return _host("GET", "/v1/comfy/checkpoint/status")


@router.post("/api/extensions/comfyui/checkpoint/download", status_code=202)
def checkpoint_download(body: DownloadConfirmation, api_key: str = Depends(verify_api_key)):
    return _host("POST", "/v1/comfy/checkpoint/download", body.model_dump())


@router.post("/api/extensions/comfyui/checkpoint/cancel", status_code=202)
def checkpoint_cancel(api_key: str = Depends(verify_api_key)):
    return _host("POST", "/v1/comfy/checkpoint/cancel", {})
