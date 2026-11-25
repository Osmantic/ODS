import assert from 'node:assert/strict';
import test from 'node:test';
import {createToolLoopGuard, OWNER_NO_TOOLS_REASON} from '../plugin/tool-loop-guard.mjs';

function context(runId, toolName) {
  return {agentId:'pixel', runId, sessionId:`session-${runId}`, toolName, toolCallId:`${runId}-${toolName}`};
}

test('explicit owner no-tools request blocks direct and deferred tools before execution', () => {
  const guard=createToolLoopGuard();
  const prompt='Reply with CHECK and 23 multiplied by 17. Do not use tools or change files.\n\n'
    +'[ODS Portal delivery requirement: Answer the owner above.]';
  guard.observeRun(context('no-tools','write'), 'pixel', {prompt});
  for (const [toolName, params] of [
    ['write',{path:'sample.txt',content:'unwanted'}],
    ['read',{path:'sample.txt'}],
    ['web_search',{query:'23 * 17'}],
    ['tool_call',{id:'openclaw:core:write',args:{path:'sample.txt',content:'unwanted'}}],
    ['tool_call',{id:'tool_search',args:{query:'write'}}],
  ]) {
    const decision=guard.beforeToolCall({toolName,params},context('no-tools',toolName));
    assert.equal(decision?.block,true,toolName);
    assert.equal(decision.blockReason,OWNER_NO_TOOLS_REASON,toolName);
  }
});

test('no-tools wording in examples and older turns does not restrict a new owner turn', () => {
  const guard=createToolLoopGuard();
  const examples='Explain the quoted phrase "Do not use tools" and the following example:\n```\nwithout using tools\n```';
  guard.observeRun(context('examples','read'),'pixel',{prompt:examples});
  assert.notEqual(guard.beforeToolCall({toolName:'read',params:{path:'sample.txt'}},context('examples','read'))?.block,true);

  guard.observeRun(context('next-turn','read'),'pixel',{messages:[
    {role:'user',content:'Do not use tools. Reply from memory.'},
    {role:'assistant',content:'Understood.'},
    {role:'user',content:'Now read sample.txt and quote it.'},
  ]});
  assert.notEqual(guard.beforeToolCall({toolName:'read',params:{path:'sample.txt'}},context('next-turn','read'))?.block,true);
});

test('without using tools applies only to the current run', () => {
  const guard=createToolLoopGuard();
  guard.observeRun(context('first','read'),'pixel',{prompt:'Answer without using any tools.'});
  assert.equal(guard.beforeToolCall({toolName:'read',params:{path:'sample.txt'}},context('first','read'))?.block,true);
  guard.observeRun(context('second','read'),'pixel',{prompt:'Read sample.txt.'});
  assert.notEqual(guard.beforeToolCall({toolName:'read',params:{path:'sample.txt'}},context('second','read'))?.block,true);
});

test('restrictions on a subset of tools do not ban an expressly requested read', () => {
  for (const [index,prompt] of [
    'Do not use tools that change files. Read sample.txt and quote it.',
    'Do not use any tool except read. Read sample.txt and quote it.',
    'Do not use tools to modify files. Read sample.txt and quote it.',
    'Do not use tool write; use read instead. Read sample.txt.',
    'Explain why one should not use tools in that example. Read sample.txt.',
  ].entries()) {
    const guard=createToolLoopGuard();
    const runId=`subset-${index}`;
    guard.observeRun(context(runId,'read'),'pixel',{prompt});
    assert.notEqual(guard.beforeToolCall({toolName:'read',params:{path:'sample.txt'}},context(runId,'read'))?.block,true,prompt);
  }
});

test('a no-tools directive joined to another request still blocks tools', () => {
  for (const [index,prompt] of [
    'Reply with CHECK and do not use tools.',
    'Summarize the file but do not use tools.',
    'Please do not use tools. Reply with CHECK.',
  ].entries()) {
    const guard=createToolLoopGuard();
    const runId=`joined-ban-${index}`;
    guard.observeRun(context(runId,'read'),'pixel',{prompt});
    assert.equal(guard.beforeToolCall({toolName:'read',params:{path:'sample.txt'}},context(runId,'read'))?.block,true,prompt);
  }
});
