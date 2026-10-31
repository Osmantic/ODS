// Portal's one-step undo for Playground projects. Project folders in Portal's
// workspace are not Git repositories, so a turn that broke a file had no way
// back: on 2026-10-30 a one-off Python patch run through exec doubled a
// game's chars.js. Now, the first time a Portal response touches a Playground
// project, ODS keeps a copy of that project's files before the call runs, and
// under the latest turn that changed the project the owner can put them back
// ("Put back files from before this turn") and undo that.
//
// Every copy and restore is done by project-snapshots.py, a child process that
// works through folder descriptors and never follows a link, so neither a
// slow disk nor a folder swapped for a link mid-copy reaches this gateway's
// event loop or anything outside the project. This module decides when a call
// touches a project, keeps the bookkeeping of each response and serves the
// owner's routes.
//
// A call touches Playground/<name> when it names a path in it: a read, write,
// edit or patch, an exec whose working directory is in it, a project folder an
// ODS tool is given, or Playground/<name> anywhere in an exec command or in
// what a process call sends (also inside interpreter code). A mention of
// Playground without one plain project name (Playground/*, cd Playground && ...,
// os.path.join('Playground', ...), find Playground), or a named project left
// with .. (cd Playground/game && cd ../notes), touches every project, up to the
// 16 whose files changed most recently. Before a response's first call that
// may change files, the helper also takes a stat-level fingerprint of every
// project; a copy taken after its project had already changed is marked late,
// and Portal says so.
import {spawn} from 'node:child_process';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

export const SNAPSHOT_DIRECTORY = '.ods-project-snapshots';
const HELPER = fileURLToPath(new URL('./project-snapshots.py', import.meta.url));
const ROUTES = Object.freeze({restore: '/pixel-ods/project-restore', options: '/pixel-ods/project-restore-options'});
const MAX_BODY = 4096;
const MAX_ANSWER = 1024 * 1024;
const HELPER_TIMEOUT_MS = 60000;
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const RUN = new RegExp(`^chatcmpl_${UUID}$`, 'i');
const SNAPSHOT = new RegExp(`^(?:chatcmpl|restore)_${UUID}$`, 'i');
const UNDO = new RegExp(`^restore_${UUID}$`);
const SHA = /^[a-f0-9]{64}$/;
const USER = /^ods-[a-f0-9]{64}$/;
const RESERVED = /^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i;
// Calls that never change files. Any other call may (a plugin tool, a child
// agent), so the first of a response takes the fingerprint before it runs.
const READ_ONLY = new Set(['read', 'web_search', 'web_fetch', 'pixel_ods_ask_user', 'pixel_ods_activity',
  'pixel_ods_goal', 'pixel_ods_status', 'pixel_ods_history', 'pixel_ods_workspace_preview_inspect']);
// The most projects one Playground-wide mention copies; the fingerprint covers the rest.
const ALL_PROJECTS = 16;
const ATTEMPTS = 3;
const COLLECT_EVERY_MS = 10 * 60 * 1000;
// The helper's refusals as the routes answer them.
const REFUSALS = Object.freeze({
  busy: [409, 'project-restore-busy'],
  'unavailable-copy': [404, 'project-snapshot-unavailable'],
  pruned: [404, 'project-snapshot-pruned'],
  damaged: [422, 'project-snapshot-damaged'],
  'folder-unusable': [422, 'project-folder-unusable'],
  'copies-unusable': [503, 'project-copies-unusable'],
  unsupported: [503, 'project-restore-unsupported'],
});

const exact = (value, keys) => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).sort().join() === keys.split(',').sort().join();
const projectName = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value)
  && !value.endsWith('.') && !RESERVED.test(value);
const realDirectory = stat => Boolean(stat?.isDirectory() && !stat.isSymbolicLink());
const refusal = (status, error) => ({status, body: {error}});
const toolOf = (toolName, params) => toolName === 'tool_call' && typeof params?.id === 'string' ? params.id.split(':').at(-1) : toolName;

