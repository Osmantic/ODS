"""Owner-authenticated connected apps for Portal (Google first): status and the owner's actions.

The host agent asks the agent's root controller, which joins the owner's choices,
whether the Source Broker holds the sign-in, and the broker's sync status. The
status reply is content-free: no tokens, messages or events ever pass through here.

The actions start and finish a Google sign-in, retry a stuck step, sync and
disconnect. The owner's Google client file passes straight through to the root
custodian; nothing here stores it. Status and the actions answer only while
PORTAL_CONNECTED_APPS_ENABLED is true; Settings asks whether it is.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from config import read_live_env_value
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable
from host_agent_client import async_request_json as request_agent_json
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from routers.auth import ODS_HELP_DISCORD_URL
from security import verify_api_key

router = APIRouter(tags=["pixel-connections"])
NO_STORE = {"Cache-Control": "no-store"}
TOKEN = r"^[a-z][a-z0-9-]{0,63}$"
INSTANT = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
SECOND = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
EMAIL = r"^[^@\s<>\x00-\x1f\x7f]{1,64}@[^@\s<>\x00-\x1f\x7f]{1,253}$"
ZONE = r"^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$"
FLOW = r"^[a-f0-9]{32}$"
SCOPE_PATTERN = re.compile(r"^(?:https://www\.googleapis\.com/auth/[a-z0-9._-]{1,100}|https://mail\.google\.com/|openid|email|profile)$")
AUTH_URL = r"^https://accounts\.google\.com/o/oauth2/auth\?[A-Za-z0-9._~%&=+-]{1,4000}$"
MAX_CLIENT_BYTES = 4096
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


class Pending(Strict):
    """A connection step in progress (waiting for Google, or Portal's gateway still to
    update), or why the last sign-in did not connect."""

    action: Literal["connect", "disconnect"]
    phase: Literal["consent", "activating", "deactivating", "failed"]
    expiresAt: str | None = Field(pattern=SECOND)
    error: str | None = Field(pattern=r"^[a-z][a-z0-9-]{0,95}$")
    since: str = Field(pattern=SECOND)

    @model_validator(mode="after")
    def _consistent(self):
        if self.action != ("disconnect" if self.phase == "deactivating" else "connect") or \
                (self.expiresAt is None) != (self.phase != "consent") or \
                self.phase == "failed" and self.error is None:
            raise ValueError("inconsistent pending step")
        return self


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
    pending: Pending | None


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


def _connected_apps_enabled() -> bool:
    return read_live_env_value("PORTAL_CONNECTED_APPS_ENABLED").strip().lower() == "true"


def _enabled() -> None:
    if not _connected_apps_enabled():
        raise HTTPException(404, "Not found", headers=NO_STORE)


@router.get("/api/pixel/connections/enabled", dependencies=[Depends(verify_api_key)])
async def get_connections_enabled():
    """Whether Settings shows Connected apps; answered here, without asking the agent runtime."""
    return JSONResponse(content={"enabled": _connected_apps_enabled()}, headers=NO_STORE)


@router.get("/api/pixel/connections", dependencies=[Depends(verify_api_key)])
async def get_connections():
    """Connected apps and their sync state, for Settings and the Portal Connect card."""
    _enabled()
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


# ---------------------------------------------------------------- owner actions

# The custodian's codes, as the owner reads them. Real failures carry the help link;
# a busy Portal or an expired sign-in is something the owner simply does again.
MESSAGES: dict[str, tuple[str, bool]] = {
    "runtime-busy": ("Portal is busy right now. Try again when it has finished.", False),
    "transition-busy": ("ODS is finishing another Portal change. Try again in a moment.", False),
    "model-lifecycle-busy": ("ODS is finishing another Portal change. Try again in a moment.", False),
    "sign-in-in-progress": ("A Google sign-in is still open. Finish it in the Google tab, or start again.", False),
    "sign-in-flow-expired": ("That sign-in took too long and expired. Start again.", False),
    "sign-in-flow-unknown": ("That sign-in is no longer open. Start again.", False),
    "sign-in-state-mismatch": ("That address belongs to a different sign-in. Start again, then paste the new address.", False),
    "address-not-from-sign-in": ("That isn't the address Google sent you to. Copy the whole address from the tab that "
                                 "didn't load, then paste it here.", False),
    "invalid-callback": ("That isn't the address Google sent you to. Copy the whole address from the tab that "
                         "didn't load, then paste it here.", False),
    "client-not-desktop": ("That file is for another kind of Google client. Create an OAuth client of type "
                           "Desktop app, download its JSON file and upload it here.", False),
    "client-invalid": ("That isn't a Google OAuth client file. Download the JSON file of your Desktop app client "
                       "from Google Cloud and upload it here.", False),
    "capability-unavailable": ("That option isn't available in this version of ODS yet.", False),
    "connection-not-connected": ("Google isn't connected.", False),
    "not-available-on-this-system": ("Connected apps aren't available on this system yet.", False),
    "source-broker-kit-unavailable": ("Connected apps need an ODS update on this machine. Update ODS, then try "
                                      "again.", True),
    "connection-record-unusable": ("ODS couldn't read your saved connection. Disconnect Google, then connect it "
                                   "again.", True),
    "gateway-change-rolled-back": ("Portal couldn't start with the new connection, so it's back on its previous "
                                   "setup. Try again.", True),
    "sign-in-exchange-unavailable": ("Google sign-in couldn't finish on this machine. Try again.", True),
    "connections-unavailable": ("ODS couldn't reach the service that connects apps to Portal. Make sure ODS is "
                                "running, then try again.", True),
    "connection-change-unconfirmed": ("ODS couldn't confirm this step. Connected apps shows where it stands; try "
                                      "again from there.", True),
}
FALLBACK = ("ODS couldn't finish this step. Try again, and ask on Discord if it keeps happening.", True)


def _refusal(status_code: int, code: str) -> HTTPException:
    message, needs_help = MESSAGES.get(code, FALLBACK)
    return HTTPException(status_code, {"code": code if re.fullmatch(r"[a-z][a-z0-9-]{0,95}", code) else "unknown",
                                       "message": message, "help": ODS_HELP_DISCORD_URL if needs_help else None},
                         headers=NO_STORE)


class ConnectRequest(Strict):
    """What the owner chose in the wizard. ``client`` is Google's downloaded JSON, unread here."""

    client: dict[str, Any]
    capabilities: Capabilities
    cloudModels: bool
    ownerTimeZone: str | None = Field(pattern=ZONE)
    loginHint: str | None = Field(pattern=EMAIL)

    @model_validator(mode="after")
    def _bounded(self):
        if len(json.dumps(self.client, separators=(",", ":"))) > MAX_CLIENT_BYTES:
            raise ValueError("client file too large")
        return self


