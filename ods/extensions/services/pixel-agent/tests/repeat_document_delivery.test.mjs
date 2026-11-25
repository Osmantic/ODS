import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

const prompt='Please deliver that existing CSV once more. Format your final answer as **Download:** in its own paragraph, followed by a standalone Markdown image labelled fleet-pr7911-laptop.csv.';
const answer='**Download:**\n\n![fleet-pr7911-laptop.csv](file:///home/michael/.openclaw/workspace-pixel/fleet-pr7911-laptop.csv)';
const retryKey='pixel-ods-workspace-document-delivery';
const receipt={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'fleet-pr7911-laptop.csv',
 siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),file:{path:'fleet-pr7911-laptop.csv',bytes:46,sha256:'b'.repeat(64)}};
function turn(options={}) {
 const guard=createToolLoopGuard();
 const context={agentId:'pixel',runId:'repeat',sessionId:'session',sessionKey:'agent:pixel:openai-user:ods-'+ 'c'.repeat(64),trigger:'user',...options.context};
 guard.observeRun(context,'pixel',{prompt:options.prompt??prompt});
 const finalize=(ctx=context)=>guard.beforeAgentFinalize({runId:context.runId,lastAssistantMessage:answer,
  messages:[{role:'assistant',content:[{type:'text',text:answer}]}]},ctx);
 return {guard,context,finalize};
}

test('a repeat download with no publication gets one real finalize revision, never an invented receipt',()=>{
 const f=turn();
 assert.equal(f.guard.acceptWorkspaceArtifact(f.context,receipt),false,'receipt requires an admitted publication attempt');
 assert.equal(f.guard.deliveryVerificationForRun('repeat').status,'failed');
 const decision=f.finalize();
 assert.equal(decision?.action,'revise');
 assert.equal(decision.retry.idempotencyKey,retryKey);
 assert.equal(decision.retry.maxAttempts,1);
 assert.match(decision.retry.instruction,/pixel_ods_workspace_artifact/);
 assert.doesNotMatch(decision.retry.instruction,/file:\/\/|fleet-pr7911/,'no path derived from an untrusted final answer');
 assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey,'bounded even if the model ignores the correction');
 const failed=f.guard.deliveryVerificationForRun('repeat');
 assert.equal(failed.status,'failed');assert.equal(failed.artifacts,undefined);
});

test('only a fresh admitted receipt completes the retry',()=>{
 const f=turn();assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
 assert.equal(f.guard.reserveWorkspaceArtifact(f.context),true);
 assert.equal(f.guard.acceptWorkspaceArtifact(f.context,receipt),true);
 assert.deepEqual(f.guard.deliveryVerificationForRun('repeat').artifacts,[receipt]);
 assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
 // A new run in the same conversation cannot adopt the old run's receipt.
 const next={...f.context,runId:'next'};f.guard.observeRun(next,'pixel',{prompt});
 assert.equal(f.guard.deliveryVerificationForRun('next').artifacts,undefined);
 assert.equal(f.guard.deliveryVerificationForRun('next').status,'failed');
});

test('no retry for foreign, noninteractive, team or no-tools requests',()=>{
 const f=turn();assert.notEqual(f.finalize({...f.context,sessionId:'foreign'})?.retry?.idempotencyKey,retryKey);
 assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey,'foreign context did not spend the real owner retry');
 for(const options of [{context:{trigger:'cron'}},{context:{sessionKey:'agent:pixel:main'}},
  {prompt:"You are the Builder in the owner's Portal team. Deliver report.csv."},
  {prompt:'Do not use tools. Explain how to download report.csv.'},
  {prompt:'Write a friendly sentence about the weather.'}]) {
  assert.notEqual(turn(options).finalize()?.retry?.idempotencyKey,retryKey,JSON.stringify(options));
 }
});

test('an attempted publication and an exhausted run are not automatically retried',()=>{
 const attempted=turn();assert.equal(attempted.guard.reserveWorkspaceArtifact(attempted.context),true);
 assert.notEqual(attempted.finalize()?.retry?.idempotencyKey,retryKey);
 const exhausted=turn();
 for(let i=0;i<RUN_PROGRESS_LIMITS.consecutiveFailures;i++) exhausted.guard.toolResultPersist({toolCallId:'failed-'+i,
  message:{role:'toolResult',toolName:'read',isError:true,content:[]}}, {...exhausted.context,toolName:'read',toolCallId:'failed-'+i});
 assert.notEqual(exhausted.finalize()?.retry?.idempotencyKey,retryKey);
});
