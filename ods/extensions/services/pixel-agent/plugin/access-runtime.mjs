// One gateway-process admission barrier for every agent, including native/cron
// runs. Decisions and disk commits are synchronous: admission cannot interleave
// with acquiring the transition lease. Never retain hook conversation payloads.
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {readRuntimeSettings} from './settings-runtime-readback.mjs';

const hex = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const revision = () => crypto.randomBytes(32).toString('hex');
const blocked = () => ({outcome: 'block', reason: 'ods-access-transition',
  message: 'Pixel access settings are changing. Retry when the transition completes.'});
const bootId = value => typeof value === 'string' && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);

function processAlive(pid) {
  try { process.kill(pid, 0); return true; }
  catch (error) { if (error.code === 'ESRCH') return false; throw error; }
}

function processStartTicks(pid) {
  let stat;
  try { stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8'); }
  catch (error) {
    if (error.code === 'ENOENT' && !processAlive(pid)) return null;
    throw error;
  }
  // comm can contain spaces and closing parentheses. Fields after its LAST
  // closing parenthesis begin at field 3; starttime is field 22, not wall time.
  const end = stat.lastIndexOf(')');
  const startTicks = stat.slice(end + 2).trim().split(/\s+/)[19];
  if (!stat.startsWith(`${pid} (`) || end < 0 || stat[end + 1] !== ' ' ||
      !/^(0|[1-9][0-9]*)$/.test(startTicks ?? '')) {
    throw new Error('process start identity unavailable');
  }
  return startTicks;
}

function processInvocation(pid) {
  // ProcSubset=pid hides boot_id. Read only this exact bounded environment
  // value; discard unrelated entries without retaining or reporting them.
  const prefix = Buffer.from('INVOCATION_ID='), fd = fs.openSync(`/proc/${pid}/environ`, 'r');
  const chunk = Buffer.alloc(4096);
  let matched = 0, value = '', found = null, bytes = 0;
  try {
    let count;
    while ((count = fs.readSync(fd, chunk, 0, chunk.length, null)) > 0) {
      bytes += count;
      if (bytes > 1048576) throw new Error('process environment identity unavailable');
      for (const byte of chunk.subarray(0, count)) {
        if (byte === 0) {
          if (matched === prefix.length) {
            if (found !== null || !/^[a-f0-9]{32}$/.test(value)) throw new Error('process invocation identity unavailable');
            found = value;
          }
          matched = 0; value = '';
        } else if (matched === prefix.length) {
          if (value.length >= 32) throw new Error('process invocation identity unavailable');
          value += String.fromCharCode(byte);
        } else if (matched >= 0) matched = byte === prefix[matched] ? matched + 1 : -1;
      }
    }
    if (matched !== 0 || found === null) throw new Error('process invocation identity unavailable');
    return found;
  } finally { chunk.fill(0); fs.closeSync(fd); }
}

function linuxProcessIdentity(pid, format = 'auto') {
  let boot;
  if (format !== 'invocation') {
    try { boot = fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim(); }
    catch (error) {
      if (format !== 'auto' || !['ENOENT', 'EACCES', 'EPERM'].includes(error.code)) throw error;
    }
    if (boot !== undefined && !bootId(boot)) throw new Error('process boot identity unavailable');
  }
  const startTicks = processStartTicks(pid);
  if (startTicks === null) return null;
  if (boot !== undefined) {
    if (fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim() !== boot) throw new Error('process boot identity changed');
    return {version: 2, pid, bootId: boot, startTicks};
  }
  const invocationId = processInvocation(pid);
  // Bind the environment read to one process incarnation, including exits
  // and PID reuse during the read. Never compare only our own invocation ID.
  if (processStartTicks(pid) !== startTicks) throw new Error('process invocation identity changed');
  return {version: 3, pid, invocationId, startTicks};
}

function previousProcessAlive(previous) {
  if (!previous || Array.isArray(previous) || !Number.isSafeInteger(previous.pid) || previous.pid < 1) {
    throw new Error('invalid process lock');
  }
  const keys = Object.keys(previous).sort().join(',');
  // A live legacy record has no reliable start identity: never infer it stale.
  if (keys === 'pid') return processAlive(previous.pid);
  const boot = previous.version === 2 && keys === 'bootId,pid,startTicks,version' && bootId(previous.bootId);
  const invocation = previous.version === 3 && keys === 'invocationId,pid,startTicks,version' &&
    typeof previous.invocationId === 'string' && /^[a-f0-9]{32}$/.test(previous.invocationId);
  if ((!boot && !invocation) || typeof previous.startTicks !== 'string' ||
      !/^(0|[1-9][0-9]*)$/.test(previous.startTicks) || process.platform !== 'linux') throw new Error('unknown process lock identity');
  // Death is independently verifiable even if an older record used boot_id
  // and this gateway's namespace now hides it. A live unknown owner stays held.
  if (!processAlive(previous.pid)) return false;
  // A reused PID may belong to an unrelated process whose environment is
  // unreadable under proc restrictions. A different start time already proves
  // that the recorded owner is gone; do not require that stranger's identity.
  // Matching start times still require the full boot/invocation check below.
  if (processStartTicks(previous.pid) !== previous.startTicks) return false;
  const current = linuxProcessIdentity(previous.pid, boot ? 'boot' : 'invocation');
  return current !== null && current.startTicks === previous.startTicks &&
    (boot ? current.bootId === previous.bootId : current.invocationId === previous.invocationId);
}

export function executionHostForAgent(config, id = 'pixel') {
  const agents = config?.agents?.list?.filter(agent => agent?.id === id) ?? [];
  if (agents.length !== 1) throw new Error('Pixel agent configuration is ambiguous');
  const mode = agents[0].sandbox?.mode ?? config.agents?.defaults?.sandbox?.mode ?? 'off';
  const host = agents[0].tools?.exec?.host ?? config.tools?.exec?.host ?? 'sandbox';
  if (mode === 'off' && host === 'gateway') return 'gateway';
  if (mode === 'all' && host === 'sandbox') return 'sandbox';
  throw new Error('Pixel execution mode is unsupported');
}

export function createAccessRuntime({directory = path.join(os.homedir(), '.openclaw', '.ods-access-runtime'),
  config, settingsConfig, createTools, resolveSandbox, execControl, runtimeVersion = 'unknown', hooksAllowed = false,
  readProcessSessions,
  probeDirectory = path.join('/var/lib/ods-pixel-access-probes', String(process.getuid?.() ?? 'unsupported'))} = {}) {
  if (typeof process.getuid !== 'function') {
    const unavailable = () => { throw new Error('POSIX admission unavailable'); };
    return {status: () => ({available: false, phase: 'unavailable', revision: null, active: 0, proof: null}),
      admit: () => ({outcome: 'pass'}), finish() {}, beforeTool() {}, afterTool() {},
      acquire: unavailable, release: unavailable, probe: unavailable, readSettings: unavailable,
      owns: () => false, isProbe: () => false};
  }
  // Admission coverage was inspected against these exact installed contracts.
  // Other releases keep normal guard behavior, but cannot change access until
  // their hook coverage is qualified. Held state always continues to block.
  const qualified = runtimeVersion === '2026.6.33' && hooksAllowed === true;
  const runs = new Set(), tools = new Set(), detached = new Map(), internalRuns = new Set();
  const filename = path.join(directory, 'state.json');
  let state, failed = false, probeRun = null, proof = null, probeFailure = null;
  let processTimer = null, processCheck = null;
  const isInternal = context => (probeRun !== null && context?.runId === probeRun) || internalRuns.has(context?.runId);
  // Construct only the SDK's scoped process-list reader. Never execute a shell,
  // change the configured policy, or retain its command/output fields.
  const inspectProcesses = readProcessSessions ?? (typeof config === 'function' && typeof createTools === 'function'
    ? async context => {
      const cfg = config(), runId = `ods-access-process-check-${crypto.randomUUID()}`;
      const agent = cfg?.agents?.list?.find(item => item?.id === context.agentId);
      if (!agent) throw new Error('process scope unavailable');
      internalRuns.add(runId);
      try {
        const reader = createTools({config: cfg, agentId: context.agentId,
          sessionKey: context.sessionKey, sessionId: context.sessionId, runId,
          workspaceDir: agent.workspace, cwd: agent.workspace, oneShotCliRun: true})
          .find(tool => tool.name === 'process');
        if (!reader) throw new Error('process reader unavailable');
        const result = await reader.execute(runId, {action: 'list'});
        if (result?.isError || result?.details?.status !== 'completed' || !Array.isArray(result.details.sessions)) {
          throw new Error('process status unavailable');
        }
        return result.details.sessions.map(({sessionId, startedAt, status}) => ({sessionId, startedAt, status}));
      } finally { internalRuns.delete(runId); }
    } : null);
  function privateEntry(target, directoryEntry = false) {
    const s = fs.lstatSync(target);
    if (s.isSymbolicLink() || s.uid !== process.getuid() || (s.mode & 0o077) ||
        (directoryEntry ? !s.isDirectory() : !s.isFile() || s.nlink !== 1)) throw new Error('unsafe runtime state');
    return s;
  }
  function save() {
    privateEntry(directory, true);
    const temporary = path.join(directory, `.state-${revision()}`);
    let fd;
    try {
      fd = fs.openSync(temporary, 'wx', 0o600);
      fs.writeFileSync(fd, JSON.stringify(state)); fs.fsyncSync(fd); fs.closeSync(fd); fd = undefined;
      fs.renameSync(temporary, filename);
      const dir = fs.openSync(directory, 'r'); try { fs.fsyncSync(dir); } finally { fs.closeSync(dir); }
    } catch (error) { failed = true; throw error; }
    finally { if (fd !== undefined) fs.closeSync(fd); if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  }
  function claimProcess() {
    // Non-Linux POSIX surfaces retain the conservative legacy PID contract.
    if (process.platform !== 'linux') return () => {};
    // Linux requires util-linux /usr/bin/flock. The inherited descriptor shares
    // the parent's open file description, so its lock survives helper exit and
    // is released by close or gateway death, even during stale-record recovery.
    const claim = path.join(directory, '.process-claim');
    if (fs.existsSync(claim)) privateEntry(claim);
    const fd = fs.openSync(claim, fs.constants.O_CREAT | fs.constants.O_RDWR | fs.constants.O_NOFOLLOW, 0o600);
    try {
      const opened = fs.fstatSync(fd), current = privateEntry(claim);
      if (opened.dev !== current.dev || opened.ino !== current.ino) throw new Error('process claim changed');
      const result = spawnSync('/usr/bin/flock', ['--exclusive', '--nonblock', '3'],
        {stdio: ['ignore', 'ignore', 'ignore', fd], timeout: 5000});
      if (result.error || result.status !== 0) throw new Error('process claim unavailable');
      return () => fs.closeSync(fd);
    } catch (error) { fs.closeSync(fd); throw error; }
  }
  try {
    if (!fs.existsSync(directory)) fs.mkdirSync(directory, {mode: 0o700});
    privateEntry(directory, true);
    const lock = path.join(directory, 'process.json');
    // Serialize inspection AND replacement, including the missing-lock case.
    // An inode check alone cannot prevent two stale claimants unlinking a new
    // live owner's record. Never unlink the reusable kernel-lock file.
    const releaseClaim = claimProcess();
    try {
      const identity = process.platform === 'linux' ? linuxProcessIdentity(process.pid) : {pid: process.pid};
      if (!identity) throw new Error('current process identity unavailable');
      if (fs.existsSync(lock)) {
        const entry = privateEntry(lock);
        if (entry.size > 4096) throw new Error('oversized process lock');
        const previous = JSON.parse(fs.readFileSync(lock, 'utf8'));
        if (previousProcessAlive(previous)) throw new Error('another runtime owns admission');
        const current = privateEntry(lock);
        if (current.dev !== entry.dev || current.ino !== entry.ino) throw new Error('process lock changed');
        fs.unlinkSync(lock);
      }
      const lockFd = fs.openSync(lock, 'wx', 0o600);
      try { fs.writeFileSync(lockFd, JSON.stringify(identity)); fs.fsyncSync(lockFd); }
      finally { fs.closeSync(lockFd); }
    } finally { releaseClaim(); }
    if (fs.existsSync(filename)) {
      privateEntry(filename);
      if (fs.statSync(filename).size > 4096) throw new Error('oversized runtime state');
      state = JSON.parse(fs.readFileSync(filename, 'utf8'));
      if (state.version !== 1 || !hex(state.revision) || !['idle','busy','held','interrupted'].includes(state.phase) ||
          !(state.tokenHash === null || hex(state.tokenHash))) throw new Error('invalid runtime state');
      if (state.phase === 'busy') state.phase = 'interrupted';
    } else state = {version: 1, phase: 'idle', revision: revision(), tokenHash: null};
    // Restart invalidates every previous runtime proof, even at identical config.
    state.revision = revision(); save();
  } catch { failed = true; }
  const busy = () => runs.size + tools.size + detached.size > 0;
  function changed() { state.revision = revision(); save(); }
  function scheduleProcessCheck() {
    if (processTimer !== null || processCheck !== null || failed || !qualified ||
        typeof inspectProcesses !== 'function' || ![...detached.values()].some(record => record.context)) return;
    processTimer = setTimeout(() => {
      processTimer = null;
      void reconcileDetached();
    }, 5000);
    processTimer.unref?.();
  }
  function reconcileDetached() {
    if (processCheck !== null) return processCheck;
    if (failed || !qualified || typeof inspectProcesses !== 'function') return Promise.resolve(status());
    if (processTimer !== null) { clearTimeout(processTimer); processTimer = null; }
    const pending = [...detached.entries()];
    processCheck = (async () => {
      for (const [sessionId, record] of pending) {
        if (!record.context || !Number.isSafeInteger(record.startedAt) || record.startedAt <= 0) continue;
        try {
          const sessions = await inspectProcesses(record.context);
          if (failed || detached.get(sessionId) !== record || !Array.isArray(sessions)) continue;
          const matches = sessions.filter(item => item?.sessionId === sessionId);
          // Absence, expiration, malformed results and a reused session ID do
          // not prove this particular process finished. Keep admission held.
          if (matches.length !== 1 || matches[0].startedAt !== record.startedAt ||
              !['completed', 'failed', 'exited'].includes(matches[0].status)) continue;
          detached.delete(sessionId);
          if (!busy() && state.phase === 'busy') state.phase = 'idle';
          changed();
        } catch { /* Keep the exact outstanding record and retry later. */ }
      }
      return status();
    })().finally(() => { processCheck = null; scheduleProcessCheck(); });
    return processCheck;
  }
  function status() {
    return {available: !failed && qualified, phase: failed ? 'unavailable' : state.phase,
      revision: failed ? null : state.revision, active: runs.size + tools.size + detached.size,
      pid: process.pid, runtime_version: runtimeVersion, proof, probe_failure: probeFailure};
  }
  function admit(_event, context) {
    const id = context?.runId;
    if (failed || typeof id !== 'string' || !id || ['held','interrupted'].includes(state.phase)) return blocked();
    runs.add(id); state.phase = 'busy';
    try { changed(); } catch { return blocked(); }
    return {outcome: 'pass'};
  }
  function finish(event, context) {
    runs.delete(context?.runId ?? event?.runId);
    if (!failed && !busy() && state.phase === 'busy') { state.phase = 'idle'; changed(); }
  }
  function beforeTool(event, context) {
    if (isInternal(context)) return;
    if (failed || ['held','interrupted'].includes(state.phase)) return {block: true, blockReason: blocked().message};
    // Missing identities cannot be paired safely; fail closed before execution.
    if (!event?.toolCallId) return {block: true, blockReason: 'Tool identity unavailable during access coordination.'};
    tools.add(event.toolCallId); state.phase = 'busy'; changed();
  }
  function afterTool(event, context) {
    if (isInternal(context)) return;
    tools.delete(event?.toolCallId);
    const detail = event?.result?.details;
    let detachedChanged = false;
    if (event?.toolName === 'exec' && detail?.status === 'running' && detail.sessionId) {
      const scope = typeof context?.agentId === 'string' && context.agentId &&
        typeof context?.sessionKey === 'string' && context.sessionKey
        ? {agentId: context.agentId, sessionKey: context.sessionKey,
          ...(typeof context.sessionId === 'string' ? {sessionId: context.sessionId} : {})} : null;
      detached.set(detail.sessionId, {startedAt: detail.startedAt, context: scope});
      detachedChanged = true;
      scheduleProcessCheck();
    }
    if (event?.toolName === 'process' && event?.params?.sessionId &&
        ['completed','failed','exited'].includes(detail?.status) &&
        (Number.isInteger(detail.exitCode) || (typeof detail.exitSignal === 'string' && detail.exitSignal))) {
      detachedChanged = detached.delete(event.params.sessionId) || detachedChanged;
    }
    if (!failed) {
      if (!busy() && state.phase === 'busy') { state.phase = 'idle'; detachedChanged = true; }
      if (detachedChanged) changed();
    }
  }
  function acquire(token, expected) {
    if (failed || !qualified || !hex(token) || !hex(expected)) throw new Error('runtime unavailable');
    if (state.phase === 'held' && state.tokenHash === hash(token)) return status();
    if (expected !== state.revision || busy() || !['idle','interrupted'].includes(state.phase)) throw new Error('runtime busy or changed');
    state.phase = 'held'; state.tokenHash = hash(token); proof = null; changed(); return status();
  }
  function owns(token) { return !failed && hex(token) && state.phase === 'held' && state.tokenHash === hash(token); }
  function readSettings(token, expected) {
    // Bind readback to this process's current hold, not a token from before
    // restart or release. Keep it synchronous with admission and lease checks.
    if (!qualified || !owns(token) || !hex(expected) || expected !== state.revision || busy() || probeRun) {
      throw new Error('runtime lease mismatch');
    }
    // A registration-time snapshot can lag hot reload. Require the SDK's
    // current runtime getter; never fall back to disk or startup config.
    if (typeof settingsConfig !== 'function') throw new Error('runtime settings snapshot unavailable');
    return readRuntimeSettings(settingsConfig(), {pid: process.pid, runtimeVersion,
      revision: state.revision, observedAt: new Date().toISOString()});
  }
  function release(token) {
    if (!owns(token) || busy() || probeRun) throw new Error('runtime lease mismatch');
    state.phase = 'idle'; state.tokenHash = null; changed(); return status();
  }
  async function probe(token) {
    if (!owns(token) || busy() || probeRun) throw new Error('runtime lease mismatch');
    probeFailure = null;
    const cfg = config();
    const agent = cfg.agents.list.find(entry => entry.id === 'pixel');
    const workspace = agent.workspace ?? cfg.agents.defaults?.workspace;
    if (!path.isAbsolute(workspace ?? '')) throw new Error('workspace unavailable');
    const executionHost = executionHostForAgent(cfg);
    const model = agent.model ?? cfg.agents.defaults?.model;
    const primary = typeof model === 'string' ? model : model?.primary;
    if (typeof primary !== 'string' || !primary.includes('/')) throw new Error('model policy identity unavailable');
    const modelProvider = primary.slice(0, primary.indexOf('/'));
    const modelId = primary.slice(primary.indexOf('/') + 1);
    const id = crypto.randomUUID(), sessionKey = `agent:pixel:ods-access-proof:${id}`;
    const runId = `ods-access-proof-${id}`;
    // Root provisions this empty owner-private fixture outside home/workspace.
    // Its host path is fixed by UID; callers cannot supply a write destination.
    const outside = probeDirectory;
    try { privateEntry(outside, true); }
    catch (error) { probeFailure = 'probe-directory-unavailable'; throw error; }
    const sentinel = path.join(outside, `sentinel-${id}`);
    const nonce = revision();
    probeRun = runId;
    let stage = 'sandbox-resolution';
    try {
      const sandbox = await resolveSandbox({config: cfg, sessionKey, workspaceDir: workspace});
      if ((executionHost === 'sandbox') !== Boolean(sandbox?.enabled)) throw new Error('sandbox runtime mismatch');
      stage = 'core-tool-construction';
      const allTools = createTools({config: cfg, agentId: 'pixel', sessionKey, runId, sessionId: id,
        workspaceDir: workspace, cwd: workspace, sandbox, modelProvider, modelId, oneShotCliRun: true});
      const exec = allTools.find(tool => tool.name === 'exec');
      const write = allTools.find(tool => tool.name === 'write');
      if (!exec || !write) throw new Error('required core tools unavailable');
      // These calls use the installed core tool constructors and unchanged
      // loaded policy. They do not request a model or accept arbitrary commands.
      const control = execControl();
      stage = 'core-exec';
      const command = control.prepare(runId, `printf '%s' '${nonce}'`);
      const result = await exec.execute(`proof-exec-${id}`, {command, timeout: 10});
      const text = result?.content?.filter(item => item.type === 'text').map(item => item.text).join('\n') ?? '';
      if (result?.isError || !text.includes(nonce) || result?.details?.status === 'running') throw new Error('core exec proof failed');
      stage = 'core-cancellation';
      const cancelledCommand = control.prepare(runId, 'sleep 30');
      let signalFailed = false;
      const timer = setTimeout(() => { try { control.signal(runId); } catch { signalFailed = true; } }, 250);
      let cancelled;
      try { cancelled = await exec.execute(`proof-cancel-${id}`, {command: cancelledCommand, timeout: 10, yieldMs: 10000}); }
      finally { clearTimeout(timer); }
      if (signalFailed || cancelled?.details?.exitCode !== 130) throw new Error('core cancellation proof failed');
      stage = 'filesystem-boundary';
      let writeDenied = false;
      try {
        const written = await write.execute(`proof-write-${id}`, {path: sentinel, content: nonce});
        writeDenied = written?.isError === true;
      } catch { writeDenied = true; }
      const present = fs.existsSync(sentinel) && fs.readFileSync(sentinel, 'utf8') === nonce;
      if (executionHost === 'gateway' ? (!present || writeDenied) : (present || !writeDenied)) throw new Error('filesystem boundary proof failed');
      proof = {mode: executionHost === 'gateway' ? 'full-access' : 'sandboxed', pid: process.pid,
        config_sha256: hash(JSON.stringify(cfg)), executed: true, at: new Date().toISOString()};
      return status();
    } catch (error) {
      // Fixed stage identifiers aid recovery without exposing commands,
      // credentials, private paths, or arbitrary provider diagnostics.
      probeFailure = stage;
      throw error;
    } finally {
      execControl().clear(runId); probeRun = null;
      // Only the unique directory and sentinel created by this probe.
      if (fs.existsSync(sentinel)) fs.unlinkSync(sentinel);
    }
  }
  return {status, admit, finish, beforeTool, afterTool, acquire, release, probe, owns, readSettings, reconcileDetached,
    isProbe: isInternal};
}
