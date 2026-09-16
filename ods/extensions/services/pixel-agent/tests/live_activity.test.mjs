import test from 'node:test';
import assert from 'node:assert/strict';
import {streamTaskActivity} from '../host/pixel_ingress.mjs';

test('streams sanitized live observations before the answer and stops polling on teardown',async()=>{
  const jobs=[]; const packets=[]; const calls=[];
  const runId='chatcmpl_11111111-2222-4333-8444-555555555555';
  let task;
  const deps={setTimeout:fn=>{jobs.push(fn);return fn;},clearTimeout:fn=>{const i=jobs.indexOf(fn);if(i>=0)jobs.splice(i,1);},fetch:async(url,options)=>{
    calls.push({url,body:JSON.parse(options.body)});
    return new Response(JSON.stringify({task}),{headers:{'content-type':'application/json'}});
  }};
  const controller=new AbortController();
  const stop=streamTaskActivity({write:text=>packets.push(text)},'ods-'+'a'.repeat(64),'private-token',18789,controller.signal,deps);
  task={schemaVersion:1,runId,startedAt:new Date().toISOString(),finishedAt:null,state:'running',calls:1,failures:0,blocked:0,truncated:false,activities:[{kind:'read',calls:1,failures:0,blocked:0}]};
  await jobs.shift()();
  assert.equal(packets.length,1);assert.match(packets[0],/ods.task.activity/);assert.doesNotMatch(packets[0],/private-token|sessionKey/);
  assert.deepEqual(calls[0].body,{user:'ods-'+'a'.repeat(64)});
  await jobs.shift()(); assert.equal(packets.length,1); // no duplicates
  task={...task,prompt:'secret'}; await jobs.shift()(); assert.equal(packets.length,1);
  task={...task,runId:runId.replace('11111111','aaaaaaaa')};delete task.prompt;
  await jobs.shift()();assert.equal(packets.length,1); // no cross-run replacement
  stop(); assert.equal(jobs.length,0);
});

test('never replays stale observations from the previous request',async()=>{
  const jobs=[], packets=[];
  const task={schemaVersion:1,runId:'chatcmpl_11111111-2222-4333-8444-555555555555',startedAt:'2020-01-01T00:00:00.000Z',finishedAt:null,state:'running',calls:0,failures:0,blocked:0,truncated:false,activities:[]};
  const deps={setTimeout:fn=>{jobs.push(fn);return fn},clearTimeout:()=>{},fetch:async()=>new Response(JSON.stringify({task}),{headers:{'content-type':'application/json'}})};
  const stop=streamTaskActivity({write:text=>packets.push(text)},'ods-'+'a'.repeat(64),'token',18789,new AbortController().signal,deps);
  await jobs.shift()();stop();assert.deepEqual(packets,[]);
});
