import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {handleAccessMode, handleSchedules, readAccessOwnerKey, requestAccessController} from '../host/access_mode_relay.mjs';
import {createHash} from 'node:crypto';

const ownerKey = 'o'.repeat(64);
const change = {mode:'full-access', confirmed:true, revision:'a'.repeat(64)};

test('schedule relay binds the authenticated ingress key, not caller-selected authority', async t => {
  const calls=[];
  const page={kind:'tasks',items:[],offset:0,hasMore:false,nextOffset:null,pendingCreates:[],pendingCount:0,pendingRemovals:[]};
  const server=http.createServer((req,res)=>void handleSchedules(req,res,{ownerKey,request:async value=>{calls.push(value);return {status:200,body:page}}}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}/v1/schedules`;
  const body=JSON.stringify({action:'list',offset:0});
  for (const key of ['', 'c'.repeat(64)]) {
    assert.equal((await fetch(url,{method:'POST',headers:{authorization:`Bearer ${key}`,'content-type':'application/json'},body})).status,403);
  }
  assert.equal(calls.length,0);
  const response=await fetch(url,{method:'POST',headers:{authorization:`Bearer ${ownerKey}`,'content-type':'application/json'},body});
  assert.equal(response.status,200);assert.deepEqual(await response.json(),page);
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.deepEqual(calls,[{operation:'schedules',request:{action:'list',offset:0},source:{kind:'owner-key',identity:createHash('sha256').update(ownerKey).digest('hex')}}]);
  assert.equal((await fetch(url+'?owner=other',{method:'POST',headers:{authorization:`Bearer ${ownerKey}`,'content-type':'application/json'},body})).status,400);
  assert.equal(calls.length,1);
});

test('large schedule results use their own bounded socket frame; legacy frame cap stays intact', async t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'schedules-socket-'));
  const socketPath=path.join(dir,'control.sock');
  const body={kind:'runs',taskId:'fixture-job',items:[{timestamp:1,status:'ok',summary:'A'.repeat(100000),summaryTruncated:false,durationMs:1}],offset:0,hasMore:false,nextOffset:null};
  const server=net.createServer(socket=>socket.once('data',()=>socket.end(JSON.stringify({status:200,body})+'\n')));
  await new Promise(resolve=>server.listen(socketPath,resolve));
  t.after(async()=>{await new Promise(resolve=>server.close(resolve));fs.rmSync(dir,{recursive:true,force:true});});
  const payload={operation:'schedules',request:{action:'runs',id:'fixture-job',offset:0}};
  assert.deepEqual((await requestAccessController(payload,{socketPath,timeout:1000})).body,body);
  await assert.rejects(requestAccessController({operation:'status'},{socketPath,timeout:1000}));
  body.items[0].summary='A'.repeat(525313);
  await assert.rejects(requestAccessController(payload,{socketPath,timeout:1000}));
});

test('owner-authenticated relay keeps the exact change and rejects ordinary chat authority', async t => {
  const calls=[];
  const server=http.createServer((req,res)=>void handleAccessMode(req,res,{ownerKey, request:async value=>{calls.push(value);return {status:200,body:{available:true}}}}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}/v1/access-mode`;
  for(const key of ['', 'c'.repeat(64)]) {
    const response=await fetch(url,{method:'POST',headers:{authorization:`Bearer ${key}`,'content-type':'application/json'},body:JSON.stringify(change)});
    assert.equal(response.status,403);
  }
  assert.equal(calls.length,0);
  const response=await fetch(url,{method:'POST',headers:{authorization:`Bearer ${ownerKey}`,'content-type':'application/json'},body:JSON.stringify(change)});
  assert.equal(response.status,200);
  assert.deepEqual(calls,[{operation:'change',request:change}]);
  for(const value of [{...change,confirmed:false},{...change,confirmed:'true'},{...change,path:'/etc'}]) {
    assert.equal((await fetch(url,{method:'POST',headers:{authorization:`Bearer ${ownerKey}`,'content-type':'application/json'},body:JSON.stringify(value)})).status,400);
  }
  assert.equal((await fetch(url+'?path=other',{headers:{authorization:`Bearer ${ownerKey}`}})).status,400);
  assert.equal(calls.length,1);
});

test('private owner key rejects symlink and broad permissions', t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'access-key-'));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const file=path.join(dir,'key');
  fs.writeFileSync(file,ownerKey,{mode:0o600});
  assert.equal(readAccessOwnerKey(file),ownerKey);
  fs.symlinkSync(file,path.join(dir,'link'));
  assert.equal(readAccessOwnerKey(path.join(dir,'link')),null);
  fs.chmodSync(file,0o640);
  assert.equal(readAccessOwnerKey(file),null);
});

test('real Unix transport sends one bounded frame and accepts fragmented controller reply', async t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'access-socket-'));
  const socketPath=path.join(dir,'control.sock');
  let captured='';
  const server=net.createServer(socket=>socket.on('data',data=>{
    captured+=data;
    socket.write('{"status":200,');
    socket.end('"body":{"surface":"wsl-systemd"}}\n');
  }));
  await new Promise(resolve=>server.listen(socketPath,resolve));
  t.after(async()=>{await new Promise(resolve=>server.close(resolve));fs.rmSync(dir,{recursive:true,force:true});});
  assert.deepEqual(await requestAccessController({operation:'status'},{socketPath,timeout:1000}),{status:200,body:{surface:'wsl-systemd'}});
  assert.equal(captured,'{"operation":"status"}\n');
});

test('controller timeout and oversized response fail without retry', async t => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'access-limit-'));
  const socketPath=path.join(dir,'control.sock');
  let connections=0;
  const sockets=new Set();
  const server=net.createServer(socket=>{sockets.add(socket);connections++;if(connections===2)socket.end('x'.repeat(65537));});
  await new Promise(resolve=>server.listen(socketPath,resolve));
  t.after(async()=>{for(const socket of sockets)socket.destroy();await new Promise(resolve=>server.close(resolve));fs.rmSync(dir,{recursive:true,force:true});});
  await assert.rejects(requestAccessController({operation:'status'},{socketPath,timeout:20}));
  await assert.rejects(requestAccessController({operation:'status'},{socketPath,timeout:1000}));
  assert.equal(connections,2);
});
