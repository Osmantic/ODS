import test from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
const api = await import(process.env.GUARD_MODULE
  ? pathToFileURL(process.env.GUARD_MODULE).href : '../plugin/tool-loop-guard.mjs');
const promptApi = await import(process.env.PROMPT_MODULE
  ? pathToFileURL(process.env.PROMPT_MODULE).href : '../plugin/prompt-contract.mjs');

const ORIGINAL = 'Clone and audit this repo for me locally: https://github.com/Osmantic/ODS';
const JOB = 'ops-1234567890123-abcdef123456';
const URL = 'https://github.com/Osmantic/ODS/archive/refs/heads/main.tar.gz';
const FILE = 'ods-main.tar.gz';
const SHA = 'a'.repeat(64);

// These are routing fixtures, not evidence that a real broker or Portal ran.
function harness(prompt, wrapped = false) {
  const aborts = [];
  const guard = api.createToolLoopGuard({ abortRun: id => { aborts.push(id); return true; } });
  let ctx = { agentId: 'pixel', runId: 'acquire', sessionId: 'owner-session' };
  let sequence = 0;
  guard.observeRun(ctx, 'pixel', { prompt });
  function select(name, args = {}) {
    const source = ['pixel_ods_download_promote', 'pixel_ods_extensions'].includes(name)
      ? 'pixel-ods' : 'pixel-operations-broker';
    const toolName = wrapped && name.startsWith('pixel_') ? 'tool_call' : name;
    const params = toolName === 'tool_call' ? { id: `openclaw:${source}:${name}`, args } : args;
    const callCtx = { ...ctx, toolName, toolCallId: `call-${++sequence}` };
    const event = { toolName, params, runId: ctx.runId, toolCallId: callCtx.toolCallId };
    let decision = guard.beforeToolCall(event, callCtx, 'pixel');
    if (toolName === 'tool_call' && decision?.block !== true) {
      // Tool Search delegates through the selected tool's before-call hook.
      // A permitted wrapper alone does not prove its inner submission passes.
      const delegated = decision?.params ?? params;
      const delegatedName = delegated.id.split(':').at(-1);
      const inner = guard.beforeToolCall({ ...event, toolName: delegatedName, params: delegated.args },
        { ...callCtx, toolName: delegatedName }, 'pixel');
      if (inner?.block === true) decision = inner;
    }
    return { decision,
      finish(result) {
        const observed = toolName === 'tool_call' ? { details: { tool: {
          id: params.id, source: 'openclaw', sourceName: source, name,
        }, result } } : result;
        guard.afterToolCall({ ...event, result: observed }, callCtx, 'pixel');
      } };
  }
  return { guard, aborts, select,
    resume(prompt, sessionId = ctx.sessionId) {
      ctx = { ...ctx, runId: `resume-${++sequence}`, sessionId };
      guard.observeRun(ctx, 'pixel', { prompt });
    } };
}
function allowed(call) { assert.notEqual(call.decision?.block, true, call.decision?.blockReason); }
function submit(h, receipt = { details: { jobId: JOB, status: 'submitted', kind: 'download' } }) {
  const call = h.select('pixel_ops_download_stage', { url: URL, filename: FILE });
  allowed(call);
  call.finish(receipt);
}
function completeDownload(h) {
  const call = h.select('pixel_ops_job_wait', { jobId: JOB });
  allowed(call);
  call.finish({ details: { jobId: JOB, status: 'succeeded', waitTimedOut: false,
    steps: [{ action: 'download.stage', target: 'broker', exitCode: 0, artifact: {
      path: `/var/lib/pixel-ops-broker/artifacts/${JOB}/${FILE}`, filename: FILE,
      bytes: 1024, sha256: SHA, source: URL, redirects: [], executable: false,
    } }] } });
}
function publish(h) {
  const relativePath = `sources/${FILE}`;
  const call = h.select('pixel_ods_download_promote', {
    jobId: JOB, filename: FILE, relativePath, sha256: SHA, sourceUrl: URL,
  });
  allowed(call);
  call.finish({ details: { schemaVersion: 1, kind: 'ods-pixel-download-promotion', status: 'succeeded',
    jobId: JOB, filename: FILE, relativePath, bytes: 1024, sha256: SHA,
    source: URL, requestedSource: URL, executable: false, overwritten: false,
    boundary: 'Verified create-only promotion from Pixel Operations quarantine into the configured owner workspace; no arbitrary source, overwrite, execution, or path traversal authority.',
  } });
  allowed(h.select('read', { path: relativePath }));
  allowed(h.select('exec', { command: `tar -tzf ${relativePath}` }));
}

