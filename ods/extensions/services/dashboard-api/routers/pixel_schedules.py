"""Owner-authenticated fixed native scheduler operations. Never replay a write."""
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable, async_request_json as request_agent_json
from pixel_schedules_public import MAX_REQUEST, ScheduleError, parse_request, validate_reply
from security import verify_api_key

router = APIRouter(tags=['pixel-schedules'])
NO_STORE = {'Cache-Control': 'no-store'}


def _error(status, code):
    return HTTPException(status, {'code': code}, headers=NO_STORE)


@router.post('/api/pixel/schedules')
async def schedules(request: Request, _key: str = Depends(verify_api_key)):
    if request.query_params or request.headers.get('content-type', '').split(';')[0].strip() != 'application/json':
        raise _error(400, 'invalid-task')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_REQUEST:
            raise _error(413, 'task-too-large')
        raw.extend(chunk)
    try:
        body = parse_request(raw)
    except ScheduleError:
        raise _error(400, 'invalid-task') from None
    try:
        result = await request_agent_json('POST', '/v1/pixel/schedules', payload=body, timeout=200)
        validate_reply(body, result)
    except AgentHTTPError as error:
        if error.status_code == 404:
            raise _error(503, 'scheduler-update-required') from None
        try:
            response = json.loads(error.response_text)
            code = response.get('error') if type(response) is dict else None
        except (ValueError, RecursionError):
            code = None
        if type(code) is not str or not re.fullmatch(r'[a-z][a-z0-9-]{0,95}', code):
            code = 'scheduler-unavailable'
        raise _error(error.status_code if error.status_code in (400, 403, 409, 413, 503) else 503, code) from None
    except (AgentUnavailable, AgentProtocolError, ValueError, RecursionError):
        # The task may already exist. The same request identity can be checked;
        # this route never repeats a native add after an uncertain reply.
        raise _error(503, 'scheduler-unavailable') from None
    return JSONResponse(result, headers=NO_STORE)
