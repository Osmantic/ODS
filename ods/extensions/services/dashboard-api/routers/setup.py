"""Setup wizard, persona management, and chat endpoints."""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from config import SERVICES, PERSONAS, SETUP_CONFIG_DIR, INSTALL_DIR
from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentUnavailable,
    request_json as request_agent_json,
)
from models import PersonaRequest, ChatRequest
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])


def get_active_persona_prompt() -> str:
    """Get the system prompt for the active persona."""
    persona_file = SETUP_CONFIG_DIR / "persona.json"
    if persona_file.exists():
        try:
            with open(persona_file) as f:
                data = json.load(f)
                return data.get("system_prompt", PERSONAS["general"]["system_prompt"])
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            logger.debug("Failed to read persona.json, using default prompt")
    return PERSONAS["general"]["system_prompt"]


@router.get("/api/setup/status")
async def setup_status(api_key: str = Depends(verify_api_key)):
    """Check if this is a first-run scenario."""
    setup_complete_file = SETUP_CONFIG_DIR / "setup-complete.json"
    first_run = not setup_complete_file.exists()

    step = 0
    progress_file = SETUP_CONFIG_DIR / "setup-progress.json"
    if progress_file.exists():
        try:
            with open(progress_file) as f:
                step = json.load(f).get("step", 0)
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            logger.debug("Failed to read setup-progress.json")

    persona = None
    persona_file = SETUP_CONFIG_DIR / "persona.json"
    if persona_file.exists():
        try:
            with open(persona_file) as f:
                persona = json.load(f).get("persona")
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            logger.debug("Failed to read persona.json for setup status")

    return {"first_run": first_run, "step": step, "persona": persona, "personas_available": list(PERSONAS.keys())}


@router.post("/api/setup/persona")
async def setup_persona(request: PersonaRequest, api_key: str = Depends(verify_api_key)):
    """Set the user's chosen persona."""
    if request.persona not in PERSONAS:
        raise HTTPException(status_code=400, detail=f"Invalid persona. Choose from: {list(PERSONAS.keys())}")

    persona_info = PERSONAS[request.persona]
    SETUP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    persona_data = {
        "persona": request.persona, "name": persona_info["name"],
        "system_prompt": persona_info["system_prompt"], "icon": persona_info["icon"],
        "selected_at": datetime.now(timezone.utc).isoformat()
    }
    with open(SETUP_CONFIG_DIR / "persona.json", "w") as f:
        json.dump(persona_data, f, indent=2)

    with open(SETUP_CONFIG_DIR / "setup-progress.json", "w") as f:
        json.dump({"step": 2, "persona_selected": True}, f)

    return {"success": True, "persona": request.persona, "name": persona_info["name"], "message": f"Great choice! Your assistant is now a {persona_info['name']}."}


@router.post("/api/setup/complete")
async def setup_complete(api_key: str = Depends(verify_api_key)):
    """Mark the first-run setup as complete."""
    SETUP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    with open(SETUP_CONFIG_DIR / "setup-complete.json", "w") as f:
        json.dump({"completed_at": datetime.now(timezone.utc).isoformat(), "version": "1.0.0"}, f, indent=2)

    progress_file = SETUP_CONFIG_DIR / "setup-progress.json"
    if progress_file.exists():
        progress_file.unlink()

    return {"success": True, "redirect": "/", "message": "Setup complete! Welcome to ODS."}


@router.get("/api/setup/persona/{persona_id}")
async def get_persona_info(persona_id: str, api_key: str = Depends(verify_api_key)):
    """Get details about a specific persona."""
    if persona_id not in PERSONAS:
        raise HTTPException(status_code=404, detail=f"Persona not found: {persona_id}")
    return {"id": persona_id, **PERSONAS[persona_id]}


@router.get("/api/setup/personas")
async def list_personas(api_key: str = Depends(verify_api_key)):
    """List all available personas."""
    return {"personas": [{"id": pid, **pdata} for pid, pdata in PERSONAS.items()]}


