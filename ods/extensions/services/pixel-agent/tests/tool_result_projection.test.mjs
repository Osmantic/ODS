import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {isDeepStrictEqual} from 'node:util';
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
const project = vm.runInNewContext(`${helpers}\n${definition[1]}\ntruncateOversizedToolResultsInMessages`, {Math, Map, Set, Array, Object, Number, JSON, Boolean});
const MAX = 16000, BUDGET = 4 * MAX;
// Return main-realm values so structural assertions compare plain data.
const realm = (projection, input) => ({messages: projection.messages === input ? input : structuredClone(projection.messages),
  truncatedCount: projection.truncatedCount});
// Below 128K tokens the base aggregate applies throughout; `wide` runs a 131K model.
const run = (messages, max = MAX) => realm(project(messages, 65536, max, 4 * max, {}), messages);
const wide = (messages, max = MAX) => realm(project(messages, 131072, max, 4 * max, {}), messages);
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

const inView = messages => messages.filter((m) => m.role === 'toolResult' && !/truncated; rerun/.test(textOf(m)))
  .reduce((sum, m) => sum + textOf(m).length, 0);

test('during a response a 131K model keeps twice the aggregate in view', () => {
  // Laptop, 2026-10-30: one response re-read all of ui.js about nine times once
  // its earlier reads were reduced, with the prompt never above 55K of 131K tokens.
  const history = [{role: 'user', content: 'owner'}];
  let previous = null, rewrites = 0;
  const steady = [];
  for (let i = 0; i < 60; i++) {
    turn(history, 6000);
    const {messages} = wide(history);
    assert.equal(textOf(messages.at(-1)).length, 6000, `result ${i} delivered whole`);
    const kept = inView(messages);
    assert.ok(kept <= 2 * BUDGET, `in view ${kept}`);
    if (i >= 25) steady.push(kept);
    const sent = messages.map((m) => (m.role === 'toolResult' ? textOf(m) : null));
    if (previous && previous.some((text, index) => text !== sent[index])) rewrites++;
    previous = sent;
  }
  // Between trims to the low watermark (half the doubled aggregate) the view
  // grows back to 128k: 60-128k in view instead of 32-64k.
  assert.ok(Math.min(...steady) >= BUDGET - 6000, `at least the base aggregate less one result (${Math.min(...steady)})`);
  const mean = steady.reduce((sum, kept) => sum + kept, 0) / steady.length;
  assert.ok(mean >= 1.4 * BUDGET, `on average ${Math.round(mean)} in view`);
  // Reductions stay rare: about one per 64k of new output.
  assert.ok(rewrites >= 3 && rewrites <= 5, `rewrites ${rewrites}`);
  // Below 128K tokens nothing changes.
  assert.ok(inView(run(history).messages) <= BUDGET);
});

// The tool loop's overflow guard (tool-result-context-guard) measures this
// projection: tool output counts twice against window x 4 x 0.9 characters,
// tool-call arguments and other text once.
const GUARD_LIMIT = Math.floor(131072 * 4 * 0.9);
const guardEstimate = messages => messages.reduce((sum, m) => sum + (m.role === 'toolResult' ? 2 * textOf(m).length
  : m.role === 'assistant' ? m.content.reduce((chars, b) => chars + (b.type === 'toolCall' ? JSON.stringify(b.arguments ?? {}).length : b.text.length), 0)
  : m.content.length), 0);
const write = (chars) => ({role: 'assistant', content: [{type: 'toolCall', id: `call-${nextId}`, name: 'write',
  arguments: {path: 'Playground/game/ui.js', content: 'x'.repeat(chars)}}]});

// The wider walk alone: at a huge window the guard limit is never reached.
const unguarded = (messages) => realm(project(messages, 1e7, MAX, 4 * MAX, {}), messages);

