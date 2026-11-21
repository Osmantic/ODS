"""Lazy dashboard-owned storage for explicit Talk conversations."""

import asyncio
from pathlib import Path

import config
from talk_conversations import ConversationBridge, ConversationError, _settle
from talk_store import StoreError, TalkStore

_bridge = None
_path = None
_init_lock = asyncio.Lock()


async def get_bridge() -> ConversationBridge:
    global _bridge, _path
    path = Path(config.DATA_DIR) / "talk" / "conversations.sqlite3"
    async with _init_lock:
        if _bridge is None or _path != path:
            try:
                store = await _settle(asyncio.to_thread(TalkStore, path))
            except StoreError as exc:
                raise ConversationError(exc.code, exc.message) from exc
            _bridge = ConversationBridge(store)
            _path = path
        return _bridge


def error_detail(exc: ConversationError) -> tuple[int, dict]:
    status = {
        "invalid": 422, "not_found": 410, "expired": 410,
        "recovery": 409, "busy": 409, "conflict": 409,
        "capacity": 429, "unavailable": 503, "unsupported": 503,
        "timeout": 504,
    }.get(exc.code, 502)
    return status, {"code": "conversation_" + exc.code, "message": exc.message}