function lstatOrNull(target) {
  try { return fs.lstatSync(target); }
  catch (error) { if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return null; throw error; }
}

// The canonical workspace root, or null for one that is unusable.
function canonicalRoot(workspaceRoot) {
  if (typeof workspaceRoot !== 'string' || !path.isAbsolute(workspaceRoot)) return null;
  try {
    const root = fs.realpathSync(path.resolve(workspaceRoot));
    return root === path.parse(root).root || !fs.statSync(root).isDirectory() ? null : root;
  } catch (error) { if (typeof error?.code === 'string') return null; throw error; }
}

// ------------------------------------------------------- what a call touches --

const FILE_KEYS = ['path', 'file_path', 'filePath'];
const PATH_KEYS = new Map([['read', FILE_KEYS], ['write', FILE_KEYS], ['edit', FILE_KEYS], ['exec', ['workdir']],
  ['pixel_ods_workspace_preview', ['relativeDirectory']], ['pixel_ods_workspace_artifact', ['relativePath', 'sourcePath']],
  ['pixel_ods_workspace_bundle', ['outputRoot']], ['pixel_ods_download_promote', ['relativePath']],
  ['pixel_ods_project_build', ['project']]]);
const PATCH_FILE = /^\*\*\* (?:(?:Add|Update|Delete) File|Move to): ([^\r\n]+)$/gm;
const MENTION = /(?<![A-Za-z0-9._-])playground(?![A-Za-z0-9_-])/gi;
const NAMED = /^[\\/]+([A-Za-z0-9][A-Za-z0-9._-]*)/;
const WORD = /[A-Za-z0-9][A-Za-z0-9._-]{0,63}/g;
// A parent folder (..) as a part of a path or a cd, not the ... of code.
const PARENT = /(?:^|[\s/'"`=:;(&|])\.\.(?=$|[\s/'"`;)&|])/m;
const MAX_SCANNED = 1024 * 1024;

// A path as the workspace sees it: relative, under the sandbox alias
// /workspace or under the configured root, normalized. Null for any other
// absolute path and for one that leaves the workspace.
function workspaceRelative(value, root) {
  if (typeof value !== 'string' || !value || value.length > 4096 || value.includes('\0')) return null;
  let text = value.replaceAll('\\', '/');
  const base = root.replaceAll('\\', '/').replace(/\/+$/, ''), folded = text.toLowerCase();
  if (folded === base.toLowerCase() || folded.startsWith(`${base.toLowerCase()}/`)) text = text.slice(base.length);
  else if (text === '/workspace' || text.startsWith('/workspace/')) text = text.slice('/workspace'.length);
  else if (/^(?:\/|[A-Za-z]:|~)/.test(text)) return null;
  const normal = path.posix.normalize(text.replace(/^\/+/, '') || '.');
  return normal === '..' || normal.startsWith('../') ? null : normal;
}

function projectOf(value, root) {
  const [first, name] = workspaceRelative(value, root)?.split('/') ?? [];
  return /^playground$/i.test(first ?? '') && projectName(name) ? name : null;
}

// The project names a text mentions as Playground/<name>, and whether it
// mentions Playground without one plain name, which touches every project.
function mentions(text) {
  const names = new Set();
  if (text.length > MAX_SCANNED) return {names, all: true};
  let all = false;
  for (const match of text.matchAll(MENTION)) {
    const after = text.slice(match.index + match[0].length, match.index + match[0].length + 80);
    const named = NAMED.exec(after);
    const name = named?.[1].replace(/\.+$/, '');
    if (named && projectName(name) && !/^[*?[{]/.test(after.slice(named[0].length))) names.add(name);
    else all = true;
  }
  return {names, all};
}

// The Playground projects one call touches, and whether it touches them all.
// folders() lists Playground, for an exec that runs inside it. Naming more
// projects than a call uses only costs a copy.
export function projectsNamed(toolName, params, root, folders = () => []) {
  const name = toolOf(toolName, params), args = toolName === 'tool_call' ? params?.args : params;
  const found = new Set();
  let all = false;
  if (!args || typeof args !== 'object' || typeof root !== 'string') return {names: [], all};
  const add = value => { const project = projectOf(value, root); if (project) found.add(project); };
  for (const key of PATH_KEYS.get(name) ?? []) add(args[key]);
  if (name === 'apply_patch' && typeof args.input === 'string') {
    for (const match of args.input.slice(0, MAX_SCANNED).matchAll(PATCH_FILE)) add(match[1].trim());
  }
  if (name === 'pixel_ods_workspace_bundle' && Array.isArray(args.files)) {
    for (const file of args.files.slice(0, 64)) add(file?.source);
  }
  if (name === 'exec' || name === 'process') {
    // An exec command, or what a process call sends to a running command.
    const text = name === 'exec' ? (typeof args.command === 'string' ? args.command : '')
      : Object.entries(args).filter(([key, value]) => typeof value === 'string' && !['action', 'sessionId'].includes(key))
        .map(([, value]) => value).join('\n');
    const mentioned = mentions(text);
    mentioned.names.forEach(item => found.add(item));
    // A project named and then left with .. (cd Playground/game && cd ../notes,
    // Playground/game/../notes): any other project.
    all ||= mentioned.all || (mentioned.names.size > 0 && PARENT.test(text));
    const where = name === 'exec' ? workspaceRelative(args.workdir, root) : null;
    if (/^playground(?:\/|$)/i.test(where ?? '')) {
      // Run in Playground itself, or leaving its project with ..: every project.
      if (!where.split('/')[1] || PARENT.test(text)) all = true;
      const present = new Set(folders());
      for (const [word] of text.slice(0, MAX_SCANNED).matchAll(WORD)) if (present.has(word) && projectName(word)) found.add(word);
    }
  }
  return {names: [...found], all};
}

// Playground/<name> in the canonical root, with the name as the folder holds
// it (on a disk that ignores case, Playground/Game is Playground/game). A
// missing project is an empty one. Null when Playground or the project is a
// file or a link.
function projectFolder(root, name) {
  if (!projectName(name)) return null;
  const playground = path.join(root, 'Playground'), top = lstatOrNull(playground);
  if (top && !realDirectory(top)) return null;
  const entry = top ? lstatOrNull(path.join(playground, name)) : null;
  if (entry && !realDirectory(entry)) return null;
  const names = entry ? fs.readdirSync(playground) : [];
  return {name: !entry || names.includes(name) ? name : names.find(item => item.toLowerCase() === name.toLowerCase()) ?? name};
}

function playgroundNames(root) {
  try { return fs.readdirSync(path.join(root, 'Playground')); }
  catch (error) { if (typeof error?.code === 'string') return []; throw error; }
}

// The projects a Playground-wide mention copies: the most recently changed
// first, by the newest change to anything inside each (the fingerprint's
// newest; an edit in place does not change its folder's time), else by the
// project folder's own time.
function busiestProjects(root, newest = {}) {
  return playgroundNames(root).filter(projectName).flatMap(name => {
    const stat = lstatOrNull(path.join(root, 'Playground', name));
    const inside = Number.isSafeInteger(newest?.[name]) ? newest[name] : 0;
    return realDirectory(stat) ? [{name, time: Math.max(inside, stat.mtimeMs)}] : [];
  }).sort((a, b) => b.time - a.time).slice(0, ALL_PROJECTS).map(item => item.name);
}

// ----------------------------------------------------------------- the helper --

// Runs project-snapshots.py with one request on stdin and parses its one answer.
export function helperRunner({python = '/usr/bin/python3', helper = HELPER, timeoutMs = HELPER_TIMEOUT_MS} = {}) {
  return request => new Promise((resolve, reject) => {
    if (process.platform === 'win32') { resolve({error: 'unsupported'}); return; }
    const child = spawn(python, ['-I', '-S', '-B', helper], {stdio: ['pipe', 'pipe', 'pipe'], cwd: '/',
      env: {PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', PYTHONDONTWRITEBYTECODE: '1'}});
    const chunks = [];
    let size = 0, stderr = '';
    const timer = setTimeout(() => child.kill('SIGKILL'), timeoutMs);
    child.stdout.on('data', chunk => {
      size += chunk.length;
      if (size > MAX_ANSWER) child.kill('SIGKILL');
      else chunks.push(chunk);
    });
    child.stderr.on('data', chunk => { if (stderr.length < 2048) stderr += chunk; });
    child.stdin.on('error', () => { /* A helper that exited early is reported by its close. */ });
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0) { reject(Object.assign(new Error(`project copy helper exited with ${code}`), {stderr})); return; }
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
      catch (error) { reject(error); }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

// ----------------------------------------------------------------- receipts --

const count = value => Number.isSafeInteger(value) && value >= 0 && value <= 65536;
// No C0, DEL or C1 control character, as dashboard-api refuses them.
const pathList = (items, total) => Array.isArray(items) && items.length <= 8 && items.length <= total
  && items.every(item => typeof item === 'string' && item.length >= 1 && item.length <= 512 && !/[\x00-\x1f\x7f-\x9f]/.test(item));

// The helper's restore answer for exactly this project and copy, or null.
function validReceipt(value, project, snapshot) {
  const confirm = value?.confirm;
  return exact(value, 'schemaVersion,status,project,snapshot,written,removed,left,leftCount,failed,failedCount,failure,late,undo,confirm')
    && value.schemaVersion === 2 && ['restored', 'unchanged', 'confirm'].includes(value.status)
    && value.project.toLowerCase() === project.toLowerCase() && value.snapshot.toLowerCase() === snapshot.toLowerCase()
    && [value.written, value.removed, value.leftCount, value.failedCount].every(count)
    && pathList(value.left, value.leftCount) && pathList(value.failed, value.failedCount)
    && [null, 'permission', 'in-use', 'changed', 'other'].includes(value.failure) && typeof value.late === 'boolean'
    && (value.status === 'restored' ? UNDO.test(value.undo ?? '') : value.undo === null)
    && (value.status === 'confirm'
      ? exact(confirm, 'token,overwrite,overwriteCount,remove,removeCount') && SHA.test(confirm.token ?? '')
        && count(confirm.overwriteCount) && count(confirm.removeCount)
        && pathList(confirm.overwrite, confirm.overwriteCount) && pathList(confirm.remove, confirm.removeCount)
      : confirm === null);
}

function validRestoreRequest(value) {
  return (exact(value, 'project,snapshot,user') || exact(value, 'confirm,project,snapshot,user') && SHA.test(value.confirm ?? ''))
    && typeof value.user === 'string' && USER.test(value.user)
    && typeof value.project === 'string' && value.project.startsWith('Playground/') && projectName(value.project.slice(11))
    && typeof value.snapshot === 'string' && SNAPSHOT.test(value.snapshot);
}

const validOptionsRequest = value => exact(value, 'runs') && Array.isArray(value.runs) && value.runs.length <= 64
  && value.runs.every(run => typeof run === 'string' && RUN.test(run));

const validOption = (entry, ids) => exact(entry, 'project,snapshot,late,restore') && typeof entry.project === 'string'
  && entry.project.startsWith('Playground/') && projectName(entry.project.slice(11)) && ids.has(entry.snapshot)
  && typeof entry.late === 'boolean' && (entry.restore === null || exact(entry.restore, 'undo,side')
    && UNDO.test(entry.restore.undo ?? '') && ['before', 'after'].includes(entry.restore.side));

// Adds a value to a bounded map, dropping the oldest entry first.
function remember(map, key, value, limit = 512) {
  map.delete(key);
  if (map.size >= limit) map.delete(map.keys().next().value);
  map.set(key, value);
  return value;
}

// One per gateway. before() runs in before_tool_call with the call's final
// decision and keeps a copy of each project a response touches, once; end()
// runs at agent_end and records how the turn left them; restore() and
// options() serve the owner's routes. workspaceRoot() is the configured
// workspace, ownerRun(context) the owner run a delegated run works for,
// busy(user) whether a response or its delegated work runs in that chat,
// portalActive() whether any Portal run, tool or background command is alive,
// and changed(root, name) hears of each restore that changed a project (again
// for a retry that gets its receipt, while the project is as it left it).
export function createProjectSnapshots({agentId = 'pixel', workspaceRoot = () => undefined, ownerRun = () => undefined,
  busy = () => true, portalActive = () => true, changed = () => {}, warn = () => {}, now = Date.now,
  stateDir = path.join(process.env.OPENCLAW_STATE_DIR || path.join(os.homedir(), '.openclaw'), SNAPSHOT_DIRECTORY),
  runHelper = helperRunner(), limits, waitMs = 10000} = {}) {
  const runs = new Map(), ending = new Map(), restoring = new Map();
  const chat = `agent:${agentId}:openai-user:`;
  // false once the helper answers that this system lacks what it needs:
  // then no copy is asked for again, and no turn offers an undo.
  let supported = true, tail = Promise.resolve(), collected = 0;
  const stillBusy = user => { try { return Boolean(busy(user)); } catch { return true; } };
  // The runs whose copies may still change: a run is live from its first call
  // until its owner's chat session ends it, and after that while its chat
  // still has delegated work. The helper never drops their copies, and ends
  // any other copy that has no end yet (a run of an earlier gateway process).
  const liveRuns = () => [...runs].filter(([, run]) => !run.ended || run.kept.size && run.user && stillBusy(run.user))
    .map(([id]) => id);
  // One helper at a time, so copies, ends and restores never overlap. ready()
  // is asked again when the request's turn comes; false answers busy.
  const queue = (request, ready = () => true) => {
    const next = tail.then(() => ready()
      ? runHelper({...request, root: stateDir, ...(request.operation === 'fingerprint' ? {} : {live: liveRuns()}),
        ...(limits ? {limits} : {})}) : {error: 'busy'});
    tail = next.catch(() => {});
    return next;
  };
  const settled = promise => promise && Promise.race([promise.catch(() => {}),
    new Promise(resolve => { setTimeout(resolve, waitMs).unref?.(); })]);
  const runOf = (event, context) => {
    const runId = ownerRun(context) ?? context?.runId ?? event?.runId;
    return typeof runId === 'string' && RUN.test(runId) ? runId.toLowerCase() : null;
  };
  // Whether a run works in its owner's own chat session (the Portal response,
  // or a continuation announced into it), not in a delegated child's session.
  const ownSession = context => typeof context?.sessionKey === 'string' ? context.sessionKey.startsWith(chat)
    : String(context?.runId ?? '').startsWith('announce:') || [undefined, context?.runId].includes(ownerRun(context));

  async function fingerprint(root) {
    try {
      const answer = await queue({operation: 'fingerprint', workspace: root});
      if (answer && typeof answer.projects === 'object' && typeof answer.complete === 'boolean') return answer;
      if (answer?.error === 'unsupported') supported = false;
      else warn(`Portal could not fingerprint the Playground projects before this turn (${answer?.error ?? 'no answer'}).`);
    } catch (error) {
      warn(`Portal could not fingerprint the Playground projects before this turn (${error.message}).`);
    }
    return null;
  }

  async function copy(event, context, decision) {
    if (!supported || decision?.block === true || context?.agentId !== agentId) return;
    const toolName = context?.toolName ?? event?.toolName;
    const mayChange = !READ_ONLY.has(toolOf(toolName, event?.params));
    // Every Pixel call that may change files waits for a running restore,
    // whatever started its run: a Portal response, a cron job, a webhook, a
    // channel, or a child whose spawn receipt is not known yet.
    if (mayChange) await Promise.all([...restoring.values()]);
    const id = runOf(event, context), root = id && canonicalRoot(workspaceRoot());
    if (!root) return;
    const folders = () => playgroundNames(root);
    // The call as the model wrote it (an exec command before ODS wraps it)
    // and as it will run (a routed path or working directory).
    const touched = [projectsNamed(toolName, event?.params, root, folders), projectsNamed(toolName, decision?.params, root, folders)];
    const names = new Set(touched.flatMap(item => item.names));
    const run = runs.get(id) ?? remember(runs, id, {kept: new Set(), attempts: new Map(), start: null,
      ended: false, ownerOpen: false, user: null});
    run.ended = false;
    if (ownSession(context)) {
      run.ownerOpen = true;
      const user = context?.sessionKey?.startsWith(chat) ? context.sessionKey.slice(chat.length) : '';
      if (USER.test(user)) run.user = user;
    }
    if (!run.start && mayChange) run.start = fingerprint(root);
    const start = run.start ? await run.start : null;
    if (!supported) return;
    if (touched.some(item => item.all)) busiestProjects(root, start?.newest).forEach(name => names.add(name));
    const wanted = [];
    for (const spelled of names) {
      const project = projectFolder(root, spelled);
      if (project && !run.kept.has(project.name) && (run.attempts.get(project.name) ?? 0) < ATTEMPTS) wanted.push(project.name);
    }
    if (!wanted.length) return;
    const collect = now() - collected >= COLLECT_EVERY_MS;
    if (collect) collected = now();
    let answer;
    try {
      answer = await queue({operation: 'copy', workspace: root, id, ...(collect ? {collect} : {}),
        projects: wanted.map(name => ({name, start: start?.projects?.[name] ?? (start?.complete ? 'absent' : null)}))});
    } catch (error) { answer = {error: error.message}; }
    if (answer?.error === 'unsupported') { supported = false; return; }
    for (const name of wanted) {
      const result = answer?.projects?.[name];
      if (['kept', 'exists'].includes(result?.status)) run.kept.add(name);
      else {
        run.attempts.set(name, (run.attempts.get(name) ?? 0) + 1);
        warn(`Portal could not keep a copy of Playground/${name} before this turn (${result?.error ?? answer?.error ?? 'no answer'}).`);
      }
    }
  }

  // A response's end is its owner session's: the Portal run, or the last
  // continuation announced into its chat. A delegated child that ends while
  // that session still works records nothing; one that outlives it records
  // the projects as the whole response left them.
  async function end(event, context) {
    if (context?.agentId !== agentId) return;
    const id = runOf(event, context), run = id && runs.get(id);
    if (!run) return;
    if (ownSession(context)) run.ownerOpen = false;
    else if (run.ownerOpen) return;
    run.ended = true;
    const root = run.kept.size && canonicalRoot(workspaceRoot());
    if (!root) return;
    const pending = queue({operation: 'after', workspace: root, id, projects: [...run.kept].map(name => ({name}))});
    remember(ending, id, pending.catch(() => {}));
    const answer = await pending;
    for (const name of run.kept) {
      if (answer?.projects?.[name]?.error) warn(`Portal could not record how this turn left Playground/${name} (${answer.projects[name].error}).`);
    }
  }

  return {
    // Never throws into before_tool_call: a copy that fails is retried by the
    // response's next call that touches the project, up to three times.
    async before(event, context, decision) {
      try { await copy(event, context, decision); }
      catch (error) { warn(`Portal could not keep a project copy before this turn (${error.message}).`); }
    },

    async end(event, context) {
      try { await end(event, context); }
      catch (error) { warn(`Portal could not record how this turn left its projects (${error.message}).`); }
    },

    // {status, body}: 200 with the receipt (restored, unchanged, or confirm
    // with the files it would overwrite or remove), 400 for a malformed
    // request, 404 when that copy was never kept or is no longer, 409 while
    // any Portal run, tool or background command is alive, 422 when the copy
    // is damaged or the project path is not a usable folder, and 503 when the
    // copies or the helper cannot be used or the outcome is unknown.
    async restore(request) {
      if (!validRestoreRequest(request)) return refusal(400, 'invalid-project-restore-request');
      const idle = () => !busy(request.user) && !portalActive();
      if (!idle()) return refusal(...REFUSALS.busy);
      const root = canonicalRoot(workspaceRoot());
      if (!root) return refusal(503, 'project-restore-unavailable');
      const project = projectFolder(root, request.project.slice('Playground/'.length));
      if (!project) return refusal(...REFUSALS['folder-unusable']);
      const snapshot = request.snapshot.toLowerCase();
      await settled(ending.get(snapshot));
      let finish;
      const marker = new Promise(resolve => { finish = resolve; });
      restoring.set(project.name, marker);
      try {
        const answer = await queue({operation: 'restore', workspace: root, project: project.name, snapshot,
          ...(request.confirm ? {confirm: request.confirm} : {})}, idle);
        if (typeof answer?.error === 'string') return refusal(...(REFUSALS[answer.error] ?? [503, 'project-restore-unavailable']));
        if (!validReceipt(answer, request.project, request.snapshot)) return refusal(503, 'project-restore-unavailable');
        if (answer.status === 'restored') changed(root, project.name);
        return {status: 200, body: {...answer, project: request.project, snapshot: request.snapshot}};
      } catch (error) {
        warn(`Portal could not put back Playground/${project.name} (${error.message}).`);
        return refusal(503, 'project-restore-unavailable');
      } finally {
        finish();
        if (restoring.get(project.name) === marker) restoring.delete(project.name);
      }
    },

    // For each project whose latest changing turn is one of `runs`: that
    // turn, whether its copy is late, and the latest restore's undo while the
    // project is still as that restore left it, with the version it holds.
    async options(request) {
      if (!validOptionsRequest(request)) return refusal(400, 'invalid-project-restore-request');
      const root = canonicalRoot(workspaceRoot());
      if (!root) return refusal(503, 'project-restore-unavailable');
      const ids = new Map(request.runs.map(run => [run.toLowerCase(), run]));
      await Promise.all([...ids.keys()].map(id => settled(ending.get(id))));
      try {
        const answer = await queue({operation: 'options', workspace: root, runs: [...ids.keys()]});
        if (answer?.error === 'unsupported') return {status: 200, body: {schemaVersion: 1, projects: []}};
        if (typeof answer?.error === 'string') return refusal(...(REFUSALS[answer.error] ?? [503, 'project-restore-unavailable']));
        const entries = answer?.projects;
        if (!Array.isArray(entries) || entries.length > 32 || !entries.every(entry => validOption(entry, ids))) {
          return refusal(503, 'project-restore-unavailable');
        }
        return {status: 200, body: {schemaVersion: 1, projects: entries.map(entry => ({...entry, snapshot: ids.get(entry.snapshot)}))}};
      } catch (error) {
        warn(`Portal could not read its project copies (${error.message}).`);
        return refusal(503, 'project-restore-unavailable');
      }
    },
  };
}

async function readRequest(req) {
  const contentType = String(req.headers?.['content-type'] ?? '').toLowerCase();
  if (contentType.split(';', 1)[0].trim() !== 'application/json') return null;
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > MAX_BODY) return null;
    chunks.push(Buffer.from(chunk));
  }
  try { return JSON.parse(Buffer.concat(chunks, total).toString('utf8')); }
  catch (error) { if (error instanceof SyntaxError) return null; throw error; }
}

// POST /pixel-ods/project-restore {"user","project","snapshot"[,"confirm"]}
// (the host ingress asks under that chat's history lock) and
// /pixel-ods/project-restore-options {"runs"}.
export function createProjectRestoreRoute({snapshots, sendJson, kind = 'restore'}) {
  return async (req, res) => {
    if (req.url !== ROUTES[kind]) { sendJson(res, 400, {error: 'invalid request'}); return true; }
    if (req.method !== 'POST') { sendJson(res, 405, {error: 'method not allowed'}); return true; }
    const request = await readRequest(req);
    const {status, body} = request ? await snapshots[kind](request) : refusal(400, 'invalid-project-restore-request');
    sendJson(res, status, body);
    return true;
  };
}