class CompleteRequest(Strict):
    flowId: str = Field(pattern=FLOW)
    address: str = Field(min_length=1, max_length=8192)


class CancelRequest(Strict):
    flowId: str = Field(pattern=FLOW)


class EmptyRequest(Strict):
    pass


class BeginReply(Strict):
    flowId: str = Field(pattern=FLOW)
    authUrl: str = Field(pattern=AUTH_URL)
    expiresAt: str = Field(pattern=SECOND)
    redirect: Literal["loopback"]


class CompleteReply(Strict):
    outcome: Literal["connected", "failed"]
    errorClass: str | None = Field(pattern=r"^[a-z][a-z0-9-]{0,95}$")
    account: str | None = Field(pattern=EMAIL)
    capabilities: Capabilities | None
    missing: list[Literal["calendar.read", "calendar.write", "calendar.direct", "gmail.read", "gmail.drafts"]]

    @model_validator(mode="after")
    def _consistent(self):
        connected = self.outcome == "connected"
        if ((self.errorClass is None) != connected or (self.account is None) == connected
                or (self.capabilities is None) == connected):
            raise ValueError("inconsistent sign-in result")
        return self


class OutcomeReply(Strict):
    outcome: Literal["applied", "unchanged", "cancelled", "started", "disconnected"]
    revoked: bool | None = None

    @model_validator(mode="after")
    def _consistent(self):
        if (self.revoked is None) == (self.outcome == "disconnected"):
            raise ValueError("revoked belongs to a disconnect")
        return self


async def _action(path: str, payload: dict, reply_model: type[BaseModel], timeout: float) -> JSONResponse:
    _enabled()
    try:
        raw = await request_agent_json("POST", path, payload=payload, timeout=timeout)
    except AgentHTTPError as exc:
        code = exc.detail if isinstance(exc.detail, str) else "unknown"
        raise _refusal(exc.status_code if exc.status_code in (400, 409, 503) else 502, code) from None
    except AgentUnavailable:
        raise _refusal(503, "connections-unavailable") from None
    except AgentProtocolError:
        raise _refusal(502, "connection-change-unconfirmed") from None
    try:
        reply = reply_model.model_validate(raw)
    except ValidationError:
        raise _refusal(502, "connection-change-unconfirmed") from None
    return JSONResponse(content=reply.model_dump(by_alias=True, exclude_none=reply_model is OutcomeReply),
                        headers=NO_STORE)


@router.post("/api/pixel/connections/google/connect", dependencies=[Depends(verify_api_key)])
async def connect_google(request: ConnectRequest):
    """Start a Google sign-in; the reply's address opens Google's consent page."""
    payload = request.model_dump(by_alias=True)
    return await _action("/v1/pixel/connections/google/begin", payload, BeginReply, 90)


@router.post("/api/pixel/connections/google/complete", dependencies=[Depends(verify_api_key)])
async def complete_google(request: CompleteRequest):
    """Finish a sign-in with the address the owner copied from another device's browser."""
    return await _action("/v1/pixel/connections/google/complete", request.model_dump(), CompleteReply, 180)


@router.post("/api/pixel/connections/google/cancel", dependencies=[Depends(verify_api_key)])
async def cancel_google(request: CancelRequest):
    return await _action("/v1/pixel/connections/google/cancel", request.model_dump(), OutcomeReply, 45)


@router.post("/api/pixel/connections/google/disconnect", dependencies=[Depends(verify_api_key)])
async def disconnect_google(request: EmptyRequest):
    """Stop syncing, revoke at Google, delete everything ODS derived, then update Portal."""
    return await _action("/v1/pixel/connections/google/disconnect", {}, OutcomeReply, 180)


@router.post("/api/pixel/connections/google/sync", dependencies=[Depends(verify_api_key)])
async def sync_google(request: EmptyRequest):
    return await _action("/v1/pixel/connections/google/sync", {}, OutcomeReply, 45)


@router.post("/api/pixel/connections/google/finish", dependencies=[Depends(verify_api_key)])
async def finish_google(request: EmptyRequest):
    """Finish connecting or disconnecting when Portal was busy, or after an interruption."""
    return await _action("/v1/pixel/connections/google/finish", {}, OutcomeReply, 330)