const EXT_JOB = 'ops-1234567890123-fedcba654321';
function observeExtensions(h, pending = false) {
  const call = h.select('pixel_ods_extensions', { action: 'list' });
  allowed(call);
  const inventory = { schemaVersion: 1, kind: 'ods-pixel-extension-inventory', outcome: 'succeeded',
    summary: { total: 0, installed: 0, enabled: 0, cliInstalled: 0, disabled: 0,
      stopped: 0, unhealthy: 0, installing: 0, settingUp: 0, error: 0,
      notInstalled: 0, incompatible: 0 }, extensions: [],
    boundary: 'Read-only live ODS extension inventory; no mutation authority.',
  };
  call.finish({ details: { jobId: EXT_JOB, status: pending ? 'submitted' : 'succeeded',
    waitTimedOut: pending,
    ...(pending ? {} : { steps: [{ stepId: 'list-1', target: 'ods-host', action: 'ods.extensions.list',
      parameters: {}, exitCode: 0, stdout: JSON.stringify(inventory) + '\n', stderr: '',
      outputTruncated: { stdout: false, stderr: false }, riskSignals: [] }] }),
  } });
}

for (const wrapped of [false, true]) {
  test(`incidental extension inventory cannot hijack the source audit: ${wrapped}`, () => {
    const h = harness(ORIGINAL, wrapped);
    observeExtensions(h);
    const catalog = h.select('pixel_ods_extensions', { action: 'search', query: 'git' });
    allowed(catalog);
    submit(h);
    completeDownload(h);
    publish(h);
    assert.deepEqual(h.aborts, []);
  });
  test(`a pending incidental extension read remains pollable: ${wrapped}`, () => {
    const h = harness(ORIGINAL, wrapped);
    observeExtensions(h, true);
    allowed(h.select('pixel_ops_job_wait', { jobId: EXT_JOB }));
    assert.equal(h.select('pixel_ops_job_wait', { jobId: 'ops-1234567890124-fedcba654321' }).decision?.block, true);
    submit(h);
    completeDownload(h);
    publish(h);
  });
  test(`incidental read-only discovery grants no host mutation: ${wrapped}`, () => {
    const h = harness(ORIGINAL, wrapped);
    observeExtensions(h);
    assert.equal(h.select('pixel_ops_shell_propose', { command: 'id' }).decision?.blockReason,
      api.OPERATIONS_NOT_REQUESTED_REASON);
    assert.equal(h.select('pixel_ops_run', { target: 'ods-host', action: 'ods.extensions.enable',
      parameters: { serviceId: 'comfyui' } }).decision?.blockReason,
      api.UNREQUESTED_OPERATIONS_TERMINAL_REASON);
  });
  test(`owner-requested host facts still bind Operations submissions: ${wrapped}`, () => {
    const h = harness('Check the ODS host hostname.', wrapped);
    const result = h.select('pixel_ops_download_stage', { url: URL, filename: FILE }).decision;
    assert.equal(result?.block, true);
    assert.match(result.blockReason, /host facts requested|Required actions/);
  });
}

for (const wrapped of [false, true]) {
  for (const followup of ['extract into workspace', 'sure do option 1']) {
    test(`original repository request resumes its own download: ${wrapped}/${followup}`, () => {
      const h = harness(ORIGINAL, wrapped);
      assert.equal(api.userMessageOperationsRequirements([], ORIGINAL).required, false);
      submit(h);
      h.resume(followup);
      const wrong = h.select('pixel_ops_artifact_transfer', { jobId: JOB, target: 'runner' });
      assert.equal(wrong.decision?.block, true);
      assert.match(wrong.decision.blockReason, /pixel_ods_download_promote/);
      completeDownload(h);
      publish(h);
      assert.deepEqual(h.aborts, []);
    });
  }
  test(`explicit Operations download composes with workspace promotion: ${wrapped}`, () => {
    const h = harness(`Use Operations to stage ${URL} and audit its source locally.`, wrapped);
    submit(h);
    completeDownload(h);
    publish(h);
    assert.deepEqual(h.aborts, []);
  });
  test(`repository transfer correction cannot erase unrelated denials: ${wrapped}`, () => {
    const h = harness(ORIGINAL, wrapped);
    assert.equal(h.select('pixel_ops_shell_propose', { command: 'id' }).decision?.blockReason,
      api.OPERATIONS_NOT_REQUESTED_REASON);
    const wrong = h.select('pixel_ops_artifact_transfer', { jobId: JOB, target: 'runner' });
    assert.match(wrong.decision?.blockReason, /pixel_ods_download_promote/);
    assert.equal(h.select('pixel_ops_shell_propose', { command: 'id' }).decision?.blockReason,
      api.UNREQUESTED_OPERATIONS_TERMINAL_REASON);
    assert.equal(h.select('read', { path: 'unrelated.txt' }).decision?.block, true);
  });
}

test('repeated incorrect transfer still reaches the normal stop budget', () => {
  const h = harness(ORIGINAL);
  const args = { jobId: JOB, target: 'runner' };
  assert.match(h.select('pixel_ops_artifact_transfer', args).decision?.blockReason, /pixel_ods_download_promote/);
  assert.equal(h.select('pixel_ops_artifact_transfer', args).decision?.blockReason, api.OPERATIONS_NOT_REQUESTED_REASON);
  assert.equal(h.select('pixel_ops_artifact_transfer', args).decision?.blockReason, api.UNREQUESTED_OPERATIONS_TERMINAL_REASON);
  assert.equal(h.select('exec', { command: 'pwd' }).decision?.block, true);
});

