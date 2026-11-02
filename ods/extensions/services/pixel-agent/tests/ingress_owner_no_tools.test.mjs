// Owner no-tools directive: buildOutgoing must force tool_choice:'none' and
// tools:[] only for an unqualified directive in the latest current user
// message, and the guard's existing no-tool behavior must remain unchanged.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync, readFileSync, copyFileSync, rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {buildOutgoing} from '../host/pixel_ingress.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const u = (content) => ({role: 'user', content});
const a = (content) => ({role: 'assistant', content});
const tools = [{type: 'function', function: {name: 'exec'}}];

function outgoing(messages, extra = {}) {
  return buildOutgoing({messages, tools, tool_choice: 'auto', ...extra}, 'ods-' + 'a'.repeat(64));
}

const REAL_PROMPT = 'Without using tools, suggest three simple vegetarian lunches using chickpeas, rice, and tomatoes. Keep it under 80 words.';

test('the separately deployed host and plugin policy copies stay identical', () => {
  assert.equal(readFileSync(new URL('../host/owner_tool_policy.mjs',import.meta.url),'utf8'),
    readFileSync(new URL('../plugin/owner-tool-policy.mjs',import.meta.url),'utf8'));
});

test('the Linux installer layout imports and applies the no-tools policy', async () => {
  const root=mkdtempSync(join(tmpdir(),'ods-ingress-layout-'));
  try {
    const script=readFileSync(new URL('../../../../installers/lib/pixel-host-install.sh',import.meta.url),'utf8');
    const declared=[...script.matchAll(/ods_sudo install[^\n]*"\$plugin_root\/host\/([^"/]+\.mjs)" \/usr\/local\/libexec\/([^\s/]+\.mjs)/g)];
    assert.ok(declared.some(([,source])=>source==='owner_tool_policy.mjs'));
    for(const [,source,target] of declared){
      assert.equal(source,basename(source)); assert.equal(target,basename(target));
      copyFileSync(new URL('../host/'+source,import.meta.url),join(root,target));
    }
    const installed=await import(pathToFileURL(join(root,'ods-pixel-ingress.mjs')).href);
    const out=installed.buildOutgoing({messages:[u(REAL_PROMPT)]},null);
    assert.equal(out.tool_choice,'none'); assert.deepEqual(out.tools,[]);
  } finally {rmSync(root,{recursive:true,force:true});}
});

test('exact real Portal prompt forces tool_choice:none and tools:[]', () => {
  const out = outgoing([u(REAL_PROMPT)]);
  assert.equal(out.tool_choice, 'none');
  assert.deepEqual(out.tools, []);
  assert.equal(out.user, 'ods-' + 'a'.repeat(64));
  assert.equal(out.model, 'openclaw/default');
});

test('text content parts are joined; image parts are ignored', () => {
  const out = outgoing([{role: 'user', content: [
    {type: 'text', text: 'Do not use any tools.'},
    {type: 'image_url', image_url: {url: 'data:image/png;base64,AAAA'}},
  ]}]);
  assert.equal(out.tool_choice, 'none');
  assert.deepEqual(out.tools, []);
});

test('an image-only latest user message does not trigger the directive', () => {
  const out = outgoing([{role: 'user', content: [
    {type: 'image_url', image_url: {url: 'data:image/png;base64,AAAA'}},
  ]}]);
  assert.equal(out.tool_choice, 'auto');
  assert.deepEqual(out.tools, tools);
});

test('quoted, fenced, and discussed no-tools text does not trigger', () => {
  for (const text of [
    'The user said "do not use any tools" earlier.',
    'Explain what `do not use any tools` means.',
    '```\nDo not use any tools.\n```',
    '> Do not use any tools.',
  ]) {
    const out = outgoing([u(text)]);
    assert.equal(out.tool_choice, 'auto', text);
    assert.deepEqual(out.tools, tools, text);
  }
});

test('qualified no-tools directives do not trigger', () => {
  for (const text of [
    'Do not use any tools that modify files.',
    'Do not use any tools to change the workspace.',
    'Do not use any tools for file writing.',
    'Do not use any tools except read.',
  ]) {
    const out = outgoing([u(text)]);
    assert.equal(out.tool_choice, 'auto', text);
    assert.deepEqual(out.tools, tools, text);
  }
});

test('an older user no-tools directive does not affect a new user turn', () => {
  const out = outgoing([
    u('Do not use any tools.'),
    a('Understood.'),
    u('Now run the tests.'),
  ]);
  assert.equal(out.tool_choice, 'auto');
  assert.deepEqual(out.tools, tools);
});

test('a tool-result continuation does not infer from an earlier user directive', () => {
  const out = outgoing([
    u('Do not use any tools.'),
    a(''),
    {role: 'tool', tool_call_id: 'call-1', content: 'result'},
  ]);
  assert.equal(out.tool_choice, 'auto');
  assert.deepEqual(out.tools, tools);
});

test('an explicit API tool_choice:none is forwarded unchanged when no directive is present', () => {
  const out = buildOutgoing({messages: [u('Summarize.')], tools, tool_choice: 'none'}, null);
  assert.equal(out.tool_choice, 'none');
  assert.deepEqual(out.tools, tools);
});

test('validation still runs before the policy override', () => {
  assert.throws(() => buildOutgoing({messages: 'nope'}, null), /invalid/);
  assert.throws(() => buildOutgoing({messages: [u('Do not use any tools.')], tools: 'nope'}, null), /invalid/);
});

test('guard still blocks tools after an owner no-tools directive', () => {
  const guard = createToolLoopGuard();
  const context = {trigger: 'user', sessionKey: 'agent:pixel:openai-user:ods-' + 'a'.repeat(64), agentId: 'pixel', runId: 'chatcmpl_11111111-2222-4333-8444-555555555555', sessionId: 'no-tools' };
  guard.observeRun(context, 'pixel', {prompt: 'Do not use any tools. Summarize.'});
  const blocked = guard.beforeToolCall({toolName: 'exec', params: {command: 'echo hi'}}, {...context, toolCallId: 'call-1'});
  assert.equal(blocked?.block, true);
});
