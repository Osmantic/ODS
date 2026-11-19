import {extractCopyPublication} from '../plugin/preview-copy-publication.mjs';
// The owner's explicit first publication with a conditional repeat:
// "If that post-publication hash check did not change the file, do not
// republish it." (native goal c211ecda, 2026-11-18, refused every preview).
// The pure recognizer, the guard's admission, the trusted per-call policy and
// the receipt handling run through the actual tool factory and guard; the host
// is a fake that applies the same one-capture rule the real broker does
// (test_workspace_preview.py proves the broker side).
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {extractConditionalRepublication} from '../plugin/preview-publication-constraint.mjs';
import {createToolLoopGuard, userMessagePreviewStance} from '../plugin/tool-loop-guard.mjs';
import {createWorkspacePreviewTool, testing} from '../plugin/workspace-preview.mjs';

const NATIVE_DIR = 'mini-service-default64-r1-20261118';
const NATIVE = `/goal Apply a small accessibility repair to our synthetic demo ${NATIVE_DIR}/index.html, then privately publish and verify it. This is a fresh task named default-gateway64-proof-20261118.
Edit only that index.html. Add aria-describedby="fnameErr" to #fname, "fphoneErr" to #fphone, "femailErr" to #femail, "fmodelErr" to #fmodel, "ftypeErr" to #ftype and "fdateErr" to #fdate. Those six error elements already exist. Preserve every other byte, JavaScript, content and styles. Do not change another file, install anything, start a server, browse externally or send data.
Use one small public plan. Check that all six references exist and that the only changes are the attributes. Publish ${NATIVE_DIR} with the existing workspace preview tool. After publication, run exactly once this foreground read-only check: sha256sum ${NATIVE_DIR}/index.html . Then mark your original plan completed and finish. If that post-publication hash check did not change the file, do not republish it. Do not claim interactive testing; I will do that independently. Do not ask permission or repeat completed work. If an action actually fails, report it truthfully.
`;
const DIR = 'demo-site';
const PROMPT = `Build and publish a website in existing ${DIR}. Edit ${DIR}/index.html. Publish ${DIR} with the existing workspace preview tool. ` +
  `After publication, run sha256sum ${DIR}/index.html once. If that post-publication hash check did not change the file, do not republish it.`;

// ---- pure recognizer ------------------------------------------------------
test('the exact native prompt and the explicit unchanged form are recognized, nothing else', () => {
  const native = extractConditionalRepublication(NATIVE);
  assert.equal(native.relativeDirectory, NATIVE_DIR);
  assert.equal(native.entryFile, 'index.html');
  assert.match(native.request, /^Publish mini-service-default64-r1-20261118 with the existing workspace preview tool$/);
  assert.doesNotMatch(native.remaining, /republish/i);
  const explicit = extractConditionalRepublication(`Edit ${DIR}/index.html. Publish ${DIR}. If the file is unchanged, do not republish it.`);
  assert.equal(explicit.relativeDirectory, DIR);
  for (const text of [
    `Publish ${DIR}. Never republish it.`,
    `Publish ${DIR}. Do not republish it.`,
    `Edit ${DIR}/index.html. Publish ${DIR}. "If the file is unchanged, do not republish it."`,
    `Edit ${DIR}/index.html. Publish ${DIR}.\n> If the file is unchanged, do not republish it.`,
    'Edit ' + DIR + '/index.html. Publish ' + DIR + '.\n```\nIf the file is unchanged, do not republish it.\n```',
    `Edit ${DIR}/index.html and other/index.html. Publish ${DIR}. If the file is unchanged, do not republish it.`,
    `Edit ${DIR}/about.html. Publish ${DIR}. If the file is unchanged, do not republish it.`,
    `Edit ${DIR}/index.html. If the file is unchanged, do not republish it. Publish ${DIR}.`,
    `Edit ${DIR}/index.html. Publish other-site. If the file is unchanged, do not republish it.`,
    `Edit ${DIR}/index.html. Publish ${DIR}. If the file is unchanged, do not republish it. If the page is unchanged, do not republish it.`,
    `Edit ${DIR}/index.html. Publish ${DIR}. If the file is unchanged, do not republish it. Never republish anything.`,
    `Edit ${DIR}/index.html. Publish ${DIR}. If the file changed, do not republish it.`,
    `Edit ${DIR}/index.html. Publish ${DIR}. If the file is unchanged, do not publish it.`,
    `Edit ${DIR}/index.html. Ask Alice to publish ${DIR}. If the file is unchanged, do not republish it.`,
  ]) assert.equal(extractConditionalRepublication(text), undefined, text);
});

// Corrected in R2 (root review): guidance must not contradict admission for the
// owner's exact conditional first publication. Genuine negations still forbid.
test('prompt guidance opens the owner conditional first publication and nothing else', () => {
  assert.equal(userMessagePreviewStance([], NATIVE), 'open');
  for (const text of [PROMPT,
    'Publish demo/index.html. If the file is unchanged, do not republish it.',
    'Publish demo/index.html; If the file is unchanged, do not republish it.',
    'Repair demo/index.html. Publish demo/index.html. If the file is unchanged, do not republish it.',
  ]) assert.equal(userMessagePreviewStance([], text), 'open', text);
  for (const text of [
    `${PROMPT} Do not publish it.`,
    PROMPT.replace(`Publish ${DIR} with the existing workspace preview tool.`, `Publish ${DIR} after I approve.`),
    PROMPT.replace('If that post-publication hash check did not change the file, do not republish it.', 'Never republish it.'),
    `${PROMPT} Hold it until I say so.`,
    `${PROMPT} Only the code, please.`,
    `${NATIVE} Do not publish anything.`,
    'Publish demo/index.html. If the file is unchanged, do not republish it. Actually, do not publish anything.',
    'Publish demo/index.html. Never republish it.',
    'Repair demo/index.html. If the file is unchanged, do not republish it.',
    'Publish first/index.html and second/index.html. If the file is unchanged, do not republish it.',
    'Only code. Publish demo/index.html. If the file is unchanged, do not republish it.',
    'Publish demo/index.html after I approve. If the file is unchanged, do not republish it.',
    'Publish demo/index.html. If the file is unchanged, do not republish it. Only after I approve, though.',
    'Publish demo/index.html. If validation passes, do not republish it.',
    'Do not publish demo/index.html. If the file is unchanged, do not republish it.',
    'Publish demo/index.html. Do not edit files or publish anything. If the file is unchanged, do not republish it.',
    'Publish demo/index.html. Do not show me the preview. If the file is unchanged, do not republish it.',
    'Publish the page. If the file is unchanged, do not republish it.',
  ]) assert.equal(userMessagePreviewStance([], text), 'forbidden', text);
  // Quoted or fenced words are not the owner's: their existing stance is kept.
  for (const text of ['"Publish demo/index.html. If the file is unchanged, do not republish it."',
    '```text\nPublish demo/index.html. If the file is unchanged, do not republish it.\n```',
    'The example text says "do not republish". Publish demo/index.html.',
  ]) assert.equal(userMessagePreviewStance([], text), 'open', text);
});