// Every request either fits the guard or sends exactly the base projection,
// so the guard trips at the same request as without the wider view; once a
// response has sent the base projection where the wider walk differs, it never
// returns to the wider view.
function assertGuardSafe(history, label, state) {
  const sent = wide(history).messages;
  const base = run(history).messages;
  const wider = unguarded(history).messages;
  const isBase = isDeepStrictEqual(sent, base);
  assert.ok(isBase || isDeepStrictEqual(sent, wider), `${label}: neither the base nor the wider view`);
  assert.ok(isBase || guardEstimate(sent) <= GUARD_LIMIT, `${label}: ${guardEstimate(sent)} sent in the wider view`);
  if (state.narrowed) assert.ok(isBase, `${label}: back to the wider view`);
  if (isBase && !isDeepStrictEqual(base, wider)) state.narrowed = true;
  if (!isBase) state.widened = true;
  if (guardEstimate(sent) > GUARD_LIMIT) state.tripped = true;
  assert.equal(guardEstimate(sent) > GUARD_LIMIT, guardEstimate(base) > GUARD_LIMIT, `${label}: trips exactly where the base does`);
  return sent;
}

test('the wider view never makes the overflow guard trip sooner, and a response never returns to it', () => {
  // A long editing response: every round writes 5k of code and reads 6k, and
  // now and then a queued owner message arrives between rounds.
  const history = [{role: 'user', content: 'owner'}];
  const state = {};
  for (let i = 0; i < 90; i++) {
    history.push(write(5000), result(6000));
    const sent = assertGuardSafe(history, `round ${i}`, state);
    assert.equal(textOf(sent.at(-1)).length, 6000, `result ${i} delivered whole`);
    // A queued owner message just big enough to pass the limit in the wider view.
    const gap = GUARD_LIMIT - guardEstimate(sent);
    if (!isDeepStrictEqual(sent, run(history).messages) && gap < 60000) {
      const probe = [...history, {role: 'user', content: 'q'.repeat(gap + 1)}];
      const probed = wide(probe).messages;
      assert.ok(isDeepStrictEqual(probed, run(probe).messages) || guardEstimate(probed) <= GUARD_LIMIT,
        `round ${i}: a queued message sent ${guardEstimate(probed)} in the wider view`);
      state.probed = true;
    }
    if (i % 7 === 3) {
      history.push({role: 'user', content: `Also keep the boss fight smooth. ${'y'.repeat(3000)}`});
      assertGuardSafe(history, `round ${i} queued message`, state);
    }
  }
  assert.ok(state.widened && state.narrowed && state.tripped && state.probed, JSON.stringify(state));
  // A reading response with little else keeps the wider view throughout.
  const reading = [{role: 'user', content: 'owner'}];
  for (let i = 0; i < 90; i++) turn(reading, 6000);
  assert.ok(inView(wide(reading).messages) > BUDGET);
});

test('once an attempt declines the wider view, it keeps the base projection though its input shrinks', () => {
  // The provider stream passes one projection state per attempt. Between its
  // requests OpenClaw prunes old images and drops prompt context, so the same
  // transcript can arrive smaller; restoring results already sent as notices
  // would rewrite the cached prompt prefix.
  const big = [{role: 'user', content: `owner ${'p'.repeat(330000)}`}];
  for (let i = 0; i < 16; i++) turn(big, 6000);
  const shrunk = [{role: 'user', content: 'owner'}, ...big.slice(1)];
  const attempt = {};
  assert.deepEqual(realm(project(big, 131072, MAX, 4 * MAX, attempt), big).messages, run(big).messages);
  assert.equal(attempt.liveWideDeclined, true);
  assert.deepEqual(realm(project(shrunk, 131072, MAX, 4 * MAX, attempt), shrunk).messages, run(shrunk).messages);
  // A new attempt decides afresh.
  assert.ok(inView(wide(shrunk).messages) > inView(run(shrunk).messages));
});

test('dense scripts give up the wider view sooner, as OpenClaw counts them', () => {
  // About one token per character: 128k characters of it would fill the window.
  const dense = [{role: 'user', content: 'owner'}];
  const latin = [{role: 'user', content: 'owner'}];
  for (let i = 0; i < 40; i++) {
    turn(dense, {...result(0), content: [{type: 'text', text: '游戏'.repeat(3000)}]});
    turn(latin, 6000);
  }
  assert.ok(inView(wide(latin).messages) > BUDGET);
  const sent = wide(dense).messages;
  assert.deepEqual(sent, run(dense).messages);
});

