"""Owner-authenticated connected apps for Portal (Google first): status and the owner's actions.

The host agent asks the agent's root controller, which joins the owner's choices,
whether the Source Broker holds the sign-in, and the broker's sync status. The
status reply is content-free: no tokens, messages or events ever pass through here.

The actions start and finish a Google sign-in, retry a stuck step, sync and
disconnect. The owner's Google client file passes straight through to the root
custodian; nothing here stores it. The calendar changes and Gmail drafts Portal
suggests are the one exception to content-free replies: each carries what it
would write, so the owner approves exactly that, and root makes only the change
or draft whose hash comes back.
Status and the actions answer only while PORTAL_CONNECTED_APPS_ENABLED is true;
Settings asks whether it is.
"""

from __future__ import annotations

import json
import re
import time
from typing import Annotated, Any, Literal

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
PROPOSAL = r"^calendar-[0-9]{13}-[a-f0-9]{8}$"
DRAFT = r"^gmail-draft-[0-9]{13}-[a-f0-9]{8}$"
HEX = r"^[a-f0-9]{64}$"
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
    authMode: Literal['managed', 'advanced'] | None = None
    reason: str | None = Field(pattern=TOKEN)
    account: str | None = Field(pattern=EMAIL)
    capabilities: Capabilities | None
    cloudModels: bool | None
    connectedAt: str | None = Field(pattern=INSTANT)
    grantedScopes: list[str] | None
    checkedAt: str | None = Field(pattern=INSTANT)
    sources: Sources
    pending: Pending | None
    waitingChanges: int | None = Field(ge=0, le=50)
    waitingDrafts: int | None = Field(ge=0, le=50)

    @model_validator(mode='after')
    def _source_mode(self):
        if 'authMode' in self.model_fields_set and (self.authMode is None or self.state == 'not-connected'):
            raise ValueError('invalid connection application mode')
        return self


class Connectors(Strict):
    google: GoogleConnection


class CatalogAuth(Strict):
    type: Literal["oauth2-pkce"]
    client: str = Field(pattern=TOKEN)
    redirects: list[Literal["loopback", "paste", "hosted-page"]]
    mode: Literal['managed'] | None = None
    state: Literal['configured', 'not-configured', 'unavailable'] | None = None
    advancedAvailable: bool | None = None

    @model_validator(mode='after')
    def _managed(self):
        added = {'mode', 'state', 'advancedAvailable'}
        present = self.model_fields_set & added
        if present and (present != added or self.client != 'ods-managed-desktop-app'
                        or self.mode is None or self.state is None or self.advancedAvailable is None):
            raise ValueError('inconsistent managed application metadata')
        if not present and self.client == 'ods-managed-desktop-app':
            raise ValueError('managed application metadata is missing')
        return self


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


PluginId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,62}$")]


class PluginCatalogEntry(Strict):
    """A reviewed OpenClaw plugin the owner can turn on for Portal, as Settings describes it."""

    id: PluginId
    label: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=400)
    details: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(max_length=8)
    tools: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]] = Field(max_length=32)


class ConnectionStatus(Strict):
    schemaVersion: Literal[1]
    available: bool
    surface: Literal["linux-systemd", "wsl-systemd", "linux", "darwin", "windows"]
    reason: str | None = Field(pattern=TOKEN)
    ownerTimeZone: str | None = Field(pattern=ZONE)
    connectors: Connectors
    plugins: dict[PluginId, bool] = Field(max_length=32)
    catalog: list[CatalogConnector]
    pluginCatalog: list[PluginCatalogEntry] = Field(max_length=32)


def _projection(raw) -> dict:
    try:
        status = ConnectionStatus.model_validate(raw)
    except ValidationError:
        raise HTTPException(502, "Connected apps status could not be verified", headers=NO_STORE) from None
    google = status.connectors.google
    connected = google.state != "not-connected"
    if ((google.account is None) == connected or (google.capabilities is None) == connected
            or (google.waitingChanges is None) == connected or (google.waitingDrafts is None) == connected
            or set(status.plugins) != {entry.id for entry in status.pluginCatalog}
            or any(SCOPE_PATTERN.fullmatch(scope) is None for scope in google.grantedScopes or [])):
        raise HTTPException(502, "Connected apps status could not be verified", headers=NO_STORE)
    # Preserve exact legacy absence of the optional managed auth fields, while
    # retaining explicit nulls in the existing connection/status contract.
    return status.model_dump(by_alias=True, exclude_unset=True)


