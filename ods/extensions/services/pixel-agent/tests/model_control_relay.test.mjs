import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {handleModelControl,validModelControl,publicModelControl} from '../host/access_mode_relay.mjs';

const ownerKey='o'.repeat(64), transactionId='a'.repeat(64), revision='b'.repeat(64);
const target={model:'Qwen 3.8 (27B)',contextLength:16384,maxTokens:8192,reasoning:true};
const state={schemaVersion:1,status:'held',revision,contract:target,pending:true,transactionId,outcome:null};
const operations=[{operation:'model-status'},
  {operation:'model-begin',request:{revision,transactionId}},
  {operation:'model-apply',request:{transactionId,target}},
  {operation:'model-finish',request:{transactionId,outcome:'commit'}},
  {operation:'model-finish',request:{transactionId,outcome:'rollback'}}];

test('exact lifecycle frames reject paths, invalid budgets and malformed fingerprints',()=>{
  for(const value of operations) assert.equal(validModelControl(value),true);
  for(const value of [null,[],{}, {...operations[0],path:'/etc'},
    {...operations[1],request:{revision,transactionId:transactionId+'\n'}},
    {...operations[2],request:{transactionId,target:{...target,contextLength:4095}}},
    {...operations[2],request:{transactionId,target:{...target,maxTokens:20000}}},
    {...operations[2],request:{transactionId,target:{...target,contextLength:true}}},
    {...operations[2],request:{transactionId,target:{...target,model:'Qwen\n'}}},
    {...operations[2],request:{transactionId,target:{...target,routeFingerprint:revision+'\n'}}},
    {...operations[2],request:{transactionId,target:{...target,endpoint:'http://foreign'}}},
    {...operations[3],request:{transactionId,outcome:'release'}}]) assert.equal(validModelControl(value),false);
});

test('public state cannot leak private journal or claim released while pending',()=>{
  assert.deepEqual(publicModelControl({...state,secret:'private',previousConfig:{key:'private'}}),state);
  for(const value of [{...state,pending:false},{...state,transactionId:null},
    {...state,status:'completed'}, {...state,revision:revision+'\n'}]) assert.throws(()=>publicModelControl(value));
  assert.equal(publicModelControl({...state,status:'completed',pending:false,outcome:'rollback'}).outcome,'rollback');
});

test('real HTTP lifecycle uses owner authority, bounded exact frames, and one controller invocation',async t=>{
  const calls=[];
  let result={status:200,body:{...state,secret:'private'}};
  const server=http.createServer((req,res)=>void handleModelControl(req,res,{ownerKey,request:async (value,options)=>{
    calls.push({value,options});
    if(result instanceof Error) throw result;
    return result;
  }}));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  const url=`http://127.0.0.1:${server.address().port}/v1/model-control`;
  const send=(body,key=ownerKey,path='')=>fetch(url+path,{method:'POST',headers:{authorization:`Bearer ${key}`,'content-type':'application/json'},body:JSON.stringify(body)});
  for(const key of ['','c'.repeat(64)]) assert.equal((await send(operations[1],key)).status,403);
  assert.equal((await send(operations[1],ownerKey,'?path=other')).status,400);
  assert.equal((await send({...operations[1],path:'/etc'})).status,400);
  assert.equal((await send({padding:'x'.repeat(2049)})).status,413);
  assert.equal(calls.length,0);
  for(const operation of operations) {
    const response=await send(operation);
    assert.equal(response.status,200);
    assert.deepEqual(await response.json(),state);
  }
  assert.deepEqual(calls.map(item=>item.value),operations.map(operation=>({
    ...operation,operation:operation.operation.replace(/^model-/, 'model-route-')
  })));
  assert.equal(calls[0].options.timeout,20000);
  assert.equal(calls[1].options.timeout,305000);
  result={status:409,body:{error:'private detail'}};
  let response=await send(operations[2]);
  assert.equal(response.status,409);
  assert.deepEqual(await response.json(),{error:'model-change-unconfirmed'});
  result=new Error('connection lost after mutation');
  response=await send(operations[3]);
  assert.equal(response.status,503);
  assert.equal(calls.length,operations.length+2);
});
