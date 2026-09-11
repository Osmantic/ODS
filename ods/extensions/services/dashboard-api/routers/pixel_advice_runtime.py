"""Strict owner-only projection for the private advisory setup flow."""
import copy
import json
import re

from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from host_agent_client import AgentHTTPError,AgentProtocolError,AgentUnavailable,async_request_json as request_agent_json
from security import verify_api_key
from .pixel_providers import _pairs,_float,_constant,_check_depth

router=APIRouter(tags=['pixel-advice-runtime'])
UUID=re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}')
HEX=re.compile(r'[a-f0-9]{64}')
RUNTIME=re.compile(r'runtime-[a-f0-9]{32}')
FIELDS={'requestId','expectedRevision','sourceSha256','candidateId','confirmed'}


def match(pattern,value):
    return isinstance(value,str) and pattern.fullmatch(value)


def revision(value):
    return type(value) is int and 0<=value<2**53-1


def job(value):
    if (not isinstance(value,dict) or set(value)!={'jobId','expectedRevision','runtimeId','candidateId','status','error'}
            or not match(UUID,value['jobId']) or not revision(value['expectedRevision'])
            or not match(RUNTIME,value['runtimeId']) or not match(HEX,value['candidateId'])
            or value['status'] not in ('running','cancelling','completed','cancelled','failed','interrupted')
            or value['error'] not in (None,'setup-failed','setup-drift')
            or (value['status']=='failed')!=(value['error'] is not None)):
        raise ValueError('invalid-setup-job')
    return copy.deepcopy(value)


def readiness(value):
    if (not isinstance(value,dict) or set(value)!={'status','revision','runtimeId','sourceSha256','candidates','job','host'}
            or value['status'] not in ('ready','missing','drift','not-configured','unsupported')
            or not revision(value['revision']) or value['runtimeId'] is not None and not match(RUNTIME,value['runtimeId'])
            or value['sourceSha256'] is not None and not match(HEX,value['sourceSha256'])
            or not isinstance(value['host'],str) or not 0<len(value['host'])<=255
            or not isinstance(value['candidates'],list) or len(value['candidates'])>16):
        raise ValueError('invalid-setup-readiness')
    if value['status'] in ('ready','drift') and (value['runtimeId'] is None or value['sourceSha256'] is None):
        raise ValueError('invalid-setup-readiness')
    seen=set()
    for candidate in value['candidates']:
        if (not isinstance(candidate,dict) or set(candidate)!={'id','path','version','canPrepare'}
                or not match(HEX,candidate['id']) or candidate['id'] in seen
                or not isinstance(candidate['path'],str) or not candidate['path'].startswith('/')
                or not 1<len(candidate['path'])<=1024 or any(ord(c)<32 for c in candidate['path'])
                or not isinstance(candidate['version'],str) or not re.fullmatch(r'3\.[0-9]{1,2}\.[0-9]{1,3}',candidate['version'])
                or type(candidate['canPrepare']) is not bool):
            raise ValueError('invalid-python-candidate')
        seen.add(candidate['id'])
    if value['job'] is not None: job(value['job'])
    return copy.deepcopy(value)


async def request_body(request,action):
    raw=bytearray()
    async for part in request.stream():
        if len(raw)+len(part)>8192:
            raise HTTPException(413,'Setup request exceeds size limit')
        raw.extend(part)
    try:
        text=bytes(raw).decode(); _check_depth(text)
        value=json.loads(text,object_pairs_hook=_pairs,parse_float=_float,parse_constant=_constant)
        if not isinstance(value,dict) or set(value)!=(FIELDS if action=='prepare' else {'jobId'}):
            raise ValueError()
        if not match(UUID,value['requestId' if action=='prepare' else 'jobId']): raise ValueError()
        if action=='prepare' and (not revision(value['expectedRevision']) or value['confirmed'] is not True
                or not match(HEX,value['sourceSha256']) or not match(HEX,value['candidateId'])):
            raise ValueError()
        return value
    except (ValueError,TypeError,KeyError,RecursionError):
        raise HTTPException(400,'Invalid setup request') from None


async def forward(method,path,body=None):
    try:
        return await request_agent_json(method,path,payload=body,timeout=10)
    except AgentHTTPError as error:
        raise HTTPException(error.status_code if error.status_code in (400,409,413,503) else 502,
            'Setup request failed. Refresh readiness and check the tracked setup before retrying.') from None
    except AgentUnavailable:
        raise HTTPException(503,'Setup service unavailable; no automatic retry was made') from None
    except AgentProtocolError:
        raise HTTPException(502,'Invalid setup response') from None


@router.get('/api/pixel/advice-runtime')
async def get_readiness(_key: str=Depends(verify_api_key)):
    result=await forward('GET','/v1/pixel/advice-runtime')
    try:
        return JSONResponse(readiness(result),headers={'Cache-Control':'no-store'})
    except (ValueError,TypeError,KeyError):
        raise HTTPException(502,'Invalid setup response') from None


@router.post('/api/pixel/advice-runtime/{action}')
async def mutate(action:str,request:Request,_key: str=Depends(verify_api_key)):
    if action not in ('prepare','status','cancel'): raise HTTPException(404,'Unknown setup action')
    body=await request_body(request,action)
    result=await forward('POST','/v1/pixel/advice-runtime/'+action,body)
    try:
        result=job(result)
        if result['jobId']!=body.get('requestId',body.get('jobId')): raise ValueError()
        if action=='prepare' and (result['expectedRevision']!=body['expectedRevision'] or result['candidateId']!=body['candidateId']):
            raise ValueError()
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except (ValueError,TypeError,KeyError):
        raise HTTPException(502,'Invalid setup response') from None