def _connected_apps_enabled() -> bool:
    # On unless the owner set it to false.
    return read_live_env_value("PORTAL_CONNECTED_APPS_ENABLED", "true").strip().lower() != "false"


def _enabled() -> None:
    if not _connected_apps_enabled():
        raise HTTPException(404, "Not found", headers=NO_STORE)


@router.get("/api/pixel/connections/enabled", dependencies=[Depends(verify_api_key)])
async def get_connections_enabled():
    """Whether Settings shows Connected apps; answered here, without asking the agent runtime."""
    return JSONResponse(content={"enabled": _connected_apps_enabled()}, headers=NO_STORE)


async def _agent_status() -> dict:
    """The validated status, or HTTPException with the owner-facing reason."""
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
    return _projection(raw)


@router.get("/api/pixel/connections", dependencies=[Depends(verify_api_key)])
async def get_connections():
    """Connected apps and their sync state, for Settings and the Portal Connect card."""
    _enabled()
    return JSONResponse(content=await _agent_status(), headers=NO_STORE)


# States the owner must act on in Settings. Syncing, a slow network and nothing
# connected at all are not worth interrupting a chat for.
ATTENTION_STATES = frozenset({"needs-reconnect", "missing-permission", "setup-broken", "blocked-by-admin",
                              "unknown-error"})
ATTENTION_SECONDS = 60.0
_attention: dict[str, Any] = {"until": 0.0, "value": None}


def _attention_of(status: dict) -> dict | None:
    google = status["connectors"]["google"] if status["available"] else None
    if google is None:
        return None
    pending, waiting, drafts = google["pending"], google["waitingChanges"] or 0, google["waitingDrafts"] or 0
    found = {"state": google["state"], "reason": google["reason"], "waiting": waiting, "drafts": drafts}
    if pending and pending["error"] and pending["phase"] in ("activating", "deactivating"):
        return dict(found, step="update-stopped")
    if google["state"] in ATTENTION_STATES or google["reason"] == "sync-stopped":
        return dict(found, step=None)
    if waiting or drafts:
        # A calendar change or Gmail draft Portal suggested waits for the owner's approval.
        return dict(found, step="changes-waiting")
    return None


