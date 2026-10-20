// RTX Spark, Elf of Doom round 9 (Qwen3.8-Flash-Next): the owner said one
// solid colour covers the arena at the end of every frame. The agent found the
// cause (ui.js:748 assigns a canvas to fillStyle, so the last colour fills the
// viewport), showed that its preview check cannot see canvas pixels, and wrote
// the owner that explanation. Its press + assert-painted check had failed, so
// Portal replaced the whole reply with its host delivery and the explanation
// never reached the owner. A failed preview outcome now keeps the agent's
// final prose, then a fixed note (the reply is the agent's own account; the
// request did not pass Portal's checks), the failed step, and the unchanged
// host facts.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import {createHash} from 'node:crypto';
import {createServer} from 'node:http';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {createIngressServer} from '../host/pixel_ingress.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, PAINT_INSPECTION_SCOPE, PRESS_INSPECTION_SCOPE, STEP_INSPECTION_SCOPE, createWorkspacePreviewInspectTool,
  inspectionPlanHash} from '../plugin/workspace-preview-inspect.mjs';
import {PREVIEW_ACCOUNT_NOTE, PREVIEW_CHECK_LABEL, PREVIEW_FAILED_NOTE, PREVIEW_PENDING_NOTE, PREVIEW_STOPPED_NOTE,
  explainedPreviewDelivery, foldedText, inspectionFailureLine, openFence} from '../plugin/preview-explanation.mjs';

const USER = 'ods-' + 'e1f'.repeat(21) + 'e';
const runId = 'chatcmpl_7e1f0d2c-4b8a-4c3e-9d5f-0a1b2c3d4e5f';
const context = {agentId: 'pixel', runId, sessionId: 'elf-session', sessionKey: `agent:pixel:openai-user:${USER}`};
const OWNER = 'Still the same: when I start a realm the whole arena is just one flat colour. Fix it in elf-of-doom, ' +
  "then pull up a fresh preview and actually look at it before you say it's fixed.";
const GAME = '<!doctype html><title>Elf of Doom</title><canvas id="arena" width="960" height="540"></canvas><script src="ui.js"></script>';
// The agent's root-cause reply (paraphrased from the round 9 transcript).
const EXPLANATION = 'Your observation is exactly the diagnostic I needed. The cause is `ui.js:748`: ' +
  '`g.fillStyle = vignetteSprite(); g.fillRect(0, 0, W, H);` runs after the world is drawn. `vignetteSprite()` returns a canvas, ' +
  'and a canvas is not a valid `fillStyle`, so the previous colour stays set and that last full-viewport fill covers the arena: ' +
  'the health-bar green when orcs are on screen, the floor colour otherwise.\n\n' +
  'My preview check reported the same single colour even with the canvas fully blanked, so my tool cannot see canvas pixels at all. ' +
  'Let me use your eyes instead: the fix is to draw the vignette with `drawImage` or drop that fill. I have not changed line 748 yet.';
const FALSE_CLAIM = 'Fixed! The arena renders again and every check passed.';
const FAILED = `${PREVIEW_CHECK_LABEL} ${PREVIEW_ACCOUNT_NOTE} ${PREVIEW_FAILED_NOTE}`;
const PAINT_FAILURE = 'Latest browser inspection of this preview: step 2 (assert-painted) on `#arena` captured one color only (#8fce3a).';
const SCOPE = 'Publication scope: this receipt verifies the published snapshot, not functional behavior or completion of other requested work.';
const PLAN = {viewport: {width: 1280, height: 800}, steps: [
  {action: 'press', locator: {selector: '#arena'}, key: 'Space'},
  {action: 'assert-painted', locator: {selector: '#arena'}},
]};

// The transcript at before_agent_finalize: narrated tool-call turns, then the
// final turn. `terminal` replaces that final assistant message.
const NARRATION = 'Let me read ui.js to find where the arena is drawn.';
function transcript(reply, terminal = {role: 'assistant', content: [{type: 'text', text: reply}], stopReason: 'stop'}) {
  return [{role: 'user', content: OWNER},
    {role: 'assistant', content: [{type: 'text', text: NARRATION}, {type: 'toolCall', id: 'read-ui', name: 'read', arguments: {path: 'elf-of-doom/ui.js'}}],
      stopReason: 'toolUse'},
    {role: 'toolResult', toolCallId: 'read-ui', toolName: 'read', content: [{type: 'text', text: 'g.fillStyle = vignetteSprite();'}]},
    terminal];
}

