import * as fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import {createHash, randomUUID} from 'node:crypto';
import {isDeepStrictEqual} from 'node:util';

export const WORKSPACE_BUNDLE_TOOL = 'pixel_ods_workspace_bundle';
const hash = value => createHash('sha256').update(value).digest('hex');
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const fail = code => Object.assign(new Error(code), {code});
const bundleQueues = new Map();
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  Object.keys(value).sort().join() === keys.split(',').sort().join();
const safePath = value => typeof value === 'string' && value.length <= 512 && value.split('/').length <= 16 &&
  value.split('/').every(part => /^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$/.test(part) && !/[. ]$/.test(part));

export function normalizeWorkspaceBundle(value) {
  if (!exact(value, 'files,mappingPath,outputRoot') || !safePath(value.outputRoot) || !safePath(value.mappingPath) ||
      !Array.isArray(value.files) || value.files.length < 1 || value.files.length > 32) throw fail('invalid-bundle-request');
  const sources = new Set(), outputs = new Set([value.mappingPath.toLowerCase()]), keys = new Set();
  for (const item of value.files) {
    if (!exact(item, 'source,key,copyTo') || !safePath(item.source) || !safePath(item.copyTo) ||
        typeof item.key !== 'string' || !item.key || item.key.length > 256 || /[\x00-\x1f]/.test(item.key) ||
        sources.has(item.source.toLowerCase()) || outputs.has(item.copyTo.toLowerCase()) || keys.has(item.key)) throw fail('invalid-bundle-file');
    sources.add(item.source.toLowerCase()); outputs.add(item.copyTo.toLowerCase()); keys.add(item.key);
  }
  const paths = [...outputs].sort();
  if (paths.some((name, index) => paths[index + 1]?.startsWith(name + '/'))) throw fail('nested-file-path');
  if (paths.some(name => !safePath(`${value.outputRoot}/bundle-${'a'.repeat(32)}/${name}`))) throw fail('bundle-path-too-long');
  return JSON.parse(JSON.stringify(value));
}

function helperReceipt(value, request, generation) {
  const prefix = `${request.outputRoot}/${generation}/`;
  const outputs = [request.mappingPath, ...request.files.map(item => item.copyTo)].sort().map(name => prefix + name);
  if (value?.schemaVersion !== 2 || value.kind !== 'ods-pixel-workspace-bundle' || value.status !== 'succeeded' ||
      value.generationPath !== prefix.slice(0,-1) || value.mappingPath !== prefix + request.mappingPath ||
      value.readbackVerified !== true || value.decodedAndRawBytesEqual !== true ||
      !Array.isArray(value.sources) || !Array.isArray(value.outputs) ||
      !isDeepStrictEqual(value.sources.map(item => item.path), request.files.map(item => item.source)) ||
      !isDeepStrictEqual(value.outputs.map(item => item.path), outputs) ||
      [...value.sources, ...value.outputs].some(item => !exact(item, 'path,bytes,sha256') ||
        !Number.isSafeInteger(item.bytes) || item.bytes < 0 || item.bytes > 6 * 1024 * 1024 + 4096 || !sha(item.sha256)) ||
      value.sources.some(item => item.bytes > 256 * 1024) ||
      value.sources.reduce((total, item) => total + item.bytes, 0) > 1024 * 1024 ||
      request.files.some((item, index) => {
        const source = value.sources[index], output = value.outputs.find(out => out.path === prefix + item.copyTo);
        return !output || output.bytes !== source.bytes || output.sha256 !== source.sha256;
      })) throw fail('unverified-bundle-receipt');
  return value;
}