// ---- guard, tool and fake host -----------------------------------------
function snapshotOf(directory, files) {
  const digest = createHash('sha256');
  let bytes = 0;
  const ordered = [...files.entries()].filter(([path]) => path.startsWith(directory + '/')).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  for (const [path, content] of ordered) {
    const name = Buffer.from(path.slice(directory.length + 1)), body = Buffer.from(content);
    const nameLength = Buffer.alloc(4), bodyLength = Buffer.alloc(8);
    nameLength.writeUInt32BE(name.length); bodyLength.writeBigUInt64BE(BigInt(body.length));
    digest.update(nameLength); digest.update(name); digest.update(bodyLength); digest.update(body);
    bytes += body.length;
  }
  const sha256 = digest.digest('hex');
  return {siteId: `site-${sha256.slice(0, 24)}`, sha256, files: ordered.length, bytes,
    entrySha256: createHash('sha256').update(files.get(`${directory}/index.html`)).digest('hex')};
}

function receipt(directory, snapshot, extra = {}) {
  return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: directory,
    siteId: snapshot.siteId, port: 9437, url: `http://${snapshot.siteId}.localhost:9437/${snapshot.siteId}/`,
    files: snapshot.files, bytes: snapshot.bytes, sha256: snapshot.sha256, entryFile: 'index.html',
    entrySha256: snapshot.entrySha256, httpStatus: 200, readbackVerified: true, executable: false,
    overwritten: false, boundary: testing.BOUNDARY, ...extra};
}

function failure(errorCode) {
  return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'failed', boundary: testing.BOUNDARY,
    error: 'ODS workspace preview publication failed', errorCode};
}

// The broker's rule from one capture of `files` at request time.
function fakeHost(files, log) {
  const created = new Set();
  return async request => {
    log.requests.push(structuredClone(request));
    const snap = snapshotOf(request.relativeDirectory, files);
    const prior = request.ifEntryUnchanged;
    if (!prior) { created.add(snap.siteId); log.created.push(snap.siteId); return receipt(request.relativeDirectory, snap); }
    if (!created.has(prior.siteId)) return failure('conditional_prior_unverified');
    if (snap.entrySha256 === prior.entrySha256) {
      if (snap.sha256 !== prior.sha256) return failure('conditional_entry_unchanged');
      return receipt(request.relativeDirectory, snap, {conditionalPublication: 'reused'});
    }
    if (!created.has(snap.siteId)) { created.add(snap.siteId); log.created.push(snap.siteId); }
    return receipt(request.relativeDirectory, snap, {conditionalPublication: 'published'});
  };
}

const written = {content: [{type: 'text', text: 'written'}], details: {status: 'completed'}};
const exited = {content: [{type: 'text', text: 'checked'}], details: {status: 'completed', exitCode: 0}};

function chain(prompt = PROMPT, {directory = DIR, guardOptions = {}, identity = {}} = {}) {
  const files = new Map([[`${directory}/index.html`, '<!doctype html><title>Original</title><h1>Original</h1>'],
    [`${directory}/styles.css`, 'h1{color:teal}']]);
  const log = {requests: [], created: []};
  const user = 'ods-' + 'e'.repeat(64);
  const context = Object.fromEntries(Object.entries({agentId: 'pixel', runId: 'run-1', sessionId: 'session-1',
    sessionKey: `agent:pixel:openai-user:${user}`, ...identity}).filter(([, value]) => value !== undefined));
  let active = context;
  let guard;
  const policy = (id, params) => guard.previewPublicationCondition(id, params);
  const tool = createWorkspacePreviewTool({request: fakeHost(files, log), publicationCondition: policy});
  guard = createToolLoopGuard({
    abortRunAndDrain: async () => ({aborted: true, drained: true, forceCleared: false}),
    publishWorkspacePreview: (params, {signal, toolCallId} = {}) => tool.execute(toolCallId ?? 'ods-preview-delivery', params, signal),
    ...guardOptions,
  });
  guard.observeRun(context, 'pixel', {prompt});
  let sequence = 0;
  const invoke = async (name, params, result, id = `call-${++sequence}`) => {
    const ctx = {...active, toolName: name, toolCallId: id};
    const prepared = guard.beforeToolCall({toolName: name, params, toolCallId: id}, ctx);
    if (prepared?.block) return {blocked: prepared.blockReason};
    const actual = prepared?.params ?? params;
    const outcome = typeof result === 'function' ? await result(id, actual) : result;
    guard.afterToolCall({toolName: name, params: actual, result: outcome, toolCallId: id}, ctx);
    // The tool message the model actually receives (the persisted result).
    const persisted = guard.toolResultPersist({toolName: name, toolCallId: id,
      message: {role: 'toolResult', toolName: name, toolCallId: id, ...outcome}}, ctx)?.message;
    return {result: outcome, persisted};
  };
  const write = (path, content) => { files.set(path, content); return invoke('write', {path, content}, written); };
  const publish = (params = {relativeDirectory: directory}) => invoke('pixel_ods_workspace_preview', params,
    (id, actual) => tool.execute(id, actual));
  const hashCheck = () => invoke('exec', {command: `sha256sum ${directory}/index.html`}, exited);
  return {guard, context, tool, files, log, invoke, write, publish, hashCheck, user,
    as: next => { active = next; },
    status: () => guard.verificationForRun(context.runId).status,
    state: () => guard.previewRepublicationForRun(context.runId)};
}

test('the native prompt admits its explicit first publication through the actual guard', async () => {
  const run = chain(NATIVE, {directory: NATIVE_DIR});
  await run.write(`${NATIVE_DIR}/index.html`, '<!doctype html><title>Form</title><input id="fname" aria-describedby="fnameErr">');
  const first = await run.publish();
  assert.equal(first.blocked, undefined, first.blocked);
  assert.equal(first.result.isError, undefined);
  assert.equal(run.log.requests.length, 1);
  assert.equal(Object.hasOwn(run.log.requests[0], 'ifEntryUnchanged'), false, 'the first publication is ordinary');
});

for (const [name, prompt] of [
  ['a later plain prohibition', `${PROMPT} Do not publish it.`],
  ['a deferred request', PROMPT.replace(`Publish ${DIR} with the existing workspace preview tool.`, `Publish ${DIR} after I approve.`)],
  ['an unconditioned republish negation', PROMPT.replace('If that post-publication hash check did not change the file, do not republish it.', 'Never republish it.')],
  ['a later hold', `${PROMPT} Hold it until I say so.`],
  ['only code', `${PROMPT} Only the code, please.`],
]) test(`genuine refusals stay refused: ${name}`, async () => {
  const run = chain(prompt);
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Changed</h1>');
  const first = await run.publish();
  assert.match(first.blocked ?? '', /prohibits displaying a preview/);
  assert.equal(run.log.requests.length, 0);
});

