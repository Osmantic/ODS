// Native sessions_spawn resolves relative cwd against the gateway process.
// Anchor only that relative spelling; authorization remains with core tools.
import fs from 'node:fs';
import path from 'node:path';

const plain = value => value && typeof value === 'object' && !Array.isArray(value);
const valid = value => typeof value === 'string' && value.length > 0 && value.length <= 4096
  && !/[\x00-\x1f\x7f]/.test(value);
const within = (root, target, paths) => {
  const relative = paths.relative(root, target);
  return relative === '' || (!paths.isAbsolute(relative) && relative !== '..' && !relative.startsWith('..' + paths.sep));
};
const deny = () => ({block: true, blockReason: 'Nothing was started: the subagent working directory must already exist. Use a path relative to the configured workspace, or an explicit absolute directory allowed by the current runtime. Relative traversal and workspace symlink escapes are not accepted.'});

export function subagentCwd(value, root, {paths = path, stat = fs.statSync, realpath = fs.realpathSync, resolveUserPath} = {}) {
  if (typeof value === 'string') value = value.trim();
  if (!valid(value) || !valid(root) || !paths.isAbsolute(root)) return deny();
  // Preserve explicit native absolute paths. This is not an authorization
  // grant: core target-agent and sandbox policy still evaluate the call.
  let directory = value;
  if (value.startsWith('~')) {
    // Let the installed SDK interpret OPENCLAW_HOME and OS-specific homes.
    // Never invent a second home expansion convention in this plugin.
    try { directory = resolveUserPath(value); } catch { return deny(); }
    if (!valid(directory) || !paths.isAbsolute(directory)) return deny();
    value = directory;
  }
  if (!paths.isAbsolute(value)) {
    if (value.split(/[\\/]/).includes('..') || /^[A-Za-z]:/.test(value)) return deny();
    directory = paths.resolve(root, value);
    try {
      if (!within(realpath(root), realpath(directory), paths)) return deny();
    } catch { return deny(); }
  }
  try { if (!stat(directory).isDirectory()) return deny(); } catch { return deny(); }
  return {cwd: directory};
}

function selected(event, context, guard) {
  const name = context?.toolName ?? event?.toolName;
  const params = guard?.params ?? event?.params;
  if (!plain(params)) return undefined;
  if (name === 'tool_call') {
    const match = /^(?:openclaw:core:)?(sessions_spawn|read|write|edit)$/.exec(params.id ?? '');
    if (!match || !plain(params.args)) return undefined;
    return {name: match[1], args: params.args, original: event?.params?.args,
      wrap: args => ({...params, args})};
  }
  if (!['sessions_spawn', 'read', 'write', 'edit'].includes(name)) return undefined;
  return {name, args: params, original: event?.params, wrap: args => args};
}

export function withPixelSubagentWorkspace(guard, event, context, agentId, config, readSession, options = {}) {
  if (guard?.block || context?.agentId !== agentId) return guard;
  const call = selected(event, context, guard);
  if (!call) return guard;
  const agent = config?.agents?.list?.filter(value => value?.id === agentId);
  if (agent?.length !== 1) return guard;
  let root = agent[0].workspace ?? config.agents?.defaults?.workspace;
  const mode = agent[0].sandbox?.mode ?? config.agents?.defaults?.sandbox?.mode ?? 'off';
  // Core explicitly disallows project cwd overrides in sandboxed children.
  // Do not translate host paths into container paths or relax that rule.
  if (mode !== 'off') return guard;
  // The configuration retains native home-relative spelling. Resolve it with
  // the same SDK as the runtime, rather than treating a valid ~/ workspace as
  // unavailable or anchoring a child against the gateway's working directory.
  try { if (options.resolveAgentWorkspaceDir) root = options.resolveAgentWorkspaceDir(config, agentId); }
  catch { root = undefined; }
  const child = typeof context?.sessionKey === 'string' && context.sessionKey.startsWith(`agent:${agentId}:subagent:`);
  let entry;
  if (child) {
    try { entry = readSession({agentId, sessionKey: context.sessionKey}); } catch { /* No inferred child cwd. */ }
    if (context.sessionId && entry?.sessionId !== context.sessionId) entry = undefined;
  }
  if (call.name === 'sessions_spawn') {
    if (call.args.runtime === 'acp' || call.args.cwd === undefined) return guard;
    if (child && !entry) return deny();
    const requesterRoot = entry?.spawnedCwd ?? root;
    const result = subagentCwd(call.args.cwd, requesterRoot, options);
    if (result.block) return result;
    return {...guard, params: call.wrap({...call.args, cwd: result.cwd})};
  }
  if (!child) return guard;
  if (!entry || (context.sessionId && entry.sessionId !== context.sessionId)
      || !valid(entry.spawnedCwd) || !path.isAbsolute(entry.spawnedCwd)
      || !valid(root) || !path.isAbsolute(root)) return guard;
  const args = {...call.args};
  // Keep native absolute-path semantics when ODS's generic canonicalizer only
  // removed the configured root. Never replace a different guard correction.
  for (const key of ['path', 'file_path', 'filePath']) {
    const value = args[key];
    if (!valid(value)) continue;
    const original = call.original?.[key];
    if (valid(original) && path.isAbsolute(original)
        && within(root, original, path) && value === path.relative(root, original)) {
      args[key] = original;
      continue;
    }
    if (path.isAbsolute(value) || value.split(/[\\/]/).includes('..')) continue;
    const childRelative = path.relative(root, entry.spawnedCwd);
    const nativeValue = path.normalize(value);
    // The task can name its known project with the owner-workspace prefix.
    // Only that exact child's prefix is an alias, never an arbitrary sibling.
    const shared = childRelative && within(root, entry.spawnedCwd, path)
      && (nativeValue === childRelative || nativeValue.startsWith(childRelative + path.sep));
    args[key] = path.resolve(shared ? root : entry.spawnedCwd, value);
  }
  return {...guard, params: call.wrap(args)};
}
