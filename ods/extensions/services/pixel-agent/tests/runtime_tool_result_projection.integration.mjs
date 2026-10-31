// OPENCLAW_PACKAGE_DIR must be an absolute path to the pinned runtime package.
// Original and candidate projection run from hash-verified runtime bytes;
// candidate edits stay in memory. Requests are converted by the runtime's own
// openai-completions transport code, as the Pixel provider receives them.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {isAbsolute, join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {isDeepStrictEqual} from 'node:util';

const packageDir = process.env.OPENCLAW_PACKAGE_DIR;
if (!packageDir || !isAbsolute(packageDir)) {
  throw new Error('Set OPENCLAW_PACKAGE_DIR to the absolute path of the pinned OpenClaw package.');
}
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-tool-result-projection.json', import.meta.url), 'utf8'));
assert.equal(manifest.version, '2026.6.33');
assert.equal(JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8')).version, manifest.version);
const installedSource = readFileSync(join(packageDir, 'dist/tool-result-truncation-CbxVHy2D.js'), 'utf8');
const sha256 = source => createHash('sha256').update(source).digest('hex');

function transform(source, reverse = false) {
  const replacements = reverse ? [...manifest.replacements].reverse() : manifest.replacements;
  for (const pair of replacements) {
    const [before, after] = reverse ? [...pair].reverse() : pair;
    assert.equal(source.split(before).length, 2, 'each reviewed replacement must match exactly once');
    source = source.replace(before, () => after);
  }
  return source;
}
const installedHash = sha256(installedSource);
assert.ok([manifest.sourceSha256, manifest.patchedSha256].includes(installedHash),
  `Unreviewed runtime module SHA-256: ${installedHash}`);
const originalSource = installedHash === manifest.sourceSha256 ? installedSource : transform(installedSource, true);
const candidateSource = transform(originalSource);
assert.equal(sha256(originalSource), manifest.sourceSha256);
assert.equal(sha256(candidateSource), manifest.patchedSha256);
assert.equal(transform(candidateSource, true), originalSource, 'the manifest must reverse byte for byte');

const dist = name => pathToFileURL(join(packageDir, 'dist', name)).href;
const {a: normalizeLowercaseStringOrEmpty} = await import(dist('string-coerce-DW4mBlAt.js'));
const {t: convertMessages} = await import(dist('openai-completions-DTj6G8AI.js'));
function load(source) {
  const start = '//#region src/agents/embedded-agent-runner/context-truncation-notice.ts';
  assert.equal(source.split(start).length, 2);
  const region = source.slice(source.indexOf(start), source.indexOf('\nexport {'));
  return new Function('normalizeLowercaseStringOrEmpty',
    `${region}\nreturn {truncateOversizedToolResultsInMessages, createToolResultPromptProjectionState,
      liveGuardChars: typeof liveGuardChars === 'function' ? liveGuardChars : undefined};`)(normalizeLowercaseStringOrEmpty);
}
const original = load(originalSource);
const candidate = load(candidateSource);

// ODS sets toolResultMaxChars 16000; the attempt passes 4x as the aggregate.
const MAX = 16000, CONTEXT = 131072;
// Below 131k tokens a response keeps the base aggregate (4 x 16k) in view.
const BASE_CONTEXT = 65536;
const model = {id: 'ods/current', provider: 'ods-gateway', api: 'openai-completions', input: ['text'], reasoning: false};
let id = 0;
const block = (chars, fill) => ({type: 'text', text: fill.repeat(Math.ceil(chars / fill.length)).slice(0, chars)});
function toolTurn(sizes, toolName = 'exec') {
  const calls = sizes.map(() => ({type: 'toolCall', id: `call_${++id}`, name: toolName, arguments: {command: 'ls'}}));
  return [{role: 'assistant', content: calls, api: model.api, provider: model.provider, model: model.id, stopReason: 'toolUse', timestamp: 1_790_000_000_000 + id},
    ...calls.map((call, index) => ({role: 'toolResult', toolCallId: call.id, toolName, isError: false, timestamp: 1_790_000_000_000 + id + index,
      content: [].concat(sizes[index]).map((chars, part) => block(chars, part ? '[ODS Pixel next step] ' : `line ${call.id} `))}))];
}
const toolMessages = messages => convertMessages(model, {messages}, {}).filter(message => message.role === 'tool');

// tower2 round 057, session 88ecde62: 40 results (76,068 chars) before the
// follow-up owner turn, then the follow-up's first results (read totals.py,
// cat, ls, ls, ls -la of the 107-entry workspace).
const RECORDED_HISTORY = [444, 77, 75, 3645, 5622, 5826, 1195, 776, 1147, 192, 492, 245, 62, 468, 88, 86, 2935, 4460, 494, 81, 87,
  5822, 91, 607, 91, 4304, 596, 3653, 995, 8462, 472, 96, 184, 184, 90, 88, 775, 3095, 14030, 3936];
const FOLLOW_UP = [[3653, 411], [3643, 181], [408, 181], [408, 181], [9502, 181], 9672, 9672];

function attempt(runtime, history, sizes, context = CONTEXT) {
  // One embedded attempt: projection at attempt start, then one provider
  // request per tool turn with the attempt's projection state.
  const state = runtime.createToolResultPromptProjectionState();
  const messages = [...history, {role: 'user', content: 'FOLLOWUP CODE TASK', timestamp: 1}];
  runtime.truncateOversizedToolResultsInMessages(history, context, MAX, 4 * MAX, state);
  // Each request as sent: an unchanged projection is the growing array itself.
  const requests = [[...runtime.truncateOversizedToolResultsInMessages(messages, context, MAX, 4 * MAX, state).messages]];
  for (const size of sizes) {
    messages.push(...toolTurn([size]));
    requests.push([...runtime.truncateOversizedToolResultsInMessages(messages, context, MAX, 4 * MAX, state).messages]);
  }
  return {messages, requests};
}
const history = () => { id = 0; return [{role: 'user', content: 'owner', timestamp: 0}, ...RECORDED_HISTORY.flatMap(size => toolTurn([size]))]; };
const expectedText = message => message.content.filter(part => part.type === 'text').map(part => part.text).join('\n');

test('pinned projection sends new results empty once the context holds 64k chars of tool output', () => {
  const {messages, requests} = attempt(original, history(), FOLLOW_UP);
  const results = messages.filter(message => message.role === 'toolResult').slice(-FOLLOW_UP.length);
  requests.slice(1).forEach((request, index) => {
    const sent = toolMessages(request).at(-1).content;
    assert.notEqual(sent, expectedText(results[index]));
    assert.ok(sent === '\n' || sent === '(see attached image)', `starved result ${index}: ${JSON.stringify(sent.slice(0, 40))}`);
  });
});

test('repaired projection delivers every new result whole through the same transport', () => {
  const {messages, requests} = attempt(candidate, history(), FOLLOW_UP, BASE_CONTEXT);
  const results = messages.filter(message => message.role === 'toolResult').slice(-FOLLOW_UP.length);
  requests.slice(1).forEach((request, index) => {
    assert.equal(toolMessages(request).at(-1).content, expectedText(results[index]), `result ${index} reaches the provider whole`);
  });
  const sent = toolMessages(requests.at(-1)).map(message => message.content);
  assert.ok(sent.every(text => text.length > 0 && text !== '(see attached image)'), 'no result is ever sent empty');
  assert.ok(sent.filter(text => /more characters truncated; rerun with narrower args if needed\]$/.test(text)).length > 0,
    'older results carry the runtime truncation notice instead');
});

