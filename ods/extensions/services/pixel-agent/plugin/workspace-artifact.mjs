import {isDeepStrictEqual} from 'node:util';
import {socketRequest} from './workspace-preview.mjs';
import {dockerWorkspacePreviewRequest} from './workspace-preview-docker.mjs';

export const ARTIFACT_TOOL = 'pixel_ods_workspace_artifact';
export const ARTIFACT_BOUNDARY = 'Create-only single-file snapshot from the configured Pixel workspace; byte integrity only, no execution or document-quality claim.';
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join(',') === keys.split(',').sort().join(',');
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const component = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(value);
const safePath = value => typeof value === 'string' && value.length >= 1 && value.length <= 512 &&
  value.split('/').length <= 12 && value.split('/').every(component);
const allowed = value => /\.(?:md|markdown|txt|csv|tsv|json|pdf|zip|rar|docx|xlsx|pptx)$/i.test(value);
export function normalizeWorkspaceArtifact(value) {
  if (!exact(value,'relativePath') || !safePath(value.relativePath) || !allowed(value.relativePath)) throw new Error('invalid-artifact-path');
  return {schemaVersion:1,action:'publish-artifact',relativePath:value.relativePath};
}
export function validDeliveredArtifact(value) {
  return Boolean(exact(value,'schemaVersion,kind,relativePath,siteId,sha256,file') && value.schemaVersion === 1 &&
    value.kind === 'ods-pixel-workspace-artifact' && safePath(value.relativePath) && allowed(value.relativePath) &&
    sha(value.sha256) && value.siteId === `site-${value.sha256.slice(0,24)}` &&
    exact(value.file,'path,bytes,sha256') && component(value.file.path) &&
    value.file.path === value.relativePath.split('/').at(-1) && Number.isSafeInteger(value.file.bytes) &&
    value.file.bytes >= 0 && value.file.bytes <= 4*1024*1024 && sha(value.file.sha256));
}
export function validDeliveredArtifacts(value) {
  return Array.isArray(value) && value.length <= 4 && value.every(validDeliveredArtifact) &&
    new Set(value.map(item=>`${item.siteId}/${item.file.path}`)).size === value.length;
}
export function artifactReceipt(value, request) {
  if (!exact(value,'schemaVersion,kind,status,relativePath,siteId,sha256,file,httpStatus,readbackVerified,executable,overwritten,boundary') ||
    value.status !== 'succeeded' || value.relativePath !== request.relativePath || value.httpStatus !== 200 ||
    value.readbackVerified !== true || value.executable !== false || value.overwritten !== false || value.boundary !== ARTIFACT_BOUNDARY) throw new Error('unverified-artifact');
  const {schemaVersion,kind,relativePath,siteId,sha256,file} = value;
  const result = {schemaVersion,kind,relativePath,siteId,sha256,file};
  if (!validDeliveredArtifact(result)) throw new Error('unverified-artifact');
  return structuredClone(result);
}

// Only a tool call admitted by the actual policy hooks may reach the broker.
// A factory context or a MEDIA line cannot supply admission or a receipt.
export function createWorkspaceArtifactAdmission() {
  const pending = new Map();
  return {
    before(event, context, decision) {
      const id=context?.toolCallId ?? event?.toolCallId;
      if (id) pending.delete(id);
      if (decision?.block || context?.agentId !== 'pixel' || !context.runId || !context.sessionId || !context.sessionKey || !id) return;
      const params=decision?.params ?? event?.params;
      const direct=event.toolName === ARTIFACT_TOOL;
      const deferred=event.toolName === 'tool_call' && [ARTIFACT_TOOL,'openclaw:pixel-ods:'+ARTIFACT_TOOL].includes(params?.id);
      if (!direct && !deferred) return;
      try {
        while (pending.size >= 128) pending.delete(pending.keys().next().value);
        pending.set(id,{scope:{...context},request:normalizeWorkspaceArtifact(deferred ? params.args : params),deferred});
      } catch { /* Invalid inputs never receive admission. */ }
    },
    take(id, request, context) {
      let entry=pending.get(id);
      if (!entry) {
        const matches=[...pending].filter(([parentId,item])=>{
          const parent=parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g,'_').slice(0,120) || 'call';
          const prefix=`tool_search_code:${parent}:${ARTIFACT_TOOL}:`;
          return item.deferred && id.startsWith(prefix) && /^[1-9][0-9]*$/.test(id.slice(prefix.length));
        });
        if (matches.length === 1) entry=matches[0][1];
      }
      if (!entry || entry.used || !isDeepStrictEqual(entry.request,request) ||
        ['agentId','sessionId','sessionKey'].some(key=>entry.scope[key] !== context?.[key])) throw new Error('artifact-call-unbound');
      entry.used=true;
      return {...entry.scope};
    },
    after(event,context) { pending.delete(context?.toolCallId ?? event?.toolCallId); },
  };
}

