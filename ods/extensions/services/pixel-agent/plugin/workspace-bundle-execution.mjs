import {readFileSync} from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {executionHostForAgent} from './access-runtime.mjs';

const fail = code => Object.assign(new Error(code), {code});
const digest = config => createHash('sha256').update(JSON.stringify(config)).digest('hex');

// Uses the installed SDK's normal scoped tools and unchanged policy. There is
// no direct host subprocess or filesystem fallback if that boundary is absent.
export function createWorkspaceBundleExecution({readConfig, createTools, resolveSandbox, execControl,
  helperSource = () => readFileSync(new URL('./workspace-bundle.py', import.meta.url), 'utf8')} = {}) {
  const prepared = new WeakMap();
  function scopeForContext(active, factory) {
    const config = readConfig(), pixel = config?.agents?.list?.find(agent => agent.id === 'pixel');
    const workspaceRoot = pixel?.workspace ?? config?.agents?.defaults?.workspace;
    if (active?.agentId !== 'pixel' || !active.runId || !active.sessionKey || !active.sessionId ||
        !path.isAbsolute(workspaceRoot ?? '') || factory?.workspaceDir !== workspaceRoot ||
        factory?.agentId !== active.agentId || factory?.sessionId !== active.sessionId || factory?.sessionKey !== active.sessionKey)
      throw fail('bundle-execution-scope-unavailable');
    return Object.freeze({...active, workspaceRoot, configDigest: digest(config), factory});
  }
  async function toolsForScope(scope, signal) {
    if (digest(readConfig()) !== scope.configDigest || signal?.aborted) throw fail('bundle-runtime-changed');
    if (!prepared.has(scope)) prepared.set(scope, (async () => {
      const config = readConfig(), executionHost = executionHostForAgent(config, 'pixel');
      const sandbox = await resolveSandbox({config, sessionKey: scope.sessionKey, workspaceDir: scope.workspaceRoot});
      if ((executionHost === 'sandbox') !== Boolean(sandbox?.enabled)) throw fail('bundle-sandbox-mismatch');
      const model = scope.factory.activeModel;
      const tools = createTools({config, agentId: 'pixel', runId: scope.runId, sessionId: scope.sessionId,
        sessionKey: scope.sessionKey, runSessionKey: scope.sessionKey,
        workspaceDir: scope.workspaceRoot, cwd: scope.workspaceRoot, sandbox,
        modelProvider: model?.provider, modelId: model?.modelId,
        oneShotCliRun: scope.factory.oneShotCliRun === true,
        emitBeforeToolCallDiagnostics: false});
      const exec = tools.find(tool => tool.name === 'exec');
      if (!exec) throw fail('bundle-core-exec-unavailable');
      return {exec, process:tools.find(tool => tool.name === 'process')};
    })());
    return prepared.get(scope);
  }
  async function runHelper(scope, payload, signal, lifecycle = {}) {
    let launched = false, ownedSession, runtimeTools, cancelled = false;
    const settled = () => lifecycle.onSettled?.();
    const cancel = () => {
      cancelled = true;
      try { execControl().signal(scope.runId); } catch { /* Exact process kill/drain remains required below. */ }
    };
    try {
      runtimeTools = await toolsForScope(scope, signal);
      const source = helperSource();
      if (typeof source !== 'string' || Buffer.byteLength(source) > 64 * 1024) throw fail('bundle-helper-unavailable');
      const encodedSource = Buffer.from(source).toString('base64');
      const encodedRequest = Buffer.from(JSON.stringify(payload)).toString('base64');
      if (encodedRequest.length > 131072) throw fail('bundle-request-too-large');
      // Fixed helper code, encoded data, isolated Python imports. No model code.
      const command = `python3 -I -S -c 'import base64; exec(compile(base64.b64decode("${encodedSource}"), "<ods-workspace-bundle>", "exec"))' '${encodedRequest}'`;
      const controlled = execControl().prepare(scope.runId, command);
      if (signal?.aborted || digest(readConfig()) !== scope.configDigest) throw fail('bundle-runtime-changed');
      signal?.addEventListener('abort', cancel, {once:true});
      launched = true;
      // Do not abandon the SDK promise on abort: cancellation is signalled to
      // the existing wrapper, then its exact process is observed to settlement.
      let result = await runtimeTools.exec.execute(`ods-bundle-${scope.toolCallId}-${payload.generation}`, {
        command: controlled, workdir: scope.workspaceRoot, timeout: 10, yieldMs: 10000, background: false,
      });
      if (result?.details?.status === 'running' && typeof result.details.sessionId === 'string') {
        ownedSession = result.details.sessionId;
        lifecycle.onProcess?.({sessionId:ownedSession,runId:scope.runId});
        if (!runtimeTools.process) throw fail('bundle-execution-unsettled');
        let killed = false;
        for (let attempt = 0; attempt < 4; attempt++) {
          if ((cancelled || signal?.aborted || attempt >= 2) && !killed) {
            await runtimeTools.process.execute(`ods-bundle-kill-${scope.toolCallId}`,
              {action:'kill',sessionId:ownedSession});
            killed = true;
          }
          result = await runtimeTools.process.execute(`ods-bundle-poll-${scope.toolCallId}-${attempt}`,
            {action:'poll',sessionId:ownedSession,timeout:10000});
          if (result?.details?.sessionId !== ownedSession) throw fail('bundle-execution-unsettled');
          if (['completed','failed'].includes(result.details.status) && typeof result.details.aggregated === 'string') break;
        }
      }
      const details = result?.details;
      const terminal = ['completed','failed'].includes(details?.status) &&
        (Number.isInteger(details.exitCode) || ownedSession && details.sessionId === ownedSession && typeof details.aggregated === 'string');
      if (!terminal) throw fail('bundle-execution-unsettled');
      settled();
      if (cancelled || signal?.aborted) throw fail('bundle-cancelled');
      if (digest(readConfig()) !== scope.configDigest) throw fail('bundle-runtime-changed');
      const text = typeof details.aggregated === 'string' ? details.aggregated
        : result.content?.filter(item => item.type === 'text').map(item => item.text).join('\n');
      let value;
      try { value = JSON.parse(text); } catch { throw fail('bundle-receipt-unavailable'); }
      if (result.isError || details.status !== 'completed' || details.exitCode !== 0 || value.status !== 'succeeded') {
        const error = fail(typeof value.error === 'string' && /^[a-z-]{1,80}$/.test(value.error) ? value.error : 'bundle-helper-failed');
        const prefix = `${payload.request.outputRoot}/${payload.generation}/`;
        const allowed = new Set([payload.request.mappingPath,...payload.request.files.map(item=>item.copyTo)].map(name=>prefix+name));
        if (Array.isArray(value.written) && value.written.every(item => allowed.has(item))) error.written = [...new Set(value.written)];
        throw error;
      }
      return value;
    } finally {
      signal?.removeEventListener('abort', cancel);
      if (!launched) settled();
    }
  }
  return {scopeForContext, runHelper};
}
