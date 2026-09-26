import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// The installed replacement runs against OpenClaw's own helpers. These stand-ins
// implement only the helper contracts the replacement calls; the real-runtime
// composition is covered by runtime_tool_result_projection.integration.mjs.
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-tool-result-projection.json', import.meta.url)));
const definition = manifest.replacements.find(([before]) => before.includes('function truncateOversizedToolResultsInMessages('));
assert.ok(definition, 'test the installed transformation, not a copied implementation');
const helpers = `
const RECOVERY_MIN_KEEP_CHARS = 0;
function formatContextLimitTruncationNotice(n) { return \`[... \${Math.max(1, Math.floor(n))} more characters truncated; rerun with narrower args if needed]\`; }
function isToolResultTextBlock(block) { return !!block && typeof block === "object" && (block.type === "text" || block.type === "toolResult") && typeof block.text === "string"; }
function getToolResultTextLength(msg) { if (!msg || msg.role !== "toolResult" || !Array.isArray(msg.content)) return 0; let n = 0; for (const b of msg.content) if (isToolResultTextBlock(b)) n += b.text.length; return n; }
function calculateMaxToolResultChars() { return 16000; }
function calculateRecoveryAggregateToolResultChars(ctx, max, aggregate) { return Math.max(1, aggregate ?? max ?? calculateMaxToolResultChars(ctx)); }
function truncateToolResultMessage(msg, maxChars) { let left = maxChars; return {...msg, content: msg.content.map(b => { if (!isToolResultTextBlock(b)) return b; const text = b.text.slice(0, Math.max(0, left)); left -= text.length; return {...b, text}; })}; }
`;
const project = vm.runInNewContext(`${helpers}\n${definition[1]}\ntruncateOversizedToolResultsInMessages`, {Math, Map, Set, Array, Object, Number});
const MAX = 16000, BUDGET = 4 * MAX;
// Return main-realm values so structural assertions compare plain data.
const realm = (projection, input) => ({messages: projection.messages === input ? input : structuredClone(projection.messages),
  truncatedCount: projection.truncatedCount});
const run = (messages, max = MAX) => realm(project(messages, 131072, max, 4 * max, {}), messages);
const textOf = (message) => message.content.filter((b) => b.type === 'text').map((b) => b.text).join('\n');
let nextId = 0;
const call = () => ({role: 'assistant', content: [{type: 'toolCall', id: `call-${nextId}`, name: 'exec', arguments: {}}]});
const result = (chars, extra = {}) => ({role: 'toolResult', toolCallId: `call-${nextId++}`, toolName: 'exec', timestamp: 1000 + nextId,
  content: [{type: 'text', text: 'r'.repeat(chars)}], ...extra});
function turn(history, ...sizes) {
  history.push(call());
  for (const size of sizes) history.push(typeof size === 'number' ? result(size) : size);
  return history;
}

test('recorded starvation: a new file read reaches the model whole once the context already holds 76k chars', () => {
  // tower2 round 057 follow-up: 40 earlier results (76,068 chars), then read totals.py (4,065 chars, two blocks).
  const history = [{role: 'user', content: 'owner'}];
  const recorded = [444, 77, 75, 3645, 5622, 5826, 1195, 776, 1147, 192, 492, 245, 62, 468, 88, 86, 2935, 4460, 494, 81, 87,
    5822, 91, 607, 91, 4304, 596, 3653, 995, 8462, 472, 96, 184, 184, 90, 88, 775, 3095, 14030, 3936];
  for (const size of recorded) turn(history, size);
  const read = result(0, {toolName: 'read', content: [{type: 'text', text: 'x'.repeat(3653)}, {type: 'text', text: '[ODS Pixel next step] ' + 'y'.repeat(389)}]});
  turn(history, read);
  const {messages} = run(history);
  assert.equal(textOf(messages.at(-1)), textOf(read), 'the file content is not cleared, cut or replaced by a notice');
  const results = messages.filter((m) => m.role === 'toolResult');
  const total = results.reduce((sum, m) => sum + textOf(m).length, 0);
  assert.ok(total <= BUDGET, `aggregate ${total} stays within the budget`);
  // The 14,030-char read that first crossed the budget was sent whole and
  // triggered one reduction of the oldest results to the low watermark.
  const large = results.filter((m, index) => recorded[index] === undefined || recorded[index] > 320);
  const reduced = large.map((m) => /truncated; rerun/.test(textOf(m)));
  assert.deepEqual(reduced, [...reduced].sort((a, b) => b - a), 'only the oldest results are reduced; short ones stay whole');
  assert.ok(results.every((m, index) => !(recorded[index] <= 320) || textOf(m).length === recorded[index]));
  assert.equal(textOf(results[38]).length, 14030);
  const kept = results.slice(0, 39).filter((m) => !/truncated; rerun/.test(textOf(m))).reduce((sum, m) => sum + textOf(m).length, 0);
  assert.ok(kept <= BUDGET / 2, `history reduced to the low watermark (${kept})`);
  assert.match(textOf(messages[2]), /^\[\.\.\. 444 more characters truncated; rerun with narrower args if needed\]$/);
});

test('every newly arriving result is delivered whole across a long tool loop', () => {
  const history = [{role: 'user', content: 'owner'}];
  for (let i = 0; i < 80; i++) {
    const size = [9684, 3834, 169, 12000, 590][i % 5];
    turn(history, size);
    const {messages} = run(history);
    assert.equal(textOf(messages.at(-1)).length, size, `result ${i} delivered whole`);
  }
});