test('a transcript that ends with a final reply keeps the base aggregate the compaction check measures', () => {
  const history = [{role: 'user', content: 'owner'}];
  for (let i = 0; i < 30; i++) turn(history, 6000);
  assert.ok(inView(wide(history).messages) > BUDGET);
  history.push({role: 'assistant', content: [{type: 'text', text: 'Done: the cache is in place.'}]});
  assert.ok(inView(wide(history).messages) <= BUDGET, 'the pre-prompt check sees what it saw before');
  // A reply that still calls tools is not final.
  history.push({role: 'assistant', content: [{type: 'text', text: 'Checking once more.'}, {type: 'toolCall', id: 'x', name: 'exec', arguments: {}}]});
  assert.ok(inView(wide(history).messages) > BUDGET);
});

test('the next owner message keeps what the response sent, so its prefix stays byte-stable', () => {
  const history = [{role: 'user', content: 'owner'}];
  for (let i = 0; i < 30; i++) turn(history, 6000);
  const during = wide(history).messages;
  history.push({role: 'assistant', content: [{type: 'text', text: 'Done.'}]}, {role: 'user', content: 'continue'});
  const next = wide(history).messages;
  assert.deepEqual(next.slice(0, during.length), during);
});

// The reviewed recipe this change extends (Osmantic/ODS#8336). The
// superseded-first order must never make the guard trip sooner than it.
const PREVIOUS_PROJECTION = '595818ac5da06acc428175a056052561c1a3adf8ef5e8d5d047670d26e07695a';
const load = (source, names) => vm.runInNewContext(`${helpers}\n${source}\n({${names}})`, {Math, Map, Set, Array, Object, Number, JSON, Boolean});
const [[, previousDefinition]] = manifest.previousReplacements[PREVIOUS_PROJECTION];
const {truncateOversizedToolResultsInMessages: previous} = load(previousDefinition, 'truncateOversizedToolResultsInMessages');
const {liveSupersededReads} = load(definition[1], 'liveSupersededReads');
const before = (messages, context = 65536, state = {}) => realm(previous(messages, context, MAX, 4 * MAX, state), messages);
// Synthetic files: every line is 60 characters with its newline, so 100 lines are 5,999.
const lineText = (file, n) => `${file} line ${n} `.padEnd(59, '.');
const fileText = (file, start, end) => Array.from({length: end - start + 1}, (_, i) => lineText(file, start + i)).join('\n');
function tool(history, name, args, text, extra = {}) {
  history.push({role: 'assistant', content: [{type: 'toolCall', id: `call-${nextId}`, name, arguments: args}]});
  history.push({role: 'toolResult', toolCallId: `call-${nextId++}`, toolName: name, timestamp: 1000 + nextId,
    content: [{type: 'text', text}], ...extra});
  return history;
}
const readLines = (history, file, start, end) => tool(history, 'read', {path: file, offset: start, limit: end - start + 1}, fileText(file, start, end));
const edited = (history, file) => tool(history, 'edit', {path: file, edits: [{oldText: 'old', newText: 'new'}]}, `Successfully replaced 1 block(s) in ${file}.`);
const superseded = (history) => Object.fromEntries(liveSupersededReads(history, MAX));
const whole = (messages) => messages.filter((m) => m.role === 'toolResult').map((m) => !/truncated; rerun/.test(textOf(m)));
const owner = () => [{role: 'user', content: 'owner'}];

test('a reduction drops reads the response made again before its fresh reads', () => {
  // Laptop, 2026-10-30: one response re-read the same parts of ui.js again and
  // again. Reducing strictly oldest first dropped its other reads and kept the copies.
  const history = owner();
  for (const file of ['a.js', 'b.js', 'c.js', 'd.js', 'e.js']) readLines(history, `game/${file}`, 1, 100);
  for (let i = 0; i < 6; i++) readLines(history, 'game/ui.js', 1, 100);
  // Eleven 6k reads pass the 64k budget once and are reduced to the 32k watermark.
  assert.deepEqual(whole(run(history).messages), [false, true, true, true, true, false, false, false, false, false, true]);
  assert.deepEqual(whole(before(history).messages), [false, false, false, false, false, false, true, true, true, true, true],
    'the projection this extends dropped every fresh read and kept five copies');
});