async def owner_attention() -> dict | None:
    """What Portal's chat should point the owner to in Connected apps, if anything.

    Portal's status poll asks often; the agent runtime is asked at most once a
    minute, and never while connected apps are off.
    """
    if not _connected_apps_enabled():
        return None
    now = time.monotonic()
    if now < _attention["until"]:
        return _attention["value"]
    try:
        value = _attention_of(await _agent_status())
    except HTTPException:
        # Unknown status interrupts nobody; Settings shows why it is unavailable.
        value = None
    _attention.update(until=now + ATTENTION_SECONDS, value=value)
    return value


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
    "sign-in-state-mismatch": ("That address belongs to a different sign-in. Paste the full address from the "
                               "Google tab opened for this connection.", False),
    "address-not-from-sign-in": ("That isn't the address Google sent you to. Copy the whole address from the tab that "
                                 "didn't load, then paste it here.", False),
    "invalid-callback": ("That isn't the address Google sent you to. Copy the whole address from the tab that "
                         "didn't load, then paste it here.", False),
    "client-not-desktop": ("That file is for another kind of Google client. Create an OAuth client of type "
                           "Desktop app, download its JSON file and upload it here.", False),
    "client-invalid": ("That isn't a Google OAuth client file. Download the JSON file of your Desktop app client "
                       "from Google Cloud and upload it here.", False),
    'google-app-not-configured': ('Google sign-in has not been configured for this ODS release yet. '
                                  'Please contact the ODS maintainers.', True),
    'google-app-config-unavailable': ('Google sign-in configuration could not be verified. '
                                      'Please contact the ODS maintainers.', True),
    'google-app-config-invalid': ('Google sign-in configuration could not be verified. '
                                  'Please contact the ODS maintainers.', True),
    'google-app-config-unsafe': ('Google sign-in configuration could not be verified. '
                                 'Please contact the ODS maintainers.', True),
    "capability-unavailable": ("That option isn't available in this version of ODS yet.", False),
    "connection-not-connected": ("Google isn't connected.", False),
    "not-available-on-this-system": ("Connected apps aren't available on this system yet.", False),
    "source-broker-kit-unavailable": ("Connected apps need an ODS update on this machine. Update ODS, then try "
                                      "again.", True),
    "connection-record-unusable": ("ODS couldn't read your saved connection. Disconnect Google, then connect it "
                                   "again.", True),
    "gateway-change-rolled-back": ("Portal couldn't start with the new connection, so it's back on its previous "
                                   "setup. Try again.", True),
    "plugin-change-rolled-back": ("Portal couldn't start with that plugin change, so it's back as it was. Try "
                                  "again.", True),
    "sign-in-exchange-unavailable": ("Google sign-in couldn't finish on this machine. Try again.", True),
    "connections-unavailable": ("ODS couldn't reach the service that connects apps to Portal. Make sure ODS is "
                                "running, then try again.", True),
    "connection-change-unconfirmed": ("ODS couldn't confirm this step. Connected apps shows where it stands; try "
                                      "again from there.", True),
    "calendar-changes-off": ("Calendar changes are off for this connection. Decline this change, or connect "
                             "Google again with calendar changes on.", False),
    "calendar-change-mismatch": ("This change is different from the one shown. Look it over again, then approve "
                                 "it.", False),
    "calendar-change-unknown": ("This change is no longer waiting. Refresh the list.", False),
    "calendar-change-invalid": ("ODS can't make this change. Decline it, and ask Portal again if you still want "
                                "it.", False),
    "calendar-change-unconfirmed": ("Google hasn't confirmed this change yet, so ODS won't send it again. Check "
                                    "your calendar in a minute.", False),
    "calendar-change-not-made": ("The change wasn't made. Try again, or decline it and ask Portal to suggest it "
                                 "again.", True),
    "calendar-change-not-checkable": ("Google can't confirm this kind of change afterwards. Check your calendar, "
                                      "then dismiss it here.", False),
    "calendar-change-result-invalid": ("ODS couldn't confirm what happened with this change. Check your calendar "
                                       "before trying again.", True),
    "calendar-change-unsafe": ("ODS couldn't read this change safely. Decline it, and ask Portal again if you "
                               "still want it.", True),
    "calendar-undo-unavailable": ("This change can't be undone any more: the event changed or is gone. Check "
                                  "your calendar.", False),
    "gmail-drafts-off": ("Gmail drafts are off for this connection. Decline this draft, or connect Google again "
                         "with Gmail drafts on.", False),
    "gmail-draft-mismatch": ("This draft is different from the one shown. Read it again, then approve it.", False),
    "gmail-draft-unknown": ("This draft is no longer waiting. Refresh the list.", False),
    "gmail-draft-invalid": ("ODS can't create this draft. Decline it, and ask Portal again if you still want it.",
                            False),
    "gmail-draft-unconfirmed": ("Gmail hasn't confirmed this draft yet, so ODS won't create it again. Check your "
                                "Drafts folder in a minute.", False),
    "gmail-draft-not-made": ("The draft wasn't created. Try again, or decline it and ask Portal to write it "
                             "again.", True),
    "gmail-draft-not-checkable": ("Gmail can't confirm this draft afterwards. Check your Drafts folder, then dismiss "
                                  "it here.", False),
    "gmail-draft-result-invalid": ("ODS couldn't confirm what happened with this draft. Check your Drafts folder "
                                   "before trying again.", True),
    "gmail-draft-unsafe": ("ODS couldn't read this draft safely. Decline it, and ask Portal again if you still "
                           "want it.", True),
    "source-broker-env-invalid": ("Connected apps need an ODS update on this machine. Update ODS, then try "
                                  "again.", True),
}
FALLBACK = ("ODS couldn't finish this step. Try again, and ask on Discord if it keeps happening.", True)


def _refusal(status_code: int, code: str) -> HTTPException:
    message, needs_help = MESSAGES.get(code, FALLBACK)
    return HTTPException(status_code, {"code": code if re.fullmatch(r"[a-z][a-z0-9-]{0,95}", code) else "unknown",
                                       "message": message, "help": ODS_HELP_DISCORD_URL if needs_help else None},
                         headers=NO_STORE)


class ConnectRequest(Strict):
    """Managed sign-in carries no client; only Advanced supplies a Desktop JSON."""

    authMode: Literal['managed', 'advanced'] | None = None
    client: dict[str, Any] | None = None
    capabilities: Capabilities
    cloudModels: bool
    ownerTimeZone: str | None = Field(pattern=ZONE)
    loginHint: str | None = Field(pattern=EMAIL)

    @model_validator(mode="after")
    def _bounded(self):
        supplied_mode = 'authMode' in self.model_fields_set
        supplied_client = 'client' in self.model_fields_set
        if supplied_mode and self.authMode == 'managed':
            if supplied_client:
                raise ValueError('managed sign-in does not accept a client')
        elif ((supplied_mode and self.authMode != 'advanced') or not supplied_client or self.client is None):
            raise ValueError('Advanced sign-in requires a client')
        if self.client is not None and len(json.dumps(self.client, separators=(",", ":"))) > MAX_CLIENT_BYTES:
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


