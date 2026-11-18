// /goal partial-work rounds through the real plugin entry: its hooks, its own
// goal-progress, task-activity and guard construction, and the verification
// route the ingress reads (goal-partial-harness.mjs). A round that saved
// owner-requested edits, settled every known call and ended announcing more
// work before publishing gets the failed stop 'unfinished-work', which goal
// mode's controller continues (dashboard-api portal_goal.py). That stop is a
// report of host-observed call settlement and the missing current preview;
// it is not a certificate that commands had no other effects.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {goalGateway, plan, catalog, catalogResult, SITE, PLAN} from './goal-partial-harness.mjs';

const GOAL = `/goal Repair the appointment form in ${SITE}/index.html and publish the site to the private preview.`;
const UNFINISHED = 'I added the error styles. Next I will replace the submit handler with validation, then publish.';
const READY = 'The appointment form is repaired and the site is ready.';
const READ = {content: [{type: 'text', text: '<!doctype html><title>Original</title><form></form>'}]};
const EDITED = {content: [{type: 'text', text: 'Successfully replaced text.'}]};
const FAILED = {isError: true, content: [{type: 'text', text: 'Edit failed: oldText not found'}], details: {status: 'error'}};
const EDIT = {path: `${SITE}/index.html`, edits: [{oldText: '<title>Original</title>', newText: '<title>Repaired</title>'}]};
const BUILD = {command: 'npm run build', workdir: `/workspace/${SITE}`};
const completed = (exitCode = 0) => ({content: [{type: 'text', text: 'built'}], details: {status: 'completed', exitCode}});
const RUNNING = {content: [{type: 'text', text: 'Command still running (session build-1).'}], details: {status: 'running', sessionId: 'build-1'}};
const polled = (sessionId = 'build-1', exitCode = 0) =>
  ({content: [{type: 'text', text: 'built'}], details: {status: 'completed', sessionId, exitCode}});

// The ordinary partial round: discovery, the goal plan, a read, an edit and a
// build, then a reply that still announces requested work. Each option
// changes one fact; `mutate` adds calls after the build.
async function round(t, {prompt = GOAL, build = 'wrapped', planId, edit = true, editFailed = false, readFailed = false,
  buildExit = 0, ending = UNFINISHED, finish = {}, mutate = async () => {}} = {}) {
  const gateway = await goalGateway(t);
  const owner = gateway.run(prompt);
  await owner.start();
  await owner.call('tool_search', {query: 'goal plan exec'}, {content: [{type: 'text', text: '[]'}], details: {results: []}});
  await plan(owner, planId ? {id: planId} : {});
  await owner.call('read', {path: `${SITE}/index.html`}, readFailed ? FAILED : READ);
  if (edit) await owner.call('edit', EDIT, editFailed ? FAILED : EDITED);
  if (build === 'wrapped') {
    await catalog(owner, 'openclaw:core:exec', BUILD, completed(buildExit));
  } else if (build === 'wrapped-outer') {
    // The outer receipt alone, as the guard's deferred-exec tests model it.
    const id = 'openclaw:core:exec';
    await owner.call('tool_call', {id, args: BUILD}, catalogResult(id, completed(buildExit)));
  } else if (build === 'async-nested') {
    await catalog(owner, 'openclaw:core:exec', BUILD, RUNNING);
    await catalog(owner, 'openclaw:core:process', {action: 'poll', sessionId: 'build-1'}, polled());
  } else if (build === 'direct') {
    await owner.call('exec', BUILD, completed(buildExit));
  } else if (build === 'async' || build === 'still-running') {
    await owner.call('exec', BUILD, RUNNING);
    if (build === 'async') await owner.call('process', {action: 'poll', sessionId: 'build-1'}, polled());
  }
  await mutate(owner, gateway);
  return owner.finish(ending, finish);
}

