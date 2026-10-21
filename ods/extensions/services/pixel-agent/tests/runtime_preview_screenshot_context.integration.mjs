// Preview screenshots against the pinned runtime's own context rules, executed
// from the exact reviewed sources, not copies: history image pruning before
// each model request (openclaw-compaction-budget.json), the Tool Search
// result envelope, the openai-completions request ODS's gateway route uses,
// and the text-only compaction summary request.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {MAX_PREVIEW_SCREENSHOTS_PER_RUN} from '../plugin/preview-screenshot.mjs';
import {INSPECTION_KIND, createWorkspacePreviewInspectTool, inspectionPlanHash}
  from '../plugin/workspace-preview-inspect.mjs';

const root = process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root, 'provide the reviewed OpenClaw package explicitly');
const sha = value => createHash('sha256').update(value).digest('hex');
// The installed bytes ODS runs: the recipe's patched source.
function reviewed(name, module) {
  const recipe = JSON.parse(fs.readFileSync(new URL(`../host/openclaw-${name}.json`, import.meta.url)));
  let source = fs.readFileSync(path.join(root, 'dist', module), 'utf8');
  const applied = sha(source) === recipe.patchedSha256 ? recipe.replacements : recipe.previousReplacements?.[sha(source)];
  if (applied) for (const [before, after] of [...applied].reverse()) { assert.equal(source.split(after).length, 2); source = source.replace(after, () => before); }
  assert.equal(sha(source), recipe.sourceSha256, 'reject unknown native runtime bytes');
  for (const [before, after] of recipe.replacements) { assert.equal(source.split(before).length, 2); source = source.replace(before, () => after); }
  assert.equal(sha(source), recipe.patchedSha256);
  return source;
}
function region(source, name, exported) {
  const start = source.indexOf(`//#region ${name}\n`);
  const end = source.indexOf('//#endregion', start);
  assert.ok(start >= 0 && end > start, name);
  return vm.runInNewContext(`${source.slice(start, end)}\n${exported}`, {Set, Map, Array, Object, JSON, String});
}
const prune = region(reviewed('compaction-budget', 'selection-BEwSQKM-.js'),
  'src/agents/embedded-agent-runner/run/history-image-prune.ts', 'pruneProcessedHistoryImages');
const serialize = region(reviewed('compaction-empty', 'proxy-Bsfwfsp-.js'),
  'packages/agent-core/src/harness/compaction/utils.ts', 'serializeConversation');
// ODS's Tool Search adapter (openclaw-image-envelope.json) that wraps a tool_call child's result.
const search = reviewed('image-envelope', 'tool-search-BInRpkE3.js');
const envelopeStart = search.indexOf('function toolCallResultEnvelope(payload) {');
const envelope = new Function('isRecord', 'jsonResult', `${search.slice(envelopeStart, search.indexOf('async function runCodeMode(params) {'))}\nreturn toolCallResultEnvelope;`)(
  value => value !== null && typeof value === 'object' && !Array.isArray(value), () => assert.fail('a result with an image keeps its native blocks'));
const {t: convertMessages} = await import(pathToFileURL(path.join(root, 'dist/openai-completions-DTj6G8AI.js')).href);
// The Portal route row: unknown and supported image input both declare ['text', 'image'].
const ROUTE = {id: 'ods/current', provider: 'ods-gateway', api: 'openai-completions', input: ['text', 'image'], reasoning: false};