test('an unchanged entry and directory reuse the verified snapshot: no publication, no proof reset, current again', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const published = run.status();
  assert.equal(published, 'passed', 'the first verified publication is current');
  const before = run.state();
  assert.equal(before.priorSiteId, run.log.created[0]);
  await run.hashCheck();
  assert.notEqual(run.status(), 'passed', 'a settled command invalidates the preview at once');
  const second = await run.publish();
  assert.equal(second.result.isError, undefined);
  assert.equal(second.result.details.conditionalPublication, 'reused');
  assert.match(second.result.content[0].text, /reused that existing preview snapshot and did not create a new publication/);
  assert.doesNotMatch(second.result.content[0].text, /independently published/);
  const request = run.log.requests[1];
  assert.deepEqual(request.ifEntryUnchanged, {siteId: before.priorSiteId, sha256: before.priorSha256, entrySha256: before.priorEntrySha256});
  assert.equal(run.log.created.length, 1, 'no new snapshot');
  assert.equal(run.status(), published, 'the verified receipt is current again');
  const after = run.state();
  assert.equal(after.inspectionGeneration, before.inspectionGeneration, 'no inspection reset');
  assert.equal(after.priorSiteId, before.priorSiteId);
  assert.equal(after.previewReceipts, before.previewReceipts + 1, 'the actual call is still counted');
});

test('a changed named entry publishes those bytes as a new publication', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const before = run.state();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired again</h1>');
  const second = await run.publish();
  assert.equal(second.result.details.conditionalPublication, 'published');
  assert.equal(run.log.created.length, 2);
  const after = run.state();
  assert.notEqual(after.priorSiteId, before.priorSiteId);
  assert.equal(after.priorSiteId, run.log.created[1]);
  assert.equal(after.inspectionGeneration, before.inspectionGeneration + 1, 'a new publication resets inspection proof as usual');
});

test('an unchanged entry with another changed file is refused, never republished or claimed current', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const before = run.state();
  await run.write(`${DIR}/styles.css`, 'h1{color:crimson}');
  const second = await run.publish();
  assert.equal(second.result.isError, true);
  assert.equal(second.result.details.errorCode, 'conditional_entry_unchanged');
  assert.equal(run.log.created.length, 1);
  assert.notEqual(run.status(), 'passed');
  assert.equal(run.state().priorSiteId, before.priorSiteId);
});

test('wrong directory, source capture or model-supplied private policy cannot publish', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  assert.match((await run.publish({relativeDirectory: 'other-site'})).blocked ?? '', /owner's conditional publication/);
  assert.match((await run.publish({relativeDirectory: DIR, sourceDirectory: DIR})).blocked ?? '', /owner's conditional publication/);
  const forged = await run.tool.execute('call-forged', {relativeDirectory: DIR,
    ifEntryUnchanged: {siteId: 'site-' + 'a'.repeat(24), sha256: 'a'.repeat(64), entrySha256: 'b'.repeat(64)}});
  assert.equal(forged.isError, true);
  assert.equal(run.log.requests.length, 1, 'no host request for any of these');
});

test('an unbound or stale call never reaches the host while a conditional run is active', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const unbound = await run.tool.execute('call-not-admitted', {relativeDirectory: DIR});
  assert.equal(unbound.isError, true);
  assert.equal(unbound.details.errorCode, 'conditional_publication_unavailable');
  assert.equal(run.log.requests.length, 1);
});

test('once a prior exists, a receipt without the trusted condition is never accepted as current', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const before = run.state();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Changed</h1>');
  // A publication that bypassed the per-call policy (e.g. a tool instance without it).
  const plain = createWorkspacePreviewTool({request: async request => {
    run.log.requests.push(structuredClone(request));
    return receipt(DIR, snapshotOf(DIR, run.files));
  }});
  const bypass = await run.invoke('pixel_ods_workspace_preview', {relativeDirectory: DIR}, (id, actual) => plain.execute(id, actual));
  assert.equal(bypass.result.isError, undefined);
  assert.notEqual(run.status(), 'passed', 'not accepted as the current publication');
  assert.equal(run.state().priorSiteId, before.priorSiteId);
  assert.equal(run.state().inspectionGeneration, before.inspectionGeneration);
});

test('an interleaved write during the call keeps the reused receipt from claiming currency', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  await run.hashCheck();
  const second = await run.invoke('pixel_ods_workspace_preview', {relativeDirectory: DIR}, async (id, actual) => {
    const result = await run.tool.execute(id, actual);
    // Another tool's effect lands before this receipt is processed.
    await run.invoke('write', {path: `${DIR}/notes.txt`, content: 'late'}, written);
    return result;
  });
  assert.equal(second.result.details.conditionalPublication, 'reused');
  assert.notEqual(run.status(), 'passed', 'no currency from a receipt an interleaved effect may contradict');
});

test('after the owner cancels, the conditional policy refuses publication', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  assert.equal(await run.guard.abortUserRun(run.user), true);
  const after = await run.publish();
  assert.ok(after.blocked || after.result.isError);
  assert.equal(run.log.requests.length, 1);
});

test("finalize's host recovery carries the same condition and reuses an unchanged snapshot", async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const published = run.status();
  assert.equal(published, 'passed');
  await run.hashCheck();
  assert.notEqual(run.status(), 'passed');
  const recovered = await run.guard.recoverWorkspacePreview({}, run.context);
  assert.equal(recovered, true);
  assert.equal(run.log.requests.length, 2);
  assert.ok(run.log.requests[1].ifEntryUnchanged, 'the recovery request carries the condition');
  assert.equal(run.log.created.length, 1, 'no new snapshot');
  assert.equal(run.status(), published);
});

test('without the owner condition, publication behaviour is unchanged', async () => {
  const run = chain(`Build and publish a website in existing ${DIR}. Publish ${DIR} with the existing workspace preview tool.`);
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  await run.hashCheck();
  await run.publish();
  assert.equal(run.log.requests.length, 2);
  assert.ok(run.log.requests.every(request => !Object.hasOwn(request, 'ifEntryUnchanged')));
  assert.equal(run.state().condition, undefined);
});

// ---- R2: retained owner intent and admitted identity (root review) ---------
const OTHER_KEY = `agent:pixel:openai-user:ods-${'f'.repeat(64)}`;

test('the same owner prompt rebuilt after publication retains the conditional policy', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  run.guard.observeRun(run.context, 'pixel', {prompt: PROMPT});
  await run.hashCheck();
  const reuse = await run.publish();
  assert.equal(reuse.blocked, undefined, 'same owner prompt cannot revoke its conditional first-publication request');
  assert.equal(reuse.result.details.conditionalPublication, 'reused');
  assert.equal(run.log.created.length, 1);
  assert.equal(run.status(), 'passed');
  // A native prompt hook can omit the session key; the tool hooks completed it.
  const {sessionKey: _omitted, ...withoutKey} = run.context;
  run.guard.observeRun(withoutKey, 'pixel', {prompt: PROMPT});
  const again = await run.publish();
  assert.equal(again.blocked, undefined, again.blocked);
  assert.equal(again.result.details.conditionalPublication, 'reused');
  assert.equal(run.log.created.length, 1);
});

