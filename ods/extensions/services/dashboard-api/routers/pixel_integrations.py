"""Owner-only Portal integrations: Obsidian notes, ntfy notifications and MCP servers.

The host integrations service keeps the settings and secrets and does the work; this
router checks the request shape and relays it through the host agent. A refusal keeps the
service's sentence so the Settings screen can show the owner what to fix.
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable
from host_agent_client import async_request_json as request_agent_json
from security import verify_api_key

router = APIRouter(tags=['pixel-integrations'])
PREFIX = '/v1/pixel/integrations'
MAX_BYTES = 64 * 1024
FIELDS = {
    'save': {'expectedRevision', 'settings', 'secrets'},
    'test-notification': set(),
    'discover': {'serverId'},
    'test': {'serverId'},
    'restart': {'serverId'},
    'decide': {'approvalId', 'decision'},
    'clear-activity': set(),
}
SLOW = {'discover', 'test'}  # A local server may download itself on its first start.


async def _body(request, action):
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, 'Integrations request exceeds size limit')
    try:
        payload = json.loads(raw or b'{}')
    except ValueError:
        raise HTTPException(400, 'Invalid integrations request') from None
    if not isinstance(payload, dict) or set(payload) - FIELDS[action] or not set(payload) >= FIELDS[action] - {'secrets'}:
        raise HTTPException(400, 'Invalid integrations request')
    return payload


async def _relay(action=None, payload=None):
    path = PREFIX + ('/' + action if action else '')
    try:
        result = await request_agent_json('POST' if action else 'GET', path, payload=payload,
                                          timeout=140 if action in SLOW else 30)
    except AgentHTTPError as error:
        try:
            refusal = json.loads(error.response_text)
        except ValueError:
            refusal = None
        if error.status_code in (400, 409) and isinstance(refusal, dict) and isinstance(refusal.get('message'), str):
            return JSONResponse({'detail': refusal['message'], 'code': str(refusal.get('error', 'failed'))},
                                status_code=error.status_code, headers={'Cache-Control': 'no-store'})
        raise HTTPException(503 if error.status_code == 503 else 502, 'Integrations are unavailable') from None
    except AgentUnavailable:
        raise HTTPException(503, 'Integrations are unavailable') from None
    except AgentProtocolError:
        raise HTTPException(502, 'Invalid integrations response') from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@router.get('/api/pixel/integrations')
async def get_integrations(_key: str = Depends(verify_api_key)):
    return await _relay()


@router.post('/api/pixel/integrations/{action}')
async def change_integrations(action: str, request: Request, _key: str = Depends(verify_api_key)):
    if action not in FIELDS:
        raise HTTPException(404, 'Integrations action not found')
    return await _relay(action, await _body(request, action))
