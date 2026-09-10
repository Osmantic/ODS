"""Owner-only inference sharing controls, separate from incoming device keys."""
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable
from host_agent_client import async_request_json as request_agent_json
from pixel_sharing_public import normalize_sharing_response
from routers.pixel_providers import MAX_BYTES, _check_depth, _constant, _float, _pairs
from security import verify_api_key

router = APIRouter(tags=['pixel-sharing'])
PREFIX = '/v1/pixel/inference-sharing'


async def _body(request, action):
    raw = bytearray()
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_BYTES:
                    raise HTTPException(413, 'Sharing request exceeds size limit')
                raw.extend(chunk)
        text = bytes(raw).decode('utf-8')
        _check_depth(text)
        payload = json.loads(text, object_pairs_hook=_pairs, parse_float=_float, parse_constant=_constant)
        field = {'issue':'settings', 'enable':'enabled', 'revoke':'deviceId', 'start':None, 'stop':None}[action]
        expected = {'expectedRevision'} | ({field} if field else set())
        if (not isinstance(payload, dict) or set(payload) != expected
                or type(payload['expectedRevision']) is not int or not 0 <= payload['expectedRevision'] < 2**53 - 1):
            raise ValueError('invalid-request')
        if action == 'issue' and not isinstance(payload['settings'], dict):
            raise ValueError('invalid-request')
        if action == 'enable' and type(payload['enabled']) is not bool:
            raise ValueError('invalid-request')
        if action == 'revoke' and not isinstance(payload['deviceId'], str):
            raise ValueError('invalid-request')
        return payload
    except (ValueError, RecursionError, TimeoutError):
        raise HTTPException(400, 'Invalid sharing request') from None


async def _request(action=None, payload=None):
    try:
        raw = await request_agent_json('POST' if action else 'GET',
            PREFIX + ('/' + action if action else ''), payload=payload, timeout=10)
    except AgentHTTPError as error:
        code = error.status_code if error.status_code in (400,409,413,503) else 502
        raise HTTPException(code, 'Sharing request failed') from None
    except AgentUnavailable:
        raise HTTPException(503, 'Sharing is unavailable') from None
    except AgentProtocolError:
        raise HTTPException(502, 'Invalid sharing response') from None
    try:
        result = normalize_sharing_response(raw, issued=action == 'issue')
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(502, 'Invalid sharing response') from None
    return JSONResponse(result, status_code=202 if action in ('start','stop') else 200, headers={'Cache-Control':'no-store'})


@router.get('/api/pixel/inference-sharing')
async def get_sharing(_key: str = Depends(verify_api_key)):
    return await _request()


@router.post('/api/pixel/inference-sharing/{action}')
async def change_sharing(action: str, request: Request, _key: str = Depends(verify_api_key)):
    if action not in ('issue','enable','revoke','start','stop'):
        raise HTTPException(404, 'Sharing action not found')
    return await _request(action, await _body(request, action))