for (const [name, change] of [
    ['changed owner words', run => run.guard.observeRun(run.context, 'pixel', {prompt: `${PROMPT} Thanks.`})],
    ['the same words under a foreign session', run => run.guard.observeRun({...run.context, sessionId: 'session-foreign'}, 'pixel', {prompt: PROMPT})],
    ['the same words under a foreign session key', run => run.guard.observeRun({...run.context, sessionKey: OTHER_KEY}, 'pixel', {prompt: PROMPT})],
    ['a later run of the same session (earlier-turn preview)', run => {
      const next = {...run.context, runId: 'run-2'};
      run.guard.observeRun(next, 'pixel', {prompt: PROMPT});
      run.as(next);
    }],
]) test(`the retained policy is never widened by ${name}`, async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  change(run);
  const after = await run.publish();
  // Refused at admission: the stance is not reopened for these words or identities.
  assert.match(after.blocked ?? '', /prohibits displaying a preview/, name);
  assert.equal(run.log.requests.length, 1, name);
  assert.equal(run.log.created.length, 1, name);
});

test('policy is consumed only by the originally admitted session', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const id = 'root-pending';
  const params = {relativeDirectory: DIR};
  run.guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params, toolCallId: id},
    {...run.context, toolName: 'pixel_ods_workspace_preview', toolCallId: id});
  run.guard.observeRun({...run.context, sessionId: 'foreign-session'}, 'pixel', {prompt: PROMPT});
  const result = await run.tool.execute(id, params);
  assert.equal(result.isError, true, 'session replacement must refuse before a host request');
  assert.equal(run.log.requests.length, 1);
});

for (const [name, change] of [
    ['no identity change (control)', () => {}],
    ['a foreign session seen by a tool hook', run => run.guard.observeRun({...run.context, sessionId: 'session-foreign'}, 'pixel')],
    ['a foreign session key seen by a tool hook', run => run.guard.observeRun({...run.context, sessionKey: OTHER_KEY}, 'pixel')],
    ['a foreign session, then the original again (sticky conflict)', run => {
      run.guard.observeRun({...run.context, sessionId: 'session-foreign'}, 'pixel');
      run.guard.observeRun(run.context, 'pixel');
    }],
]) test(`a pending conditional call admitted in the original session, with ${name} before execution`, async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  await run.hashCheck();
  const id = 'pending-call', params = {relativeDirectory: DIR};
  const admitted = run.guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params, toolCallId: id},
    {...run.context, toolName: 'pixel_ods_workspace_preview', toolCallId: id});
  assert.equal(admitted?.block, undefined, name);
  change(run);
  const result = await run.tool.execute(id, params);
  if (name === 'no identity change (control)') {
    assert.equal(result.details.conditionalPublication, 'reused', name);
    assert.equal(run.log.requests.length, 2, name);
    assert.equal(run.log.requests[1].ifEntryUnchanged.siteId, run.state().priorSiteId, name);
  } else {
    assert.equal(result.isError, true, name);
    assert.equal(result.details.errorCode, 'conditional_publication_unavailable', name);
    assert.equal(run.log.requests.length, 1, name);
  }
  assert.equal(run.log.created.length, 1, name);
});

for (const missing of ['sessionKey', 'sessionId']) test(`a conditional run without a ${missing} never publishes under the condition`, async () => {
  const run = chain(PROMPT, {identity: {[missing]: undefined}});
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  const first = await run.publish();
  if (missing === 'sessionId') {
    // No session to bind: the stance is not reopened at all.
    assert.match(first.blocked ?? '', /prohibits displaying a preview/, missing);
  } else {
    // The key can still arrive with a tool hook; until then the per-call policy refuses.
    assert.equal(first.blocked, undefined, missing);
    assert.equal(first.result.details.errorCode, 'conditional_publication_unavailable', missing);
  }
  assert.equal(run.log.requests.length, 0);
});

test('a call admitted before the session key was known cannot publish under the condition once it appears', async () => {
  const run = chain(PROMPT, {identity: {sessionKey: undefined}});
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  const id = 'keyless-pending', params = {relativeDirectory: DIR};
  const admitted = run.guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params, toolCallId: id},
    {...run.context, toolName: 'pixel_ods_workspace_preview', toolCallId: id});
  assert.equal(admitted?.block, undefined);
  run.guard.observeRun({...run.context, sessionKey: `agent:pixel:openai-user:${run.user}`}, 'pixel');
  const result = await run.tool.execute(id, params);
  assert.equal(result.isError, true);
  assert.equal(result.details.errorCode, 'conditional_publication_unavailable');
  assert.equal(run.log.requests.length, 0);
});

test('an identity change between the host call and its receipt keeps the reuse from claiming currency', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  await run.hashCheck();
  const before = run.state();
  const reuse = await run.invoke('pixel_ods_workspace_preview', {relativeDirectory: DIR}, async (id, actual) => {
    const result = await run.tool.execute(id, actual);
    run.guard.observeRun({...run.context, sessionKey: OTHER_KEY}, 'pixel');
    return result;
  });
  assert.equal(reuse.result.details.conditionalPublication, 'reused', 'the host reused; the guard decides currency');
  assert.notEqual(run.status(), 'passed', 'not current after an identity conflict');
  assert.equal(run.state().inspectionGeneration, before.inspectionGeneration);
  assert.equal(run.log.created.length, 1);
});

test('an ordinary run without the owner condition keeps its behaviour under the same identity changes', async () => {
  const run = chain(`Build and publish a website in existing ${DIR}. Publish ${DIR} with the existing workspace preview tool.`,
    {identity: {sessionKey: undefined}});
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  const first = await run.publish();
  assert.equal(first.blocked, undefined, first.blocked);
  assert.equal(run.log.requests.length, 1);
  assert.ok(run.log.requests.every(request => !Object.hasOwn(request, 'ifEntryUnchanged')));
});

// ---- Coaching after a settled command in the owner's conditional run (native 6efc2d3c) ----
// Native: after the owner's post-publication sha256sum, the host demanded an
// unconditional republication. Eligibility (workspaceRevalidationCandidate)
// admits any settled foreground exec, including a shell write, so guidance must
// be neutral: completion proves neither changed nor unchanged bytes, a real edit
// stays actionable, and only the trusted comparison restores currency.
const STALE_REPUBLISH = /Files or checks changed after the earlier publication/;
const NEUTRAL = /That foreground command completed\. Completion alone shows neither that files in demo-site changed nor that they are unchanged\./;
const PUBLISH_IF_CHANGED = 'If this command or a later owner-requested edit changed files in demo-site after the earlier publication, finish any remaining requested edits and then call tool_call with id pixel_ods_workspace_preview and args {"relativeDirectory":"demo-site"}; otherwise do not republish only because a command ran, and do not repeat a command that already completed.';
const textOf = persisted => (persisted?.content ?? []).filter(block => block?.type === 'text').map(block => block.text).join('\n');
const readResult = {content: [{type: 'text', text: '<!doctype html><h1>Repaired</h1>'}], details: {status: 'completed'}};
const comparingVerifier = runRef => async preview => snapshotOf(DIR, runRef.run.files).sha256 === preview.sha256;

