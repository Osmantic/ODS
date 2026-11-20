// Source-bound proof for the owner-input seam carried by the reviewed runtime
// recipes (host/openclaw-yield-usage.json, host/openclaw-compaction-budget.json).
// The seam code is read from the exact manifest replacements, never from a copy:
// the host cell and attempt classifier are extracted from the recipe text and
// evaluated inertly, then drive the actual guard and preview tool with a fake
// publication host. No OpenClaw runtime is imported or started.
//
// Optional complete-module proof: set OPENCLAW_EMBEDDED_AGENT_MODULE and
// OPENCLAW_SELECTION_MODULE to the exact reviewed OpenClaw 2026.6.33 modules
// (vanilla, a recorded predecessor or the current patched bytes; the same
// variables test_openclaw_tool_recovery.py reads). Those modules are read as text.
//
// Trust boundary: the projection is a host-only context path. Model output,
// prompt text, messages and tool params cannot reach it; it is not
// cryptographic authentication against other in-process host or plugin code.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, randomBytes} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createWorkspacePreviewTool, testing} from '../plugin/workspace-preview.mjs';

const recipe = name => JSON.parse(readFileSync(new URL(`../host/openclaw-${name}.json`, import.meta.url), 'utf8'));
const sha = text => createHash('sha256').update(text, 'utf8').digest('hex');
const count = (text, needle) => text.split(needle).length - 1;
const lineOf = (text, needle) => text.slice(0, text.indexOf(needle)).split('\n').length;
const REGION_START = '//#region ODS candidate: host-only owner-input provenance';

// The seam is composed after the exact previous public recipe: the previous
// replacement list is the unchanged prefix and stays the recorded predecessor.
function seamOf(manifest, length) {
  const prefixes = Object.entries(manifest.previousReplacements ?? {}).filter(([, pairs]) =>
    pairs.length < manifest.replacements.length &&
    pairs.every((pair, index) => JSON.stringify(pair) === JSON.stringify(manifest.replacements[index])));
  const [[, previous]] = prefixes.sort((a, b) => b[1].length - a[1].length);
  const seam = manifest.replacements.slice(previous.length);
  assert.equal(seam.length, length);
  return {previous, seam};
}
const YIELD = recipe('yield-usage');
const BUDGET = recipe('compaction-budget');
const EMBEDDED_SEAM = seamOf(YIELD, 8).seam;
const SELECTION_SEAM = seamOf(BUDGET, 3).seam;

test('both recipes retain the previous public recipe as exact prefix and predecessor', () => {
  for (const [manifest, previousPatched, previousCount] of [
    [YIELD, 'd00804f960d617b6b3e2ae6b1691f3aa9866029c8da8772f236a35a6033b37f8', 9],
    [BUDGET, '2382052dd075b2eae2d6fee59d5485bc5b63a988286a1595cb9e122c0fd87d12', 17]]) {
    const previous = manifest.previousReplacements[previousPatched];
    assert.equal(previous.length, previousCount);
    assert.deepEqual(manifest.replacements.slice(0, previous.length), previous);
    assert.notEqual(manifest.patchedSha256, previousPatched);
    // Seam context lines are anchors only: each replacement keeps every old line.
    for (const [before, after] of manifest.replacements.slice(previous.length)) {
      const kept = after.split('\n');
      let at = 0;
      for (const line of before.split('\n')) {
        const found = kept.indexOf(line, at);
        // The one edited line is the hook-context literal gaining a trailing comma.
        if (found === -1) { assert.equal(line, '\t\t\t\t\t})'); assert.ok(kept.includes('\t\t\t\t\t}),')); continue; }
        at = found + 1;
      }
    }
  }
});

// ---- inert extraction of the recipe's cell and classifier -------------------
function extracted() {
  const [region] = EMBEDDED_SEAM.filter(([, after]) => after.includes(REGION_START)).map(([, after]) => after);
  assert.equal(count(region, REGION_START), 1);
  const body = region.slice(region.indexOf(REGION_START), region.indexOf('//#endregion'));
  // Self-contained: only randomBytes, which the target module already imports.
  assert.doesNotMatch(body, /\b(?:import|require|process|globalThis|fetch|params\.prompt)\b/);
  return new Function('randomBytes', `${body}\nreturn {createOdsOwnerInputCell, classifyOdsOwnerInputAttempt};`)(randomBytes);
}

