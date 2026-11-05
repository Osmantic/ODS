"""Native Windows local chat router (/api/local-chat/*).

Provides direct streaming from llama-server.exe with contextStart KV-cache
prefix preservation, server-persisted conversations, document/PDF extraction,
and sandboxed local workspace file creation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from native_llm import (
    DEFAULT_NATIVE_SYSTEM_PROMPT,
    extract_document_text,
    get_native_llm_status,
    get_workspace_root,
    load_persisted_conversations,
    save_persisted_conversations,
    stream_native_chat_completion,
)
from security import verify_api_key

router = APIRouter(prefix="/api/local-chat", tags=["local-chat"])


class LocalChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str | list[dict[str, Any]]


class LocalChatStreamRequest(BaseModel):
    messages: list[LocalChatMessage]
    systemPrompt: str = DEFAULT_NATIVE_SYSTEM_PROMPT
    contextStart: int = Field(default=0, ge=0)
    contextWindow: int | None = Field(default=None, ge=512, le=262144)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    maxTokens: int | None = Field(default=None, ge=16, le=32768)
    model: str | None = None


class LocalConversationsPayload(BaseModel):
    conversations: list[dict[str, Any]]


@router.get("/status", dependencies=[Depends(verify_api_key)])
async def local_chat_status() -> dict[str, Any]:
    """Return live status of the native llama-server runtime and GPU preference."""
    return await get_native_llm_status()


@router.get("/conversations", dependencies=[Depends(verify_api_key)])
async def get_local_conversations() -> dict[str, Any]:
    """Return server-persisted local chat conversations."""
    return {"conversations": load_persisted_conversations()}


@router.put("/conversations", dependencies=[Depends(verify_api_key)])
async def put_local_conversations(payload: LocalConversationsPayload) -> dict[str, Any]:
    """Persist local chat conversations (including per-conversation contextStart)."""
    try:
        saved = save_persisted_conversations(payload.conversations)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"saved": len(saved), "conversations": saved}


@router.delete("/conversations/{conversation_id}", dependencies=[Depends(verify_api_key)])
async def delete_local_conversation(conversation_id: str) -> dict[str, Any]:
    """Delete a single persisted conversation by ID."""
    existing = load_persisted_conversations()
    kept = [c for c in existing if str(c.get("id", "")) != conversation_id]
    save_persisted_conversations(kept)
    return {"deleted": len(existing) - len(kept), "conversations": kept}


@router.post("/extract-document", dependencies=[Depends(verify_api_key)])
async def extract_document_endpoint(file: UploadFile = File(...)) -> dict[str, Any]:
    """Extract UTF-8 text from an uploaded PDF, Markdown, CSV, JSON, or code file."""
    raw_bytes = await file.read()
    try:
        return extract_document_text(
            filename=file.filename or "document.txt",
            raw_bytes=raw_bytes,
            content_type=file.content_type or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


@router.post("/stream", dependencies=[Depends(verify_api_key)])
async def stream_local_chat(request: LocalChatStreamRequest) -> StreamingResponse:
    """Stream a chat completion from native llama-server with KV-cache prefix reuse."""
    raw_messages = [m.model_dump() for m in request.messages]
    context_window = request.contextWindow
    if not context_window:
        status = await get_native_llm_status()
        context_window = int(status.get("contextLength") or 8192)

    generator = stream_native_chat_completion(
        raw_messages,
        system_prompt=request.systemPrompt,
        context_start=request.contextStart,
        context_window=context_window,
        temperature=request.temperature,
        max_tokens=request.maxTokens,
        model_name=request.model,
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/workspace/files", dependencies=[Depends(verify_api_key)])
async def list_workspace_files() -> dict[str, Any]:
    """List files created in the local chat workspace."""
    root = get_workspace_root()
    files: list[dict[str, Any]] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file() or item.name.startswith("."):
            continue
        rel = item.relative_to(root).as_posix()
        files.append(
            {
                "path": rel,
                "sizeBytes": item.stat().st_size,
                "downloadUrl": f"/api/local-chat/workspace/files/{rel}",
            }
        )
    return {"files": files}


@router.get("/workspace/files/{file_path:path}", dependencies=[Depends(verify_api_key)])
async def download_workspace_file(file_path: str) -> FileResponse:
    """Download a file from the local chat workspace."""
    root = get_workspace_root()
    cleaned = (file_path or "").strip().replace("\\", "/").lstrip("/")
    if not cleaned or ".." in cleaned.split("/"):
        raise HTTPException(status_code=400, detail="Invalid workspace file path")
    target = (root / cleaned).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid workspace file path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Workspace file not found")
    return FileResponse(path=str(target), filename=target.name)
