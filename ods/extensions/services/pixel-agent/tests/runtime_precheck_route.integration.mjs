// OPENCLAW_PACKAGE_DIR must be an absolute path to the pinned runtime package.
// The original and candidate pre-prompt check run from hash-verified runtime
// bytes (the candidate edit stays in a temporary copy of that one module), with
// the runtime's own session manager, prompt projection and session-file
// tool-result truncation.
import test, {after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {isAbsolute, join} from 'node:path';
import {pathToFileURL} from 'node:url';

const packageDir = process.env.OPENCLAW_PACKAGE_DIR;
if (!packageDir || !isAbsolute(packageDir)) {
  throw new Error('Set OPENCLAW_PACKAGE_DIR to the absolute path of the pinned OpenClaw package.');
}
const MODULE = 'attempt.tool-run-context-yigSIkBW.js';
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-precheck-route.json', import.meta.url), 'utf8'));
assert.equal(JSON.parse(readFileSync(join(packageDir, 'package.json'), 'utf8')).version, manifest.version);
const sha256 = (source) => createHash('sha256').update(source).digest('hex');
function transform(source, reverse = false) {
  for (const pair of manifest.replacements) {
    const [from, to] = reverse ? [...pair].reverse() : pair;
    assert.equal(source.split(from).length, 2, 'each reviewed replacement must match exactly once');
    source = source.replace(from, () => to);
  }
  return source;
}
const installed = readFileSync(join(packageDir, 'dist', MODULE), 'utf8');
assert.ok([manifest.sourceSha256, manifest.patchedSha256].includes(sha256(installed)), `Unreviewed runtime module SHA-256: ${sha256(installed)}`);
const originalSource = sha256(installed) === manifest.sourceSha256 ? installed : transform(installed, true);
const candidateSource = transform(originalSource);
assert.equal(sha256(originalSource), manifest.sourceSha256);
assert.equal(sha256(candidateSource), manifest.patchedSha256);
assert.equal(transform(candidateSource, true), originalSource, 'the manifest must reverse byte for byte');

const dist = (name) => pathToFileURL(join(packageDir, 'dist', name)).href;
const scratch = mkdtempSync(join(tmpdir(), 'ods-precheck-route-'));
after(() => rmSync(scratch, {recursive: true, force: true}));
async function load(source, name) {
  // Same bytes, with sibling chunk specifiers pointed at the package.
  const file = join(scratch, name);
  writeFileSync(file, source.replace(/(from |import )"\.\/([^"]+)"/g, (_, keyword, chunk) => `${keyword}"${dist(chunk)}"`));
  const module = await import(pathToFileURL(file).href);
  return {decide: module.c, estimate: module.a};
}
const original = await load(originalSource, 'original.mjs');
const candidate = await load(candidateSource, 'candidate.mjs');
const {t: SessionManager} = await import(dist('session-manager-3lTZxT-y.js'));
const {u: truncateSessionToolResults, c: projectToolResults, r: projectionState} = await import(dist('tool-result-truncation-CbxVHy2D.js'));

// ODS 64k hosts: contextWindow 65536, reserveTokens 19661, toolResultMaxChars 16000.
const CONTEXT = 65536, RESERVE = 19661, MAX = 16000, BUDGET = CONTEXT - RESERVE;
const SYSTEM = 'S'.repeat(63705); // recorded Pixel system prompt size (tower1 round 071)
const PROMPT = 'FOLLOWUP CODE TASK: add the --minimum-total flag. '.repeat(38);
const RESULTS = [3645, 5622, 1195, 776, 2935, 4460, 494, 5822, 607, 4304, 596, 3653, 995, 8462, 3095, 1147];
const text = (chars, seed) => (seed + ' ').repeat(Math.ceil(chars / (seed.length + 1))).slice(0, chars);

function session(turns, writeChars) {
  const manager = SessionManager.inMemory(scratch);
  let clock = 1_790_000_000_000;
  manager.appendMessage({role: 'user', content: [{type: 'text', text: text(1600, 'Create the website')}], timestamp: clock++});
  for (let turn = 0; turn < turns; turn++) {
    const id = `call_${turn}`;
    manager.appendMessage({role: 'assistant', api: 'openai-completions', provider: 'fixture', model: 'test', stopReason: 'toolUse', timestamp: clock++,
      usage: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0},
      content: [{type: 'text', text: 'Next step.'}, {type: 'toolCall', id, name: turn % 3 ? 'exec' : 'write',
        arguments: turn % 3 ? {command: `python3 -m unittest -v tests_${turn}`} : {path: `site/page_${turn}.html`, content: text(writeChars, '<div class="card">')}}]});
    manager.appendMessage({role: 'toolResult', toolCallId: id, toolName: turn % 3 ? 'exec' : 'write', isError: false, timestamp: clock++,
      content: [{type: 'text', text: text(RESULTS[turn % RESULTS.length], `line ${turn}`)}]});
  }
  manager.appendMessage({role: 'assistant', api: 'openai-completions', provider: 'fixture', model: 'test', stopReason: 'stop', timestamp: clock++,
    usage: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0}, content: [{type: 'text', text: text(900, 'All checks passed')}]});
  return manager;
}
// The attempt projects history before the check (live aggregate 4x the cap).
const history = (manager) => projectToolResults(manager.buildSessionContext().messages, CONTEXT, MAX, 4 * MAX, projectionState()).messages;
const decide = (runtime, messages) => runtime.decide({messages, systemPrompt: SYSTEM, prompt: PROMPT, contextTokenBudget: CONTEXT,
  reserveTokens: RESERVE, toolResultMaxChars: MAX});