// 2026-10-30: in a large-context response the same history and follow-up
// (114k characters of tool output) stay whole, so nothing is read again.
test('a large-context response keeps the recorded history whole while it fits twice the base aggregate', () => {
  const {messages, requests} = attempt(candidate, history(), FOLLOW_UP);
  const results = messages.filter(message => message.role === 'toolResult');
  assert.deepEqual(toolMessages(requests.at(-1)).map(message => message.content), results.map(expectedText));
  // A turn that ends with the final reply, as the pre-prompt compaction check
  // sees it, is measured at the base aggregate.
  const ended = [...messages, {role: 'assistant', content: [{type: 'text', text: 'Done.'}], api: model.api, provider: model.provider,
    model: model.id, stopReason: 'stop', timestamp: 1_790_000_100_000}];
  const measured = candidate.truncateOversizedToolResultsInMessages(ended, CONTEXT, MAX, 4 * MAX).messages;
  assert.ok(toolMessages(measured).some(message => /more characters truncated; rerun with narrower args if needed\]$/.test(message.content)));
});

for (const context of [BASE_CONTEXT, CONTEXT])
test(`repaired projection keeps the sent prefix stable across calls and across the next attempt (${context / 1024}k)`, () => {
  const base = history();
  const first = attempt(candidate, base, FOLLOW_UP.slice(0, 3), context);
  const tools = request => toolMessages(request).map(message => message.content);
  let rewrites = 0;
  for (let index = 1; index < first.requests.length; index++) {
    const before = tools(first.requests[index - 1]), after = tools(first.requests[index]);
    if (before.some((text, position) => text !== after[position])) rewrites++;
  }
  assert.ok(rewrites <= 1, `earlier sends were rewritten ${rewrites} times`);
  // The next owner turn (fresh attempt state) re-sends identical history bytes.
  const last = tools(first.requests.at(-1));
  const next = attempt(candidate, first.messages, [], context);
  assert.deepEqual(tools(next.requests[0]).slice(0, last.length), last);
  // The pinned projection re-trims at every attempt start instead.
  const pinned = attempt(original, base, FOLLOW_UP.slice(0, 3));
  const pinnedLast = tools(pinned.requests.at(-1));
  const pinnedNext = tools(attempt(original, pinned.messages, []).requests[0]);
  assert.notDeepEqual(pinnedNext.slice(0, pinnedLast.length), pinnedLast);
});

test('within the aggregate budget both projections send identical requests', () => {
  id = 0;
  const small = [{role: 'user', content: 'owner', timestamp: 0}, ...[900, 1200, [3000, 400]].flatMap(size => toolTurn([size]))];
  const start = id;
  const pinned = attempt(original, small, [2000, 500]);
  id = start;
  const repaired = attempt(candidate, small, [2000, 500]);
  assert.deepEqual(repaired.requests.map(toolMessages), pinned.requests.map(toolMessages));
});

