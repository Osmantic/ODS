// Candidate capability. Registration requires an authenticated controller
// transport; there is deliberately no shell fallback or implicit permission.
import {setTimeout as delay} from 'node:timers/promises';
import {capabilityToolResult, capabilityUnavailable} from './project-capabilities.mjs';
import {validateDiagnostic} from './project-diagnostics.mjs';
const JOB = /^ods-project-[a-f0-9]{24}$/;
const PATH = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;
const STATES = new Set(['queued', 'running', 'succeeded', 'failed', 'cancelled', 'unconfirmed']);
class ProjectRequestError extends Error {
  constructor(field, hint) { super('invalid project request'); this.field=field; this.hint=hint; }
}
const invalid = (field, hint) => { throw new ProjectRequestError(field, hint); };

export function normalizeProjectBuild(params) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) throw Error('invalid request');
  const keys = Object.keys(params).sort().join(',');
  if (params.action === 'capabilities') {
    if (keys !== 'action,runtime' || params.runtime !== 'python') throw Error('invalid capability query');
  } else if (params.action === 'diagnose') {
    if (keys !== 'action,runtime' || !['npm','python'].includes(params.runtime)) throw Error('invalid diagnostic request');
  } else if (params.action === 'submit') {
    if (Object.hasOwn(params, 'runtime')) invalid('runtime', 'Omit runtime on submit: npm or Python is inferred from the project manifests.');
    if (keys !== 'action,outputDirectory,project') invalid('fields', 'Submit accepts only action, project and outputDirectory.');
    if (typeof params.project !== 'string'
        || params.project.length > 1024 || params.project.split('/').length > 8
        || !params.project.split('/').every(part => PATH.test(part) && !['.', '..'].includes(part)))
      invalid('project', 'Use a relative project directory inside the workspace, such as Playground/my-site.');
    if (typeof params.outputDirectory !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(params.outputDirectory))
      invalid('outputDirectory', 'Use only the output directory basename within the project, such as dist or out, not the project path.');
  } else if (!['observe', 'cancel'].includes(params.action) || keys !== 'action,jobId'
             || typeof params.jobId !== 'string' || !JOB.test(params.jobId)) throw Error('invalid observation');
  return {schemaVersion: 1, ...params};
}

export function projectInvalidRequest(params) {
  try { normalizeProjectBuild(params); return undefined; }
  catch (error) {
    const details={schemaVersion:1,kind:'ods-project-job',status:'invalid-request',code:'invalid-request',
      field:error instanceof ProjectRequestError ? error.field : 'request',
      hint:error instanceof ProjectRequestError ? error.hint : 'Use the exact fields for the selected action from tool_describe.',
      executionStarted:false,nextAction:{code:'correct-request',automaticRetry:false}};
    return {isError:true,content:[{type:'text',text:JSON.stringify(details)}],details};
  }
}

