import test from 'node:test';
import assert from 'node:assert/strict';
import {createTaskActivity} from '../plugin/task-activity.mjs';
import {parseTaskActivity} from '../host/task_activity_schema.mjs';
const runId = 'chatcmpl_11111111-2222-4333-8444-555555555555';
const ctx = {agentId:'pixel',runId};
const now = () => '2026-09-08T20:00:00.000Z';

test('projects real attempts without arguments, outputs or unknown tool names', () => {
  const recorder = createTaskActivity({now});
  recorder.begin({prompt:'private prompt'},ctx);
  recorder.before({toolName:'tool_call',params:{id:'openclaw:core:read',path:'secret.txt'}},{...ctx,toolCallId:'a'});
  recorder.after({result:{content:[{text:'secret output'}]}},{...ctx,toolCallId:'a'});
  recorder.before({toolName:'read'},{...ctx,toolCallId:'a'}); // replay must not regress a settled call
  recorder.before({toolName:'exec'},{...ctx,toolCallId:'b'});
  recorder.after({result:{details:{exitCode:1}}},{...ctx,toolCallId:'b'});
  recorder.before({toolName:'edit'},{...ctx,toolCallId:'c'},true);
  recorder.after({result:{}},{...ctx,toolCallId:'c'});
  recorder.after({toolName:'private-tool-name',result:{}},{...ctx,toolCallId:'d'});
  recorder.finish({success:true},ctx);
  const result=recorder.projection(runId);
  assert.equal(result.calls,4);
  assert.equal(result.failures,2);
  assert.equal(result.blocked,1);
  assert.deepEqual(result.activities.map(item=>item.kind),['read','run','edit','unknown']);
  assert.equal(parseTaskActivity(result,runId),result);
  assert.doesNotMatch(JSON.stringify(result),/secret|private|prompt|output|toolCallId/);
});
test('isolates other agents and evicts only settled runs with bounded calls', () => {
  const recorder=createTaskActivity({now,maximumRuns:1,maximumCalls:1});
  recorder.begin({}, {...ctx,agentId:'other'});
  assert.equal(recorder.projection(runId),null);
  recorder.begin({},ctx);
  recorder.before({toolName:'read'}, {...ctx,toolCallId:'a'});
  recorder.before({toolName:'read'}, {...ctx,toolCallId:'b'});
  assert.equal(recorder.projection(runId).truncated,true);
  const next={...ctx,runId:runId.replace('11111111','aaaaaaaa')};
  recorder.begin({},next);
  assert.equal(recorder.projection(next.runId),null);
  recorder.finish({success:false},ctx);
  recorder.begin({},next);
  assert.equal(recorder.projection(runId),null);
  assert.equal(recorder.projection(next.runId).calls,0);
});
test('counts Tool Search outer and inner hooks as one invocation by explicit parent ID', () => {
  const recorder=createTaskActivity({now}); recorder.begin({},ctx);
  const outer={...ctx,toolCallId:'parent',toolName:'tool_call'};
  const inner={...ctx,toolCallId:'tool_search_code:parent:edit:1',toolName:'edit'};
  recorder.before({params:{id:'openclaw:core:edit'}},outer);
  recorder.before({},inner);
  recorder.after({error:'failed'},inner);
  recorder.after({error:'failed'},outer);
  assert.equal(recorder.projection(runId).calls,1);
  assert.equal(recorder.projection(runId).failures,1);
});
test('rejects cross-run, unbounded, extra-field, duplicate and impossible projections', () => {
  const recorder=createTaskActivity({now}); recorder.begin({},ctx); recorder.finish({success:true},ctx);
  const value=recorder.projection(runId);
  const invalid=[{...value,runId:'other'}, {...value,prompt:'secret'}, {...value,calls:513},
    {...value,finishedAt:'2026-01-01T00:00:00.000Z'}, {...value,state:'done'},
    {...value,activities:[{kind:'read',calls:1,failures:2,blocked:0}]},
    {...value,calls:2,activities:[{kind:'read',calls:1,failures:0,blocked:0},{kind:'read',calls:1,failures:0,blocked:0}]}];
  for (const item of invalid) assert.equal(parseTaskActivity(item,runId),null);
});