test('each outer invocation mints one distinct host input; only host code mutates it', () => {
  const {createOdsOwnerInputCell} = extracted();
  const a = createOdsOwnerInputCell('run-1'), b = createOdsOwnerInputCell('run-1');
  const pa = a.project({attempt: 1, kind: 'run_input', retryReasons: Object.freeze([]), sessionId: 's', sessionKey: 'k'});
  const pb = b.project({attempt: 1, kind: 'run_input', retryReasons: Object.freeze([]), sessionId: 's', sessionKey: 'k'});
  assert.match(pa.inputId, /^[0-9a-f]{32}$/);
  assert.notEqual(pa.inputId, pb.inputId, 'a reused run ID still gets a new input');
  assert.ok(Object.isFrozen(pa) && Object.isFrozen(a));
  assert.deepEqual(Object.keys(pa).sort(), ['attempt', 'inputId', 'kind', 'retryReasons', 'runId', 'schema', 'sessionId', 'sessionKey', 'status']);
  assert.equal(pa.schema, 'ods.owner-input.v1');
  assert.deepEqual(pa.status(), {inputId: pa.inputId, queuedInputs: 0, cancelled: false, terminated: false});
  assert.ok(Object.isFrozen(pa.status()));
  a.noteQueuedInput(); a.noteQueuedInput(); a.noteCancelled(); a.terminate();
  assert.deepEqual(pa.status(), {inputId: pa.inputId, queuedInputs: 2, cancelled: true, terminated: true});
  assert.deepEqual(pb.status(), {inputId: pb.inputId, queuedInputs: 0, cancelled: false, terminated: false});
  assert.equal(typeof pa.noteQueuedInput, 'undefined', 'the projection exposes no mutator');
  assert.throws(() => { 'use strict'; pa.kind = 'run_input'; });
});

test('attempts are classified from host loop state only', () => {
  const {classifyOdsOwnerInputAttempt: classify} = extracted();
  const base = {override: null, overrideReason: null, reasoningOnly: null, emptyResponse: null, compaction: null};
  const shape = value => ({attempt: value.attempt, kind: value.kind, retryReasons: [...value.retryReasons]});
  assert.deepEqual(shape(classify({...base, ordinal: 1})), {attempt: 1, kind: 'run_input', retryReasons: []});
  assert.deepEqual(shape(classify({...base, ordinal: 2})), {attempt: 2, kind: 'host_retry', retryReasons: ['same_input_repeat']});
  assert.deepEqual(shape(classify({...base, ordinal: 1, override: 'continue', overrideReason: 'midturn_continuation'})).retryReasons, ['midturn_continuation']);
  assert.deepEqual(shape(classify({...base, ordinal: 3, override: 'x', overrideReason: 'finalize_revision'})).retryReasons, ['finalize_revision']);
  assert.deepEqual(shape(classify({...base, ordinal: 3, override: 'x'})).retryReasons, ['host_override']);
  assert.deepEqual(shape(classify({...base, ordinal: 2, compaction: 'continue', reasoningOnly: 'r', emptyResponse: ' e '})).retryReasons,
    ['reasoning_only', 'empty_response', 'compaction_continuation']);
  assert.equal(classify({...base, ordinal: 2, compaction: '   '}).retryReasons[0], 'same_input_repeat', 'blank additions are not submitted');
  assert.ok(Object.isFrozen(classify({...base, ordinal: 1}).retryReasons));
});

// ---- structural proof on the recipe replacements ----------------------------
const seamText = seam => seam.map(([, after]) => after).join('\n');
test('embedded-agent recipe: cell per invocation, classified before the override is consumed, terminated in finally', () => {
  const text = seamText(EMBEDDED_SEAM);
  assert.equal(count(text, 'const odsOwnerInputCell = createOdsOwnerInputCell(params.runId);'), 1);
  assert.equal(count(text, '\t\t\t\tforgetPromptBuildDrainCacheForRun(params.runId);\n\t\t\t\todsOwnerInputCell.terminate();\n'), 1);
  const [dispatch] = EMBEDDED_SEAM.map(([, after]) => after).filter(after => after.includes('classifyOdsOwnerInputAttempt({'));
  assert.ok(dispatch.indexOf('override: nextAttemptPromptOverride,') < dispatch.indexOf('nextAttemptPromptOverride = null;'));
  assert.ok(dispatch.indexOf('nextAttemptPromptOverride = null;') < dispatch.indexOf('nextAttemptPromptOverrideReason = null;'));
  assert.ok(dispatch.indexOf('const basePrompt = nextAttemptPromptOverride ??') > 0);
  // Each seam that assigns a non-null override names its host reason next.
  const lines = text.split('\n');
  const assignments = lines.map((line, i) => [line, i]).filter(([line]) => /^\s*nextAttemptPromptOverride = (?!null;)/.test(line));
  assert.equal(assignments.length, 3);
  for (const [line, i] of assignments) assert.match(lines[i + 1], /^\s*nextAttemptPromptOverrideReason = "(?:midturn_continuation|finalize_revision)";$/, line);
  assert.equal(count(text, 'odsOwnerInput: {\n\t\t\t\t\t\t\tcell: odsOwnerInputCell,\n\t\t\t\t\t\t\tattempt: odsOwnerInputAttempt\n\t\t\t\t\t\t},'), 1);
});