test('reductions are rare, oldest first, and leave earlier sends byte-stable between them', () => {
  const history = [{role: 'user', content: 'owner'}];
  let previous = null, rewrites = 0;
  for (let i = 0; i < 60; i++) {
    turn(history, 6000);
    const sent = run(history).messages.map((m) => (m.role === 'toolResult' ? textOf(m) : null));
    if (previous && previous.some((text, index) => text !== sent[index])) rewrites++;
    const reduced = sent.filter((text) => text !== null).map((text) => /truncated/.test(text));
    assert.deepEqual(reduced, [...reduced].sort((a, b) => b - a), 'reduced results are always the oldest ones');
    previous = sent;
  }
  // 360k chars through a 64k budget and 32k watermark: about one rewrite per 32k of new output, not per call.
  assert.ok(rewrites >= 7 && rewrites <= 10, `rewrites ${rewrites}`);
});

test('the projection is a function of transcript order: a fresh attempt sends the same bytes', () => {
  const history = [{role: 'user', content: 'owner'}];
  for (let i = 0; i < 25; i++) turn(history, 3000 + 700 * (i % 4));
  const first = run(history).messages, fresh = run(structuredClone(history)).messages;
  assert.deepEqual(fresh, first);
  // Appending a turn below the budget never changes what was already sent.
  const trimmed = history.slice(0, 3);
  const before = run(trimmed).messages;
  assert.deepEqual(run(turn(trimmed, 500)).messages.slice(0, before.length), before);
});

test('a parallel batch is delivered whole; only results before it are reduced', () => {
  const history = [{role: 'user', content: 'owner'}];
  for (let i = 0; i < 12; i++) turn(history, 5000);
  turn(history, 15000, 15000, 15000);
  const messages = run(history).messages;
  for (const message of messages.slice(-3)) assert.equal(textOf(message).length, 15000);
  assert.ok(messages.slice(0, -3).filter((m) => m.role === 'toolResult').every((m) => /truncated/.test(textOf(m))));
});

test('short results stay whole and identity, error state and images survive reduction', () => {
  const history = [{role: 'user', content: 'owner'}];
  const shortError = result(0, {isError: true, content: [{type: 'text', text: 'exec host not allowed (requested gateway; configured host is auto)'}]});
  const withImage = result(0, {toolName: 'pixel_ods_workspace_preview_inspect',
    content: [{type: 'text', text: 's'.repeat(5000)}, {type: 'image', mimeType: 'image/png', data: 'fixture'}, {type: 'text', text: 't'.repeat(900)}]});
  turn(history, shortError);
  turn(history, withImage);
  for (let i = 0; i < 12; i++) turn(history, 6000);
  const messages = run(history).messages;
  assert.deepEqual(messages[2], shortError);
  const reduced = messages[4];
  assert.equal(reduced.toolCallId, withImage.toolCallId);
  assert.equal(reduced.toolName, withImage.toolName);
  assert.deepEqual(reduced.content.map((b) => b.type), ['text', 'image']);
  assert.equal(reduced.content[0].text, '[... 5900 more characters truncated; rerun with narrower args if needed]');
  assert.equal(withImage.content[0].text.length, 5000, 'the stored transcript message is not mutated');
});

test('within budget nothing changes; an oversized result is still capped per result', () => {
  const history = turn([{role: 'user', content: 'owner'}], 1200, 900);
  const unchanged = run(history);
  assert.equal(unchanged.messages, history);
  assert.equal(unchanged.truncatedCount, 0);
  turn(history, 20000);
  const capped = run(history);
  assert.equal(textOf(capped.messages.at(-1)).length, MAX);
  assert.equal(capped.truncatedCount, 1);
});

test('repair is selected by Linux/WSL installation, foreign restore and native macOS composition', () => {
  const linux = readFileSync(new URL('../../../../installers/lib/pixel-host-install.sh', import.meta.url), 'utf8');
  const mac = readFileSync(new URL('../../../../installers/macos/lib/pixel-runtime-bundle.py', import.meta.url), 'utf8');
  const helper = readFileSync(new URL('../host/openclaw_tool_recovery.py', import.meta.url), 'utf8');
  assert.match(linux, /--openclaw-bin "\$openclaw_bin" --tool-result-projection/);
  assert.match(linux, /ods-runtime-patches\/tool-result-projection"/);
  assert.match(linux, /-f "\$plugin_root\/host\/openclaw-tool-result-projection\.json"/);
  assert.match(linux.split('--restore-foreign')[1].split('>>')[0], /\btool-result-projection\b/);
  assert.match(helper, /TOOL_RESULT_PROJECTION_MODULE = "tool-result-truncation-CbxVHy2D\.js"/);
  assert.match(mac, /\('openclaw-tool-result-projection\.json', 'tool-result-truncation-CbxVHy2D\.js'\),\r?\n(?:\s+\('[a-z-]+\.json', '[A-Za-z0-9._-]+\.js'\),\r?\n)*\s+\('openclaw-compaction-budget\.json', 'selection-BEwSQKM-\.js'\),\r?\n\)/,
    'the stream-progress patch still composes on the last (selection) repair');
  assert.equal(manifest.version, '2026.6.33');
  assert.match(manifest.sourceSha256, /^[a-f0-9]{64}$/);
  assert.match(manifest.patchedSha256, /^[a-f0-9]{64}$/);
});
