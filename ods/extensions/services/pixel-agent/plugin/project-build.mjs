// Candidate capability. Registration requires an authenticated controller
// transport; there is deliberately no shell fallback or implicit permission.
import {setTimeout as delay} from 'node:timers/promises';
import {capabilityToolResult, capabilityUnavailable} from './project-capabilities.mjs';
const JOB = /^ods-project-[a-f0-9]{24}$/;
const PATH = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const STATES = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled', 'unconfirmed']);

export function normalizeProjectBuild(params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) throw Error('invalid request');
  const keys = Object.keys(params).sort().join(',');
  if (params.action === 'capabilities') {
    if (keys !== 'action,runtime' || params.runtime !== 'python') throw Error('invalid capability query');
  } else if (params.action === 'submit') {
    if (keys !== 'action,outputDirectory,project' || typeof params.project !== 'string'
        || params.project.length > 1024 || params.project.split('/').length > 8
        || !params.project.split('/').every(part => PATH.test(part) && !['.', '..'].includes(part))
        || typeof params.outputDirectory !== 'string'
        || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(params.outputDirectory)) throw Error('invalid submission');
  } else if (!['observe', 'cancel'].includes(params.action) || keys !== 'action,jobId'
             || typeof params.jobId !== 'string' || !JOB.test(params.jobId)) throw Error('invalid observation');
  return {schemaVersion: 1, ...params};
}

function validateReceipt(value, params) {
  if (!value || value.schemaVersion !== 1 || value.kind !== 'ods-project-job'
      || typeof value.jobId !== 'string' || !JOB.test(value.jobId) || !STATES.has(value.status)
      || typeof value.project !== 'string' || typeof value.cancelRequested !== 'boolean'
      || !Array.isArray(value.steps) || value.steps.length > 3
      || (params.action === 'submit' ? value.project !== params.project : value.jobId !== params.jobId)) {
    throw Error('unconfirmed controller response');
  }
  if (value.status === 'succeeded') {
    if (value.steps.length !== 3 || value.steps.some((step, i) =>
      step.stage !== ['acquire', 'test', 'build'][i] || step.status !== 'succeeded' || step.exitCode !== 0)
      || !value.output || !/^[a-f0-9]{64}$/.test(value.output.sha256)
      || !Number.isInteger(value.output.files) || value.output.files < 1 || value.output.files > 128
      || value.output.relativeDirectory !== `${value.project}/ods-builds/${value.jobId.slice(12)}/site`) {
      throw Error('incomplete build evidence');
    }
  }
  return value;
}

export function createProjectBuildTool({request, wait = (ms, signal) => delay(ms, undefined, {signal}), now = () => performance.now(), capabilityMaxChars = 4000} = {}) {
  if (typeof request !== 'function') throw Error('authenticated project transport required');
  const tool = {
    name: 'pixel_ods_project_build',
    description: 'Managed dependency acquisition, tests and build; no host install. First call {action:capabilities,runtime:python} before choosing Python wheel hashes: complete ABI/tags describe only the installed executor image, never host architecture or universal portability. Query grants no execution permission. Submit requires project and outputDirectory (e.g. out). npm: package.json + matching package-lock.json; npm test then npm run build. Python 3.11: ods-project.json exactly {"runtime":"python"}; requirements.lock with exact package==version and verified --hash=sha256: wheel hashes, including all transitive dependencies; empty lock supports stdlib. Require main.py and passing tests/test_*.py unittest tests. Public PyPI wheels only; no URLs, sdists, editable or unpinned installs. Acquisition precedes project code; tests/build are offline, nonroot. Observe jobId until terminal (waits up to four minutes); never resubmit an unknown outcome. Cancellation is confirmed only by cancelled status. Output is generated files, not a published site; deliver files or publish/inspect separately. Preserve the requested framework and report real failures; never substitute a static mock.',
    parameters: {type: 'object', additionalProperties: false, required: ['action'], properties: {
      action: {type: 'string', enum: ['capabilities', 'submit', 'observe', 'cancel']},
      runtime: {type: 'string', enum: ['python']},
      project: {type: 'string'}, outputDirectory: {type: 'string'}, jobId: {type: 'string'},
    }},
    execute: async (toolCallId, params, signal) => {
      try {
        signal?.throwIfAborted();
        const normalized = normalizeProjectBuild(params);
        const deadline = now() + 240000;
        let raw = await request(normalized, {toolCallId, signal});
        if (normalized.action === 'capabilities') return capabilityToolResult(tool, raw, capabilityMaxChars);
        // Pace read-only observations inside one tool call. This leaves the
        // controller socket free for cancellation between requests and avoids
        // spending model turns on identical instantaneous running receipts.
        // Never retry submission, unknown responses or transport failures.
        if (normalized.action === 'observe') {
          for (let poll = 0; poll < 48 && ['queued','running'].includes(raw?.status); poll++) {
            validateReceipt(raw, normalized);
            const remaining = deadline - now();
            if (remaining <= 0) break;
            await wait(Math.min(5000, remaining), signal);
            signal?.throwIfAborted();
            if (now() >= deadline) break;
            raw = await request(normalized, {toolCallId, signal});
          }
        }
        if (raw?.schemaVersion === 1 && raw.kind === 'ods-project-job'
            && ['denied', 'invalid-request'].includes(raw.status)
            && Object.keys(raw).sort().join(',') === 'kind,schemaVersion,status') {
          return {isError: true, content: [{type: 'text', text: raw.status === 'denied'
            ? 'Project operation denied by the controller. Review Portal permissions; do not bypass them.'
            : 'Project request rejected as invalid. Correct the parameters before trying again.'}], details: raw};
        }
        const receipt = validateReceipt(raw, normalized);
        return {isError: ['failed', 'unconfirmed'].includes(receipt.status),
          content: [{type: 'text', text: JSON.stringify(receipt)}], details: receipt};
      } catch {
        if (params?.action === 'capabilities') return capabilityUnavailable();
        // A lost response or abort is not proof that the accepted job stopped.
        const receipt = {schemaVersion: 1, kind: 'ods-project-job', status: 'unconfirmed',
          ...(JOB.test(params?.jobId ?? '') ? {jobId: params.jobId} : {}),
          message: 'No confirmed result. Do not resubmit automatically; recover the accepted job before continuing.'};
        return {isError: true, content: [{type: 'text', text: receipt.message}], details: receipt};
      }
    },
  };
  return tool;
}