@router.post("/api/setup/test")
async def run_setup_diagnostics(api_key: str = Depends(verify_api_key)):
    """Run diagnostic tests for setup wizard."""
    script_path = Path(INSTALL_DIR) / "scripts" / "ods-test-functional.sh"
    if not script_path.exists():
        script_path = Path(os.getcwd()) / "ods-test-functional.sh"

    if not script_path.exists():
        async def error_stream():
            yield "Diagnostic script not found. Running basic connectivity tests...\n"
            all_ok = True
            async with aiohttp.ClientSession() as session:
                services = [
                    (cfg.get("name", sid), f"http://{cfg.get('host', sid)}:{cfg.get('port', 80)}{cfg.get('health', '/')}")
                    for sid, cfg in SERVICES.items()
                ]
                for name, url in services:
                    try:
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                yield f"\u2713 {name}: {resp.status}\n"
                            else:
                                yield f"\u2717 {name}: {resp.status}\n"
                                all_ok = False
                    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                        yield f"\u2717 {name}: {e}\n"
                        all_ok = False
            # Emit trailer + sentinel in a single chunk (see run_tests() for
            # why separate yields drop the sentinel at the Starlette boundary).
            trailer = "All tests passed!" if all_ok else "Some tests failed."
            result = "PASS" if all_ok else "FAIL"
            rc = 0 if all_ok else 1
            yield f"\n{trailer}\n__ODS_RESULT__:{result}:{rc}\n"
        return StreamingResponse(error_stream(), media_type="text/plain")

    async def run_tests():
        process = await asyncio.create_subprocess_exec(
            "bash", str(script_path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            try:
                async for line in process.stdout:
                    yield line.decode()
                await process.wait()
                # Emit the human-readable trailer AND the machine-readable sentinel
                # as a SINGLE chunk. Starlette's StreamingResponse finalizes the
                # HTTP stream as soon as the async generator exits; when trailer
                # and sentinel are separate yields, the final sentinel bytes have
                # been observed to never reach the client (the generator yields
                # them but the transport drops the last chunk during close).
                # Combining into one yield guarantees both land on the wire.
                trailer = "All tests passed!" if process.returncode == 0 else "Some tests failed."
                status = "PASS" if process.returncode == 0 else "FAIL"
                yield f"\n{trailer}\n__ODS_RESULT__:{status}:{process.returncode}\n"
            except (OSError, asyncio.CancelledError):
                # Re-raise cancellation/disconnect — the client is gone, no point
                # emitting a sentinel into a dead stream and CancelledError must
                # propagate so the runtime can finalize the task tree.
                raise
            except Exception as exc:  # noqa: BLE001 — sentinel contract requires *some* terminal signal
                # The frontend SetupWizard parser treats absence of a sentinel as
                # failure, so even when the runner blows up unexpectedly we still
                # close the stream with a FAIL sentinel rather than leaving the
                # client to fall back on best-effort log scraping.
                logger.exception("run_setup_diagnostics generator raised: %s", exc)
                yield f"\nDiagnostic runner error: {exc}\n__ODS_RESULT__:FAIL:1\n"
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except OSError:
                    pass
                await process.wait()

    return StreamingResponse(run_tests(), media_type="text/plain")


@router.post("/api/chat")
async def chat(request: ChatRequest, api_key: str = Depends(verify_api_key)):
    """Simple chat endpoint for the setup wizard QuickWin step."""
    system_prompt = request.system or get_active_persona_prompt()

    _llm = SERVICES.get("llama-server", {})
    llm_url = os.environ.get("OLLAMA_URL", f"http://{_llm.get('host', 'llama-server')}:{_llm.get('port', 0)}")
    model = os.environ.get("LLM_MODEL", "qwen3-coder-next")

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": request.message}],
        "max_tokens": 2048, "temperature": 0.7
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            _api_path = os.environ.get("LLM_API_BASE_PATH", "/v1")
            async with session.post(f"{llm_url}{_api_path}/chat/completions", json=payload, headers={"Content-Type": "application/json"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    response_text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    # Strip thinking model tags — content may contain <think>...</think> blocks
                    response_text = re.sub(r'<think>[\s\S]*?</think>\s*', '', response_text).strip()
                    return {"response": response_text, "success": True}
                else:
                    error_text = await resp.text()
                    raise HTTPException(status_code=resp.status, detail=f"LLM error: {error_text}")
    except aiohttp.ClientError:
        logger.exception("Cannot reach LLM backend")
        raise HTTPException(status_code=503, detail="Cannot reach LLM backend")


# ---------------------------------------------------------------------------
# Network configuration — Wi-Fi scan / connect / status
#
# These are thin proxies in front of ods-host-agent. Network mutation needs
# root, so the dashboard-api never touches nmcli directly — it forwards to
# the host-agent which runs as root and has the actual platform integration.
#
# The host-agent already enforces:
#   * Linux + nmcli precondition (returns 501 otherwise)
#   * SSID/password validation (length + control chars)
#   * Password is never logged
#
# So this layer just validates the request shape, forwards it, and translates
# host-agent errors into FastAPI HTTPException with sensible status codes.
# ---------------------------------------------------------------------------


class WifiConnectRequest(BaseModel):
    ssid: str = Field(..., min_length=1, max_length=32)
    password: str = Field(default="", max_length=63)

    @field_validator("ssid")
    @classmethod
    def _ssid_no_control_chars(cls, v: str) -> str:
        if any(c in v for c in ("\n", "\r", "\0")):
            raise ValueError("ssid contains invalid characters")
        return v

    @field_validator("password")
    @classmethod
    def _password_no_control_chars(cls, v: str) -> str:
        if any(c in v for c in ("\n", "\r", "\0")):
            raise ValueError("password contains invalid characters")
        return v


def _call_agent(path: str, method: str = "GET", payload: dict | None = None, timeout: int = 60) -> dict:
    """Forward a request to the host-agent.

    Raises HTTPException with a status code derived from the host-agent's
    response — 503 when the agent itself is unreachable, otherwise the
    upstream status. Never logs the request payload (which may contain
    Wi-Fi passwords); logs only the path and resulting status.
    """
    try:
        return request_agent_json(method, path, payload=payload, timeout=timeout)
    except AgentHTTPError as exc:
        logger.info("host-agent %s %s -> %s", method, path, exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except AgentUnavailable as exc:
        logger.warning("host-agent %s %s unreachable: %s", method, path, exc)
        raise HTTPException(
            status_code=503,
            detail="ODS host agent is not reachable.",
        ) from exc
    except AgentProtocolError as exc:
        logger.exception("host-agent %s %s failed", method, path)
        raise HTTPException(
            status_code=500,
            detail=f"Host agent call failed: {exc}",
        ) from exc


@router.get("/api/setup/wifi-scan", dependencies=[Depends(verify_api_key)])
async def setup_wifi_scan() -> dict:
    """Return nearby Wi-Fi networks (Linux + NetworkManager only)."""
    return await asyncio.to_thread(_call_agent, "/v1/network/wifi-scan", "GET", None, 25)


@router.post("/api/setup/wifi-connect", dependencies=[Depends(verify_api_key)])
async def setup_wifi_connect(payload: WifiConnectRequest) -> dict:
    """Attempt to join a Wi-Fi network. Returns 400 on wrong-password or unknown SSID."""
    return await asyncio.to_thread(
        _call_agent,
        "/v1/network/wifi-connect",
        "POST",
        {"ssid": payload.ssid, "password": payload.password},
        60,
    )


@router.get("/api/setup/network-status", dependencies=[Depends(verify_api_key)])
async def setup_network_status() -> dict:
    """Current network state: connected interface, IP, gateway, Wi-Fi flag.

    Always returns 200 even on non-Linux — the response carries
    `platform_supported: false` so the wizard can render a fallback.
    """
    return await asyncio.to_thread(_call_agent, "/v1/network/status", "GET", None, 10)


class WifiForgetRequest(BaseModel):
    connection: str = Field(..., min_length=1, max_length=64)

    @field_validator("connection")
    @classmethod
    def _no_control_chars(cls, v: str) -> str:
        if any(c in v for c in ("\n", "\r", "\0")):
            raise ValueError("connection contains invalid characters")
        return v


@router.post("/api/setup/wifi-forget", dependencies=[Depends(verify_api_key)])
async def setup_wifi_forget(payload: WifiForgetRequest) -> dict:
    """Delete a saved NetworkManager connection profile."""
    return await asyncio.to_thread(
        _call_agent,
        "/v1/network/wifi-forget",
        "POST",
        {"connection": payload.connection},
        15,
    )
