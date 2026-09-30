// Candidate capability. Registration requires an authenticated controller
// transport; there is deliberately no shell fallback or implicit permission.
import {setTimeout as delay} from 'node:timers/promises';
const JOB = /^ods-project-[a-f0-9]{24}$/;
const PATH = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const STATES = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled', 'unconfirmed']);

export function normalizeProjectBuild(params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) throw Error('invalid request');
  const keys = Object.keys(params).sort().join(',');
  if (params.action === 'submit') {
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

export function createProjectBuildTool({request, wait = (ms, signal) => delay(ms, undefined, {signal}), now = () => performance.now()} = {}) {
  if (typeof request !== 'function') throw Error('authenticated project transport required');
  return {
    name: 'pixel_ods_project_build',
    description: 'Acquire locked npm dependencies, run the existing project tests and build in an isolated managed job. Submit an existing workspace project with package.json and package-lock.json and its output directory (out or dist). Observe the returned jobId until terminal; observe waits for up to four minutes and returns early on completion. Use observe directly, not shell sleep commands. Never resubmit an unknown outcome. Cancellation is requested, not confirmed, until the returned state says cancelled. Successful output is a generated workspace directory, not a published site: publish and inspect it separately. Preserve the project framework and report actual failures; do not replace a failing project with a static mock.',
    parameters: {type: 'object', additionalProperties: false, required: ['action'], properties: {
      action: {type: 'string', enum: ['submit', 'observe', 'cancel']},
      project: {type: 'string'}, outputDirectory: {type: 'string'}, jobId: {type: 'string'},
    }},
    execute: async (toolCallId, params, signal) => {
      try {
        signal?.throwIfAborted();
        const normalized = normalizeProjectBuild(params);
        const deadline = now() + 240000;
        let raw = await request(normalized, {toolCallId, signal});
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
        // A lost response or abort is not proof that the accepted job stopped.
        const receipt = {schemaVersion: 1, kind: 'ods-project-job', status: 'unconfirmed',
          ...(JOB.test(params?.jobId ?? '') ? {jobId: params.jobId} : {}),
          message: 'No confirmed result. Do not resubmit automatically; recover the accepted job before continuing.'};
        return {isError: true, content: [{type: 'text', text: receipt.message}], details: receipt};
      }
    },
  };
}
