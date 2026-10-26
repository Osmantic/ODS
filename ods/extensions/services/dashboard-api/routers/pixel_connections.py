"""Owner-authenticated, read-only status of Portal's connected apps (Google first).

The host agent asks the agent's root controller, which joins the owner's choices,
whether the Source Broker holds the sign-in, and the broker's sync status. The
reply is content-free: no tokens, messages or events ever pass through here.
"""

from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable
from host_agent_client import async_request_json as request_agent_json
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from security import verify_api_key

router = APIRouter(tags=["pixel-connections"])
NO_STORE = {"Cache-Control": "no-store"}
TOKEN = r"^[a-z][a-z0-9-]{0,63}$"
INSTANT = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
EMAIL = r"^[^@\s<>\x00-\x1f\x7f]{1,64}@[^@\s<>\x00-\x1f\x7f]{1,253}$"
ZONE = r"^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$"
SCOPE_PATTERN = re.compile(r"^(?:https://www\.googleapis\.com/auth/[a-z0-9._-]{1,100}|https://mail\.google\.com/|openid|email|profile)$")
State = Literal["not-connected", "first-sync", "connected", "needs-reconnect", "blocked-by-admin",
                "setup-broken", "missing-permission", "unknown-error", "behind"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)


class SourceStatus(Strict):
    state: Literal["ok", "error", "disabled"]
    errorClass: str | None = Field(pattern=TOKEN)
    httpStatus: int | None = Field(ge=100, le=599)
    lastSuccessAt: str | None = Field(pattern=INSTANT)
    failingSince: str | None = Field(pattern=INSTANT)
    recordCount: int = Field(ge=0)
    complete: bool | None


class Sources(Strict):
    email: SourceStatus | None
    calendar: SourceStatus | None


class Capabilities(Strict):
    calendar_read: bool = Field(alias="calendar.read")
    calendar_write: bool = Field(alias="calendar.write")
    calendar_direct: bool = Field(alias="calendar.direct")
    gmail_read: bool = Field(alias="gmail.read")
    gmail_drafts: bool = Field(alias="gmail.drafts")


class GoogleConnection(Strict):
    state: State
    reason: str | None = Field(pattern=TOKEN)
    account: str | None = Field(pattern=EMAIL)
    capabilities: Capabilities | None
    cloudModels: bool | None
    connectedAt: str | None = Field(pattern=INSTANT)
    grantedScopes: list[str] | None
    checkedAt: str | None = Field(pattern=INSTANT)
    sources: Sources


class Connectors(Strict):
    google: GoogleConnection


class CatalogAuth(Strict):
    type: Literal["oauth2-pkce"]
    client: str = Field(pattern=TOKEN)
    redirects: list[Literal["loopback", "paste", "hosted-page"]]


class CatalogCapability(Strict):
    id: str = Field(pattern=r"^[a-z]+\.[a-z-]+$")
    label: str = Field(min_length=1, max_length=120)
    effect: Literal["read", "private-write", "external-write", "draft"]
    scopes: list[str]
    tools: list[str]
    default: bool
    available: bool
    requires: list[str]


class CatalogData(Strict):
    calendarId: str = Field(min_length=1, max_length=255)
    calendarPastDays: int = Field(ge=0, le=365)
    calendarFutureDays: int = Field(ge=1, le=730)
    gmailWindowDays: int = Field(ge=1, le=3650)


class CatalogConnector(Strict):
    id: str = Field(pattern=TOKEN)
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=200)
    kinds: list[Literal["source", "actuator", "channel", "local-service", "mcp"]]
    platforms: list[Literal["linux-systemd", "wsl-systemd", "linux", "darwin", "windows"]]
    auth: CatalogAuth
    capabilities: list[CatalogCapability]
    data: CatalogData


class ConnectionStatus(Strict):
    schemaVersion: Literal[1]
    available: bool
    surface: Literal["linux-systemd", "wsl-systemd", "linux", "darwin", "windows"]
    reason: str | None = Field(pattern=TOKEN)
    ownerTimeZone: str | None = Field(pattern=ZONE)
    connectors: Connectors
    catalog: list[CatalogConnector]


def _projection(raw) -> dict:
    try:
        status = ConnectionStatus.model_validate(raw)
    except ValidationError:
        raise HTTPException(502, "Connected apps status could not be verified", headers=NO_STORE) from None
    google = status.connectors.google
    connected = google.state != "not-connected"
    if ((google.account is None) == connected or (google.capabilities is None) == connected
            or any(SCOPE_PATTERN.fullmatch(scope) is None for scope in google.grantedScopes or [])):
        raise HTTPException(502, "Connected apps status could not be verified", headers=NO_STORE)
    return status.model_dump(by_alias=True)


@router.get("/api/pixel/connections", dependencies=[Depends(verify_api_key)])
async def get_connections():
    """Connected apps and their sync state, for Settings and the Portal Connect card."""
    try:
        raw = await request_agent_json("GET", "/v1/pixel/connections", timeout=25)
    except AgentHTTPError as exc:
        code = exc.status_code if exc.status_code in (400, 409, 503) else 502
        raise HTTPException(code, "Connected apps status is unavailable right now. Try again in a moment.",
                            headers=NO_STORE) from None
    except AgentUnavailable:
        raise HTTPException(503, "Connected apps status is unavailable right now. Try again in a moment.",
                            headers=NO_STORE) from None
    except AgentProtocolError:
        raise HTTPException(502, "Connected apps status could not be verified", headers=NO_STORE) from None
    return JSONResponse(content=_projection(raw), headers=NO_STORE)