test('selection recipe: projection last in the prompt-build context; queue and external cancel noted first', () => {
  const text = seamText(SELECTION_SEAM);
  assert.match(text, /\t\t\t\t\t\tchannelContext: params\.channelContext\n\t\t\t\t\t\}\),\n\t\t\t\t\townerInputProvenance: params\.odsOwnerInput\?\.cell && params\.odsOwnerInput\.attempt \? params\.odsOwnerInput\.cell\.project\(\{\n\t+\.\.\.params\.odsOwnerInput\.attempt,\n\t+sessionId: params\.sessionId,\n\t+sessionKey: params\.sessionKey\n\t+\}\) : void 0\n\t\t\t\t\};/);
  assert.match(text, /queueMessage: async \(text, options\) => \{\n\t+params\.odsOwnerInput\?\.cell\?\.noteQueuedInput\(\);\n\t+if \(options\?\.steeringMode\)/);
  assert.match(text, /const abortActiveRunExternally = \(reason\) => \{\n\t+params\.odsOwnerInput\?\.cell\?\.noteCancelled\(\);\n\t+externalAbort = true;/);
  assert.equal(count(text, 'ownerInputProvenance'), 1);
});

// ---- optional: complete reviewed modules supplied by the operator ------------
function completeModule(variable, manifest) {
  const path = process.env[variable];
  if (!path) return undefined;
  let text = readFileSync(path, 'utf8');
  const predecessor = manifest.previousReplacements?.[sha(text)];
  if (predecessor) for (const [before, after] of [...predecessor].reverse()) {
    assert.equal(count(text, after), 1); text = text.replace(after, before);
  }
  if (sha(text) === manifest.sourceSha256) for (const [before, after] of manifest.replacements) {
    assert.equal(count(text, before), 1); text = text.replace(before, after);
  }
  assert.equal(sha(text), manifest.patchedSha256);
  return text;
}
const EMBEDDED = completeModule('OPENCLAW_EMBEDDED_AGENT_MODULE', YIELD);
const SELECTION = completeModule('OPENCLAW_SELECTION_MODULE', BUDGET);

test('complete embedded-agent: every override assignment names a reason; finally terminates the cell',
  {skip: EMBEDDED ? false : 'requires OPENCLAW_EMBEDDED_AGENT_MODULE'}, () => {
    assert.match(EMBEDDED, /^import \{ randomBytes \} from "node:crypto";$/m);
    const cellLine = lineOf(EMBEDDED, 'const odsOwnerInputCell = createOdsOwnerInputCell(params.runId);');
    const finallyTerminate = '\t\t\t\tforgetPromptBuildDrainCacheForRun(params.runId);\n\t\t\t\todsOwnerInputCell.terminate();\n';
    assert.equal(count(EMBEDDED, finallyTerminate), 1);
    assert.equal(count(EMBEDDED, 'odsOwnerInputCell.terminate()'), 1);
    const preceding = EMBEDDED.split('\n').slice(cellLine, lineOf(EMBEDDED, finallyTerminate));
    assert.ok(preceding.some(line => line === '\t\t\t} finally {'), 'terminate sits in the run-loop finally');
    assert.ok(cellLine < lineOf(EMBEDDED, 'const rawAttempt = await runEmbeddedAttemptWithBackend({'));
    const lines = EMBEDDED.split('\n');
    const assignments = lines.map((line, i) => [line, i]).filter(([line]) => /^\s*nextAttemptPromptOverride = (?!null;)/.test(line));
    assert.equal(assignments.length, 3, 'no unlabelled host override exists in the module');
    for (const [line, i] of assignments) assert.match(lines[i + 1], /^\s*nextAttemptPromptOverrideReason = "(?:midturn_continuation|finalize_revision)";$/, line);
  });

