"""Public contracts for persistent chat context, independent of model or OS.

The snapshot is conversation data, never a replacement for runtime instructions.
Only the ingress owns delivery cursors and native compaction; the dashboard must
not manufacture a second summary or claim delivery from a browser-side counter.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_HISTORY_BYTES = 4 * 1024 * 1024
MAX_HISTORY_MESSAGES = 2000


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_HISTORY_BYTES)


class HistorySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schemaVersion: Literal[1]
    messages: list[HistoryMessage] = Field(min_length=1, max_length=MAX_HISTORY_MESSAGES)

    @field_validator("messages")
    @classmethod
    def bounded_content(cls, messages):
        if sum(len(item.content.encode("utf-8")) for item in messages) > MAX_HISTORY_BYTES:
            raise ValueError("Conversation history exceeds its storage limit")
        return messages


class _Projection(BaseModel):
    # Future internal fields (session files, summaries, credentials) must never
    # leak to the browser merely because the gateway adds them to its response.
    model_config = ConfigDict(extra="ignore", strict=True)


class ContextUsage(_Projection):
    used: int = Field(ge=0, le=100_000_000)
    window: int = Field(ge=1, le=10_000_000)
    measuredAt: str = Field(min_length=1, max_length=64, pattern=r"^[0-9TZ: .+\-]+$")


class ContextModel(_Projection):
    id: str = Field(min_length=1, max_length=512, pattern=r"^[^\x00-\x1f\x7f]+$")
    provider: str = Field(min_length=1, max_length=128, pattern=r"^[^\x00-\x1f\x7f]+$")
    contextWindow: int = Field(ge=1, le=10_000_000)
    routeFingerprint: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class CompactionState(_Projection):
    status: Literal["idle", "running", "completed", "skipped", "failed", "unknown"]
    count: int = Field(ge=0)
    requestId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    tokensBefore: int | None = Field(default=None, ge=0, le=100_000_000)
    tokensAfter: int | None = Field(default=None, ge=0, le=100_000_000)
    reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,95}$")


class HistoryState(_Projection):
    status: Literal["ready", "pending", "unknown"]
    revision: str | None = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledgedMessages: int = Field(ge=0, le=MAX_HISTORY_MESSAGES)
    reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,95}$")


class ChatContextState(_Projection):
    schemaVersion: Literal[1]
    status: Literal["ready", "missing", "busy", "unavailable"]
    sessionRevision: str | None = Field(max_length=160, pattern=r"^[A-Za-z0-9:_-]+$")
    context: ContextUsage | None
    model: ContextModel | None
    compaction: CompactionState
    history: HistoryState


def public_context(value):
    state = ChatContextState.model_validate(value)
    if state.context and state.model and state.context.window != state.model.contextWindow:
        raise ValueError("Context measurement does not belong to the active model")
    if state.compaction.status == "completed" and state.compaction.tokensBefore is not None and state.compaction.tokensAfter is not None and state.compaction.tokensAfter > state.compaction.tokensBefore:
        raise ValueError("Compaction tokens after cannot exceed tokens before")
    result = state.model_dump(exclude_none=True)
    # These explicit nulls distinguish unavailable measurements from zero.
    for key in ("sessionRevision", "context", "model"):
        if getattr(state, key) is None:
            result[key] = None
    result["history"]["revision"] = state.history.revision
    return result
