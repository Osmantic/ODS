import {createHash} from 'node:crypto';
import path from 'node:path';
import {readonlyShellCommand} from './readonly-execution-config.mjs';
import {normalizeReadonlyExec} from './readonly-execution-tool.mjs';

const fail = (code, cause) => Object.assign(new Error(code), {code, ...(cause ? {cause} : {})});
const digest = config => createHash('sha256').update(JSON.stringify(config)).digest('hex');
const absolute = value => typeof value === 'string' && value !== '/' && path.posix.isAbsolute(value) &&
  path.posix.normalize(value) === value && !value.endsWith('/') && !/[\\\x00-\x1f\x7f]/.test(value);
const nonempty = value => typeof value === 'string' && value.length > 0;

function safeError(error, code) {
  const wrapped=fail(typeof error?.code === 'string' ? error.code : code,error);
  if(error?.readonlyCleanupConfirmed === true)wrapped.readonlyCleanupConfirmed=true;
  if(typeof error?.readonlyRecovery?.cleanup === 'function')wrapped.readonlyRecovery=error.readonlyRecovery;
  return wrapped;
}

// The provider never provisions a host sandbox itself. Every allocation and
// recovery is delegated to the injected allocator, whose errors must carry
// readonlyCleanupConfirmed:true or readonlyRecovery:{cleanup} when uncertain.
export function createReadonlyExecutionProvider({readConfig,createTools,resolveSandbox,allocator} = {}) {
  if (typeof readConfig !== 'function' || typeof createTools !== 'function' ||
      typeof resolveSandbox !== 'function' || typeof allocator?.allocate !== 'function' ||
      typeof allocator?.recover !== 'function')
    throw new TypeError('readonly provider dependencies required');

  function scopeForContext(active, factory) {
    const config = readConfig();
    const pixels = config?.agents?.list?.filter(agent => agent?.id === 'pixel');
    if (active?.agentId !== 'pixel' || !nonempty(active.runId) || !nonempty(active.sessionId) ||
        !nonempty(active.sessionKey) || !nonempty(active.toolCallId) || pixels?.length !== 1)
      throw fail('readonly-scope-invalid');
    const workspaceRoot = pixels[0].workspace ?? config?.agents?.defaults?.workspace;
    if (!absolute(workspaceRoot) || factory?.workspaceDir !== workspaceRoot ||
        factory?.agentId !== active.agentId || factory?.sessionId !== active.sessionId ||
        factory?.sessionKey !== active.sessionKey)
      throw fail('readonly-scope-mismatch');
    const model = factory.activeModel;
    if(model != null && (!nonempty(model.provider) || !nonempty(model.modelId)))
      throw fail('readonly-model-unavailable');
    const frozenFactory = Object.freeze({agentId:factory.agentId,sessionId:factory.sessionId,
      sessionKey:factory.sessionKey,workspaceDir:factory.workspaceDir,oneShotCliRun:factory.oneShotCliRun === true,
      activeModel:model == null ? undefined : Object.freeze({provider:model.provider,modelId:model.modelId})});
    return Object.freeze({agentId:active.agentId, runId:active.runId, sessionId:active.sessionId,
      sessionKey:active.sessionKey, toolCallId:active.toolCallId, workspaceRoot,
      configDigest:digest(config), factory:frozenFactory});
  }

  async function prepare(scope, request) {
    let normalized,config;
    const current = () => digest(readConfig()) === scope.configDigest;
    try {
      if(scope?.agentId !== 'pixel' || !['runId','sessionId','sessionKey','toolCallId'].every(key=>nonempty(scope[key])) ||
          !absolute(scope.workspaceRoot) || !nonempty(scope.configDigest) ||
          scope.factory?.workspaceDir !== scope.workspaceRoot ||
          ['agentId','sessionId','sessionKey'].some(key=>scope[key] !== scope.factory?.[key]))
        throw fail('readonly-scope-invalid');
      normalized=normalizeReadonlyExec(request);
      if(!current())throw fail('readonly-config-drift');
      await allocator.recover();
      config=structuredClone(readConfig());
      if(digest(config)!==scope.configDigest)throw fail('readonly-config-drift');
    } catch(error) {
      const wrapped=safeError(error,'readonly-preparation-unavailable');
      wrapped.readonlyCleanupConfirmed=true; // This call has not allocated anything.
      throw wrapped;
    }
    let allocation;
    try {
      allocation = await allocator.allocate(scope,config);
      if (!allocation || !allocation.config || !nonempty(allocation.sandboxSessionKey) ||
          allocation.ownerWorkspace !== scope.workspaceRoot || typeof allocation.attach !== 'function' ||
          typeof allocation.cleanup !== 'function')
        throw fail('readonly-allocation-invalid');
      if (!current()) throw fail('readonly-config-drift');
      const sandbox = await resolveSandbox({config:allocation.config,
        sessionKey:allocation.sandboxSessionKey, workspaceDir:scope.workspaceRoot});
      if (!sandbox || sandbox.enabled !== true || sandbox.workspaceAccess !== 'ro')
        throw fail('readonly-sandbox-invalid');
      if (!absolute(sandbox.agentWorkspaceDir) || sandbox.agentWorkspaceDir !== scope.workspaceRoot)
        throw fail('readonly-sandbox-owner-mismatch');
      if (!absolute(sandbox.workspaceDir) || sandbox.workspaceDir === sandbox.agentWorkspaceDir)
        throw fail('readonly-sandbox-bootstrap-invalid');
      await allocation.attach(sandbox);
      if (!current()) throw fail('readonly-config-drift');
      const model = scope.factory.activeModel;
      const toolArgs = {config:allocation.config, agentId:'pixel', runId:scope.runId,
        sessionId:scope.sessionId, sessionKey:scope.sessionKey, runSessionKey:scope.sessionKey,
        workspaceDir:scope.workspaceRoot, cwd:scope.workspaceRoot, sandbox,
        oneShotCliRun:scope.factory.oneShotCliRun === true, emitBeforeToolCallDiagnostics:false};
      if (model && nonempty(model.provider) && nonempty(model.modelId)) {
        toolArgs.modelProvider = model.provider;
        toolArgs.modelId = model.modelId;
      }
      const tools = createTools(toolArgs);
      const exec = tools?.find(tool => tool.name === 'exec');
      if (!exec || typeof exec.execute !== 'function') throw fail('readonly-exec-unavailable');
      const params = Object.freeze({command:readonlyShellCommand(normalized),
        workdir:sandbox.workspaceDir, timeout:normalized.timeoutSeconds, yieldMs:120000, background:false});
      let cleaned = false;
      const cleanup = async () => {
        if (cleaned) return;
        await allocation.cleanup();
        cleaned = true;
      };
      return {exec, params, stillCurrent:current, cleanup};
    } catch (error) {
      if (!allocation) throw error;
      const err = safeError(error, 'readonly-prepare-failed');
      try {
        await allocation.cleanup();
        err.readonlyCleanupConfirmed = true;
      } catch (cleanupError) {
        err.readonlyRecovery = {cleanup:() => allocation.cleanup()};
        err.code = 'readonly-cleanup-unconfirmed';
        try { err.cause = cleanupError; } catch {}
      }
      throw err;
    }
  }

  async function recover() {
    return allocator.recover();
  }

  return {scopeForContext, prepare, recover};
}