function validateReceipt(value, params) {
  const diagnostic=value?.purpose==='diagnostic';
  if (!value || value.schemaVersion !== 1 || value.kind !== 'ods-project-job'
      || typeof value.jobId !== 'string' || !JOB.test(value.jobId) || !STATES.has(value.status)
      || (diagnostic ? value.project!==null || value.scope!=='managed-executor' || !['npm','python'].includes(value.runtime) : typeof value.project !== 'string')
      || typeof value.cancelRequested !== 'boolean'
      || !Array.isArray(value.steps) || value.steps.length > 3
      || (params.action === 'submit' ? diagnostic || value.project !== params.project
        : params.action === 'diagnose' ? !diagnostic || value.runtime!==params.runtime : value.jobId !== params.jobId)) {
    throw Error('unconfirmed controller response');
  }
  if(diagnostic) {
    if(value.steps.length>1 || value.steps.some(step=>step.stage!=='diagnose')) throw Error('invalid diagnostic stages');
    if(value.output?.kind==='ods-project-diagnostic') validateDiagnostic(value.output,value.runtime);
    if(value.status==='succeeded' && (value.steps.length!==1 || value.steps[0].status!=='succeeded'
      || value.steps[0].exitCode!==0 || value.output?.kind!=='ods-project-diagnostic')) throw Error('incomplete diagnostic evidence');
    return value;
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
    description: 'Managed npm/Python jobs; no host shell fallback. Diagnose with {action:"diagnose",runtime:"python"} (or npm): fixed offline tool/venv/scratch checks, automatic owned cleanup, structured nextAction. This checks the executor, not the chat sandbox. For Python wheel hashes first query {action:"capabilities",runtime:"python"}; use actual installed ABI/tags, never guessed host compatibility. Submit exactly {action:"submit",project:"Playground/my-site",outputDirectory:"dist"}; runtime is inferred from manifests, so omit runtime on submit. outputDirectory is a basename inside that project (dist or out), never a full path: npm needs package.json + matching package-lock.json; Python needs ods-project.json runtime python, exact/hash-pinned requirements.lock (all transitive public wheels), main.py and unittest tests/test_*.py. Empty Python lock permits stdlib. Acquire precedes offline tests/build. Observe jobId until terminal; never resubmit unknown outcomes. Cancelled confirms Stop. Output files are not a published site; publish/inspect separately. Preserve requested frameworks and report denied, missing or incompatible resources.',
    parameters: {type: 'object', additionalProperties: false, required: ['action'], properties: {
      action: {type: 'string', enum: ['capabilities', 'diagnose', 'submit', 'observe', 'cancel']},
      runtime: {type: 'string', enum: ['npm', 'python'],description:'Only for diagnose/capabilities. Omit for submit: manifests determine the runtime.'},
      project: {type: 'string',description:'Workspace-relative project directory, for example Playground/my-site.'},
      outputDirectory: {type: 'string',pattern:'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$',description:'Submit only: basename inside the project, for example dist or out. Never include the project path.'},
      jobId: {type: 'string'},
    }},
    execute: async (toolCallId, params, signal) => {
      let diagnosticSubmitted=false;
      try {
        signal?.throwIfAborted();
        if(params?.action==='diagnose' && Object.keys(params).sort().join(',')==='action,runtime'
            && typeof params.runtime==='string' && !['npm','python'].includes(params.runtime)) {
          const receipt={schemaVersion:1,kind:'ods-project-diagnostic',scope:'managed-executor',code:'unsupported',
            supportedRuntimes:['npm','python'],nextAction:{code:'choose-supported-runtime'}};
          return {isError:true,content:[{type:'text',text:JSON.stringify(receipt)}],details:receipt};
        }
        const invalidRequest = projectInvalidRequest(params);
        if (invalidRequest) return invalidRequest;
        const normalized = normalizeProjectBuild(params);
        const deadline = now() + 240000;
        diagnosticSubmitted=normalized.action==='diagnose';
        let raw = await request(normalized, {toolCallId, signal});
        if (normalized.action === 'capabilities') return capabilityToolResult(tool, raw, capabilityMaxChars);
        if (normalized.action === 'diagnose' && raw?.kind==='ods-project-diagnostic') {
          const receipt=validateDiagnostic(raw,normalized.runtime);
          return {isError:receipt.code!=='ready',content:[{type:'text',text:JSON.stringify(receipt)}],details:receipt};
        }
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
          const details=normalized.action==='diagnose'?{...raw,code:raw.status,scope:'managed-executor',runtime:normalized.runtime,
            nextAction:{code:raw.status==='denied'?'review-portal-permissions':'correct-request',automaticInstall:false}}:raw;
          if(normalized.action==='diagnose') return {isError:true,content:[{type:'text',text:JSON.stringify(details)}],details};
          return {isError: true, content: [{type: 'text', text: raw.status === 'denied'
            ? 'Project operation denied by the controller. Review Portal permissions; do not bypass them.'
            : 'Project request rejected as invalid. Correct the parameters before trying again.'}], details};
        }
        const receipt = validateReceipt(raw, normalized);
        return {isError: ['failed', 'unconfirmed'].includes(receipt.status)
            || (receipt.purpose==='diagnostic' && receipt.output?.code
              && (receipt.output.code!=='ready' || receipt.output.cleanup==='unconfirmed')),
          content: [{type: 'text', text: JSON.stringify(receipt)}], details: receipt};
      } catch {
        if (params?.action === 'capabilities') return capabilityUnavailable();
        if(params?.action==='diagnose') {
          const receipt={schemaVersion:1,kind:'ods-project-job',scope:'managed-executor',
            status:diagnosticSubmitted?'unconfirmed':'invalid-request',code:diagnosticSubmitted?'unavailable':'invalid-request',
            nextAction:{code:diagnosticSubmitted?'recover-unknown-job':'correct-request',automaticRetry:false},
            message:diagnosticSubmitted?'Execution outcome is unknown; do not resubmit automatically.':'Use only action diagnose and runtime npm or python.'};
          return {isError:true,content:[{type:'text',text:JSON.stringify(receipt)}],details:receipt};
        }
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
