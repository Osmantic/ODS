"""Owner-only capsule advisory jobs. No implicit chat/history transfer."""
import copy
import hashlib
import json
import re

from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from host_agent_client import AgentHTTPError,AgentProtocolError,AgentUnavailable,async_request_json as request_agent_json
from security import verify_api_key
from .pixel_providers import _pairs,_float,_constant,_check_depth

router = APIRouter(tags=['pixel-advice'])
JOB_ID = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}')
FIELDS = {'requestId','expectedRevision','providerId','capsule','allowCloud',
          'acceptUnknownCost','maxOutputTokens','deadlineSeconds'}
PUBLIC = {'jobId','revision','providerId','providerLabel','model','kind','capsuleSha256',
          'maxOutputTokens','deadlineSeconds','scope','leaderChanged','toolsAllowed','costStatus','status','result','error'}


def normalize_result(value):
    if (not isinstance(value,dict) or set(value) != PUBLIC
            or not isinstance(value['jobId'],str) or not JOB_ID.fullmatch(value['jobId'])
            or type(value['revision']) is not int or not 0 <= value['revision'] < 2**53-1
            or type(value['maxOutputTokens']) is not int or not 1 <= value['maxOutputTokens'] <= 4096
            or type(value['deadlineSeconds']) is not int or not 1 <= value['deadlineSeconds'] <= 180
            or value['scope'] != 'reviewed-capsule-only' or value['leaderChanged'] is not False
            or value['toolsAllowed'] is not False or value['costStatus'] != 'unknown'
            or value['kind'] not in ('local','ods-peer','cloud')
            or value['status'] not in ('running','cancelling','completed','cancelled','failed','interrupted')
            or value['error'] not in (None,'advice-failed','advice-dependencies-missing')):
        raise ValueError('invalid-advice-response')
    for key,limit in [('providerId',64),('providerLabel',256),('model',256),('capsuleSha256',64)]:
        if not isinstance(value[key],str) or not 0 < len(value[key]) <= limit:
            raise ValueError('invalid-advice-response')
    if not re.fullmatch(r'[a-f0-9]{64}',value['capsuleSha256']):
        raise ValueError('invalid-advice-response')
    if (not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',value['providerId'])
            or (value['status'] == 'failed') != (value['error'] is not None)):
        raise ValueError('invalid-advice-response')
    result = value['result']
    if value['status'] == 'completed':
        if (not isinstance(result,dict) or set(result) != {'text','usage','costStatus','trusted'}
                or result['trusted'] is not False or result['costStatus'] != 'unknown'
                or not isinstance(result['text'],str) or not 0 < len(result['text'].encode()) <= 65536
                or not isinstance(result['usage'],dict)
                or set(result['usage']) != {'prompt_tokens','completion_tokens','total_tokens'}
                or any(v is not None and (type(v) is not int or not 0 <= v <= 10**10) for v in result['usage'].values())):
            raise ValueError('invalid-advice-response')
    elif result is not None:
        raise ValueError('invalid-advice-response')
    return copy.deepcopy(value)


async def body_for(request,action):
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>128*1024:
            raise HTTPException(413,'Advisory request exceeds size limit')
        raw.extend(chunk)
    try:
        text=bytes(raw).decode('utf-8'); _check_depth(text)
        body=json.loads(text,object_pairs_hook=_pairs,parse_float=_float,parse_constant=_constant)
        if not isinstance(body,dict) or set(body) != (FIELDS if action == 'start' else {'jobId'}):
            raise ValueError()
        job=body['requestId' if action == 'start' else 'jobId']
        if not isinstance(job,str) or not JOB_ID.fullmatch(job):
            raise ValueError()
        if action == 'start' and (
                type(body['expectedRevision']) is not int or not 0 <= body['expectedRevision'] < 2**53-1
                or not isinstance(body['providerId'],str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',body['providerId'])
                or not isinstance(body['capsule'],str) or not body['capsule'].strip()
                or len(body['capsule'].encode())>16384 or '\x00' in body['capsule']
                or type(body['allowCloud']) is not bool or type(body['acceptUnknownCost']) is not bool
                or type(body['maxOutputTokens']) is not int or not 1 <= body['maxOutputTokens'] <= 4096
                or type(body['deadlineSeconds']) is not int or not 1 <= body['deadlineSeconds'] <= 180):
            raise ValueError()
    except (ValueError,TypeError,KeyError,RecursionError):
        raise HTTPException(400,'Invalid advisory request') from None
    return body


@router.post('/api/pixel/advice/{action}')
async def advice(action: str,request: Request,_key: str=Depends(verify_api_key)):
    if action not in ('start','status','cancel'):
        raise HTTPException(404,'Unknown advisory action')
    body=await body_for(request,action)
    try:
        raw=await request_agent_json('POST','/v1/pixel/advice/'+action,payload=body,timeout=10)
    except AgentHTTPError as exc:
        raise HTTPException(exc.status_code if exc.status_code in (400,409,413,503) else 502,
                            'Advisory request failed; check the tracked job before starting another') from None
    except AgentUnavailable:
        raise HTTPException(503,'Advisory service unavailable; check the tracked job before starting another') from None
    except AgentProtocolError:
        raise HTTPException(502,'Invalid advisory response') from None
    try:
        result=normalize_result(raw)
        if result['jobId'] != body.get('requestId',body.get('jobId')):
            raise ValueError()
        if action == 'start' and (result['providerId'] != body['providerId']
                or result['revision'] != body['expectedRevision']
                or result['capsuleSha256'] != hashlib.sha256(body['capsule'].encode()).hexdigest()
                or result['maxOutputTokens'] != body['maxOutputTokens']
                or result['deadlineSeconds'] > body['deadlineSeconds']
                or result['kind'] == 'cloud' and not (body['allowCloud'] and body['acceptUnknownCost'])):
            raise ValueError()
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except (ValueError,TypeError,RecursionError):
        raise HTTPException(502,'Invalid advisory response') from None
