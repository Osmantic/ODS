import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {handleAccessMode, handleConnections, readAccessOwnerKey, requestAccessController} from '../host/access_mode_relay.mjs';

const ownerKey = 'o'.repeat(64);
const change = {mode:'full-access', confirmed:true, revision:'a'.repeat(64)};

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

test('connected-apps status is owner-only, read-only and asks the controller one fixed question', async t => {
  const calls=[];
  const server=http.createServer((req,res)=>void handleConnections(req,res,{ownerKey, request:async (value, options)=>{calls.push([value, options]);return {status:200,body:{schemaVersion:1}}}}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}/v1/connections`;
  for(const key of ['', 'c'.repeat(64)]) {
    assert.equal((await fetch(url,{headers:{authorization:`Bearer ${key}`}})).status,403);
  }
  assert.equal((await fetch(url,{method:'PUT',headers:{authorization:`Bearer ${ownerKey}`,'content-type':'application/json'},body:'{}'})).status,405);
  assert.equal((await fetch(url+'?account=other',{headers:{authorization:`Bearer ${ownerKey}`}})).status,400);
  assert.equal(calls.length,0);
  const response=await fetch(url,{headers:{authorization:`Bearer ${ownerKey}`}});
  assert.equal(response.status,200);
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.deepEqual(await response.json(),{schemaVersion:1});
  assert.deepEqual(calls,[[{operation:'connection-status'},{timeout:20000}]]);
});

async function connectionServer(t, answer) {
  const calls=[];
  const server=http.createServer((req,res)=>void handleConnections(req,res,{ownerKey, request:async (value, options)=>{calls.push([value, options]);return answer(value);}}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}/v1/connections`;
  const post=(body, headers={})=>fetch(url,{method:'POST',headers:{authorization:`Bearer ${ownerKey}`,'content-type':'application/json',...headers},body});
  return {calls, url, post};
}

test('each custodian action reaches the controller once, with its own time budget', async t => {
  const {calls, post}=await connectionServer(t, value=>({status:200,body:{outcome:value.operation==='connection-sync'?'started':'applied'}}));
  for (const [operation, request, budget] of [['connection-sync', {}, 25000], ['connection-activate', {}, 305000],
      ['connection-cancel', {flowId:'a'.repeat(32)}, 25000], ['connection-disconnect', {connector:'google'}, 155000],
      ['connection-proposals', {}, 25000], ['connection-decline', {proposalId:'calendar-1761500000000-0badc0de'}, 25000],
      ['connection-approve', {proposalId:'calendar-1761500000000-0badc0de', sha256:'c'.repeat(64)}, 155000],
      ['connection-reconcile', {proposalId:'calendar-1761500000000-0badc0de'}, 155000],
      ['connection-activity', {}, 25000], ['connection-undo', {proposalId:'calendar-1761500000000-0badc0de'}, 155000],
      ['connection-drafts', {}, 25000], ['connection-plugin', {plugin:'memory-wiki', enabled:true}, 305000],
      ['connection-approve', {proposalId:'gmail-draft-1761500000000-0badc0de', sha256:'c'.repeat(64)}, 155000]]) {
    calls.length=0;
    const response=await post(JSON.stringify({operation, request}));
    assert.equal(response.status,200);
    assert.equal(response.headers.get('cache-control'),'no-store');
    await response.json();
    assert.deepEqual(calls,[[{operation, request},{timeout:budget}]]);
  }
});

test('only an exact action frame reaches the controller', async t => {
  const {calls, url, post}=await connectionServer(t, ()=>({status:200,body:{outcome:'started'}}));
  for (const body of ['{}', '[]', 'not json', JSON.stringify({operation:'connection-status', request:{}}),
      JSON.stringify({operation:'connection-sync', request:[]}), JSON.stringify({operation:'connection-sync', request:{}, extra:1}),
      JSON.stringify({operation:'__proto__', request:{}})]) {
    assert.equal((await post(body)).status,400,body);
  }
  assert.equal((await post(JSON.stringify({operation:'connection-sync', request:{pad:'x'.repeat(4096)}}))).status,413);
  assert.equal((await post('{}',{'content-type':'text/plain'})).status,415);
  assert.equal((await fetch(url,{method:'POST',headers:{authorization:'Bearer '+'c'.repeat(64),'content-type':'application/json'},
    body:JSON.stringify({operation:'connection-sync', request:{}})})).status,403);
  assert.equal((await fetch(url+'?x=1',{method:'POST',headers:{authorization:`Bearer ${ownerKey}`,'content-type':'application/json'},
    body:JSON.stringify({operation:'connection-sync', request:{}})})).status,400);
  assert.equal(calls.length,0);
});

test('a controller refusal keeps only its code', async t => {
  let answer={status:409,body:{error:'runtime-busy'}};
  const {post}=await connectionServer(t, ()=>answer);
  const sync=JSON.stringify({operation:'connection-sync', request:{}});
  let response=await post(sync);
  assert.equal(response.status,409);
  assert.deepEqual(await response.json(),{error:'runtime-busy'});
  answer={status:503,body:{error:'Private Path /etc'}};
  response=await post(sync);
  assert.equal(response.status,503);
  assert.deepEqual(await response.json(),{error:'connection-change-unconfirmed'});
});

test('an unreachable controller gives a plain unavailable answer', async t => {
  const server=http.createServer((req,res)=>void handleConnections(req,res,{ownerKey, request:async ()=>{throw new Error('private-sentinel');}}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const response=await fetch(`http://127.0.0.1:${server.address().port}/v1/connections`,{headers:{authorization:`Bearer ${ownerKey}`}});
  assert.equal(response.status,503);
  const text=await response.text();
  assert.match(text,/connections-unavailable/);
  assert.doesNotMatch(text,/private-sentinel/);
});