// The pinned tool-loop guard's own estimate (tool-result-context-guard). It
// measures this projection, so the wider view must never let it see more than
// window x 4 x 0.9 characters where the base projection would not.
const selection = readFileSync(join(packageDir, 'dist/selection-BEwSQKM-.js'), 'utf8');
const estimatorStart = '//#region src/agents/embedded-agent-runner/run/midturn-precheck.ts';
const estimatorEnd = '//#region src/agents/embedded-agent-runner/tool-result-context-guard.ts';
assert.ok(selection.split(estimatorStart).length === 2 && selection.split(estimatorEnd).length === 2);
const {estimateMessageChars} = new Function(
  `${selection.slice(selection.indexOf(estimatorStart), selection.indexOf(estimatorEnd))}\nreturn {estimateMessageChars};`)();
const guardChars = messages => messages.reduce((sum, message) => sum + estimateMessageChars(message), 0);
const GUARD_LIMIT = Math.floor(CONTEXT * 4 * 0.9);
const image = {type: 'image', data: 'iVBORw0KGgo=', mimeType: 'image/png'};

test('the projection counts every message at least as the pinned guard does', () => {
  const shapes = [
    {role: 'user', content: 'plain owner text'},
    {role: 'user', content: [{type: 'text', text: 'see this'}, image, {type: 'custom', value: 1}]},
    {role: 'assistant', content: [{type: 'thinking', thinking: 'plan the cache'}, {type: 'text', text: 'Reading ui.js.'},
      {type: 'toolCall', id: 'c1', name: 'write', arguments: {path: 'ui.js', content: 'x'.repeat(5000)}}, {type: 'other', n: 2}]},
    {role: 'toolResult', toolCallId: 'c1', content: [{type: 'text', text: 'r'.repeat(6000)}, image]},
    {role: 'toolResult', toolCallId: 'c2', content: 'string result'},
    {role: 'tool', content: [{type: 'text', text: 'openai-style result'}]},
    {role: 'custom', content: 'other role'},
    {role: 'user', content: '游戏缓存'.repeat(500)},
    {role: 'toolResult', toolCallId: 'c3', content: [{type: 'text', text: '敵のスプライト'.repeat(300)}]},
  ];
  for (const message of shapes) {
    const pinned = estimateMessageChars(message), counted = candidate.liveGuardChars(message);
    assert.ok(counted >= pinned, `${JSON.stringify(message).slice(0, 60)}: ${counted} < ${pinned}`);
    if (!/[\u2E80-\u9FFF\uAC00-\uD7AF]/.test(JSON.stringify(message))) assert.equal(counted, pinned, JSON.stringify(message).slice(0, 60));
  }
});

test('every request sends the wider view only within the pinned guard limit, otherwise the base projection', () => {
  let seed = 20261030;
  const random = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  let checked = 0, wider = 0, based = 0;
  for (let response = 0; response < 24; response++) {
    const messages = [{role: 'user', content: `owner ${'o'.repeat(Math.floor(random() * 4000))}`, timestamp: 0}];
    const argChars = Math.floor(random() * 6000), resultChars = 1000 + Math.floor(random() * 15000);
    for (let round = 0; round < 70; round++) {
      const calls = Array.from({length: random() < 0.2 ? 2 : 1}, () => ({type: 'toolCall', id: `call_${++id}`, name: 'exec',
        arguments: {command: 'x'.repeat(Math.floor(random() * argChars))}}));
      messages.push({role: 'assistant', api: model.api, provider: model.provider, model: model.id, stopReason: 'toolUse', timestamp: id,
        content: random() < 0.3 ? [{type: 'thinking', thinking: 't'.repeat(Math.floor(random() * 3000))}, ...calls] : calls});
      for (const call of calls) {
        const text = random() < 0.1 ? '画'.repeat(Math.floor(random() * resultChars / 3)) : 'r'.repeat(Math.floor(random() * resultChars));
        messages.push({role: 'toolResult', toolCallId: call.id, toolName: 'exec', isError: false, timestamp: id,
          content: random() < 0.15 ? [{type: 'text', text}, image] : [{type: 'text', text}]});
      }
      if (random() < 0.05) messages.push({role: 'user', content: `queued ${'q'.repeat(Math.floor(random() * 6000))}`, timestamp: id});
      const sent = candidate.truncateOversizedToolResultsInMessages(messages, CONTEXT, MAX, 4 * MAX).messages;
      const base = candidate.truncateOversizedToolResultsInMessages(messages, BASE_CONTEXT, MAX, 4 * MAX).messages;
      checked++;
      if (isDeepStrictEqual(sent, base)) based++;
      else {
        wider++;
        assert.ok(guardChars(sent) <= GUARD_LIMIT, `response ${response} round ${round}: ${guardChars(sent)} in the wider view`);
      }
    }
  }
  assert.ok(wider > 100 && based > 100, JSON.stringify({checked, wider, based}));
});