// Native Tool Search calls the catalog child before reporting its envelope.
// The first publication must survive both callbacks without being mistaken
// for a second, unbound conditional publication (native c54032de, 2026-11-18).
async function nestedFirstPublication(run, {childId, afterChild, envelope, expectedChildRefusal = false, directory = DIR} = {}) {
  const name = 'pixel_ods_workspace_preview', params = {relativeDirectory: directory};
  return run.invoke('tool_call', {id: `openclaw:pixel-ods:${name}`, args: params}, async outerId => {
    const child = await run.invoke(name, params, (id, actual) => run.tool.execute(id, actual),
      childId ?? `tool_search_code:${outerId}:${name}:1`);
    assert.equal(child.blocked, undefined);
    assert.equal(child.result.isError, expectedChildRefusal ? true : undefined);
    if (expectedChildRefusal) assert.equal(child.result.details.errorCode, 'conditional_publication_unavailable');
    if (afterChild) await afterChild();
    const tool = {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name};
    const result = {content: [{type: 'text', text: JSON.stringify({tool, result: child.result})}],
      details: {tool, result: child.result}};
    return envelope ? envelope(result) : result;
  }, 'nested-preview');
}

test('a nested first publication keeps neutral coaching and needs the trusted comparison', async () => {
  const ref = {};
  const run = ref.run = chain(PROMPT, {guardOptions: {verifyWorkspacePreview: comparingVerifier(ref)}});
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  const first = await nestedFirstPublication(run);
  assert.equal(first.blocked, undefined);
  assert.equal(run.status(), 'passed');
  assert.equal(run.state().previewReceipts, 1, 'one publication, not one per hook callback');
  assertNeutral(textOf((await run.hashCheck()).persisted));
  assert.notEqual(run.status(), 'passed', 'the hash receipt alone never proves currency');
  assert.equal(await run.guard.revalidateWorkspacePreview({}, run.context), true);
  assert.equal(run.status(), 'passed');
  assert.equal(run.log.requests.length, 1, 'no repeat host publication');
});

for (const variant of ['foreign-child', 'invalid-child-sequence', 'mismatching-snapshot', 'wrong-envelope-source',
  'outer-failure', 'intervening-write', 'identity-conflict']) {
  test(`a nested first publication never preserves an unbound duplicate: ${variant}`, async () => {
    const ref = {};
    const run = ref.run = chain(PROMPT, {guardOptions: {verifyWorkspacePreview: comparingVerifier(ref)}});
    await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
    const options = {};
    if (variant === 'foreign-child') {
      options.childId = 'tool_search_code:other:pixel_ods_workspace_preview:1';
      options.expectedChildRefusal = true;
    }
    if (variant === 'invalid-child-sequence') options.childId = 'tool_search_code:nested-preview:pixel_ods_workspace_preview:invalid';
    if (variant === 'mismatching-snapshot') options.envelope = result => {
      result.details.result = structuredClone(result.details.result);
      result.details.result.details.sha256 = 'f'.repeat(64);
      return result;
    };
    if (variant === 'wrong-envelope-source') options.envelope = result => {
      result.details.tool.sourceName = 'other'; return result;
    };
    if (variant === 'outer-failure') options.envelope = result => ({...result, isError: true});
    if (variant === 'intervening-write') options.afterChild = () => run.write(`${DIR}/index.html`, '<!doctype html><h1>Changed</h1>');
    if (variant === 'identity-conflict') options.afterChild = () => run.guard.observeRun({...run.context,
      sessionKey: `agent:pixel:openai-user:ods-${'f'.repeat(64)}`}, 'pixel');
    await nestedFirstPublication(run, options);
    await run.hashCheck();
    assert.equal(await run.guard.revalidateWorkspacePreview({}, run.context), false);
    assert.notEqual(run.status(), 'passed');
    assert.equal(run.log.requests.length, variant === 'foreign-child' ? 0 : 1);
  });
}

function assertNeutral(note) {
  assert.doesNotMatch(note, STALE_REPUBLISH, 'no unconditional republication demand');
  assert.match(note, NEUTRAL);
  assert.ok(note.includes(PUBLISH_IF_CHANGED), 'a real edit after the publication stays actionable with exact args');
  assert.match(note, /The owner asked not to republish demo-site\/index\.html if it did not change\./);
  assert.match(note, /Only a successful trusted ODS comparison with the earlier verified publication can establish that the earlier snapshot is still current; do not claim it is current\./);
  assert.doesNotMatch(note, /Before your final answer|ODS compares|will compare/, 'no promise that a comparison runs or passes');
  assert.doesNotMatch(note, /read-only|this check/i, 'never calls an arbitrary command a read-only check');
}

test("after the owner's post-publication hash check, coaching is neutral instead of demanding republication", async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const check = await run.hashCheck();
  assertNeutral(textOf(check.persisted));
  // Coaching restores nothing: the earlier snapshot is not current until compared.
  assert.notEqual(run.status(), 'passed');
  assert.equal(run.log.requests.length, 1);
  const read = await run.invoke('read', {path: `${DIR}/index.html`}, readResult);
  assert.doesNotMatch(textOf(read.persisted), STALE_REPUBLISH);
});

test('the same settled command through a Tool Search exec gets the same neutral coaching', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const args = {command: `sha256sum ${DIR}/index.html`};
  const check = await run.invoke('tool_call', {id: 'openclaw:core:exec', args}, {
    content: [{type: 'text', text: 'checked'}],
    details: {tool: {id: 'openclaw:core:exec', source: 'openclaw', sourceName: 'core', name: 'exec'},
      result: {content: [{type: 'text', text: 'checked'}], details: {status: 'completed', exitCode: 0}}},
  });
  assert.equal(check.blocked, undefined, check.blocked);
  assert.notEqual(run.status(), 'passed');
  assertNeutral(textOf(check.persisted));
});

for (const [name, verified, expected] of [['succeeds', true, 'passed'], ['fails', false, undefined]]) {
  test(`after the neutral coaching, currency returns only if the trusted comparison ${name}`, async () => {
    const calls = [];
    const run = chain(PROMPT, {guardOptions: {verifyWorkspacePreview: async preview => { calls.push(preview.siteId); return verified; }}});
    await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
    await run.publish();
    const prior = run.state().priorSiteId;
    assertNeutral(textOf((await run.hashCheck()).persisted));
    assert.notEqual(run.status(), 'passed');
    const restored = await run.guard.revalidateWorkspacePreview({}, run.context);
    assert.deepEqual(calls, [prior], 'exactly one trusted comparison of the prior snapshot');
    assert.equal(restored, verified);
    if (expected) assert.equal(run.status(), expected);
    else assert.notEqual(run.status(), 'passed');
    assert.equal(run.log.requests.length, 1, 'no publication by coaching or comparison');
  });
}

