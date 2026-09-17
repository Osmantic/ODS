import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from aiohttp import web, ClientSession

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('PIXEL_OPENWEBUI_KEY','c'*64)
os.environ.setdefault('PIXEL_PREVIEW_PROXY_KEY','o'*64)
import pixel_edge as edge

TX='a'*64
REV='b'*64
TARGET=dict(model='Qwen3.8-27B',contextLength=16384,maxTokens=8192,reasoning=True)
STATE=dict(schemaVersion=1,status='held',revision=REV,contract=TARGET,pending=True,transactionId=TX,outcome=None)
BEGIN=dict(operation='model-begin',request=dict(transactionId=TX,revision=REV))


class ModelTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.calls=[]
        self.reply={**STATE,'privateJournal':{'credential':'private'}}
        self.reply_status=200
        async def upstream(request):
            self.calls.append((request.method,request.headers.get('Authorization'),await request.text()))
            return web.json_response(self.reply,status=self.reply_status)
        app=web.Application()
        app.router.add_post('/v1/model-control',upstream)
        self.upstream=web.AppRunner(app)
        await self.upstream.setup()
        socket=str(Path(self.directory.name)/'control.sock')
        await web.UnixSite(self.upstream,socket).start()
        self.patches=[patch.object(edge,'_SOCKET_PATH',socket),patch.object(edge,'config_token','c'*64),patch.object(edge,'preview_proxy_token','o'*64)]
        for item in self.patches: item.start()
        app=web.Application()
        app.router.add_post('/v1/model-control',edge.handle_model_control)
        self.runner=web.AppRunner(app)
        await self.runner.setup()
        site=web.TCPSite(self.runner,'127.0.0.1',0)
        await site.start()
        self.url='http://127.0.0.1:%d/v1/model-control'%site._server.sockets[0].getsockname()[1]
        self.client=ClientSession()

    async def asyncTearDown(self):
        await self.client.close()
        await self.runner.cleanup()
        await self.upstream.cleanup()
        for item in reversed(self.patches): item.stop()
        self.directory.cleanup()

    async def send(self,body,key='o'*64,suffix=''):
        response=await self.client.post(self.url+suffix,headers={'Authorization':'Bearer '+key},json=body)
        async with response:
            return response.status,await response.json()

    async def test_owner_only_closed_frames(self):
        for key in ('','c'*64): self.assertEqual((await self.send(BEGIN,key))[0],401)
        for body in ({**BEGIN,'path':'/etc'},{'operation':'model-status','request':{}},
                     {'operation':'model-apply','request':dict(transactionId=TX,target={**TARGET,'endpoint':'http://foreign'})},
                     {'operation':'model-finish','request':dict(transactionId=TX,outcome='release')}, {'padding':'x'*2049}):
            self.assertEqual((await self.send(body))[0],400)
        self.assertEqual((await self.send(BEGIN,suffix='?user=root'))[0],400)
        self.assertEqual(self.calls,[])

    async def test_projected_reply_exact_forwarding(self):
        self.assertEqual(await self.send(BEGIN),(200,STATE))
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0][:2],('POST','Bearer '+'o'*64))
        self.assertEqual(json.loads(self.calls[0][2]),BEGIN)

    async def test_rejection_is_not_retried_or_leaked(self):
        self.reply_status=409
        self.reply={'error':'private path or credential'}
        self.assertEqual(await self.send(BEGIN),(409,{'error':'model-change-unconfirmed'}))
        self.assertEqual(len(self.calls),1)

    async def test_inconsistent_contract_cannot_look_complete(self):
        for value in ({**STATE,'pending':False}, {**STATE,'contract':{**TARGET,'maxTokens':65536}},
                      {**STATE,'status':'completed'}, {**STATE,'transactionId':None}):
            self.reply=value
            self.assertEqual((await self.send({'operation':'model-status'}))[0],503)


if __name__=='__main__': unittest.main()