function afterSessionTrim(turns, writeChars) {
  // truncate_tool_results_only: the runtime's session-file trim, then the retry's check.
  const manager = session(turns, writeChars);
  const outcome = truncateSessionToolResults({sessionManager: manager, contextWindowTokens: CONTEXT, maxCharsOverride: MAX});
  return {outcome, estimate: original.estimate({messages: history(manager), systemPrompt: SYSTEM, prompt: PROMPT})};
}

const cases = [];
for (const writeChars of [300, 1800, 4200]) {
  for (let turns = 8; turns <= 44; turns += 2) {
    const messages = history(session(turns, writeChars));
    const pinned = decide(original, messages), repaired = decide(candidate, messages);
    cases.push({turns, writeChars, pinned, repaired, trim: pinned.overflowTokens > 0 ? afterSessionTrim(turns, writeChars) : null});
  }
}

test('the repaired route changes only the recovery chosen for an overflow, never whether the check fires', () => {
  const count = (pick) => Object.entries(Object.groupBy(cases, pick)).map(([route, items]) => `${route}=${items.length}`).join(' ');
  console.log(`pinned: ${count(({pinned}) => pinned.route)}; repaired: ${count(({repaired}) => repaired.route)}`);
  for (const {pinned, repaired} of cases) {
    assert.equal(repaired.estimatedPromptTokens, pinned.estimatedPromptTokens);
    assert.equal(repaired.overflowTokens, pinned.overflowTokens);
    assert.equal(repaired.toolResultReducibleChars, pinned.toolResultReducibleChars);
    assert.equal(repaired.route === 'fits', pinned.route === 'fits');
    if (pinned.route === 'fits' || pinned.route === 'compact_only') assert.equal(repaired.route, pinned.route);
  }
  assert.ok(cases.some(({pinned}) => pinned.route === 'fits') && cases.some(({pinned}) => pinned.overflowTokens > 0), 'the sweep crosses the budget');
});

test('whenever the repaired check chooses the trim, the runtime session trim alone fits the budget', () => {
  const trims = cases.filter(({repaired}) => repaired.route === 'truncate_tool_results_only');
  assert.ok(trims.length >= 10, `sweep exercises the trim route (${trims.length})`);
  for (const {turns, writeChars, trim} of trims) {
    assert.equal(trim.outcome.truncated, true, `${turns}/${writeChars}: the session trim rewrote tool results`);
    assert.ok(trim.estimate <= BUDGET, `${turns}/${writeChars}: retry estimate ${trim.estimate} <= ${BUDGET}`);
  }
});

test('overflows the trim can fix no longer summarize the history', () => {
  const recovered = cases.filter(({pinned, repaired}) => pinned.route === 'compact_then_truncate' && repaired.route === 'truncate_tool_results_only');
  assert.ok(recovered.length >= 5, `pinned compacted ${recovered.length} overflows that the trim alone fixes`);
  for (const {pinned, trim} of recovered) {
    assert.ok(trim.estimate <= BUDGET, 'the avoided compaction was not needed');
    assert.ok(pinned.toolResultReducibleChars < Math.max(pinned.overflowTokens * 4 + 2048, Math.ceil(pinned.overflowTokens * 6)));
  }
  // A clear fit after the trim (beyond the route buffer) is always routed to it.
  for (const {repaired, trim} of cases.filter(({trim}) => trim && trim.estimate <= BUDGET - 1.2 * 512 - 64)) {
    assert.equal(repaired.route, 'truncate_tool_results_only');
  }
});

test('overflows the trim cannot fix still compact', () => {
  const stillCompact = cases.filter(({trim, repaired}) => trim && trim.estimate > BUDGET && repaired.route !== 'fits');
  assert.ok(stillCompact.length >= 3, `sweep includes overflows beyond the trim (${stillCompact.length})`);
  for (const {repaired} of stillCompact) assert.equal(repaired.route, 'compact_then_truncate');
});