// Root control: a successful foreground exec that really writes the entry is
// eligible exactly like the hash check. It must get the same neutral guidance,
// which keeps the real edit publishable and never calls it a check.
const SHELL_EDIT = `sed -i 's/Repaired/Changed by shell/' ${DIR}/index.html`;
const shellEdit = run => {
  run.files.set(`${DIR}/index.html`, '<!doctype html><h1>Changed by shell</h1>');
  return run.invoke('exec', {command: SHELL_EDIT}, {content: [{type: 'text', text: ''}], details: {status: 'completed', exitCode: 0}});
};

test('a successful shell write of the entry gets the neutral guidance and its publication stays actionable', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const edit = await shellEdit(run);
  assert.equal(edit.blocked, undefined, edit.blocked);
  const note = textOf(edit.persisted);
  assertNeutral(note);
  assert.doesNotMatch(note, /do not call pixel_ods_workspace_preview/);
  assert.notEqual(run.status(), 'passed');
  // Following the guidance: the publication carries the owner's condition and
  // the host publishes the changed entry as a new snapshot.
  const published = await run.publish();
  assert.equal(published.blocked, undefined, published.blocked);
  assert.ok(run.log.requests[1].ifEntryUnchanged, 'conditional request');
  assert.equal(published.result.details.conditionalPublication, 'published');
  assert.equal(run.log.created.length, 2);
  assert.equal(run.status(), 'passed');
  assert.equal(run.state().priorEntrySha256, snapshotOf(DIR, run.files).entrySha256);
});

test('after an unpublished shell write, the trusted comparison fails and finalize recovery publishes the change', async () => {
  const ref = {};
  const run = ref.run = chain(PROMPT, {guardOptions: {verifyWorkspacePreview: comparingVerifier(ref)}});
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  assertNeutral(textOf((await shellEdit(run)).persisted));
  assert.equal(await run.guard.revalidateWorkspacePreview({}, run.context), false, 'changed bytes never compare as current');
  assert.notEqual(run.status(), 'passed');
  assert.equal(await run.guard.recoverWorkspacePreview({}, run.context), true);
  assert.ok(run.log.requests[1].ifEntryUnchanged);
  assert.equal(run.log.created.length, 2, 'the changed entry is published, not reused');
  assert.equal(run.status(), 'passed');
});

test('after an unchanged hash check, the same comparison restores the earlier snapshot without publishing', async () => {
  const ref = {};
  const run = ref.run = chain(PROMPT, {guardOptions: {verifyWorkspacePreview: comparingVerifier(ref)}});
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  assertNeutral(textOf((await run.hashCheck()).persisted));
  assert.equal(await run.guard.revalidateWorkspacePreview({}, run.context), true);
  assert.equal(run.status(), 'passed');
  assert.equal(run.log.requests.length, 1);
});

const failedCheck = {content: [{type: 'text', text: 'sha256sum: missing'}], isError: true,
  details: {status: 'completed', exitCode: 1}};
const runningCheck = {content: [{type: 'text', text: 'running'}], details: {status: 'running', sessionId: 'exec-session-1'}};
for (const [name, act, observed] of [
  ['an actual core write after publication', run => run.write(`${DIR}/index.html`, '<!doctype html><h1>Changed</h1>'), 'stale'],
  ['a failed command, then a read', async run => {
    await run.invoke('exec', {command: `sha256sum ${DIR}/index.html`}, failedCheck);
    return run.invoke('read', {path: `${DIR}/index.html`}, readResult);
  }, 'stale'],
  ['a command still running, then a read', async run => {
    await run.invoke('exec', {command: `sha256sum ${DIR}/index.html`}, runningCheck);
    return run.invoke('read', {path: `${DIR}/index.html`}, readResult);
  }, 'executed'],
  ['a foreign session key observed before the command', run => {
    run.guard.observeRun({...run.context, sessionKey: OTHER_KEY}, 'pixel');
    return run.hashCheck();
  }, 'executed'],
  ['an owner cancellation before the command', async run => {
    assert.equal(await run.guard.abortUserRun(run.user), true);
    return run.hashCheck();
  }, 'blocked'],
]) test(`the neutral conditional coaching is not given after ${name}`, async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const last = await act(run);
  if (observed === 'blocked') assert.ok(last.blocked, name);
  else assert.equal(last.blocked, undefined, name);
  if (observed === 'stale') assert.match(textOf(last.persisted), STALE_REPUBLISH, name);
  assert.doesNotMatch(textOf(last?.persisted), NEUTRAL, name);
  assert.equal(run.log.requests.length, 1, name);
});

test('a real core write after the neutral coaching switches back to the existing republication coaching', async () => {
  const run = chain();
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  assertNeutral(textOf((await run.hashCheck()).persisted));
  const write = await run.write(`${DIR}/index.html`, '<!doctype html><h1>Changed</h1>');
  assert.match(textOf(write.persisted), STALE_REPUBLISH);
  assert.doesNotMatch(textOf(write.persisted), NEUTRAL);
});

test('an ordinary run without the owner condition keeps the existing coaching after the same command', async () => {
  const run = chain(`Build and publish a website in existing ${DIR}. Edit ${DIR}/index.html. Publish ${DIR} with the existing workspace preview tool.`);
  await run.write(`${DIR}/index.html`, '<!doctype html><h1>Repaired</h1>');
  await run.publish();
  const check = await run.hashCheck();
  assert.match(textOf(check.persisted), STALE_REPUBLISH);
  assert.doesNotMatch(textOf(check.persisted), NEUTRAL);
});

const NATIVE_COPY = "/goal Create and finish a separate edited copy of our synthetic MINI demo. This is a fresh task named copy-workflow-proof-20261118.\nRead mini-service-nested-r1-20261118/index.html as input only. Copy it to the NEW mini-service-copy-r1-20261118/index.html before editing. Never edit or publish the original. Only the new copy is the requested deliverable.\nIn the copy make exactly these six literal text replacements, preserving every other byte, all JavaScript, styles, form validation and the existing field types:\n1. Replace \"<title>MINI Drive Center \u2014 Sample Preview</title>\" with \"<title>MINI Service Arrival \u2014 Demo</title>\".\n2. Replace \"<span>MINI Drive Center<small>\" with \"<span>MINI Service Arrival<small>\".\n3. Replace \"<p>MINI Drive Center \u2014 demo page with sample content only. Not a real dealership listing.</p>\" with \"<p>MINI Service Arrival \u2014 demo page with sample content only. Not a real dealership listing.</p>\".\n4. Replace \"<h1>Keep your MINI <span>going the extra mile</span>.</h1>\" with \"<h1>Your MINI <span>service visit, made simple</span>.</h1>\".\n5. Replace \"<p>Meet your service advisor. We confirm the work order, walk through the items, and confirm pricing before anything begins.</p>\" with \"<p>Pull into the marked service drive. A service advisor will greet you, confirm your appointment and review the work before it begins.</p>\".\n6. Replace \"<p>Book a service visit in a couple of taps. Pick a time that works and we will confirm it by text. This button is a sample placeholder \u2014 no real booking is sent.</p>\" with \"<p>This is a local demo. Try the form below; no appointment is sent and no real booking is made.</p>\".\nUse one small public plan and finish the work. Do file/command checks before publication: prove the original is unchanged and the copy contains only those six replacements. Publish mini-service-copy-r1-20261118 with the existing workspace preview tool.\nAfter publication use the actual preview inspector with its returned siteId and full sha256. In separate bounded inspection calls verify: (a) the first FAQ answer starts hidden, opens after its question is clicked and hides again after a second click; (b) submitting the untouched blank appointment form visibly shows all six required-field errors. Also confirm the changed hero and service-drive paragraph are visible. Use source-derived selectors and visible postconditions, not click dispatch alone. Keep every real check, split plans to fit the tool limits, and do not change the UI or field types just to satisfy an inspector limitation.\nAfter publishing, use only read-only preview inspection for verification so that the publication stays current. Finish the original plan and deliver the actual result, not a promise to continue. If any real check fails or remains unsupported, say so accurately. No installs, servers, external browsing, bookings, data sending, other agents or edits outside the new copy. Do not repeat completed writes or publications.\n";