// Records only an actually admitted tool call, including Tool Search's exact
// parent/child ID convention. The factory context alone has no live run ID.
export function createWorkspaceBundleAdmission() {
  const pending = new Map();
  return {
    before(event, context, decision) {
      if (decision?.block || context?.agentId !== 'pixel') return;
      const params = decision?.params ?? event?.params;
      const direct = event?.toolName === WORKSPACE_BUNDLE_TOOL;
      const deferred = event?.toolName === 'tool_call' &&
        [WORKSPACE_BUNDLE_TOOL, 'openclaw:pixel-ods:' + WORKSPACE_BUNDLE_TOOL].includes(params?.id);
      if (!direct && !deferred) return;
      const id = context?.toolCallId ?? event?.toolCallId;
      if (!id || !context?.runId || !context?.sessionId || !context?.sessionKey) return;
      while (pending.size >= 128) pending.delete(pending.keys().next().value);
      try { pending.set(id, {context: {...context}, request: normalizeWorkspaceBundle(deferred ? params.args : params), deferred}); }
      catch { /* The actual tool reports invalid arguments without admitting execution. */ }
    },
    take(id, request, factory) {
      let entry = pending.get(id);
      if (!entry) {
        const matches = [...pending].filter(([parentId, item]) => {
          const parent = parentId.trim().replace(/[^A-Za-z0-9_.:-]+/g, '_').slice(0, 120) || 'call';
          const prefix = `tool_search_code:${parent}:${WORKSPACE_BUNDLE_TOOL}:`;
          return item.deferred && id.startsWith(prefix) && /^[1-9][0-9]*$/.test(id.slice(prefix.length));
        });
        if (matches.length === 1) entry = matches[0][1];
      }
      if (!entry || entry.used || !isDeepStrictEqual(entry.request, request) ||
          ['agentId', 'sessionId', 'sessionKey'].some(key => entry.context[key] !== factory?.[key])) throw fail('bundle-call-unbound');
      entry.used = true;
      return Object.freeze({...entry.context});
    },
    after(event, context) { pending.delete(context?.toolCallId ?? event?.toolCallId); },
  };
}

function privateDirectory(directory) {
  if (!path.isAbsolute(directory)) throw fail('bundle-state-unavailable');
  fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  const info = fs.lstatSync(directory);
  if (!info.isDirectory() || info.isSymbolicLink() || fs.realpathSync(directory) !== directory ||
      process.platform !== 'win32' && (info.uid !== process.getuid() || (info.mode & 0o077))) throw fail('bundle-state-unavailable');
}

function readPrivate(file) {
  let before;
  try { before = fs.lstatSync(file); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
  if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size > 128 * 1024 ||
      process.platform !== 'win32' && (before.uid !== process.getuid() || (before.mode & 0o077))) throw fail('bundle-state-unavailable');
  const fd = fs.openSync(file, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW ?? 0));
  try {
    const opened = fs.fstatSync(fd);
    if (opened.ino !== before.ino || opened.dev !== before.dev) throw fail('bundle-state-changed');
    return JSON.parse(fs.readFileSync(fd, 'utf8'));
  } finally { fs.closeSync(fd); }
}

function savePrivate(file, value) {
  const temporary = file + '.' + randomUUID();
  const fd = fs.openSync(temporary, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW ?? 0), 0o600);
  try { fs.writeFileSync(fd, JSON.stringify(value)); fs.fsyncSync(fd); }
  finally { fs.closeSync(fd); }
  try { fs.renameSync(temporary, file); }
  finally { try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; } }
}