test('complete selection: projection in the prompt-build hookCtx; every owner steer goes through queueMessage',
  {skip: SELECTION ? false : 'requires OPENCLAW_SELECTION_MODULE'}, () => {
    const ctxStart = SELECTION.indexOf('\t\t\t\tconst hookCtx = {\n\t\t\t\t\trunId: params.runId,\n\t\t\t\t\ttrace: freezeDiagnosticTraceContext(diagnosticTrace),');
    assert.notEqual(ctxStart, -1);
    const ctxEnd = SELECTION.indexOf('\n\t\t\t\t};', ctxStart);
    const literal = SELECTION.slice(ctxStart, ctxEnd);
    assert.ok(literal.lastIndexOf('...buildAgentHookContextIdentityFields(') < literal.indexOf('ownerInputProvenance:'), 'no spread can override it');
    assert.match(SELECTION.slice(ctxEnd, ctxEnd + 600), /resolvePromptBuildHookResult\(\{[\s\S]*?\n\t+hookCtx,\n/);
    assert.equal(count(SELECTION, 'ownerInputProvenance'), 1);
    const steerCalls = [...SELECTION.matchAll(/activeSession\.steer\(/g)].map(match => lineOf(SELECTION, SELECTION.slice(match.index, match.index + 40)));
    assert.equal(steerCalls.length, 2);
    const helper = lineOf(SELECTION, 'async function steerAndWaitForTranscriptCommit(');
    const optional = lineOf(SELECTION, 'async function steerActiveSessionWithOptionalDeliveryWait(');
    assert.ok(steerCalls.every(line => line > helper && line < optional + 8));
    assert.equal(count(SELECTION, 'steerActiveSessionWithOptionalDeliveryWait('), 2, 'definition plus queueMessage');
    assert.equal(count(SELECTION, '.agent.steer('), 1, 'only the host-authored sessions_yield interrupt');
    assert.ok(lineOf(SELECTION, 'const hookResult = isRawModelRun ? void 0 : await resolvePromptBuildHookResult({') <
      lineOf(SELECTION, 'const leased = leasePendingAgentSteeringItems({'));
  });

// ---- the extracted runtime functions drive the actual guard -----------------
const DIR = 'demo-site';
const OPEN = `Build and publish a website in existing ${DIR}. Edit ${DIR}/index.html. Publish ${DIR} with the existing workspace preview tool.`;
// Host-authored retry texts. Their exact wording is irrelevant to the guard:
// only the host projection, never prompt words, retains the owner request.
const CONTINUATION = 'Continue from the current transcript.';
const COMPACTION = 'The conversation was compacted. Continue the task.';
const FINALIZE_PREFIX = 'Before finishing, revise the answer.';

function simulatedRun(runtime) {
  const {createOdsOwnerInputCell, classifyOdsOwnerInputAttempt} = runtime;
  const files = new Map([[`${DIR}/index.html`, '<!doctype html><h1>Changed</h1>']]);
  const requests = [];
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', sessionKey: `agent:pixel:openai-user:ods-${'b'.repeat(64)}`};
  let guard;
  const tool = createWorkspacePreviewTool({
    request: async request => {
      requests.push(request);
      const sha256 = createHash('sha256').update(files.get(`${DIR}/index.html`)).digest('hex');
      return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: DIR,
        siteId: `site-${sha256.slice(0, 24)}`, port: 9437, url: `http://site-${sha256.slice(0, 24)}.localhost:9437/site-${sha256.slice(0, 24)}/`,
        files: 1, bytes: 10, sha256, entryFile: 'index.html', entrySha256: sha256, httpStatus: 200, readbackVerified: true,
        executable: false, overwritten: false, boundary: testing.BOUNDARY};
    },
    publicationCondition: (id, params) => guard.previewPublicationCondition(id, params),
  });
  guard = createToolLoopGuard({abortRunAndDrain: async () => ({aborted: true, drained: true, forceCleared: false})});
  let cell, ordinal, override, overrideReason;
  const invocation = () => { cell = createOdsOwnerInputCell(context.runId); ordinal = 0; override = null; overrideReason = null; };
  const attempt = ({prompt = OPEN, compaction = null, provenance = true} = {}) => {
    const classified = classifyOdsOwnerInputAttempt({ordinal: ordinal += 1, override, overrideReason,
      reasoningOnly: null, emptyResponse: null, compaction});
    const base = override ?? prompt;
    override = null; overrideReason = null;
    const submitted = compaction ? `${base}\n\n${compaction}` : base;
    const hookCtx = {...context, trigger: 'user',
      ownerInputProvenance: provenance ? cell.project({...classified, sessionId: context.sessionId, sessionKey: context.sessionKey}) : undefined};
    guard.observeRun(hookCtx, 'pixel', {prompt: submitted, messages: []});
    return classified;
  };
  const admit = id => {
    const params = {relativeDirectory: DIR};
    const prepared = guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params, toolCallId: id},
      {...context, toolName: 'pixel_ods_workspace_preview', toolCallId: id});
    return {prepared, execute: () => tool.execute(id, prepared?.params ?? params)};
  };
  const write = () => {
    const id = 'write-entry', params = {path: `${DIR}/index.html`, content: files.get(`${DIR}/index.html`)};
    const ctx = {...context, toolName: 'write', toolCallId: id};
    const result = {content: [{type: 'text', text: 'written'}], details: {status: 'completed'}};
    assert.notEqual(guard.beforeToolCall({toolName: 'write', params, toolCallId: id}, ctx)?.block, true);
    guard.afterToolCall({toolName: 'write', params, result, toolCallId: id}, ctx);
    guard.toolResultPersist({toolName: 'write', toolCallId: id, message: {role: 'toolResult', toolName: 'write', toolCallId: id, ...result}}, ctx);
  };
  return {requests, invocation, attempt, admit, write, cell: () => cell,
    continueFromTranscript: () => { override = CONTINUATION; overrideReason = 'midturn_continuation'; },
    finalizeRevision: reason => { override = `${FINALIZE_PREFIX}\n\n${reason}`; overrideReason = 'finalize_revision'; }};
}