Short = Annotated[str, Field(max_length=64)]
Attendee = Annotated[str, Field(min_length=3, max_length=320)]


class CalendarChange(Strict):
    """Every value the change sends to Google; None keeps what the event has."""

    summary: str | None = Field(max_length=1000)
    start: Short | None
    end: Short | None
    allDay: bool | None
    timeZone: str | None = Field(max_length=100)
    location: str | None = Field(max_length=1000)
    description: str | None = Field(max_length=20000)
    attendees: list[Attendee] | None = Field(max_length=100)
    sendUpdates: Literal["none", "all"]


class CalendarEvent(Strict):
    """The event an update or delete targets, as the last calendar sync saw it."""

    title: str | None = Field(max_length=1000)
    start: Short | None
    end: Short | None
    allDay: bool


class CalendarProposal(Strict):
    proposalId: str = Field(pattern=PROPOSAL)
    sha256: str | None = Field(pattern=HEX)
    state: Literal["waiting", "unconfirmed", "applied", "rejected", "not-applied", "invalid"]
    action: Literal["create", "update", "delete"] | None
    createdAt: Short | None
    change: CalendarChange | None
    event: CalendarEvent | None
    tooLarge: bool

    @model_validator(mode="after")
    def _consistent(self):
        unreadable = self.state == "invalid"
        shown = not unreadable and not self.tooLarge
        if ((self.action is None) != unreadable or (self.sha256 is None) == shown or (self.change is None) == shown
                or not shown and self.event is not None or unreadable and (self.tooLarge or self.createdAt)):
            raise ValueError("inconsistent calendar change")
        return self


class ProposalsReply(Strict):
    proposals: list[CalendarProposal] = Field(max_length=20)
    more: int = Field(ge=0)


class ApproveRequest(Strict):
    proposalId: str = Field(pattern=PROPOSAL)
    sha256: str = Field(pattern=HEX)


class ChangeRequest(Strict):
    proposalId: str = Field(pattern=PROPOSAL)


class CalendarMoment(Strict):
    start: Short | None
    end: Short | None
    allDay: bool


class CalendarActivity(Strict):
    """A private change Portal made by itself, and whether it can be undone."""

    proposalId: str = Field(pattern=PROPOSAL)
    action: Literal["create", "update"]
    title: str | None = Field(max_length=1000)
    start: Short | None
    end: Short | None
    allDay: bool
    previous: CalendarMoment | None
    appliedAt: Short | None
    undo: Literal["available", "syncing", "done", "refused", "unconfirmed", "changed", "gone"]


class ActivityReply(Strict):
    changes: list[CalendarActivity] = Field(max_length=20)
    more: int = Field(ge=0)


class ChangeReply(Strict):
    proposalId: str = Field(pattern=PROPOSAL)
    outcome: Literal["applied", "rejected", "not-applied", "unconfirmed", "waiting", "declined"]


Recipient = Annotated[str, Field(min_length=3, max_length=320)]


class DraftReplyTo(Strict):
    """The message a reply answers, as the last email sync saw it; None when that sync does not hold it."""

    sender: str | None = Field(alias="from", max_length=320)
    subject: str | None = Field(max_length=998)


class GmailDraft(Strict):
    """Everything the draft holds; Gmail keeps it in Drafts, and the owner sends it."""

    to: list[Recipient] = Field(min_length=1, max_length=50)
    cc: list[Recipient] = Field(max_length=50)
    subject: str = Field(max_length=998, pattern=r"^[^\r\n]*$")
    body: str = Field(min_length=1, max_length=100000)
    replyTo: DraftReplyTo | None


class DraftProposal(Strict):
    proposalId: str = Field(pattern=DRAFT)
    sha256: str | None = Field(pattern=HEX)
    state: Literal["waiting", "unconfirmed", "applied", "rejected", "not-applied", "invalid"]
    createdAt: Short | None
    draft: GmailDraft | None
    tooLarge: bool

    @model_validator(mode="after")
    def _consistent(self):
        unreadable = self.state == "invalid"
        shown = not unreadable and not self.tooLarge
        if ((self.sha256 is None) == shown or (self.draft is None) == shown
                or unreadable and (self.tooLarge or self.createdAt)):
            raise ValueError("inconsistent Gmail draft")
        return self