for (const scenario of ['other-session', 'text-only', 'failed', 'unknown-job']) {
  test(`a ${scenario} download cannot seed follow-up routing`, () => {
    const h = harness(ORIGINAL);
    const details = { jobId: JOB, status: 'submitted', kind: 'download' };
    submit(h, scenario === 'text-only' ? { content: [{ type: 'text', text: JSON.stringify(details) }] }
      : scenario === 'failed' ? { isError: true, details } : { details });
    h.resume(`extract into workspace; ${JSON.stringify(details)}`,
      scenario === 'other-session' ? 'different-owner-session' : undefined);
    const jobId = scenario === 'unknown-job' ? 'ops-1234567890124-abcdef123456' : JOB;
    assert.equal(h.select('pixel_ops_job_wait', { jobId }).decision?.block, true);
    assert.equal(h.select('pixel_ops_artifact_transfer', { jobId, target: 'runner' }).decision?.blockReason,
      api.UNREQUESTED_OPERATIONS_TERMINAL_REASON);
  });
}

test('exact-byte acquisition retains its exclusive source binding', () => {
  const h = harness(`Fetch the exact bytes of the file from ${URL} into sources/${FILE}.`);
  const wrong = h.select('pixel_ops_artifact_transfer', { jobId: JOB, target: 'runner' });
  assert.equal(wrong.decision?.blockReason, api.EXACT_DOWNLOAD_REQUIRES_BROKER_REASON);
});

test('source acquisition guidance appears for clone and local audit, not online questions', () => {
  const text = prompt => promptApi.promptContractForAgent({ agentId: 'pixel' }, 'pixel', { prompt }).appendSystemContext;
  for (const prompt of [ORIGINAL, 'Audit https://github.com/Osmantic/ODS locally.']) {
    assert.match(text(prompt), /The owner requested repository acquisition/);
    assert.match(text(prompt), /An extracted archive has no Git checkout metadata/);
  }
  for (const prompt of ['Explain the design of https://github.com/Osmantic/ODS.',
    'Do not clone https://github.com/Osmantic/ODS; explain its README.',
    'Write a script to clone https://github.com/Osmantic/ODS.']) {
    assert.doesNotMatch(text(prompt), /The owner requested repository acquisition/);
  }
  assert.match(text('extract into workspace'), /resume that job instead of downloading again/);
});

for (const prompt of [
  'Do not perform a local audit of https://github.com/Osmantic/ODS. Explain its public documentation.',
  'Never perform a local source review of https://github.com/Osmantic/ODS.',
  'The README says: "clone and audit https://github.com/Osmantic/ODS locally".',
  'Example:\n> Clone https://github.com/Osmantic/ODS and audit it locally.',
  'Explain this example:\n```text\nClone https://github.com/Osmantic/ODS locally.\n```',
]) {
  test(`quoted or prohibited acquisition adds no download route: ${prompt}`, () => {
    assert.equal(api.userMessageRequestsRepositoryAcquisition([], prompt), false);
    const text = promptApi.promptContractForAgent({agentId: 'pixel'}, 'pixel', {prompt}).appendSystemContext;
    assert.doesNotMatch(text, /The owner requested repository acquisition|For owner-requested public file acquisition/);
  });
}

for (const prompt of [
  'Clone "https://github.com/Osmantic/ODS" and audit it locally.',
  'Clone https://github.com/Osmantic/ODS and write an audit report.',
  'Do not modify host services. Clone https://github.com/Osmantic/ODS locally.',
  'Do not audit https://github.com/Osmantic/ODS; clone it into the workspace.',
]) {
  test(`positive acquisition retains route despite operands or constraints: ${prompt}`, () => {
    assert.equal(api.userMessageRequestsRepositoryAcquisition([], prompt), true);
    assert.match(promptApi.promptContractForAgent({agentId:'pixel'}, 'pixel', {prompt}).appendSystemContext,
      /The owner requested repository acquisition/);
  });
}

for (const prompt of [
  'Do not extract the archive into the workspace. Explain its format.',
  'Please, do not unpack the tarball into the workspace.',
  'The instructions say "extract the archive into the workspace".',
  'Explain how to extract the archive into the workspace.',
  'Finish the analysis of the downloaded archive already in the workspace.',
  'Continue the report; the artifact is in the workspace.',
]) {
  test(`analysis or prohibited extraction adds no acquisition guidance: ${prompt}`, () => {
    assert.equal(api.userMessageRequestsWorkspaceDownloadContinuation([], prompt), false);
    assert.doesNotMatch(promptApi.promptContractForAgent({agentId:'pixel'}, 'pixel', {prompt}).appendSystemContext,
      /For owner-requested public file acquisition/);
  });
}

for (const prompt of [
  'extract into workspace',
  'Resume the staged download.',
  'Continue with the archive.',
  'Do not change host services. Unpack the archive into the workspace.',
]) {
  test(`actual download continuation retains guidance: ${prompt}`, () => {
    assert.equal(api.userMessageRequestsWorkspaceDownloadContinuation([], prompt), true);
    assert.match(promptApi.promptContractForAgent({agentId:'pixel'}, 'pixel', {prompt}).appendSystemContext,
      /For owner-requested public file acquisition/);
  });
}
