"""Disposable real dashboard-router -> real host-agent boundary, synthetic keys.

Optional built dashboard is served for browser qualification. Only this fixture
accepts a fixed owner cookie and injects its synthetic API key (standing in for
the already-qualified ODS Edge session boundary). No production session is used.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import sys

ROOT=Path(__file__).resolve().parents[5]
parser=argparse.ArgumentParser()
parser.add_argument('--data',required=True)
parser.add_argument('--ready',required=True)
parser.add_argument('--dashboard')
args=parser.parse_args()
os.environ['DASHBOARD_API_KEY']='synthetic-handoff-owner-key'
sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'extensions/services/dashboard-api'))
from test_pixel_provider_host_api import HostHTTP
HostHTTP.setUpClass()
HostHTTP.agent.DATA_DIR=Path(args.data)
import host_agent_client
host_agent_client.AGENT_URL='http://127.0.0.1:'+str(HostHTTP.server.server_address[1])
host_agent_client.ODS_AGENT_KEY='synthetic-provider-test-key'
from fastapi import FastAPI,Request
from fastapi.responses import FileResponse,JSONResponse,HTMLResponse
from fastapi.staticfiles import StaticFiles
from routers import pixel_handoff,pixel_scopes,pixel_providers
import uvicorn

app=FastAPI()
if args.dashboard:
    @app.middleware('http')
    async def fixture_session(request:Request,call_next):
        if request.cookies.get('ods-handoff-fixture-owner')=='fixture-approved-browser':
            request.scope['headers']=[(k,v) for k,v in request.scope['headers'] if k!=b'authorization']+[
                (b'authorization',b'Bearer synthetic-handoff-owner-key')]
        return await call_next(request)

    @app.get('/fixture-login')
    def fixture_login():
        response=HTMLResponse('<!doctype html><title>Disposable owner fixture</title><p>Synthetic owner session. No production authentication.</p><a href="/pixel">Open Pixel fixture</a>')
        response.set_cookie('ods-handoff-fixture-owner','fixture-approved-browser',httponly=True,samesite='strict',max_age=600)
        return response

app.include_router(pixel_handoff.router)
app.include_router(pixel_scopes.router)
app.include_router(pixel_providers.router)

@app.get('/health')
def health(): return {'status':'ready','fixture':True}

if args.dashboard:
    dashboard=Path(args.dashboard).resolve()
    app.mount('/assets',StaticFiles(directory=dashboard/'assets'),name='assets')

    @app.get('/api/setup/status')
    def setup_status(): return {'firstRun':False,'first_run':False}

    @app.get('/api/pixel/status')
    def pixel_status(): return {'status':'available','runtime':{'source':'local-switchboard','model':'Synthetic approval fixture','contextLength':32768}}

    @app.get('/api/{path:path}')
    def fixture_unrelated(path):
        return JSONResponse({'fixture':True},status_code=503)

    @app.post('/api/auth/session')
    def fixture_session_bootstrap(): return {'fixture':True}

    @app.get('/{path:path}')
    def page(path):
        return FileResponse(dashboard/'index.html',headers={'Cache-Control':'no-store'})

sock=socket.socket(); sock.bind(('127.0.0.1',0)); sock.listen(64)
ready=Path(args.ready)
fd=os.open(ready,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as stream:
    json.dump({'url':'http://127.0.0.1:'+str(sock.getsockname()[1]),'hostPort':HostHTTP.server.server_address[1],
        'pid':os.getpid(),'productionActivation':False},stream)
try:
    uvicorn.Server(uvicorn.Config(app,log_level='warning')).run(sockets=[sock])
finally:
    HostHTTP.tearDownClass()