class DraftsReply(Strict):
    drafts: list[DraftProposal] = Field(max_length=20)
    more: int = Field(ge=0)


class DraftApproveRequest(Strict):
    proposalId: str = Field(pattern=DRAFT)
    sha256: str = Field(pattern=HEX)


class DraftRequest(Strict):
    proposalId: str = Field(pattern=DRAFT)


class DraftActionReply(Strict):
    proposalId: str = Field(pattern=DRAFT)
    outcome: Literal["applied", "rejected", "not-applied", "unconfirmed", "waiting", "declined"]


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
    payload = request.model_dump(by_alias=True, exclude_unset=True)
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


class PluginRequest(Strict):
    plugin: PluginId
    enabled: bool


@router.post("/api/pixel/connections/plugin", dependencies=[Depends(verify_api_key)])
async def change_plugin(request: PluginRequest):
    """Turn one reviewed plugin on or off for Portal; Portal's gateway restarts with it."""
    return await _action("/v1/pixel/connections/plugin", request.model_dump(), OutcomeReply, 330)


# ---------------------------------------------------------------- calendar changes Portal suggests

@router.get("/api/pixel/connections/google/changes", dependencies=[Depends(verify_api_key)])
async def google_changes():
    """Calendar changes Portal suggested, oldest first; approving one carries the hash shown with it."""
    return await _action("/v1/pixel/connections/google/changes", {}, ProposalsReply, 45)


async def _change_action(path: str, payload: dict, timeout: float,
                         reply_model: type[BaseModel] = ChangeReply) -> JSONResponse:
    try:
        return await _action(path, payload, reply_model, timeout)
    finally:
        # Portal's chat line counts waiting changes and drafts; let it see this one at once.
        _attention.update(until=0.0)


@router.post("/api/pixel/connections/google/changes/approve", dependencies=[Depends(verify_api_key)])
async def approve_google_change(request: ApproveRequest):
    """Make exactly the change the owner saw: root checks the hash before Google is asked."""
    return await _change_action("/v1/pixel/connections/google/approve", request.model_dump(), 180)


@router.post("/api/pixel/connections/google/changes/decline", dependencies=[Depends(verify_api_key)])
async def decline_google_change(request: ChangeRequest):
    return await _change_action("/v1/pixel/connections/google/decline", request.model_dump(), 45)


@router.post("/api/pixel/connections/google/changes/check", dependencies=[Depends(verify_api_key)])
async def check_google_change(request: ChangeRequest):
    """Ask whether Google made a new event it did not confirm; nothing is sent again."""
    return await _change_action("/v1/pixel/connections/google/check", request.model_dump(), 180)



@router.get("/api/pixel/connections/google/activity", dependencies=[Depends(verify_api_key)])
async def google_activity():
    """The private changes Portal made by itself this week, newest first, each with whether it can be undone."""
    return await _action("/v1/pixel/connections/google/activity", {}, ActivityReply, 45)


@router.post("/api/pixel/connections/google/activity/undo", dependencies=[Depends(verify_api_key)])
async def undo_google_change(request: ChangeRequest):
    """Reverse one change Portal made by itself; the owner's click is the approval."""
    return await _change_action("/v1/pixel/connections/google/undo", request.model_dump(), 180)


# ---------------------------------------------------------------- Gmail drafts Portal writes

@router.get("/api/pixel/connections/google/drafts", dependencies=[Depends(verify_api_key)])
async def google_drafts():
    """Gmail drafts Portal wrote, oldest first; approving one carries the hash shown with it."""
    return await _action("/v1/pixel/connections/google/drafts", {}, DraftsReply, 45)


@router.post("/api/pixel/connections/google/drafts/approve", dependencies=[Depends(verify_api_key)])
async def approve_google_draft(request: DraftApproveRequest):
    """Create exactly the draft the owner read, in Gmail's Drafts; nothing is sent."""
    return await _change_action("/v1/pixel/connections/google/approve", request.model_dump(), 180, DraftActionReply)


@router.post("/api/pixel/connections/google/drafts/decline", dependencies=[Depends(verify_api_key)])
async def decline_google_draft(request: DraftRequest):
    return await _change_action("/v1/pixel/connections/google/decline", request.model_dump(), 45, DraftActionReply)


@router.post("/api/pixel/connections/google/drafts/check", dependencies=[Depends(verify_api_key)])
async def check_google_draft(request: DraftRequest):
    """Ask whether Gmail made a draft it did not confirm; nothing is created again."""
    return await _change_action("/v1/pixel/connections/google/check", request.model_dump(), 180, DraftActionReply)