const shown = {count: 1, visible: true, display: 'block', visibility: 'visible', opacity: '1', hidden: false, hiddenUntilFound: false, rectCount: 1};
function receipt(params, {painted}) {
  const paint = painted
    ? {width: 256, height: 144, distinctColors: 212, dominant: {hex: '#1b2a1f', percent: 41},
      colors: [{name: 'dark gray', hex: '#1b2a1f', percent: 41}, {name: 'green', hex: '#3f7a2c', percent: 22}], fingerprint: '0f1e2d3c4b5a6978'}
    : {width: 256, height: 144, distinctColors: 1, dominant: {hex: '#8fce3a', percent: 100},
      colors: [{name: 'green', hex: '#8fce3a', percent: 100}], fingerprint: '8fce3a8fce3a8fce'};
  return request => ({schemaVersion: 1, kind: INSPECTION_KIND, status: painted ? 'passed' : 'failed',
    siteId: params.siteId, sha256: params.sha256, planSha256: inspectionPlanHash(request), viewport: params.viewport,
    steps: [
      {index: 0, action: 'press', locator: {selector: '#arena'}, key: 'Space', before: shown, after: shown,
        focus: {element: false, document: true}, stable: true, status: 'passed'},
      {index: 1, action: 'assert-painted', locator: {selector: '#arena'}, before: shown, stable: true,
        status: painted ? 'passed' : 'failed', ...(painted ? {} : {errorCode: 'uniform_paint'}), paint},
    ],
    diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [],
    scope: STEP_INSPECTION_SCOPE + PRESS_INSPECTION_SCOPE + PAINT_INSPECTION_SCOPE});
}

// One Elf of Doom turn through the real guard: write, publish, then the
// press + assert-painted check (failed: one colour; or painted), and the
// agent's final reply at finalization when `reply` is a string (with its
// transcript, or `messages` when given).
async function elfTurn({reply, painted = false, before, after, messages, prompt = OWNER, guardOptions = {}, runOptions} = {}) {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, ...guardOptions});
  guard.observeRun({...context, trigger: 'user'}, 'pixel', {prompt}, runOptions);
  let sequence = 0;
  const invoke = (toolName, params, result) => {
    const ctx = {...context, toolName, toolCallId: `call-${sequence++}`};
    const prepared = guard.beforeToolCall({toolName, runId, toolCallId: ctx.toolCallId, params}, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    const actual = prepared?.params ?? params;
    if (result) guard.afterToolCall({toolName, runId, toolCallId: ctx.toolCallId, params: actual, result}, ctx);
    return actual;
  };
  before?.({guard, invoke});
  const written = invoke('write', {path: 'elf-of-doom/index.html', content: GAME},
    {content: [{type: 'text', text: 'Successfully wrote file.'}]});
  const relativeDirectory = written.path.slice(0, -'/index.html'.length);
  const sha256 = createHash('sha256').update(relativeDirectory + GAME).digest('hex'), siteId = `site-${sha256.slice(0, 24)}`;
  const preview = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory, siteId, sha256,
    entryFile: 'index.html', entrySha256: createHash('sha256').update(GAME).digest('hex'), files: 2, bytes: 4096, port: 9437,
    url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true, executable: false, overwritten: false};
  invoke('pixel_ods_workspace_preview', {relativeDirectory}, {details: preview});
  const params = {siteId, sha256, ...structuredClone(PLAN)};
  const capsule = receipt(params, {painted});
  const tool = createWorkspacePreviewInspectTool({request: async request => capsule(request)});
  const prepared = invoke(PREVIEW_INSPECTION_TOOL, params);
  const ctx = {...context, toolName: PREVIEW_INSPECTION_TOOL, toolCallId: `call-${sequence - 1}`};
  const result = await tool.execute(ctx.toolCallId, prepared);
  guard.afterToolCall({toolName: PREVIEW_INSPECTION_TOOL, runId, toolCallId: ctx.toolCallId, params: prepared, result,
    ...(result.isError ? {error: result.content[0].text} : {})}, ctx);
  after?.({guard, invoke, preview});
  const replyText = typeof reply === 'function' ? reply(preview) : reply;
  if (typeof replyText === 'string') guard.beforeAgentFinalize({lastAssistantMessage: replyText, messages: messages === undefined ? transcript(replyText) : messages}, context);
  return {guard, preview, inspection: result, delivery: guard.deliveryVerificationForRun(runId)};
}