test('a read of a file that a later change replaced goes before fresh reads', () => {
  const history = owner();
  for (const file of ['a.js', 'b.js', 'c.js', 'd.js', 'e.js', 'f.js', 'g.js', 'h.js']) readLines(history, `game/${file}`, 1, 100);
  readLines(history, 'game/ui.js', 1, 100);
  edited(history, 'game/ui.js');
  readLines(history, 'game/i.js', 1, 100);
  readLines(history, 'game/j.js', 1, 100);
  // a..e are the oldest fresh reads; the stale ui.js read goes first. The
  // short edit confirmation stays whole.
  assert.deepEqual(whole(run(history).messages), [false, false, false, false, false, true, true, true, false, true, true, true]);
  assert.deepEqual(whole(before(history).messages), [false, false, false, false, false, false, true, true, true, true, true, true]);
});

test('only a change its tool reports as made makes earlier reads stale', () => {
  const patch = '*** Begin Patch\n*** Update File: game/ui.js\n@@\n-old\n+new\n*** End Patch';
  const changes = [
    ['edit', {path: 'game/ui.js', edits: []}, 'Successfully replaced 1 block(s) in game/ui.js.', {}, true],
    ['write', {path: '/workspace/game/ui.js', content: 'x'}, 'Successfully wrote 1 bytes to /workspace/game/ui.js', {}, true],
    ['apply_patch', {input: patch}, 'Success. Updated the following files:\nM game/ui.js', {}, true],
    ['apply_patch', {input: patch.replace('Update File: game/ui.js', 'Update File: game/other.js\n*** Move to: game/ui.js')},
      'Success. Updated the following files:\nM game/ui.js', {}, true],
    ['edit', {path: 'game/ui.js', edits: []}, '{"status": "error", "tool": "edit", "error": "Could not find edits[0]"}', {isError: true}, false],
    ['edit', {path: 'game/ui.js', edits: []}, 'Tool call blocked because before_tool_call hook failed', {}, false],
    ['write', {path: 'game/ui.js', content: 'x'}, 'Successfully wrote 1 bytes to game/ui.js', {isError: true}, false],
    ['edit', {path: 'game/other.js', edits: []}, 'Successfully replaced 1 block(s) in game/other.js.', {}, false],
    ['exec', {command: "sed -i 's/old/new/' game/ui.js"}, '', {}, false],
  ];
  for (const [name, args, text, extra, stale] of changes) {
    const history = readLines(owner(), 'game/ui.js', 1, 100);
    tool(history, name, args, text, extra);
    assert.deepEqual(superseded(history), stale ? {2: 4} : {}, `${name}: ${text.slice(0, 40)}`);
  }
});

test('a later read supersedes an earlier one only when it shows that text again', () => {
  const pair = (first, second, secondText) => {
    const history = readLines(owner(), 'game/ui.js', ...first);
    if (secondText === undefined) return superseded(readLines(history, 'game/ui.js', ...second));
    return superseded(tool(history, 'read', {path: 'game/ui.js', offset: second[0], limit: second[1] - second[0] + 1}, secondText));
  };
  assert.deepEqual(pair([1, 100], [1, 100]), {2: 4});
  assert.deepEqual(pair([101, 200], [1, 300]), {2: 4}, 'a wider range');
  assert.deepEqual(pair([1, 300], [101, 200]), {}, 'a narrower range');
  assert.deepEqual(pair([101, 200], [150, 300]), {}, 'an overlapping range');
  assert.deepEqual(pair([1, 100], [1, 100], fileText('game/ui.js', 1, 100).replace('line 50 ', 'line 50 changed ')), {},
    'the file changed in a way the transcript does not show');
  // A later read cut at the per-result cap shows only its first 16k characters.
  assert.deepEqual(pair([101, 200], [1, 1000]), {2: 4});
  assert.deepEqual(pair([501, 600], [1, 1000]), {}, 'lines past the cap are not shown again');
  // The read tool's closing line after a partial read is not content.
  const partial = (start, end) => `${fileText('game/ui.js', start, end)}\n\n[${2000 - end} more lines in file. Use offset=${end + 1} to continue.]`;
  const history = tool(owner(), 'read', {path: 'game/ui.js', offset: 1, limit: 100}, partial(1, 100));
  tool(history, 'read', {path: 'game/ui.js', offset: 1, limit: 200}, partial(1, 200));
  tool(history, 'read', {path: 'game/ui.js', offset: 1}, `${fileText('game/ui.js', 1, 200)}\n\n[Showing lines 1-200 of 2000 (50.0KB limit). Use offset=201 to continue.]`);
  assert.deepEqual(superseded(history), {2: 4, 4: 6});
  // The same text read from other lines (a repeated block) is not the same read.
  const block = fileText('game/ui.js', 1, 100);
  for (const [name, first, second] of [
    ['read', {path: 'game/ui.js', offset: 501, limit: 100}, {path: 'game/ui.js', offset: 1, limit: 100}],
    ['exec', {command: 'sed -n 501,600p game/ui.js'}, {command: 'sed -n 1,100p game/ui.js'}],
  ]) assert.deepEqual(superseded(tool(tool(owner(), name, first, block), name, second, block)), {}, name);
  // A result marked as an error never supersedes, whatever it printed.
  const failed = readLines(owner(), 'game/ui.js', 1, 100);
  tool(failed, 'read', {path: 'game/ui.js', offset: 1, limit: 100}, fileText('game/ui.js', 1, 100), {isError: true});
  assert.deepEqual(superseded(failed), {});
});

