import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// The installed replacement runs inside OpenClaw's pre-prompt check with the
// runtime's own constants; runtime_precheck_route.integration.mjs covers the
// composed module. Here the reviewed snippets are evaluated with those constants.
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-precheck-route.json', import.meta.url)));
assert.equal(manifest.replacements.length, 1, 'one reviewed replacement');
const [before, after] = manifest.replacements[0];
assert.match(before, /overflowTokens \* ESTIMATED_CHARS_PER_TOKEN/);
const RUNTIME = {ESTIMATED_CHARS_PER_TOKEN: 4, TOOL_RESULT_CHARS_PER_TOKEN: 2, TRUNCATION_ROUTE_BUFFER_TOKENS: 512, SAFETY_MARGIN: 1.2};
const threshold = (snippet) => (overflowTokens) =>
  vm.runInNewContext(`${snippet}\ntruncateOnlyThresholdChars`, {...RUNTIME, overflowTokens, Math});
const pinned = threshold(before), repaired = threshold(after);
// shouldPreemptivelyCompactBeforePrompt's route decision around the snippet.
const route = (limit, event) => event.overflowTokens <= 0 ? 'fits'
  : event.toolResultReducibleChars <= 0 ? 'compact_only'
    : event.toolResultReducibleChars >= limit(event.overflowTokens) ? 'truncate_tool_results_only' : 'compact_then_truncate';
// Tool-result text is what the truncation removes; the estimate prices it at
// SAFETY_MARGIN / TOOL_RESULT_CHARS_PER_TOKEN tokens per char.
const tokensRemoved = (chars) => chars * RUNTIME.SAFETY_MARGIN / RUNTIME.TOOL_RESULT_CHARS_PER_TOKEN;

const recorded = JSON.parse(readFileSync(new URL('./fixtures/precheck-route-recorded-events.json', import.meta.url))).events;

test('the pinned route reproduces every recorded owner-turn recovery', () => {
  assert.equal(recorded.length, 44);
  assert.equal(recorded.filter((event) => event.heldOut).length, 10);
  for (const event of recorded) {
    const expected = event.recordedRecovery === 'truncation' ? 'truncate_tool_results_only' : 'compact_then_truncate';
    assert.equal(route(pinned, event), expected, `${event.session} attempt ${event.attempt}`);
  }
});

test('recorded compactions that trimming old tool output alone would have fixed now take the trim route', () => {
  const compactions = recorded.filter((event) => event.recordedRecovery === 'compaction');
  assert.equal(compactions.length, 38);
  const trimmed = compactions.filter((event) => route(repaired, event) === 'truncate_tool_results_only');
  assert.equal(trimmed.length, 24, '24 of 38 recorded summary compactions become a transcript trim');
  // The 4 later journeys were not used to choose the threshold.
  const heldOut = compactions.filter((event) => event.heldOut);
  assert.equal(heldOut.length, 10);
  assert.equal(heldOut.filter((event) => route(repaired, event) === 'truncate_tool_results_only').length, 5);
  for (const event of recorded.filter((e) => route(repaired, e) === 'truncate_tool_results_only')) {
    assert.ok(event.estimateAfterSessionTruncation <= event.promptBudgetBeforeReserve,
      `${event.session} attempt ${event.attempt}: the runtime's own trim fits (${event.estimateAfterSessionTruncation})`);
  }
  // Where trimming cannot fit the budget, compaction remains the recovery.
  for (const event of compactions.filter((e) => e.estimateAfterSessionTruncation > e.promptBudgetBeforeReserve)) {
    assert.equal(route(repaired, event), 'compact_then_truncate', `${event.session} attempt ${event.attempt}`);
  }
  assert.ok(recorded.filter((e) => e.recordedRecovery === 'truncation').every((e) => route(repaired, e) === 'truncate_tool_results_only'),
    'recorded trims stay trims');
});

test('the repaired threshold is the smallest tool-result reduction that covers the overflow and route buffer', () => {
  for (const overflow of [1, 17, 511, 512, 2000, 9659, 11571, 21515, 100000]) {
    const limit = repaired(overflow);
    assert.ok(tokensRemoved(limit) >= overflow + RUNTIME.TRUNCATION_ROUTE_BUFFER_TOKENS, `enough at ${overflow}`);
    assert.ok(tokensRemoved(limit - 1) < overflow + RUNTIME.TRUNCATION_ROUTE_BUFFER_TOKENS, `minimal at ${overflow}`);
    // The pinned test prices those chars at the prose rate with a 1.5x factor.
    assert.ok(pinned(overflow) > limit, `pinned asks for more at ${overflow}`);
  }
  assert.equal(pinned(10000) / repaired(10000) > 2.8, true, 'pinned demanded about 3x the tool-result text it needs');
});

test('routes without tool-result text or without overflow are unchanged', () => {
  for (const overflowTokens of [0, 1, 5000]) {
    for (const toolResultReducibleChars of [0, 100, 40000]) {
      const event = {overflowTokens, toolResultReducibleChars};
      if (overflowTokens === 0 || toolResultReducibleChars === 0) assert.equal(route(repaired, event), route(pinned, event));
    }
  }
});

test('repair is selected by Linux/WSL installation, foreign restore and native macOS composition', () => {
  const linux = readFileSync(new URL('../../../../installers/lib/pixel-host-install.sh', import.meta.url), 'utf8');
  const mac = readFileSync(new URL('../../../../installers/macos/lib/pixel-runtime-bundle.py', import.meta.url), 'utf8');
  const helper = readFileSync(new URL('../host/openclaw_tool_recovery.py', import.meta.url), 'utf8');
  assert.match(linux, /--openclaw-bin "\$openclaw_bin" --precheck-route/);
  assert.match(linux, /ods-runtime-patches\/precheck-route"/);
  assert.match(linux, /-f "\$plugin_root\/host\/openclaw-precheck-route\.json"/);
  assert.match(linux.split('--restore-foreign')[1].split('>>')[0], /\bprecheck-route\b/);
  assert.match(helper, /PRECHECK_ROUTE_MODULE = "attempt\.tool-run-context-yigSIkBW\.js"/);
  assert.match(mac, /\('openclaw-precheck-route\.json', 'attempt\.tool-run-context-yigSIkBW\.js'\),/);
  assert.ok(mac.indexOf('openclaw-precheck-route.json') < mac.indexOf("('openclaw-compaction-budget.json'"),
    'the stream-progress patch still composes on the last (selection) repair');
});

test('the manifest changes only the route threshold', () => {
  assert.equal(before.split('\n').filter((line) => !line.trim().startsWith('//')).length, 2);
  assert.equal(after.split('\n').filter((line) => !line.trim().startsWith('//')).length, 2);
  assert.match(after, /const overflowChars = /);
  assert.match(after, /const truncateOnlyThresholdChars = overflowChars;/);
  assert.match(after, /TOOL_RESULT_CHARS_PER_TOKEN \/ SAFETY_MARGIN/);
  assert.match(manifest.sourceSha256, /^[0-9a-f]{64}$/);
  assert.match(manifest.patchedSha256, /^[0-9a-f]{64}$/);
  assert.notEqual(manifest.sourceSha256, manifest.patchedSha256);
  assert.equal(manifest.version, '2026.6.33');
});