// Native goal 773fe8d6: the original's exclusion is not the copy's exclusion.
const COPY_SOURCE = 'original-demo';
const COPY_DEST = 'edited-demo';
const COPY_PROMPT = `Read ${COPY_SOURCE}/index.html as input only. Copy it to the NEW ${COPY_DEST}/index.html before editing. Never edit or publish the original. Only the new copy is the requested deliverable. Publish ${COPY_DEST} with the existing workspace preview tool.`;

// Native goal 77368eca: a command ban explicitly limited to after publication
// must not block the commands required to prepare the copy beforehand.
for (const suffix of [
  'Do not run shell commands after publication.',
  'Do not run shell/read/diff/grep/hash commands after publication.',
  'After publication, do not run shell commands.',
  'After successful publication, never execute commands.',
]) for (const wrapped of [false, true]) test(`postpublication-only command ban starts after a verified publication (${wrapped}): ${suffix}`, async () => {
  const run = chain(`${COPY_PROMPT} ${suffix}`, {directory:COPY_DEST});
  const command = () => wrapped
    ? run.invoke('tool_call', {id:'openclaw:core:exec', args:{command:'ls'}}, {details:{result:exited}})
    : run.invoke('exec', {command:'ls'}, exited);
  assert.equal((await command()).blocked, undefined, 'preparation command remains allowed');
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  assert.equal((await run.publish()).blocked, undefined);
  assert.ok((await command()).blocked, 'the owner ban applies after verified publication');
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Changed</title>');
  assert.ok((await command()).blocked, 'later edits do not erase the after-publication boundary');
});

for (const suffix of [
  'Do not run shell commands.',
  'Do not run shell commands after publication or before it.',
  'If the files changed after publication, do not run shell commands.',
  'Do not run shell commands. After publication, do not run shell commands.',
  'After publication, do not run shell commands or contact external sites.',
  'Do not run shell commands after publication but also before it.',
  'Do not run shell commands after publication then before it too.',
  'Do not run shell commands after publication. Do not run shell commands after publication but also before it.',
]) test(`postpublication parsing preserves global or ambiguous bans: ${suffix}`, async () => {
  const run = chain(`${COPY_PROMPT} ${suffix}`, {directory:COPY_DEST});
  assert.ok((await run.invoke('exec', {command:'ls'}, exited)).blocked);
});

test('a failed publication does not activate a postpublication command ban', async () => {
  const run = chain(`${COPY_PROMPT} Do not run shell commands after publication.`, {directory:COPY_DEST});
  const failed = await run.invoke('pixel_ods_workspace_preview', {relativeDirectory:COPY_DEST},
    {isError:true, details:failure('missing_entry'), content:[{type:'text',text:'Missing entry'}]});
  assert.equal(failed.blocked, undefined);
  assert.equal((await run.invoke('exec', {command:'ls'}, exited)).blocked, undefined);
});

test('an unverified publication receipt cannot activate the temporal boundary', async () => {
  const run = chain(`${COPY_PROMPT} Do not run shell commands after publication.`, {directory:COPY_DEST});
  const unverified = receipt(COPY_DEST, snapshotOf(COPY_DEST, run.files), {readbackVerified:false});
  await run.invoke('pixel_ods_workspace_preview', {relativeDirectory:COPY_DEST}, {details:unverified});
  assert.equal((await run.invoke('exec', {command:'ls'}, exited)).blocked, undefined);
});

test('a temporal command ban does not weaken an independent no-web exclusion', async () => {
  const run = chain(`${COPY_PROMPT} After publication, do not run shell commands. Do not contact external sites.`, {directory:COPY_DEST});
  assert.equal((await run.invoke('exec', {command:'ls'}, exited)).blocked, undefined);
  assert.ok((await run.invoke('web_fetch', {url:'https://example.invalid'}, {})).blocked);
});