test('plain sed -n and cat reads in exec name the same file as the read tool', () => {
  const text = fileText('Playground/game/ui.js', 1, 100);
  // [tool, arguments, reads the whole file]
  const same = [
    ['read', {path: '/workspace/Playground/game/ui.js', offset: 1, limit: 100}, false],
    ['read', {path: 'Playground/x/../game/./ui.js', offset: 1, limit: 100}, false],
    ['exec', {command: 'cd /workspace/Playground/game && sed -n 1,100p ui.js'}, false],
    ['exec', {command: "cd /workspace/Playground/game; sed -n '1,100p' ui.js"}, false],
    ['exec', {command: 'sed -n "1,100p" Playground/game/ui.js'}, false],
    ['exec', {command: 'sed -n 1,100p ui.js', workdir: 'Playground/game'}, false],
    ['exec', {command: "cd game && sed -n '1,$p' ui.js", workdir: '/workspace/Playground'}, true],
    ['exec', {command: 'cat Playground/game/ui.js'}, true],
    ['exec', {command: "cd '/workspace/Playground/game' && cat ui.js"}, true],
  ];
  for (const [name, args, wholeFile] of same) {
    const history = tool(readLines(owner(), 'Playground/game/ui.js', 1, 100), name, args, text);
    assert.deepEqual(superseded(history), {2: 4}, JSON.stringify(args));
    // The other way round, lines 1-100 supersede it only when it read no more.
    const reverse = readLines(tool(owner(), name, args, text), 'Playground/game/ui.js', 1, 100);
    assert.deepEqual(superseded(reverse), wholeFile ? {} : {2: 4}, `${JSON.stringify(args)}, then the read tool`);
  }
  // A numbered listing piped to sed -n is a read in its own format.
  const numbered = (start, end) => fileText('Playground/game/ui.js', start, end).split('\n').map((line, i) => `${start + i}:${line}`).join('\n');
  const listing = tool(owner(), 'exec', {command: `cd /workspace/Playground/game && grep -n "" ui.js | sed -n '1,100p'`}, numbered(1, 100));
  tool(listing, 'exec', {command: `cd /workspace/Playground/game && nl -ba ui.js | sed -n '1,300p'`}, numbered(1, 300));
  tool(listing, 'exec', {command: `cat -n Playground/game/ui.js | sed -n 1,300p`}, numbered(1, 300));
  assert.deepEqual(superseded(listing), {2: 4, 4: 6});
  assert.deepEqual(superseded(tool(readLines(owner(), 'Playground/game/ui.js', 1, 100), 'exec',
    {command: `grep -n '' Playground/game/ui.js | sed -n '1,100p'`}, numbered(1, 100))), {}, 'numbered lines do not show the plain text');
  const other = [
    {command: 'cd /workspace/Playground/game && sed -n 1,100p ui.js; echo done'},
    {command: 'sed -n 1,100p Playground/game/ui.js | head -100'},
    {command: 'cat Playground/game/ui.js Playground/game/b.js'},
    {command: 'cat Playground/game/*.js'},
    {command: "sed -n '1,100p;200,300p' Playground/game/ui.js"},
    {command: 'sed -n 1,100p $FILE'},
    {command: 'sed -n 1,$p Playground/game/ui.js'},
    {command: 'cd /tmp && sed -n 1,100p ui.js'},
    {command: 'cd ~ && sed -n 1,100p ui.js'},
    {command: 'cd /workspace/Playground; cd game && sed -n 1,100p ui.js'},
    {command: 'sed -n 1,100p ../../ui.js'},
    {command: 'sed -n 1,100p ui.js', workdir: '/tmp'},
    {command: 'sed -n 1,100p\nui.js'},
  ];
  for (const args of other) {
    assert.deepEqual(superseded(tool(tool(owner(), 'exec', args, text), 'exec', args, text)), {}, JSON.stringify(args));
  }
  for (const path of ['~/ui.js', '/etc/ui.js', '..\\ui.js', '']) {
    const args = {path, offset: 1, limit: 100};
    assert.deepEqual(superseded(tool(tool(owner(), 'read', args, text), 'read', args, text)), {}, path);
  }
});