test('Elf of Doom: the failed paint check is the real uniform-colour receipt', async () => {
  const {inspection, delivery} = await elfTurn();
  assert.equal(inspection.isError, true);
  assert.equal(inspection.details.status, 'failed');
  assert.match(inspection.content[0].text, /captured one color only \(#8fce3a\)/);
  assert.equal(delivery.status, 'failed');
  assert.match(delivery.text, /^The attempted preview interaction checks have not passed browser inspection\./m);
  assert.doesNotMatch(delivery.text, /Latest browser inspection/, 'the host-only delivery is unchanged');
});

test('Elf of Doom: the explanation reaches the owner, then the fixed note, the failed step and the unchanged host facts', async () => {
  const plain = await elfTurn({reply: ''});
  const {guard, preview, delivery} = await elfTurn({reply: EXPLANATION});
  assert.equal(delivery.status, 'failed', 'keeping the prose never certifies the preview');
  assert.deepEqual(delivery.preview, plain.delivery.preview);
  assert.equal(delivery.text, `${EXPLANATION}\n\n${FAILED}\n\n${PAINT_FAILURE}\n\n${plain.delivery.text}`);
  assert.match(delivery.text, /attempted preview interaction checks have not passed browser inspection/);
  assert.ok(delivery.text.endsWith(`Your preview is ready.\n\n[Open preview](${preview.url})\n\nPublished from your workspace.`));
  assert.equal(Object.keys(delivery).sort().join(), Object.keys(plain.delivery).sort().join(), 'the ingress schema is unchanged');
  // Channel delivery sends the same text in place of the model's reply.
  assert.equal(guard.replyPayloadSending({runId, kind: 'final', payload: {text: EXPLANATION}}).payload.text, delivery.text);
});

for (const stream of [false, true]) test(`Elf of Doom: Portal ingress delivers the kept explanation (stream=${stream})`, {timeout: 10000}, async () => {
  const {delivery} = await elfTurn({reply: EXPLANATION});
  const gateway = createServer((request, response) => {
    request.resume();
    request.on('end', () => {
      response.writeHead(200, {'Content-Type': 'application/json'});
      response.end(JSON.stringify(request.url === '/pixel-ods/subagent-delivery'
        ? {schemaVersion: 1, kind: 'ods-subagent-delivery', runId, status: 'not-delegated'}
        : request.url === '/pixel-ods/verification' ? delivery
          : {id: runId, choices: [{index: 0, message: {role: 'assistant', content: EXPLANATION}, finish_reason: 'stop'}]}));
    });
  });
  const listen = server => new Promise((resolve, reject) => {
    server.once('error', reject); server.listen(0, '127.0.0.1', () => resolve(server.address().port));
  });
  const close = async server => {
    if (!server?.listening) return;
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  };
  let ingress;
  try {
    ingress = createIngressServer({token: 'fixture-only-0123456789abcdef', gatewayPort: await listen(gateway)});
    const port = await listen(ingress);
    const response = await fetch(`http://127.0.0.1:${port}/v1/chat/completions`, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({stream, messages: [{role: 'user', content: OWNER}]}),
      signal: AbortSignal.timeout(8000)});
    const raw = await response.text();
    assert.equal(response.status, 200, raw);
    let content, terminal;
    if (stream) {
      const frames = raw.split('\n').filter(line => line.startsWith('data: {')).map(line => JSON.parse(line.slice(6)));
      terminal = frames.at(-1);
      content = frames.map(frame => frame.choices?.[0]?.delta?.content ?? '').join('');
    } else content = JSON.parse(raw).choices[0].message.content;
    assert.equal(content, delivery.text);
    assert.ok(content.startsWith(`${EXPLANATION}\n\n${FAILED}`));
    if (stream) {
      assert.equal(terminal.pixel_outcome.status, 'failed');
      assert.deepEqual(terminal.pixel.preview, delivery.preview);
    }
  } finally { await close(ingress); await close(gateway); }
});

// The note is the same whatever the prose claims: no wording heuristic can
// miss a claim (quoted, in code, a checklist, negation-shaped, keyword-free).
test('every kept reply, however it claims success, is followed by the same plain failure note', async () => {
  const plain = await elfTurn({reply: ''});
  const recorded = JSON.parse(fs.readFileSync(new URL('./requested-text-tower1-round067.json', import.meta.url), 'utf8')).finalAnswer;
  const claims = [FALSE_CLAIM, '> ✅ Verified: the game works and every check passed.',
    'Inspection log:\n\n```\nassert-painted #arena ..... passed (212 colours)\n```', 'Inspection status: `PASSED`',
    '- ✅ Requirements met\n- ✅ Arena renders\n- ✅ Orcs and floor visible', 'Everything works without any errors now.',
    'I fixed the canvas bug, so the arena is no longer one flat colour.', 'The game is alive and playing.',
    'The flat colour bug is gone.'];
  for (const claim of claims) {
    const {delivery} = await elfTurn({reply: claim});
    assert.equal(delivery.status, 'failed');
    assert.equal(delivery.text, `${claim}\n\n${FAILED}\n\n${PAINT_FAILURE}\n\n${plain.delivery.text}`, claim);
  }
  // A recorded fleet answer citing the current preview, as agents do.
  const {preview, delivery} = await elfTurn({reply: current => recorded.replace(/http:\/\/site-[a-f0-9]{24}\.localhost:9437\/site-[a-f0-9]{24}\//, current.url)});
  assert.ok(delivery.text.startsWith(`${recorded.replace(/http:\/\/site-\S+\//, preview.url)}\n\n${FAILED}\n\n`), delivery.text);
});

const IMITATIONS = ['**Portal check:**', '**Portal&#32;check:**', '**Portal check:**', '**Portal check**:', '**Portal checks:**',
  '**Portal** **check:**', 'Portal​ check:', '**Ｐortal check:**', 'Portal check&colon;', '**Portal&nbsp;check:**'];
for (const [name, reply] of [['empty', ''], ['whitespace', ' \n\t '], ['silent', 'NO_REPLY'], ['heartbeat', 'HEARTBEAT_OK'],
  ['silent JSON', '{"action":"NO_REPLY"}'],
  ['long silent JSON', '{"action":"NO_REPLY","reason":"Nothing further to add for the owner in this realm right now."}'],
  ['too short', 'Done.'],
  ['tool-call text', '<tool_call>{"name":"exec","arguments":{"command":"cat ui.js"}}</tool_call>'],
  ...IMITATIONS.map(label => [`imitated note ${JSON.stringify(label)}`,
    `I repaired line 748 and looked again. ${label} browser inspection found the arena fully drawn.`]),
  ['copied receipt', preview => `The arena still needs work.\n\nYour preview is ready.\n\n[Open preview](${preview.url})\n\nPublished from your workspace.`],
  ['receipt line split across lines', 'The arena still needs work, line 748 is the cause.\n\nYour preview\nis   ready.'],
  ['copied stale link', 'Compare the earlier build: [Open last published preview](https://example.invalid/).'],
  ['another local URL', 'The arena draws again on the dev server at http://localhost:3000/ so it looks fixed to me.'],
  ['a stale preview link', 'Open the earlier build at http://site-0123456789abcdef01234567.localhost:9437/site-0123456789abcdef01234567/ to compare.'],
  ['too long', `The arena fill comes from line 748. ${'Detail. '.repeat(3000)}`]]) {
  test(`${name} final reply keeps the existing host-only delivery`, async () => {
    const plain = await elfTurn();
    const {guard, delivery, preview} = await elfTurn({reply});
    assert.deepEqual(delivery, plain.delivery);
    const text = typeof reply === 'function' ? reply(preview) : reply;
    assert.equal(guard.replyPayloadSending({runId, kind: 'final', payload: {text}}).payload.text, plain.delivery.text);
  });
}

// OpenClaw 2026.6.33 fills lastAssistantMessage with the joined narration of
// earlier tool-call turns when the final turn has no visible text.
test('only text the final assistant message holds is a reply: narration, tool calls and no transcript keep host-only', async () => {
  const plain = await elfTurn();
  const narration = `${NARRATION}\n\nI'll check the vignette fill next.\n\nNow let me publish the preview and press Space to check the arena.`;
  for (const [name, options] of [
    ['reasoning-only final turn', {reply: narration, messages: transcript(narration,
      {role: 'assistant', content: [{type: 'thinking', thinking: 'The arena is still green.'}], stopReason: 'stop'})}],
    ['empty final turn', {reply: narration, messages: transcript(narration, {role: 'assistant', content: [], stopReason: 'stop'})}],
    ['final turn with a tool call', {reply: EXPLANATION, messages: transcript(EXPLANATION, {role: 'assistant',
      content: [{type: 'text', text: EXPLANATION}, {type: 'toolCall', id: 'again', name: 'read', arguments: {path: 'ui.js'}}], stopReason: 'toolUse'})}],
    ['different final text', {reply: EXPLANATION, messages: transcript('The arena is green.')}],
    ['no transcript', {reply: EXPLANATION, messages: null}],
  ]) assert.deepEqual((await elfTurn(options)).delivery, plain.delivery, name);
  // The harness's visible-text extraction is injected (index.js passes
  // OpenClaw's): here it reads only the final-answer block of the last message.
  const terminal = {role: 'assistant', stopReason: 'stop',
    content: [{type: 'text', text: 'Working notes.'}, {type: 'text', text: EXPLANATION}]};
  assert.deepEqual((await elfTurn({reply: EXPLANATION, messages: transcript(EXPLANATION, terminal)})).delivery, plain.delivery);
  const {delivery} = await elfTurn({reply: EXPLANATION, messages: transcript(EXPLANATION, terminal),
    guardOptions: {finalText: message => message.content.at(-1).text}});
  assert.ok(delivery.text.startsWith(`${EXPLANATION}\n\n${FAILED}`));
});

test('a reply that announces more work says the run stopped and the step has not started', async () => {
  const plain = await elfTurn();
  const stopped = `${PREVIEW_CHECK_LABEL} ${PREVIEW_ACCOUNT_NOTE} ${PREVIEW_FAILED_NOTE} ${PREVIEW_STOPPED_NOTE}`;
  for (const reply of ["Found it: ui.js:748 assigns a canvas to fillStyle. I'll fix that line now and republish the preview so you can check it.",
    'The cause is ui.js:748, a canvas assigned to fillStyle.\n\nNow let me republish the preview and press Space to check the arena.']) {
    const {delivery} = await elfTurn({reply});
    assert.equal(delivery.text, `${reply}\n\n${stopped}\n\n${PAINT_FAILURE}\n\n${plain.delivery.text}`);
  }
});

test('a later model call voids the reply seen at finalization (the run continued with tools)', async () => {
  const plain = await elfTurn();
  const {guard} = await elfTurn({reply: EXPLANATION});
  guard.observeModelCall({callId: 'next-round'}, context);
  assert.deepEqual(guard.deliveryVerificationForRun(runId), plain.delivery);
  // The next final reply is captured again.
  guard.beforeAgentFinalize({lastAssistantMessage: EXPLANATION, messages: transcript(EXPLANATION)}, context);
  assert.ok(guard.deliveryVerificationForRun(runId).text.startsWith(`${EXPLANATION}\n\n${FAILED}`));
});

test('the owner cancelling after finalization keeps the host-only delivery', async () => {
  const plain = await elfTurn();
  const {guard} = await elfTurn({reply: EXPLANATION,
    guardOptions: {abortRun: () => true, abortRunAndDrain: async () => ({aborted: true, drained: true})}});
  assert.equal(await guard.abortUserRun(USER), true);
  assert.equal(guard.deliveryVerificationForRun(runId).text, plain.delivery.text);
});

test('the current preview link stays deliverable and an open code block is closed with its own fence', async () => {
  const {preview} = await elfTurn();
  const linked = `The arena is still one colour in [the preview](${preview.url}); line 748 is the cause.`;
  assert.ok((await elfTurn({reply: linked})).delivery.text.startsWith(`${linked}\n\n${FAILED}\n\n`));
  const code = 'g.fillStyle = vignetteSprite();\ng.fillRect(0, 0, W, H);';
  for (const [reply, closing] of [
    [`The last draw call covers the frame:\n\n\`\`\`js\n${code}`, '```'],
    [`Nested sample:\n\n\`\`\`\`markdown\n\`\`\`js\n${code}\n\`\`\``, '````'],
    [`Mixed fences:\n\n\`\`\`markdown\n~~~\n${code}`, '```'],
    [`Tilde block:\n\n~~~~\n${code}\n~~~`, '~~~~'],
    [`Closed block:\n\n\`\`\`js\n${code}\n\`\`\``, ''],
    [`Inline marker:\n\n\`\`\`inline\`\`\` is not a fence, and line 748 is the cause.`, ''],
  ]) {
    const {delivery} = await elfTurn({reply});
    assert.ok(delivery.text.startsWith(`${reply}${closing ? `\n${closing}` : ''}\n\n${FAILED}\n\n`), delivery.text.slice(0, 400));
    assert.equal(openFence(delivery.text), undefined, 'nothing after the reply is inside a code block');
  }
});

test('passing checks keep the existing appended delivery, without repeating the prose', async () => {
  const plain = await elfTurn({painted: true});
  const {guard, delivery} = await elfTurn({painted: true, reply: EXPLANATION});
  assert.equal(plain.delivery.status, 'passed');
  assert.deepEqual(delivery, plain.delivery);
  assert.equal(delivery.deliveryMode, 'append');
  assert.ok(!delivery.text.includes(EXPLANATION));
  assert.equal(guard.replyPayloadSending({runId, kind: 'final', payload: {text: EXPLANATION}}).payload.text,
    `${EXPLANATION}\n\n${delivery.text}\n${SCOPE}`);
});

test('a still-running check keeps the explanation and says the checks have not finished', async () => {
  const pending = ({invoke}) => invoke('exec', {command: 'python3 -m unittest -v', background: true},
    {details: {status: 'running', sessionId: 'elf-check-session'}});
  const plain = await elfTurn({before: pending});
  assert.equal(plain.delivery.status, 'pending');
  const {delivery} = await elfTurn({before: pending, reply: EXPLANATION});
  assert.equal(delivery.status, 'pending');
  assert.equal(delivery.text, `${EXPLANATION}\n\n${PREVIEW_CHECK_LABEL} ${PREVIEW_ACCOUNT_NOTE} ${PREVIEW_PENDING_NOTE}\n\n` +
    `${PAINT_FAILURE}\n\n${plain.delivery.text}`);
  assert.deepEqual(delivery.preview, plain.delivery.preview);
});

test('a stale snapshot keeps the explanation ahead of the last-published-preview note', async () => {
  const stale = ({invoke}) => invoke('exec', {command: 'python3 -c "print(42)"'},
    {details: {status: 'completed', exitCode: 0, aggregated: '42'}});
  const plain = await elfTurn({after: stale});
  assert.match(plain.delivery.text, /^Your last published preview is still available\./);
  const {delivery} = await elfTurn({after: stale, reply: EXPLANATION});
  assert.equal(delivery.status, 'failed');
  // The failed step named an earlier snapshot state, so it is not repeated.
  assert.equal(delivery.text, `${EXPLANATION}\n\n${FAILED}\n\n${plain.delivery.text}`);
  assert.deepEqual(delivery.preview, plain.delivery.preview);
});

test('a newer inspection of the snapshot that has not returned voids the failed-step line', async () => {
  const rerun = ({invoke, preview}) => invoke(PREVIEW_INSPECTION_TOOL, {siteId: preview.siteId, sha256: preview.sha256, ...structuredClone(PLAN)});
  const plain = await elfTurn({after: rerun});
  const {delivery} = await elfTurn({after: rerun, reply: EXPLANATION});
  assert.equal(delivery.text, `${EXPLANATION}\n\n${FAILED}\n\n${plain.delivery.text}`);
});

test('a requested download that was not attached follows the explanation, even over a passing preview', async () => {
  const prompt = 'Fix the arena in elf-of-doom, pull up a fresh preview, and give me the downloadable project.';
  const plain = await elfTurn({painted: true, prompt});
  assert.equal(plain.delivery.status, 'failed');
  assert.match(plain.delivery.text, /^The requested download was not attached/);
  const {delivery} = await elfTurn({painted: true, prompt, reply: EXPLANATION});
  assert.equal(delivery.text, `${EXPLANATION}\n\n${FAILED}\n\n${plain.delivery.text}`);
});

test('a stopped extension lane over a passing preview keeps the explanation ahead of both results', async () => {
  const prompt = '/extensions install https://github.com/example/project; create /workspace/elf-of-doom/index.html and show its preview.';
  const extensionFailures = ({guard}) => {
    for (let i = 0; i < 4; i++) guard.afterToolCall({toolName: 'pixel_ods_extension_request_prepare', toolCallId: `prepare-${i}`,
      params: {}, result: {isError: true}}, context);
  };
  const runOptions = {executionHost: 'sandbox'};
  const plain = await elfTurn({painted: true, prompt, before: extensionFailures, runOptions});
  assert.equal(plain.delivery.status, 'failed');
  assert.match(plain.delivery.text, /extension portion.*stopped/);
  const {delivery} = await elfTurn({painted: true, prompt, before: extensionFailures, runOptions, reply: EXPLANATION});
  assert.equal(delivery.text, `${EXPLANATION}\n\n${FAILED}\n\n${plain.delivery.text}`);
});

test('an unpublished site keeps the explanation and still forbids local links', () => {
  const turn = reply => {
    const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true});
    guard.observeRun(context, 'pixel', {prompt: 'Build the Elf of Doom game in elf-of-doom and publish a preview.'});
    const ctx = {...context, toolName: 'write', toolCallId: 'write'};
    const prepared = guard.beforeToolCall({toolName: 'write', runId, toolCallId: 'write', params: {path: 'elf-of-doom/index.html', content: GAME}}, ctx);
    guard.afterToolCall({toolName: 'write', runId, toolCallId: 'write', params: prepared?.params ?? {path: 'elf-of-doom/index.html', content: GAME},
      result: {content: [{type: 'text', text: 'Successfully wrote file.'}]}}, ctx);
    if (reply !== undefined) guard.beforeAgentFinalize({lastAssistantMessage: reply, messages: transcript(reply)}, context);
    return guard.deliveryVerificationForRun(runId);
  };
  const plain = turn();
  assert.equal(plain.status, 'failed');
  assert.equal(plain.preview, undefined);
  const reason = 'The publish step was refused because the .harness folder in the game directory is not allowed in a preview.';
  assert.equal(turn(reason).text, `${reason}\n\n${FAILED}\n\n${plain.text}`);
  assert.deepEqual(turn('Your game is live at http://localhost:8080/ and works.'), plain);
});

test('the failed-step line reads only bounded receipt fields, bound to the snapshot', () => {
  const preview = {siteId: `site-${'a'.repeat(24)}`, sha256: 'a'.repeat(64)};
  const failed = (step, extra = {}) => ({status: 'failed', siteId: preview.siteId, sha256: preview.sha256,
    steps: [{index: 0, action: 'press', status: 'passed'}, {index: 1, status: 'failed', ...step}], ...extra});
  assert.equal(inspectionFailureLine(failed({action: 'assert-painted', locator: {selector: '#arena'}, errorCode: 'uniform_paint',
    paint: {dominant: {hex: '#8fce3a'}}}), preview), PAINT_FAILURE);
  assert.equal(inspectionFailureLine(failed({action: 'click', locator: {role: 'button', name: 'Start `realm`', exact: true},
    errorCode: 'no_match'}), preview),
    'Latest browser inspection of this preview: step 2 (click) on the button named `` Start `realm` `` matched no element.');
  assert.equal(inspectionFailureLine(failed({action: 'assert-text', locator: {selector: '#score'}, errorCode: 'constructor'}), preview),
    'Latest browser inspection of this preview: step 2 (assert-text) on `#score` did not pass (constructor).');
  assert.equal(inspectionFailureLine(failed({action: 'assert-painted', locator: {selector: '#arena'}, errorCode: 'uniform_paint',
    paint: {dominant: {hex: 'url(javascript:alert(1))'}}}), preview),
    'Latest browser inspection of this preview: step 2 (assert-painted) on `#arena` did not pass (uniform_paint).');
  assert.equal(inspectionFailureLine({status: 'failed', siteId: preview.siteId, sha256: preview.sha256, errorCode: 'timeout'}, preview),
    'Latest browser inspection of this preview did not complete (timeout).');
  for (const details of [failed({action: 'click'}, {status: 'passed'}), failed({action: 'click'}, {sha256: 'b'.repeat(64)}),
    failed({action: 'click', index: 'x'}), {status: 'failed', errorCode: 'invalid_request'}, undefined])
    assert.equal(inspectionFailureLine(details, preview), undefined);
  assert.equal(inspectionFailureLine(failed({action: 'click'}), undefined), undefined);
});

test('folded text sees the label however markdown or HTML draws it; fences follow CommonMark', () => {
  for (const label of IMITATIONS) assert.match(foldedText(label), /portal[\s\p{P}]{0,3}checks?\b/iu, label);
  assert.equal(foldedText("Portal's checks"), "Portal's checks");
  assert.equal(openFence('```js\ncode'), '```');
  assert.equal(openFence('````\n```\ncode\n```'), '````');
  assert.equal(openFence('```\n~~~\ncode'), '```');
  assert.equal(openFence('~~~~\ncode\n~~~'), '~~~~');
  assert.equal(openFence('```js\ncode\n```'), undefined);
  assert.equal(openFence('```inline``` text'), undefined);
  assert.equal(openFence('    ```\ncode'), undefined, 'an indented line is code, not a fence');
});

test('only failed or pending outcomes with host text are explained, within the ingress bound, in fixed English', () => {
  for (const result of [{status: 'passed', text: 'Ready.'}, {status: 'none'}, {status: 'failed'}, undefined])
    assert.equal(explainedPreviewDelivery(result, {reply: EXPLANATION}), result);
  const failed = {status: 'failed', text: 'The browser check failed.'};
  assert.deepEqual(explainedPreviewDelivery(failed, {reply: EXPLANATION}), {status: 'failed',
    text: `${EXPLANATION}\n\n${FAILED}\n\nThe browser check failed.`});
  assert.deepEqual(explainedPreviewDelivery(failed, {reply: EXPLANATION, inspection: PAINT_FAILURE}), {status: 'failed',
    text: `${EXPLANATION}\n\n${FAILED}\n\n${PAINT_FAILURE}\n\nThe browser check failed.`});
  assert.equal(explainedPreviewDelivery({status: 'pending', text: 'Still running.'}, {reply: EXPLANATION}).text,
    `${EXPLANATION}\n\n${PREVIEW_CHECK_LABEL} ${PREVIEW_ACCOUNT_NOTE} ${PREVIEW_PENDING_NOTE}\n\nStill running.`);
  assert.equal(failed.text, 'The browser check failed.', 'the host result is not mutated');
  assert.equal(explainedPreviewDelivery(failed), failed);
  const long = {status: 'failed', text: 'x'.repeat(12000)};
  assert.equal(explainedPreviewDelivery(long, {reply: `${'The arena fill comes from line 748. '.repeat(400)}`}), long);
  for (const fixed of [PREVIEW_CHECK_LABEL, PREVIEW_ACCOUNT_NOTE, PREVIEW_FAILED_NOTE, PREVIEW_PENDING_NOTE, PREVIEW_STOPPED_NOTE])
    assert.match(fixed, /^[\x20-\x7e]+$/);
});