test('an original-only exclusion admits the explicit copied destination, through the real guard/tool chain', async () => {
  const run = chain(COPY_PROMPT, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  const published = await run.publish();
  assert.equal(published.blocked, undefined, published.blocked);
  assert.equal(published.result?.isError, undefined);
  assert.equal(run.log.requests.length, 1);
  assert.equal(run.log.requests[0].relativeDirectory, COPY_DEST);
});

test('the complete failed native request admits only its explicitly requested copy', async () => {
  const run = chain(NATIVE_COPY, {directory:'mini-service-copy-r1-20261118'});
  await run.write('mini-service-copy-r1-20261118/index.html', '<!doctype html><title>Copy</title>');
  const published = await run.publish();
  assert.equal(published.blocked, undefined, published.blocked);
  assert.equal(run.log.requests.length, 1);
  assert.equal(run.log.requests[0].relativeDirectory, 'mini-service-copy-r1-20261118');
});

for (const params of [
  {relativeDirectory:COPY_SOURCE},
  {relativeDirectory:'other-site'},
  {directory:COPY_SOURCE},
  {relativeDirectory:COPY_DEST, sourceDirectory:COPY_SOURCE},
  {relativeDirectory:COPY_DEST, directory:COPY_SOURCE},
]) test(`the copy exclusion cannot publish another target: ${JSON.stringify(params)}`, async () => {
  const run = chain(COPY_PROMPT, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  await run.write(`${COPY_SOURCE}/index.html`, '<!doctype html><title>Original</title>');
  await run.write('other-site/index.html', '<!doctype html><title>Other</title>');
  assert.ok((await run.publish(params)).blocked);
  assert.equal(run.log.requests.length, 0);
});

for (const suffix of [
  'Never publish anything.',
  'Do not show me a preview.',
  'Do not edit files or publish anything.',
  'Only the code, please.',
  'Hold it until I say so.',
  'Only after I approve, though.',
  'Never publish the copy.',
]) test(`a copy never overrides a separate owner restriction: ${suffix}`, async () => {
  const run = chain(`${COPY_PROMPT} ${suffix}`, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  assert.ok((await run.publish()).blocked);
  assert.equal(run.log.requests.length, 0);
});

test('a deferred copy publication stays refused', async () => {
  const run = chain(COPY_PROMPT.replace(`Publish ${COPY_DEST} with the existing workspace preview tool.`, `Publish ${COPY_DEST} after I approve.`), {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  assert.ok((await run.publish()).blocked);
  assert.equal(run.log.requests.length, 0);
});

test('guidance sets aside only the recognized original exclusion', () => {
  assert.equal(userMessagePreviewStance([], COPY_PROMPT), 'open');
  assert.equal(userMessagePreviewStance([], NATIVE_COPY), 'open');
  assert.equal(userMessagePreviewStance([], COPY_PROMPT + ' Do not publish anything.'), 'forbidden');
});


test('copy-scope recognition refuses ambiguous, overlapping, quoted and malformed targets', () => {
  assert.deepEqual(extractCopyPublication(COPY_PROMPT)?.relativeDirectory, COPY_DEST);
  for (const text of [
    COPY_PROMPT.replace('Read original-demo/index.html as input only.', ''),
    COPY_PROMPT.replace('Copy it', 'Copy something'),
    COPY_PROMPT.replace('edited-demo/index.html', '../edited-demo/index.html'),
    COPY_PROMPT.replace('edited-demo/index.html', '/tmp/edited-demo/index.html'),
    COPY_PROMPT.replaceAll(COPY_DEST, COPY_SOURCE),
    COPY_PROMPT.replaceAll(COPY_DEST, COPY_SOURCE + '/nested'),
    COPY_PROMPT.replaceAll(COPY_SOURCE, COPY_DEST + '/nested'),
    COPY_PROMPT.replace(`Publish ${COPY_DEST}`, `Publish ${COPY_DEST.toUpperCase()}`),
    COPY_PROMPT + ' Copy it to the NEW another-demo/index.html before editing.',
    COPY_PROMPT + ' Read other-original/index.html as input only.',
    COPY_PROMPT + ' Publish original-demo.',
    COPY_PROMPT.replace('Never edit or publish the original.', 'Never edit or publish the original or anything else.'),
    'Never edit or publish the original. "' + COPY_PROMPT + '"',
    "Never edit or publish the original. '" + COPY_PROMPT + "'",
    'Never edit or publish the original. ```text\n' + COPY_PROMPT + '\n```',
    'Never edit or publish the original.\n> ' + COPY_PROMPT,
    COPY_PROMPT + ' Inspect unrelated/other.html.',
  ]) assert.equal(extractCopyPublication(text), undefined, text);
});

test('an explicit source-to-copy command is accepted without an ambiguous pronoun', async () => {
  const prompt = COPY_PROMPT.replace('Copy it', `Copy ${COPY_SOURCE}/index.html`);
  const run = chain(prompt, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  assert.equal((await run.publish()).blocked, undefined);
  assert.equal(run.log.requests.length, 1);
});

test('a copied preview works through the nested Tool Search transport', async () => {
  const run = chain(COPY_PROMPT, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  const result = await nestedFirstPublication(run, {directory:COPY_DEST});
  assert.equal(result.blocked, undefined, result.blocked);
  assert.equal(run.log.requests.length, 1);
  assert.equal(run.log.requests[0].relativeDirectory, COPY_DEST);
});

for (const identity of [{sessionId:undefined}, {sessionKey:undefined}])
  test(`a copy exception needs complete identity: ${JSON.stringify(identity)}`, async () => {
    const run = chain(COPY_PROMPT, {directory:COPY_DEST, identity});
    await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
    assert.ok((await run.publish()).blocked);
    assert.equal(run.log.requests.length, 0);
  });

for (const [label, change] of [
  ['foreign call session', run => run.as({...run.context,sessionId:'foreign'})],
  ['foreign call owner', run => run.as({...run.context,sessionKey:OTHER_KEY})],
  ['conflicting observation', run => run.guard.observeRun({...run.context,sessionKey:OTHER_KEY}, 'pixel', {prompt:COPY_PROMPT})],
  ['changed owner words', run => run.guard.observeRun(run.context, 'pixel', {prompt:'Publish original-demo/index.html.'})],
  ['later owner refusal', run => run.guard.observeRun(run.context, 'pixel', {prompt:COPY_PROMPT + ' Do not publish anything.'})],
  ['owner cancellation', run => run.guard.abortUserRun(run.user)],
]) test(`a scoped copy cannot publish after ${label}`, async () => {
  const run = chain(COPY_PROMPT, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  await change(run);
  assert.ok((await run.publish()).blocked, label);
  assert.equal(run.log.requests.length, 0);
});

test('the original-exclusion exception is not a reusable unbound host grant', async () => {
  const run = chain(COPY_PROMPT, {directory:COPY_DEST});
  assert.equal((await run.tool.execute('unbound', {relativeDirectory:COPY_DEST})).isError, true);
  assert.equal(run.log.requests.length, 0);
});

test('a failed copy publication remains failed instead of becoming delivery evidence', async () => {
  const run = chain(COPY_PROMPT, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  await run.invoke('pixel_ods_workspace_preview', {relativeDirectory:COPY_DEST}, {isError:true,
    content:[{type:'text',text:'host failure'}],details:{status:'failed'}});
  assert.notEqual(run.status(), 'passed');
  assert.equal(run.log.requests.length, 0);
});


test('quoted, unfinished fenced, and explicitly embedded example instructions do not establish copy authority', () => {
  const direct = COPY_PROMPT.replace('Copy it', `Copy ${COPY_SOURCE}/index.html`);
  for (const text of [
    'Never edit or publish the original. “' + COPY_PROMPT + '”',
    'Never edit or publish the original.\n```text\n' + COPY_PROMPT,
    'Never edit or publish the original.\n~~~text\n' + COPY_PROMPT,
    'Write instructions.txt containing:\n' + direct,
    'Write instructions.txt with the following text:\n' + direct,
  ]) {
    assert.equal(extractCopyPublication(text), undefined, text);
    assert.notEqual(userMessagePreviewStance([], text), 'open', text);
  }
});

for (const [label, change] of [
  ['a late owner refusal', run => run.guard.observeRun(run.context, 'pixel', {prompt:COPY_PROMPT + ' Do not publish anything.'})],
  ['a late identity conflict', run => run.guard.observeRun({...run.context,sessionKey:OTHER_KEY}, 'pixel', {prompt:COPY_PROMPT})],
  ['changed owner words', run => run.guard.observeRun(run.context, 'pixel', {prompt:'Publish original-demo/index.html.'})],
  ['a cancellation', run => run.guard.abortUserRun(run.user)],
]) test(`execution policy independently refuses a previously admitted copy after ${label}`, async () => {
  const run = chain(COPY_PROMPT, {directory:COPY_DEST});
  await run.write(`${COPY_DEST}/index.html`, '<!doctype html><title>Copy</title>');
  const name='pixel_ods_workspace_preview',params={relativeDirectory:COPY_DEST},id='admitted-copy';
  const admitted=run.guard.beforeToolCall({toolName:name,params,toolCallId:id},{...run.context,toolName:name,toolCallId:id});
  assert.equal(admitted?.block, undefined);
  await change(run);
  const result=await run.tool.execute(id, params);
  assert.equal(result.isError, true);
  assert.equal(run.log.requests.length, 0);
});