export function createWorkspaceArtifactTool(context, {admission,reserve,accept,unavailableReason,request,transport='unix'}={}) {
  if (!['unix','docker-desktop'].includes(transport)) throw new Error('invalid-artifact-transport');
  request ??= transport === 'docker-desktop' ? dockerWorkspacePreviewRequest : socketRequest;
  return {
    name:ARTIFACT_TOOL,
    description:'For an ordinary owner-interactive Portal chat turn only (not teams, subagents or background goals), deliver one owner-requested document or archive already created in the Pixel workspace, without creating a website or opening its preview. Required argument: relativePath, for example {"relativePath":"Playground/report.pdf"}. Use the exact existing source relativePath directly: no staging copy or bundle is needed unless the owner explicitly requests copies. The argument name path is not supported. Discover the exact schema with tool_describe if needed. Accepted formats are Markdown, TXT, CSV, TSV, JSON, PDF, ZIP, RAR, DOCX, XLSX and PPTX, at most 4 MiB each and four publication attempts per response. Returns a verified immutable download receipt; the Portal supplies the download control. Do not emit MEDIA paths or invent URLs. Publishing verifies bytes, not document rendering, archive integrity or correctness; perform those checks separately. No arbitrary host files, directories, dependencies, execution or network destinations.',
    parameters:{type:'object',additionalProperties:false,required:['relativePath'],properties:{relativePath:{type:'string',minLength:1,maxLength:512}}},
    async execute(id,params,signal) {
      const fail=(code,text,status='failed')=>({isError:true,details:{status,kind:'ods-pixel-workspace-artifact',code},content:[{type:'text',text}]});
      let payload;
      try {
        payload=normalizeWorkspaceArtifact(params);
      } catch {
        return fail('invalid-arguments','No document publication was attempted: invalid arguments. Use exactly {"relativePath":"Playground/report.pdf"}, replacing only the example value with the actual workspace-relative file. The argument is relativePath, not path; no other fields are accepted. Call tool_describe with id "pixel_ods_workspace_artifact" for its exact schema, then retry with the corrected arguments. Paths must use 1 to 12 slash-separated ASCII components (letters, digits, dot, underscore or hyphen), start each component with a letter or digit, and stay within 512 characters and 128 per component. Use a supported document extension. This argument rejection says nothing about file permissions or broker availability.');
      }
      let scope;
      try {
        scope=admission.take(id,payload,context);
      } catch {
        return fail('admission-unavailable','No document publication was attempted: this call has no matching active policy admission. Use a fresh normal tool call in the current owner chat; do not reuse a previous call identity, bypass policy or change file permissions. Preserve the workspace file.');
      }
      if (signal?.aborted) return fail('publication-cancelled','The document call was cancelled before publication. No broker request was sent. Preserve the workspace file.');
      if (!reserve(scope)) {
        const reasons={
          'run-identity-conflict':'The runtime reported conflicting session identities for this run; document delivery is refused without transferring authority to another session.',
          'trigger-unavailable':'The runtime did not identify this turn as owner-interactive. This is a runtime integration limitation, not an exhausted publication limit.',
          'noninteractive-turn':'This is a background or other noninteractive turn; document cards require an ordinary owner chat turn.',
          'owner-session-required':'This session is not the ordinary owner Portal chat surface.',
          'team-surface-unsupported':'Team workers cannot attach documents through this surface yet. Hand the exact workspace-relative path to the owner chat.',
          'run-cancelled':'The owner cancelled this run.',
          'run-ended':'This run has already ended.',
          'run-superseded':'A newer run or different session superseded this call.',
          'progress-budget-exhausted':'The current run has reached its overall progress budget.',
          'owner-question-pending':'The current run is waiting for the owner to answer a question.',
          'publication-attempt-limit':'This run has already reserved four document publication attempts.',
        };
        const code=unavailableReason?.(scope);
        return fail(Object.hasOwn(reasons,code)?code:'run-unavailable','No document publication was attempted. '+(reasons[code]??'The current run is unavailable for document delivery.')+' Preserve the workspace file. Do not claim it was attached or infer another failure cause.','unavailable');
      }
      let response;
      try {
        response=await request(payload,{signal});
      } catch {
        return fail(signal?.aborted?'publication-cancelled':'publication-unavailable','No verified document receipt was returned by the publication transport. Publication may have completed on the host, but no download is attached. Preserve the file; this does not establish a file-permission problem. Do not relax permissions or invent a download URL.');
      }
      let result;
      try {
        result=artifactReceipt(response,payload);
      } catch {
        return fail('receipt-unverified','The publication service did not return a valid matching byte-verified receipt, so no download is attached. The failure does not prove file corruption or incorrect permissions. Preserve the file; verify its actual workspace path, supported format, size and link/ownership status before retrying. Do not relax permissions blindly or invent a URL.');
      }
      if (signal?.aborted || !accept(scope,result)) return fail('delivery-no-longer-active','A verified snapshot was prepared, but the originating run is no longer eligible to deliver it. No download is attached to this response. Preserve the workspace file; do not claim delivery or attach the receipt to another conversation.');
      return {details:result,content:[{type:'text',text:'Verified document snapshot prepared for this response. On completed delivery, Portal renders the filename, size and download card automatically. Give a brief final answer; do not repeat full workspace paths or hashes unless the owner asks. '+ARTIFACT_BOUNDARY+'\n'+JSON.stringify(result)}]};
    },
  };
}
