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
    `${region}\nreturn {truncateOversizedToolResultsInMessages, createToolResultPromptProjectionState};`)(normalizeLowercaseStringOrEmpty);
}
const original = load(originalSource);
const candidate = load(candidateSource);

// ODS sets toolResultMaxChars 16000; the attempt passes 4x as the aggregate.
const MAX = 16000, CONTEXT = 131072;
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

function attempt(runtime, history, sizes) {
  // One embedded attempt: projection at attempt start, then one provider
  // request per tool turn with the attempt's projection state.
  const state = runtime.createToolResultPromptProjectionState();
  const messages = [...history, {role: 'user', content: 'FOLLOWUP CODE TASK', timestamp: 1}];
  runtime.truncateOversizedToolResultsInMessages(history, CONTEXT, MAX, 4 * MAX, state);
  const requests = [runtime.truncateOversizedToolResultsInMessages(messages, CONTEXT, MAX, 4 * MAX, state).messages];
  for (const size of sizes) {
    messages.push(...toolTurn([size]));
    requests.push(runtime.truncateOversizedToolResultsInMessages(messages, CONTEXT, MAX, 4 * MAX, state).messages);
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
  const {messages, requests} = attempt(candidate, history(), FOLLOW_UP);
  const results = messages.filter(message => message.role === 'toolResult').slice(-FOLLOW_UP.length);
  requests.slice(1).forEach((request, index) => {
    assert.equal(toolMessages(request).at(-1).content, expectedText(results[index]), `result ${index} reaches the provider whole`);
  });
  const sent = toolMessages(requests.at(-1)).map(message => message.content);
  assert.ok(sent.every(text => text.length > 0 && text !== '(see attached image)'), 'no result is ever sent empty');
  assert.ok(sent.filter(text => /more characters truncated; rerun with narrower args if needed\]$/.test(text)).length > 0,
    'older results carry the runtime truncation notice instead');
});

test('repaired projection keeps the sent prefix stable across calls and across the next attempt', () => {
  const base = history();
  const first = attempt(candidate, base, FOLLOW_UP.slice(0, 3));
  const tools = request => toolMessages(request).map(message => message.content);
  let rewrites = 0;
  for (let index = 1; index < first.requests.length; index++) {
    const before = tools(first.requests[index - 1]), after = tools(first.requests[index]);
    if (before.some((text, position) => text !== after[position])) rewrites++;
  }
  assert.ok(rewrites <= 1, `earlier sends were rewritten ${rewrites} times`);
  // The next owner turn (fresh attempt state) re-sends identical history bytes.
  const last = tools(first.requests.at(-1));
  const next = attempt(candidate, first.messages, []);
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