export function createWorkspaceBundleService({runHelper, invalidatePreview,
  stateDir = path.join(process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw'), '.ods-workspace-bundle-lanes')} = {}) {
  return async function execute(scope, supplied, signal) {
    const request = normalizeWorkspaceBundle(supplied);
    if (typeof scope?.workspaceRoot !== 'string' || !path.isAbsolute(scope.workspaceRoot)) throw fail('workspace-unavailable');
    const identity = hash(scope.workspaceRoot), queueKey = path.join(stateDir, identity);
    const previousJob = bundleQueues.get(queueKey) ?? Promise.resolve();
    const work = previousJob.catch(() => {}).then(async () => {
      if (signal?.aborted) throw fail('bundle-cancelled');
      privateDirectory(stateDir);
      const file = path.join(stateDir, identity + '.json');
      if (readPrivate(file)) throw fail('bundle-workspace-execution-unsettled');
      const generation = 'bundle-' + randomUUID().replaceAll('-','');
      const state = {schemaVersion:2, workspaceRoot:scope.workspaceRoot, generation,
        runId:scope.runId, sessionId:scope.sessionId, sessionKey:scope.sessionKey, status:'pending'};
      let fd;
      try { fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | (fs.constants.O_NOFOLLOW ?? 0), 0o600); }
      catch (error) { if(error.code === 'EEXIST') throw fail('bundle-workspace-execution-unsettled'); throw error; }
      try { fs.writeFileSync(fd, JSON.stringify(state)); fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
      let settled = false;
      try {
        if (signal?.aborted || !invalidatePreview(scope)) { settled = true; throw fail('bundle-run-unavailable'); }
        const result = await runHelper(scope, {request, generation}, signal, {
          onProcess: execution => savePrivate(file, {...state, execution}),
          onSettled: () => { settled = true; },
        });
        const receipt = helperReceipt(result, request, generation);
        return {...receipt, request,
          boundary:'Immutable derived bytes only. Use returned generation paths; fixed-path requests require ordinary file tools. No execution, publication, or task-correctness proof.'};
      } finally {
        // Failure to prove quiescence leaves a durable guard even after this
        // process exits. A retry must not overlap an uncertain older helper.
        if (settled && readPrivate(file)?.generation === generation) fs.unlinkSync(file);
      }
    });
    bundleQueues.set(queueKey, work);
    try { return await work; }
    finally { if (bundleQueues.get(queueKey) === work) bundleQueues.delete(queueKey); }
  };
}

export function createWorkspaceBundleTool(context, {admission, execute, scopeForContext}) {
  if (context?.agentId !== 'pixel') return null;
  return {name: WORKSPACE_BUNDLE_TOOL, label: 'Bundle existing workspace files',
    description: 'Create an immutable generation of exact UTF-8 source maps and raw copies without retyping contents. outputRoot selects a workspace directory; mappingPath and copyTo are filenames relative to the new generation. Source paths are workspace-relative. Every call returns a fresh generation path with the original keys and filenames for publication/download links; include an index.html copy when the generation should itself be browser-publishable. Never replaces prior files. If the owner requires a literal fixed path, use ordinary file tools for that request rather than silently substituting these version paths. Does not run tests or publish a preview.',
    parameters: {type: 'object', additionalProperties: false, required: ['files', 'mappingPath', 'outputRoot'], properties: {
      files: {type: 'array', minItems: 1, maxItems: 32, items: {type: 'object', additionalProperties: false,
        required: ['source', 'key', 'copyTo'], properties: {source: {type: 'string'}, key: {type: 'string'}, copyTo: {type: 'string'}}}},
      mappingPath: {type: 'string'}, outputRoot: {type:'string'},
    }},
    async execute(id, args, signal) {
      try {
        const request = normalizeWorkspaceBundle(args), active = admission.take(id, request, context);
        const scope = scopeForContext(active, context);
        const details = await execute(scope, request, signal);
        return {details, content: [{type: 'text', text: 'Immutable workspace bundle verified byte-for-byte. ' + details.boundary + '\n' + JSON.stringify(details)}]};
      } catch (error) {
        return {isError: true, details: {status: 'failed', readbackVerified: false,
          ...(Array.isArray(error?.written) ? {written: error.written} : {})}, content: [{type: 'text',
          text: 'Workspace bundle was not verified. ' + (/^[a-z-]{1,80}$/.test(error?.code ?? '') ? error.code : 'bundle-unavailable') +
            '. Preserve existing files; do not claim these artifacts passed verification.'}]};
      }
    }};
}
