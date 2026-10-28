import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {once} from 'node:events';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {handleConnections,requestAccessController} from '../host/access_mode_relay.mjs';

test('reviewed app Node HTTP -> framed UDS preserves exact operations, long callback and controller budgets',
  {skip:process.platform==='win32'}, async()=>{
    const directory=await fs.mkdtemp(path.join(os.tmpdir(),'ods-native-relay-'));
    const socketPath=path.join(directory,'control.sock');
    const calls=[],budgets=[];
    const root=net.createServer(socket=>{
      let raw='';
      socket.on('data',chunk=>{
        raw+=chunk.toString('utf8');
        if(!raw.endsWith('\n'))return;
        const payload=JSON.parse(raw);
        calls.push(payload);
        socket.end(JSON.stringify({status:409,body:{error:'native-flow-mismatch'}})+'\n');
      });
    });
    root.listen(socketPath);await once(root,'listening');
    const ownerKey=randomBytes(32).toString('hex');
    const server=http.createServer((req,res)=>handleConnections(req,res,{ownerKey,
      request:(payload,{timeout})=>{budgets.push(timeout);return requestAccessController(payload,{socketPath,timeout});}}));
    server.listen(0,'127.0.0.1');await once(server,'listening');
    const url=`http://127.0.0.1:${server.address().port}/v1/connections`;
    async function post(payload,{key=ownerKey,raw}={}){
      const response=await fetch(url,{method:'POST',headers:{authorization:`Bearer ${key}`,'content-type':'application/json'},
        body:raw??JSON.stringify(payload)});
      return {status:response.status,body:await response.json()};
    }
    try{
      const flow='a'.repeat(32);
      const address=`http://127.0.0.1:43123/?state=${'s'.repeat(128)}&code=PRIVATE-${'c'.repeat(2000)}`;
      const cases=[
        ['connection-app-status',{},22000],['connection-app-begin',{app:'notion',redirectPort:43123},305000],
        ['connection-app-complete',{app:'notion',flowId:flow,address},75000],
        ['connection-app-finish',{app:'notion',flowId:flow},305000],
        ['connection-app-cancel',{app:'notion',flowId:flow},305000],['connection-app-disconnect',{app:'notion'},305000],
        ['connection-app-check',{app:'notion'},75000],
      ];
      for(const [operation,request,timeout] of cases){
        const payload={operation,request};
        if(operation==='connection-app-complete')assert.ok(Buffer.byteLength(JSON.stringify(payload))>2048);
        const result=await post(payload);
        assert.deepEqual(result,{status:409,body:{error:'native-flow-mismatch'}});
        assert.deepEqual(calls.at(-1),payload);assert.equal(budgets.at(-1),timeout);
      }
      const count=calls.length;
      assert.equal((await post({operation:'connection-app-status',request:{}},{key:'wrong'})).status,403);
      assert.equal((await post({operation:'connection-app-write',request:{app:'notion'}})).status,400);
      const ordinary=JSON.stringify({operation:'connection-sync',request:{}});
      assert.equal((await post(null,{raw:ordinary+' '.repeat(2048)})).status,413);
      assert.equal((await post(null,{raw:'PRIVATE-'+ 'x'.repeat(16384)})).status,413);
      assert.equal(calls.length,count,'rejected frames must not reach Root');
      const malformed=await post(null,{raw:'{"operation":"connection-app-complete","request":"PRIVATE-code'});
      assert.deepEqual(malformed,{status:400,body:{error:'invalid-request'}});
      assert.ok(!JSON.stringify(malformed).includes('PRIVATE'));
    }finally{
      server.closeAllConnections();await new Promise(resolve=>server.close(resolve));
      await new Promise(resolve=>root.close(resolve));await fs.rm(directory,{recursive:true,force:true});
    }
  });