// The real tool's result for a vision model, from a stand-in broker.
const site = {siteId: 'site-' + 'a'.repeat(24), sha256: 'a'.repeat(64)};
const PLAN = {...site, viewport: {width: 800, height: 600}, steps: [{action: 'assert-visible', locator: {selector: '#game'}}]};
const segment = (marker, payload) => Buffer.concat([Buffer.from([0xff, marker, (payload.length + 2) >> 8, (payload.length + 2) & 255]), payload]);
function jpeg(seed) {
  const frame = Buffer.from([8, 600 >> 8, 600 & 255, 800 >> 8, 800 & 255, 1, 1, 0x11, 0]);
  return Buffer.concat([Buffer.from([0xff, 0xd8]), segment(0xc0, frame), segment(0xda, Buffer.from([1, 1, 0, 0, 0x3f, 0])),
    Buffer.alloc(2000, seed), Buffer.from([0xff, 0xd9])]);
}
const tool = createWorkspacePreviewInspectTool({request: async ({screenshot, ...request}) => {
  assert.ok(screenshot, 'the directive travels beside the plan');
  const image = jpeg(Number(request.steps[0].locator.selector.slice('#game-'.length)));
  const state = {count: 1, visible: true, display: 'block', visibility: 'visible', opacity: '1', hidden: false, hiddenUntilFound: false, rectCount: 1};
  return {schemaVersion: 1, kind: INSPECTION_KIND, status: 'passed', ...site, planSha256: inspectionPlanHash(request),
    viewport: request.viewport, steps: [{index: 0, ...request.steps[0], before: state, stable: true, status: 'passed'}],
    diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [],
    scope: 'Only the listed CSS layout visibility, normalized visible-text assertions and click dispatches were tested; not pixel paint, occlusion, clipping, a full accessibility audit, or overall functionality.',
    screenshot: {status: 'captured', mimeType: 'image/jpeg', width: 800, height: 600, bytes: image.length, sha256: sha(image), data: image.toString('base64')}};
}, screenshots: {capability: () => ({supported: true, directive: {maxSide: 1200, maxPixels: 1048576, maxBytes: 131072}}), reserve: () => 'reserved'}});
const plan = seed => ({...structuredClone(PLAN), steps: [{action: 'assert-visible', locator: {selector: `#game-${seed}`}}]});
async function inspected(seed) {
  const result = await tool.execute(`call-${seed}`, plan(seed));
  assert.deepEqual(result.content.map(block => block.type), ['text', 'image']);
  return result;
}
const call = (seed, name = 'pixel_ods_workspace_preview_inspect', args = plan(seed)) =>
  ({role: 'assistant', content: [{type: 'toolCall', id: `call-${seed}`, name, arguments: args}], stopReason: 'toolUse', timestamp: 1000 + seed});
const direct = async seed => [call(seed), {role: 'toolResult', toolCallId: `call-${seed}`, toolName: 'pixel_ods_workspace_preview_inspect',
  content: (await inspected(seed)).content, isError: false, timestamp: 1000 + seed}];