test('the newest batch stays whole even when it reads one range twice', () => {
  const history = owner();
  for (const file of ['a.js', 'b.js', 'c.js', 'd.js', 'e.js', 'f.js', 'g.js', 'h.js', 'i.js']) readLines(history, `game/${file}`, 1, 100);
  const calls = [`call-${nextId}`, `call-${nextId + 1}`];
  history.push({role: 'assistant', content: calls.map((id) => ({type: 'toolCall', id, name: 'read', arguments: {path: 'game/ui.js', offset: 1, limit: 100}}))});
  for (const id of calls) history.push({role: 'toolResult', toolCallId: id, toolName: 'read', timestamp: 1000 + nextId++, content: [{type: 'text', text: fileText('game/ui.js', 1, 100)}]});
  assert.deepEqual(superseded(history), {20: 21});
  assert.deepEqual(whole(run(history).messages).slice(-2), [true, true]);
});

test('a reduction counts only what superseded a result by its batch, so earlier sends stay byte-stable', () => {
  // ui.js is read once among other reads and read again only after the reduction.
  const history = owner();
  for (const file of ['a.js', 'b.js', 'c.js', 'd.js', 'e.js', 'f.js', 'g.js']) readLines(history, `game/${file}`, 1, 100);
  readLines(history, 'game/ui.js', 1, 100);
  for (const file of ['h.js', 'i.js', 'j.js']) readLines(history, `game/${file}`, 1, 100);
  const sent = run(history).messages;
  assert.deepEqual(whole(sent), [false, false, false, false, false, false, true, true, true, true, true]);
  readLines(history, 'game/ui.js', 1, 100);
  assert.deepEqual(run(history).messages.slice(0, sent.length), sent, 'the earlier copy is now superseded, but the reduction already sent stays');
  // Over a long response of sliding re-reads, reductions stay as rare as before.
  const reading = owner();
  let previousSent = null, rewrites = 0;
  for (let i = 0; i < 60; i++) {
    readLines(reading, 'game/ui.js', 1 + (i % 9) * 100, 100 + (i % 9) * 100);
    const now = run(reading).messages.map((m) => (m.role === 'toolResult' ? textOf(m) : null));
    if (previousSent && previousSent.some((text, index) => text !== now[index])) rewrites++;
    previousSent = now;
  }
  assert.ok(rewrites >= 7 && rewrites <= 10, `rewrites ${rewrites}`);
  assert.deepEqual(run(structuredClone(reading)).messages, run(reading).messages, 'a fresh attempt sends the same bytes');
});