function stops(result) {
  assert.equal(result.served.status, 'failed');
  assert.equal(result.served.stop, 'unfinished-work');
  assert.equal(result.parsed.stop, 'unfinished-work', 'the ingress accepts the stop it is served');
  // Failed stays failed: the stop adds only the closed cause, not a verdict.
  assert.match(result.served.text, /couldn't publish a preview/);
}
function continuesNot(result, message) {
  assert.notEqual(result.served.stop, 'unfinished-work', message);
}

test('settled edits, discovery, the catalog plan and a wrapped build stop as unfinished work', async t => {
  const result = await round(t);
  stops(result);
  assert.equal(result.served.task.failures, 0);
  assert.equal(result.served.task.goal.steps.length, PLAN.steps.length, 'the real plan receipt reached the goal');
});

test('a direct synchronous build is settled by its bound terminal receipt', async t => {
  stops(await round(t, {build: 'direct'}));
});

test('an asynchronous build settled by an owned terminal poll is not still pending', async t => {
  stops(await round(t, {build: 'async'}));
});

test('a catalog build and its catalog terminal poll, with their nested hooks, settle one owned session', async t => {
  stops(await round(t, {build: 'async-nested'}));
});

test('an outer-only catalog build receipt is also bound', async t => {
  stops(await round(t, {build: 'wrapped-outer'}));
});

test('an owned log read admitted and received with the same params settles the build', async t => {
  stops(await round(t, {build: 'none', mutate: async owner => {
    await owner.call('exec', BUILD, RUNNING);
    await owner.call('process', {action: 'log', sessionId: 'build-1'}, polled());
  }}));
});

test('an intermediate owned poll that saw the build still running is settled by the session end', async t => {
  stops(await round(t, {build: 'none', mutate: async owner => {
    await owner.call('exec', BUILD, RUNNING);
    await owner.call('process', {action: 'poll', sessionId: 'build-1'},
      {content: [{type: 'text', text: 'still running'}], details: {status: 'running', sessionId: 'build-1'}});
    await owner.call('process', {action: 'poll', sessionId: 'build-1'}, polled());
  }}));
});

test('the plan tool by its bare id, as the goal contract names it, is accepted', async t => {
  stops(await round(t, {planId: 'pixel_ods_goal'}));
});

for (const [name, options] of [
  ['a plain chat without /goal', {prompt: GOAL.replace('/goal ', '')}],
  ['no saved edit', {edit: false}],
  ['a reply that announces nothing more', {ending: READY}],
  ['goal metadata from the wrong catalog source', {planId: 'openclaw:core:pixel_ods_goal'}],
  ['a failed edit', {editFailed: true}],
  ['a failed read', {readFailed: true}],
  ['a failed build', {buildExit: 1}],
  ['a build still running in the background', {build: 'still-running'}],
  ['a cut-off final reply', {finish: {stopReason: 'length', content: [{type: 'text', text: UNFINISHED},
    {type: 'toolCall', id: 'cut', name: 'write', arguments: {path: `${SITE}/index.html`}}]}}],
  ['a requested download', {prompt: `${GOAL} Then give me a download of the project files.`}],
  ['a managed extension request', {prompt: `${GOAL} Also install the extension https://github.com/example/form-tools.`}],
]) test(`no unfinished-work stop: ${name}`, async t => {
  const result = await round(t, options);
  continuesNot(result, name);
});

for (const [name, mutate] of [
  ['an unknown tool', async owner => owner.call('memory_search', {query: 'form'}, {content: [{type: 'text', text: '[]'}]})],
  ['a browser read', async owner => owner.call('web_fetch', {url: 'https://example.com/'}, {content: [{type: 'text', text: 'page'}]})],
  ['a blocked plan update', async owner => owner.call('tool_call', {id: 'openclaw:pixel-ods:pixel_ods_goal', args: {completed: true}},
    {content: [{type: 'text', text: 'unused'}]})],
  ['a duplicate success after a failed receipt', async owner => {
    const ctx = {...owner.ctx, toolName: 'exec', toolCallId: 'dup'};
    const [decision] = await owner.fire('before_tool_call', {toolName: 'exec', toolCallId: 'dup', params: BUILD}, ctx);
    const params = {...BUILD, ...decision?.params};
    await owner.fire('after_tool_call', {toolName: 'exec', toolCallId: 'dup', params, result: completed(2)}, ctx);
    await owner.fire('after_tool_call', {toolName: 'exec', toolCallId: 'dup', params, result: completed(0)}, ctx);
  }],
  ['an after receipt without its before hook', async owner => {
    const ctx = {...owner.ctx, toolName: 'read', toolCallId: 'after-only'};
    await owner.fire('after_tool_call', {toolName: 'read', toolCallId: 'after-only', params: {path: `${SITE}/index.html`}, result: READ}, ctx);
  }],
  ['a receipt from another session', async owner => owner.call('read', {path: `${SITE}/index.html`}, READ,
    {afterContext: {sessionId: 'session-from-elsewhere'}})],
  ['a receipt without a result', async owner => owner.call('read', {path: `${SITE}/index.html`}, undefined, {persist: false})],
  ['a receipt with an empty session key', async owner => owner.call('read', {path: `${SITE}/index.html`}, READ,
    {afterContext: {sessionKey: ''}})],
  ['a hook without a call id', async owner => {
    const context = {...owner.ctx, toolName: 'read', toolCallId: undefined};
    await owner.fire('before_tool_call', {toolName: 'read', params: {path: `${SITE}/index.html`}}, context);
    await owner.fire('after_tool_call', {toolName: 'read', params: {path: `${SITE}/index.html`}, result: READ}, context);
  }],
  ['a receipt whose event names another tool than its context', async owner => owner.call('read', {path: `${SITE}/index.html`}, READ,
    {event: {toolName: 'write'}})],
  ['a poll receipt with other params than the ones admitted', async owner => {
    await owner.call('exec', BUILD, RUNNING);
    await owner.call('process', {action: 'log', sessionId: 'build-1'}, polled(), {afterParams: {action: 'poll', sessionId: 'build-1'}});
  }],
  ['a receipt naming another run', async owner => owner.call('read', {path: `${SITE}/index.html`}, READ,
    {event: {runId: 'chatcmpl_99999999-0000-4000-8000-000000000000'}})],
  ['a build receipt with params other than the ones executed', async owner =>
    owner.call('exec', BUILD, completed(), {afterParams: BUILD})],
  ['a poll of a session this run did not start', async owner =>
    owner.call('process', {action: 'poll', sessionId: 'other-session'}, polled('other-session'))],
  ['input written to a build session', async owner => {
    await owner.call('exec', BUILD, RUNNING);
    await owner.call('process', {action: 'poll', sessionId: 'build-1'}, polled());
    await owner.call('process', {action: 'write', sessionId: 'build-1', data: 'y\n'}, {content: [{type: 'text', text: 'written'}]});
  }],
  ['an attempted preview publication', async owner => owner.call('pixel_ods_workspace_preview', {relativeDirectory: SITE},
    {isError: true, content: [{type: 'text', text: 'Preview failed.'}], details: {status: 'failed'}})],
  ['an owner question', async owner => {
    const id = 'openclaw:pixel-ods:pixel_ods_ask_user';
    const questions = [{id: 'style', question: 'Which error style?', options: ['Inline', 'Banner']}];
    await owner.call('tool_call', {id, args: {questions}}, catalogResult(id, {content: [{type: 'text', text: 'Asked.'}],
      details: {status: 'awaiting_user', questions}}));
  }],
  ['an owner cancel', async owner => { assert.equal((await owner.abort()).status, 200); }],
  ['a changed project file that does not parse', async (owner, gateway) => {
    // Portal checks the Playground projects a call touches (changed-file-checks.mjs).
    const relative = 'Playground/form-kit/form.mjs', file = path.join(gateway.workspace, relative);
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, 'export const ok = 1;\n');
    const params = {path: relative, edits: [{oldText: 'export const ok = 1;', newText: 'export const broken = ;'}]};
    const ctx = {...owner.ctx, toolName: 'edit', toolCallId: 'broken-module'};
    await owner.fire('before_tool_call', {toolName: 'edit', toolCallId: 'broken-module', params}, ctx);
    fs.writeFileSync(file, 'export const broken = ;\n');
    await owner.fire('after_tool_call', {toolName: 'edit', toolCallId: 'broken-module', params, result: EDITED}, ctx);
  }],
]) test(`no unfinished-work stop: ${name}`, async t => {
  const result = await round(t, {mutate});
  continuesNot(result, name);
});