// The same inspection reached through Tool Search: the tool_call result names its child first.
async function searched(seed) {
  const child = {id: 'openclaw:pixel-ods:pixel_ods_workspace_preview_inspect', source: 'openclaw', sourceName: 'pixel-ods',
    name: 'pixel_ods_workspace_preview_inspect', label: 'pixel_ods_workspace_preview_inspect', description: tool.description};
  const wrapped = envelope({tool: child, result: await inspected(seed)});
  return [call(seed, 'tool_call', {id: child.id, args: plan(seed)}),
    {role: 'toolResult', toolCallId: `call-${seed}`, toolName: 'tool_call', content: wrapped.content, isError: false, timestamp: 1000 + seed}];
}
const answer = text => ({role: 'assistant', content: [{type: 'text', text}], stopReason: 'stop', timestamp: 5000});
const OWNER_IMAGE = {type: 'image', mimeType: 'image/png', data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR9kAAAAASUVORK5CYII='};
const images = messages => messages.flatMap(message => Array.isArray(message.content) ? message.content.filter(block => block.type === 'image') : []);
const seeds = messages => images(messages).filter(block => block.mimeType === 'image/jpeg').map(block => Buffer.from(block.data, 'base64').at(-3));
const wireImages = messages => convertMessages(ROUTE, {systemPrompt: 'system', messages}, {})
  .flatMap(message => Array.isArray(message.content) ? message.content.filter(part => part.type === 'image_url') : []);

test('an earlier run\'s screenshots leave the prompt at the next user message; the current run keeps its own', async () => {
  const messages = [];
  for (let turn = 0; turn < 5; turn++) {
    // Owner images in the first and last earlier turns keep the runtime's three-turn rule.
    messages.push({role: 'user', content: [{type: 'text', text: `turn ${turn}`}, ...([0, 4].includes(turn) ? [OWNER_IMAGE] : [])]},
      ...await direct(turn), answer('Looked.'));
  }
  messages.push({role: 'user', content: 'current'});
  for (let n = 0; n < MAX_PREVIEW_SCREENSHOTS_PER_RUN; n++) messages.push(...await direct(10 + n));
  const sent = prune(messages);
  assert.deepEqual(seeds(sent), [10, 11, 12], 'only this run\'s screenshots reach the model');
  assert.equal(images(sent).filter(block => block.mimeType === 'image/png').length, 1, 'the newest owner image stays, the oldest is pruned');
  assert.equal(sent[0].content[1].text, '[image data removed - already processed by model]');
  for (let turn = 0; turn < 5; turn++) {
    const result = sent.find(item => item.role === 'toolResult' && item.toolCallId === `call-${turn}`);
    assert.deepEqual(result.content.map(block => block.type), ['text', 'text']);
    assert.equal(result.content[1].text, '[image data removed - already processed by model]');
    // What the model was told about that screenshot stays in its text.
    assert.match(result.content[0].text, /Screenshot attached: one 800x600 JPEG/);
  }
  assert.equal(prune(sent), null, 'pruning is idempotent, so later requests resend identical history');
  assert.equal(images(messages).length, 2 + 5 + MAX_PREVIEW_SCREENSHOTS_PER_RUN, 'the stored transcript is not mutated');
  // A retry prompt inside the same run is a user message too: what came before it is pruned.
  assert.deepEqual(seeds(prune([...messages, answer('Done.'), {role: 'user', content: 'Revise the reply.'}])), []);
});

test('a Tool Search result of the inspection is pruned the same way; other tool_call images keep the runtime rule', async () => {
  const other = envelope({tool: {id: 'openclaw:core:image', source: 'openclaw', sourceName: 'core', name: 'image'},
    result: {content: [{type: 'text', text: 'An image.'}, OWNER_IMAGE]}});
  const [searchCall, searchResult] = await searched(1);
  assert.equal(searchResult.content[0].text, '{"tool":{"id":"openclaw:pixel-ods:pixel_ods_workspace_preview_inspect","source":"openclaw","sourceName":"pixel-ods","name":"pixel_ods_workspace_preview_inspect"}}');
  assert.deepEqual(searchResult.content.map(block => block.type), ['text', 'text', 'image']);
  const run = [{role: 'user', content: 'Look at the arena.'}, searchCall, searchResult,
    call(2, 'tool_call', {id: 'openclaw:core:image', args: {}}), {role: 'toolResult', toolCallId: 'call-2', toolName: 'tool_call', content: other.content, isError: false, timestamp: 1002}];
  assert.equal(prune(run), null, 'the run that took the screenshot sends it');
  const sent = prune([...run, answer('Fixed.'), {role: 'user', content: 'Make the sprite blue.'}]);
  assert.deepEqual(sent[2].content.map(block => block.type), ['text', 'text', 'text']);
  assert.deepEqual(sent[2].content[0], searchResult.content[0], 'the envelope identity stays');
  assert.deepEqual(sent[4].content, other.content, 'another tool\'s image is not a preview screenshot');
});

test('after a model switch, no earlier screenshot reaches the next request; the run that took it sends it', async () => {
  // The Portal alias keeps its id across switches and both supported and
  // unknown routes declare image input, so only pruning can stop the replay.
  const run = [{role: 'user', content: 'Fix the arena.'}, ...await direct(1)];
  assert.equal(wireImages(prune(run) ?? run).length, 1, 'the model that took the screenshot sees it');
  const next = [...run, answer('Fixed.'), {role: 'user', content: 'Now make the sprite blue.'}];
  assert.equal(wireImages(prune(next) ?? next).length, 0, 'the next owner turn, on whatever route, carries no screenshot bytes');
  // Worst case: four earlier runs at the per-run bound, and this run at it too.
  const many = [];
  for (let turn = 0; turn < 4; turn++) {
    many.push({role: 'user', content: `turn ${turn}`});
    for (let n = 0; n < MAX_PREVIEW_SCREENSHOTS_PER_RUN; n++) many.push(...await (n === 1 ? searched : direct)(10 * turn + n));
    many.push(answer('Looked.'));
  }
  many.push({role: 'user', content: 'current'});
  for (let n = 0; n < MAX_PREVIEW_SCREENSHOTS_PER_RUN; n++) many.push(...await direct(50 + n));
  assert.equal(wireImages(prune(many) ?? many).length, MAX_PREVIEW_SCREENSHOTS_PER_RUN, 'one request carries at most one run\'s screenshots');
});

test('a compaction summary request carries the screenshot sentence, never its bytes', async () => {
  const [inspectCall, result] = await direct(7);
  const text = serialize([{role: 'user', content: 'Fix the arena.'}, inspectCall, result]);
  assert.match(text, /\[Tool result\]: Preview inspection passed\. .*Screenshot attached: one 800x600 JPEG of the whole 800x600 preview viewport/s);
  assert.equal(text.includes(result.content[1].data.slice(0, 32)), false);
});
