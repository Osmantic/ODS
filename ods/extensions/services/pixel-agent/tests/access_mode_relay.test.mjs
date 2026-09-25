import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import net from 'node:net';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {handleAccessMode, readAccessOwnerKey, requestAccessController} from '../host/access_mode_relay.mjs';

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
