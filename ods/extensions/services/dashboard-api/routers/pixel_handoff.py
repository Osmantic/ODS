"""Owner-only checkpoint inspection and decisions; no publication/agent API."""
import copy
import hashlib
import json
import re

from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from host_agent_client import AgentHTTPError,AgentProtocolError,AgentUnavailable,async_request_json as request_agent_json
from security import verify_api_key
from .pixel_providers import _pairs,_float,_constant,_check_depth

router=APIRouter(tags=['pixel-handoff'])
RUN=re.compile(r'(?:chatcmpl_)?[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}',re.I)
ID=re.compile(r'[a-z][a-z0-9_-]{0,63}')
DIGEST=re.compile(r'[a-f0-9]{64}')
META={'runId','checkpointDigest','expiresAt','checkpointBytes','recipient','status'}
DECISION={'runId','checkpointDigest','approved','allowCloud','acceptUnknownCost'}
STATUSES={'pending','approved','declined','expired','cancelled','interrupted'}


def _parse(text):
    _check_depth(text)
    return json.loads(text,object_pairs_hook=_pairs,parse_float=_float,parse_constant=_constant)


def normalize_status(value,*,checkpoint):
    if (type(value) is not dict or set(value)!=(META|{'checkpointJson'} if checkpoint else META)
            or type(value['runId']) is not str or not RUN.fullmatch(value['runId'])
            or type(value['checkpointDigest']) is not str or not DIGEST.fullmatch(value['checkpointDigest'])
            or type(value['expiresAt']) is not int or not 0<value['expiresAt']<2**53
            or type(value['checkpointBytes']) is not int or not 0<value['checkpointBytes']<=2*1024*1024
            or value['status'] not in STATUSES):
        raise ValueError('invalid-handoff-response')
    recipient=value['recipient']
    fields={'id','label','kind','baseUrl','model','revision','scope','previousProviderId'}
    if (type(recipient) is not dict or set(recipient) not in (fields,fields|{'selectionScope'})
            or 'selectionScope' in recipient and recipient['selectionScope'] not in ('task','conversation','default')
            or recipient['scope']!='run' or recipient['kind'] not in ('local','ods-peer','cloud')
            or type(recipient['revision']) is not int or not 0<=recipient['revision']<2**53
            or any(type(recipient[k]) is not str or not ID.fullmatch(recipient[k]) for k in ('id','previousProviderId'))
            or recipient['id']==recipient['previousProviderId']):
        raise ValueError('invalid-handoff-recipient')
    for key,limit in [('label',256),('model',256),('baseUrl',2048)]:
        if type(recipient[key]) is not str or not 0<len(recipient[key])<=limit or '\0' in recipient[key]:
            raise ValueError('invalid-handoff-recipient')
    if checkpoint:
        raw=value['checkpointJson']
        if type(raw) is not str or len(raw.encode())!=value['checkpointBytes'] or hashlib.sha256(raw.encode()).hexdigest()!=value['checkpointDigest']:
            raise ValueError('invalid-handoff-checkpoint')
        document=_parse(raw)
        if (type(document) is not dict or set(document)!={'schemaVersion','runId','sessionId','agentId','workspaceDir',
                'recipient','dataScope','returnAction','prompt','systemPrompt','messages'}
                or type(document['schemaVersion']) is not int or document['schemaVersion']!=1
                or document['runId']!=value['runId'] or document['recipient']!=recipient
                or document['agentId']!='pixel' or document['dataScope']!='conversation-and-this-run-tool-results'
                or document['returnAction']!=('owner-scope-return-or-end' if 'selectionScope' in recipient else 'configured-leader-on-next-run') or type(document['messages']) is not list
                or any(type(document[k]) is not str for k in ('sessionId','workspaceDir','prompt','systemPrompt'))):
            raise ValueError('invalid-handoff-checkpoint')
    return copy.deepcopy(value)


async def _body(request,action):
    raw=bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>4096: raise HTTPException(413,'Handoff decision too large')
        raw.extend(chunk)
    try:
        body=_parse(bytes(raw).decode())
        if type(body) is not dict or set(body)!=({'runId'} if action=='status' else DECISION if action=='decide' else set()):
            raise ValueError()
        if action!='list' and (type(body['runId']) is not str or not RUN.fullmatch(body['runId'])): raise ValueError()
        if action=='decide' and (type(body['checkpointDigest']) is not str or not DIGEST.fullmatch(body['checkpointDigest'])
                or any(type(body[k]) is not bool for k in ('approved','allowCloud','acceptUnknownCost'))): raise ValueError()
        return body
    except (ValueError,TypeError,KeyError,RecursionError):
        raise HTTPException(400,'Invalid handoff request') from None


@router.post('/api/pixel/handoff/{action}')
async def handoff(action:str,request:Request,_key:str=Depends(verify_api_key)):
    if action not in ('list','status','decide'): raise HTTPException(404,'Unknown handoff action')
    body=await _body(request,action)
    try:
        raw=await request_agent_json('POST','/v1/pixel/handoff/'+action,payload=body,timeout=10)
    except AgentHTTPError as exc:
        raise HTTPException(exc.status_code if exc.status_code in (400,409,413,503) else 502,
                            'Handoff state uncertain; reload this request before deciding again') from None
    except AgentUnavailable:
        raise HTTPException(503,'Handoff service unavailable; reload before deciding again') from None
    except AgentProtocolError:
        raise HTTPException(502,'Invalid handoff response') from None
    try:
        if action=='list':
            if (type(raw) is not dict or set(raw)!={'items','unavailableCount'} or type(raw['items']) is not list
                    or len(raw['items'])>4096 or type(raw['unavailableCount']) is not int or not 0<=raw['unavailableCount']<=4096):
                raise ValueError()
            result={'items':[normalize_status(row,checkpoint=False) for row in raw['items']],
                    'unavailableCount':raw['unavailableCount']}
            if any(row['status']!='pending' for row in result['items']): raise ValueError()
        else:
            result=normalize_status(raw,checkpoint=True)
            if result['runId']!=body['runId']: raise ValueError()
            if action=='decide' and result['checkpointDigest']!=body['checkpointDigest']: raise ValueError()
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except (ValueError,TypeError,KeyError,RecursionError):
        raise HTTPException(502,'Invalid handoff response') from None
