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

export function createWorkspaceArtifactTool(context, {admission,reserve,accept,request,transport='unix'}={}) {
  if (!['unix','docker-desktop'].includes(transport)) throw new Error('invalid-artifact-transport');
  request ??= transport === 'docker-desktop' ? dockerWorkspacePreviewRequest : socketRequest;
  return {
    name:ARTIFACT_TOOL,
    description:'For an ordinary owner-interactive Portal chat turn only (not teams, subagents or background goals), deliver one owner-requested document or archive already created in the Pixel workspace, without creating a website or opening its preview. Pass a workspace-relative file path; accepted formats are Markdown, TXT, CSV, TSV, JSON, PDF, ZIP, RAR, DOCX, XLSX and PPTX, at most 4 MiB each and four publication attempts per response. Returns a verified immutable download receipt; the Portal supplies the download control. Do not emit MEDIA paths or invent URLs. Publishing verifies bytes, not document rendering, archive integrity or correctness; perform those checks separately. No arbitrary host files, directories, dependencies, execution or network destinations.',
    parameters:{type:'object',additionalProperties:false,required:['relativePath'],properties:{relativePath:{type:'string',minLength:1,maxLength:512}}},
    async execute(id,params,signal) {
      try {
        const payload=normalizeWorkspaceArtifact(params);
        const scope=admission.take(id,payload,context);
        if (signal?.aborted) throw new Error('artifact-run-unavailable');
        if (!reserve(scope)) return {isError:true,details:{status:'unavailable',kind:'ods-pixel-workspace-artifact'},content:[{type:'text',text:'No document publication was attempted. This download tool requires the current ordinary owner-interactive Portal chat turn and fewer than four publication attempts. Team workers, subagents and background goal turns cannot attach files through this surface. Preserve the workspace file and hand its exact relative path to the owner chat; do not claim it was attached.'}]};
        const result=artifactReceipt(await request(payload,{signal}),payload);
        if (signal?.aborted || !accept(scope,result)) throw new Error('artifact-run-unavailable');
        return {details:result,content:[{type:'text',text:'Verified document snapshot prepared for this response. '+ARTIFACT_BOUNDARY+'\n'+JSON.stringify(result)}]};
      } catch {
        return {isError:true,details:{status:'failed',kind:'ods-pixel-workspace-artifact'},content:[{type:'text',text:'No verified document download was attached. Preserve the workspace file. Check its workspace-relative path, supported format, 4 MiB limit, ownership and absence of links; do not relax permissions blindly. A cancelled publication may have completed on the host but grants no delivered receipt.'}]};
      }
    },
  };
}
