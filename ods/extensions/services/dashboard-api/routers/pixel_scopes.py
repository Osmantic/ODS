"""Authenticated owner scope preferences; never a checkpoint approval API."""
import copy
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from host_agent_client import AgentHTTPError, AgentProtocolError, AgentUnavailable, async_request_json as request_agent_json
from security import verify_api_key
from .pixel_handoff import _parse

router = APIRouter(tags=['pixel-provider-scopes'])
CHAT = re.compile(r'[A-Za-z0-9_-]{1,128}')
TASK = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}')
ID = re.compile(r'[a-z][a-z0-9_-]{0,63}')
SCOPES = ('task', 'conversation', 'default')
COMMON = {'chatId', 'expectedRevision', 'taskId'}
RULE = {'providerId', 'providerRevision', 'allowCloud', 'acceptUnknownCost'}
FIELDS = {'status': {'chatId'}, 'begin': COMMON, 'end': COMMON,
          'select': COMMON | {'scope'} | RULE, 'return': COMMON | {'scope'}}


def revision(value):
    return type(value) is int and 0 <= value < 2**53-1


def rule(value):
    if value is None:
        return
    if (type(value) is not dict or set(value) != RULE or type(value['providerId']) is not str
            or not ID.fullmatch(value['providerId']) or not revision(value['providerRevision'])
            or any(type(value[key]) is not bool for key in ('allowCloud', 'acceptUnknownCost'))):
        raise ValueError()


def normalize_status(value, chat):
    fields = {'schemaVersion', 'revision', 'chatId', 'taskId', 'taskSelection', 'conversationSelection',
              'defaultSnapshot', 'defaultSelection', 'effectiveScope', 'effectiveSelection', 'runtimeStatus', 'checkpointApproval'}
    if (type(value) is not dict or set(value) != fields or value['chatId'] != chat
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or not revision(value['revision'])
            or value['runtimeStatus'] != 'preference-only' or value['checkpointApproval'] != 'required-each-handoff-run'
            or value['taskId'] is not None and (type(value['taskId']) is not str or not TASK.fullmatch(value['taskId']))):
        raise ValueError()
    for key in ('taskSelection', 'conversationSelection', 'defaultSnapshot', 'defaultSelection', 'effectiveSelection'):
        rule(value[key])
    if value['taskId'] is None and (value['taskSelection'] is not None or value['defaultSnapshot'] is not None):
        raise ValueError()
    effective = next(((scope, value[key]) for scope, key in (('task', 'taskSelection'),
        ('conversation', 'conversationSelection'), ('default', 'defaultSnapshot')) if value[key] is not None), (None, None))
    if (value['effectiveScope'], value['effectiveSelection']) != effective:
        raise ValueError()
    return copy.deepcopy(value)


@router.post('/api/pixel/provider-scopes/{action}')
async def scopes(action: str, request: Request, _key: str = Depends(verify_api_key)):
    if action not in FIELDS:
        raise HTTPException(404, 'Unknown scope action')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk) > 4096:
            raise HTTPException(413, 'Scope request too large')
        raw.extend(chunk)
    try:
        body = _parse(bytes(raw).decode())
        if type(body) is not dict or set(body) != FIELDS[action] or type(body['chatId']) is not str or not CHAT.fullmatch(body['chatId']):
            raise ValueError()
        if action != 'status':
            if not revision(body['expectedRevision']): raise ValueError()
            if body['taskId'] is not None and (type(body['taskId']) is not str or not TASK.fullmatch(body['taskId'])): raise ValueError()
            if action in ('begin', 'end') and body['taskId'] is None: raise ValueError()
        if action in ('select', 'return') and body['scope'] not in SCOPES: raise ValueError()
        if action == 'select': rule({key: body[key] for key in RULE})
    except (ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(400, 'Invalid scope request') from None
    try:
        result = await request_agent_json('POST', '/v1/pixel/provider-scopes/'+action, payload=body, timeout=10)
        return JSONResponse(normalize_status(result, body['chatId']), headers={'Cache-Control': 'no-store'})
    except AgentHTTPError as error:
        raise HTTPException(error.status_code if error.status_code in (400, 409, 413, 503) else 502,
                            'Preference outcome uncertain; reload before retrying') from None
    except AgentUnavailable:
        raise HTTPException(503, 'Preference service unavailable; reload before retrying') from None
    except (AgentProtocolError, ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(502, 'Invalid scope response; reload before retrying') from None