test('runtime retry shapes keep the original owner request through the actual guard', async () => {
  const run = simulatedRun(extracted());
  run.invocation();
  assert.equal(run.attempt().kind, 'run_input');
  run.write();
  assert.equal(run.attempt({compaction: COMPACTION}).kind, 'host_retry');
  run.continueFromTranscript();
  assert.deepEqual([...run.attempt().retryReasons], ['midturn_continuation']);
  run.finalizeRevision('Do not publish anything. Revise the answer.');
  assert.deepEqual([...run.attempt().retryReasons], ['finalize_revision']);
  const call = run.admit('after-retries');
  assert.equal(call.prepared?.block, undefined, call.prepared?.blockReason);
  assert.equal((await call.execute()).isError, undefined);
  assert.equal(run.requests.length, 1);
});

test('the same changed retry text without host provenance withdraws publication', async () => {
  const run = simulatedRun(extracted());
  run.invocation();
  run.attempt();
  run.write();
  run.finalizeRevision('Revise the answer.');
  run.attempt({provenance: false});
  assert.equal(run.admit('unproven').prepared?.block, true);
  assert.equal(run.requests.length, 0);
});

test('a queued input through the noted seam revokes an already admitted publication before the host', async () => {
  const run = simulatedRun(extracted());
  run.invocation();
  run.attempt();
  run.write();
  const call = run.admit('admitted');
  assert.equal(call.prepared?.block, undefined);
  run.cell().noteQueuedInput();
  assert.equal((await call.execute()).isError, true);
  run.attempt({compaction: COMPACTION});
  assert.equal(run.admit('after-retry').prepared?.block, true, 'a later retry never revives it');
  assert.equal(run.requests.length, 0);
});

test('a late external cancellation and outer termination each refuse an admitted call', async () => {
  for (const end of ['noteCancelled', 'terminate']) {
    const run = simulatedRun(extracted());
    run.invocation();
    run.attempt();
    run.write();
    const call = run.admit(`admitted-${end}`);
    assert.equal(call.prepared?.block, undefined);
    run.cell()[end]();
    assert.equal((await call.execute()).isError, true, end);
    assert.equal(run.requests.length, 0, end);
  }
});

test('a model-fallback style second invocation with the same prompt keeps a withdrawal', async () => {
  const run = simulatedRun(extracted());
  run.invocation();
  run.attempt();
  run.write();
  run.cell().noteQueuedInput();
  run.cell().terminate();
  run.invocation();
  assert.equal(run.attempt().kind, 'run_input');
  assert.equal(run.admit('fallback').prepared?.block, true);
  assert.equal(run.requests.length, 0);
});

test('a second invocation with the same prompt and nothing withdrawn can publish once', async () => {
  const run = simulatedRun(extracted());
  run.invocation();
  run.attempt();
  run.write();
  run.cell().terminate();
  run.invocation();
  run.attempt();
  const call = run.admit('fallback-clean');
  assert.equal(call.prepared?.block, undefined, call.prepared?.blockReason);
  assert.equal((await call.execute()).isError, undefined);
  assert.equal(run.requests.length, 1);
});