test('the superseded-first order never makes the guard trip sooner than the projection it extends', () => {
  // A long editing response: it re-reads ui.js in sliding windows, reads other
  // files once each, edits ui.js now and then, and writes large payloads until
  // the guard limit is reached.
  for (const context of [65536, 131072]) {
    const limit = Math.floor(context * 4 * 0.9);
    const history = owner();
    const attempt = {}, reference = {};
    let ordered = 0, tripped = false, referenceTripped = false, declined = false;
    for (let i = 0; i < 400 && !(tripped && declined); i++) {
      readLines(history, 'game/ui.js', 1 + (i % 7) * 120, 120 + (i % 7) * 120);
      readLines(history, `game/part-${i}.js`, 1, 40);
      if (i % 9 === 8) edited(history, 'game/ui.js');
      history.push(write(context / 40), result(200));
      const sent = realm(project(history, context, MAX, 4 * MAX, {}), history).messages;
      const base = before(history, context).messages;
      if (!isDeepStrictEqual(sent, base)) {
        ordered++;
        assert.ok(guardEstimate(sent) <= limit, `${context} round ${i}: ${guardEstimate(sent)} sent in the superseded-first order`);
      }
      tripped ||= guardEstimate(sent) > limit;
      referenceTripped ||= guardEstimate(base) > limit;
      assert.ok(!tripped || referenceTripped, `${context} round ${i}: the guard trips sooner`);
      // One attempt: once it declines the order, it is projected exactly as the
      // projection it extends projects it from then on.
      const streamed = realm(project(history, context, MAX, 4 * MAX, attempt), history).messages;
      const wasDeclined = declined;
      declined = attempt.liveOrderDeclined === true;
      if (wasDeclined) {
        reference.liveWideDeclined ??= attempt.liveWideDeclined;
        assert.deepEqual(streamed, before(history, context, reference).messages, `${context} round ${i}: back to the order`);
      } else if (!declined) assert.deepEqual(streamed, sent);
    }
    assert.ok(ordered > 10 && tripped && declined, JSON.stringify({context, ordered, tripped, declined}));
  }
});

test('once an attempt declines the superseded-first order it keeps declining it though its input shrinks', () => {
  const big = [{role: 'user', content: `owner ${'p'.repeat(232000)}`}];
  for (const file of ['a.js', 'b.js', 'c.js', 'd.js', 'e.js']) readLines(big, `game/${file}`, 1, 100);
  for (let i = 0; i < 7; i++) readLines(big, 'game/ui.js', 1, 100);
  const shrunk = [{role: 'user', content: 'owner'}, ...big.slice(1)];
  const attempt = {};
  assert.deepEqual(realm(project(big, 65536, MAX, 4 * MAX, attempt), big).messages, before(big).messages);
  assert.equal(attempt.liveOrderDeclined, true);
  assert.deepEqual(realm(project(shrunk, 65536, MAX, 4 * MAX, attempt), shrunk).messages, before(shrunk).messages);
  // A new attempt decides afresh.
  assert.notDeepEqual(run(shrunk).messages, before(shrunk).messages);
});

test('a transcript that ends with a final reply is measured exactly as before', () => {
  const history = owner();
  for (const file of ['a.js', 'b.js', 'c.js', 'd.js', 'e.js']) readLines(history, `game/${file}`, 1, 100);
  for (let i = 0; i < 30; i++) readLines(history, 'game/ui.js', 1 + (i % 3) * 100, 100 + (i % 3) * 100);
  assert.notDeepEqual(wide(history).messages, before(history, 131072).messages);
  history.push({role: 'assistant', content: [{type: 'text', text: 'Done: the cache is in place.'}]});
  for (const context of [65536, 131072]) assert.deepEqual(realm(project(history, context, MAX, 4 * MAX, {}), history).messages, before(history, context).messages);
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
  assert.match(mac, /\('openclaw-tool-result-projection\.json', 'tool-result-truncation-CbxVHy2D\.js'\),\r?\n(?:\s+\('[a-z0-9-]+\.json', '[A-Za-z0-9._-]+\.js'\),\r?\n)*\s+\('openclaw-compaction-budget\.json', 'selection-BEwSQKM-\.js'\),\r?\n\)/,
    'the stream-progress patch still composes on the last (selection) repair');
  assert.equal(manifest.version, '2026.6.33');
  assert.match(manifest.sourceSha256, /^[a-f0-9]{64}$/);
  assert.match(manifest.patchedSha256, /^[a-f0-9]{64}$/);
});