// Tool Search sanitizes "pair x" and "pair_x" to one child prefix. With only
// these builds in the round (no other build that could trip a repeat fuse),
// the bound build's child receipt must not settle the unbound one, in either
// order; the bound build alone stops as unfinished work.
async function collision(owner, {unboundFirst}) {
  // A different command, so no repeat-command refusal is involved.
  const id = 'openclaw:core:exec', params = {id, args: {...BUILD, command: 'npm run build:preview'}};
  const unbound = async () => {
    const ctx = {...owner.ctx, toolName: 'tool_call', toolCallId: unboundFirst ? 'pair x' : 'pair y'};
    await owner.fire('before_tool_call', {toolName: 'tool_call', toolCallId: ctx.toolCallId, params}, ctx);
    // The original, unwrapped args: not the guard's executed params.
    await owner.fire('after_tool_call', {toolName: 'tool_call', toolCallId: ctx.toolCallId, params,
      result: catalogResult(id, completed())}, ctx);
  };
  if (unboundFirst) await unbound();
  await catalog(owner, id, BUILD, completed(), {callId: unboundFirst ? 'pair_x' : 'pair_y'});
  if (!unboundFirst) await unbound();
}
test('a bound catalog build alone, under a colliding-style ID, stops as unfinished work', async t => {
  stops(await round(t, {build: 'none', mutate: owner => catalog(owner, 'openclaw:core:exec', BUILD, completed(), {callId: 'pair_x'})}));
});
for (const unboundFirst of [true, false]) test(`a sanitized child-prefix collision settles neither build (unbound first: ${unboundFirst})`, async t => {
  continuesNot(await round(t, {build: 'none', mutate: owner => collision(owner, {unboundFirst})}));
});
