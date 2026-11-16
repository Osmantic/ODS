import {isDeepStrictEqual} from 'node:util';

export const READONLY_EXEC_TOOL = 'pixel_ods_readonly_exec';

const fail = code => Object.assign(new Error(code), {code});
const MAX_COMMAND = 16384;
const MAX_TIMEOUT = 60;
const MAX_PENDING = 128;
const KNOWN_KEYS = ['command', 'workdir', 'timeoutSeconds'];
const CONTROL = /[\x00-\x1f\x7f]/;

const isPlainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value) &&
  (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);

function normalizeWorkdir(value) {
  if (typeof value !== 'string' || value.length < 1 || value.length > 512) throw fail('invalid-workdir');
  if (value === '.') return value;
  if (CONTROL.test(value)) throw fail('invalid-workdir');
  if (value.startsWith('/')) throw fail('invalid-workdir');
  if (value.includes('\\')) throw fail('invalid-workdir');
  const segments = value.split('/');
  for (const segment of segments) {
    if (segment === '' || segment === '.' || segment === '..') throw fail('invalid-workdir');
  }
  return value;
}

export function normalizeReadonlyExec(value) {
  if (!isPlainObject(value)) throw fail('invalid-readonly-exec-request');
  for (const key of Reflect.ownKeys(value)) {
    if (!KNOWN_KEYS.includes(key)) throw fail('unknown-readonly-exec-field');
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor.enumerable || !('value' in descriptor)) throw fail('invalid-readonly-exec-request');
  }
  const {command} = value;
  if (typeof command !== 'string' || command.length < 1 || command.length > MAX_COMMAND || command.includes('\u0000')) {
    throw fail('invalid-command');
  }
  const workdir = value.workdir === undefined ? '.' : normalizeWorkdir(value.workdir);
  const timeoutSeconds = value.timeoutSeconds === undefined ? 10 : value.timeoutSeconds;
  if (!Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > MAX_TIMEOUT) {
    throw fail('invalid-timeout');
  }
  return Object.freeze({command, workdir, timeoutSeconds});
}

function sanitizeParent(id) {
  return id.trim().replace(/[^A-Za-z0-9_.:-]+/g, '_').slice(0, 120) || 'call';
}

function deferredPrefix(parentId) {
  return `tool_search_code:${sanitizeParent(parentId)}:${READONLY_EXEC_TOOL}:`;
}

function isDeferredChild(id, parentId) {
  const prefix = deferredPrefix(parentId);
  if (!id.startsWith(prefix)) return false;
  return /^[1-9][0-9]*$/.test(id.slice(prefix.length));
}

function snapshotParams(params) {
  return JSON.parse(JSON.stringify(params));
}

function contextComplete(context) {
  return context && typeof context === 'object' &&
    typeof context.agentId === 'string' && context.agentId.length > 0 &&
    typeof context.runId === 'string' && context.runId.length > 0 &&
    typeof context.sessionId === 'string' && context.sessionId.length > 0 &&
    typeof context.sessionKey === 'string' && context.sessionKey.length > 0 &&
    typeof context.toolCallId === 'string' && context.toolCallId.length > 0;
}

function factoryMatches(entry, factory) {
  if (!factory || typeof factory !== 'object') return false;
  return entry.context.agentId === factory.agentId &&
    entry.context.sessionId === factory.sessionId &&
    entry.context.sessionKey === factory.sessionKey;
}

function isDirectToolName(name) {
  return name === READONLY_EXEC_TOOL;
}

function isDeferredToolName(name) {
  return name === 'tool_call';
}

function deferredIdMatches(params) {
  if (!params || typeof params !== 'object') return false;
  return params.id === READONLY_EXEC_TOOL || params.id === 'openclaw:pixel-ods:' + READONLY_EXEC_TOOL;
}

export function createReadonlyExecAdmission() {
  const pending = new Map();

  function evictOldest() {
    while (pending.size >= MAX_PENDING) {
      const oldest = pending.keys().next().value;
      pending.delete(oldest);
    }
  }

  function revokeForCallId(callId) {
    if (!callId) return;
    pending.delete(callId);
    for (const [parentId, entry] of pending) {
      if (entry.deferred && isDeferredChild(callId, parentId)) pending.delete(parentId);
    }
  }

  return {
    before(event, context, decision) {
      // A new observation of this ID supersedes its old admission, including
      // incomplete context, a different tool, or a later refusal.
      const observedId = context?.toolCallId ?? event?.toolCallId;
      if (typeof observedId === 'string') pending.delete(observedId);
      if (!contextComplete(context)) return;
      if (context.agentId !== 'pixel') return;
      if (decision?.block) {
        revokeForCallId(context.toolCallId);
        return;
      }
      const direct = isDirectToolName(event?.toolName);
      const deferred = isDeferredToolName(event?.toolName) && deferredIdMatches(decision?.params ?? event?.params);
      if (!direct && !deferred) return;
      const rawParams = deferred ? (decision?.params ?? event?.params)?.args : (decision?.params ?? event?.params);
      let request;
      try {
        request = normalizeReadonlyExec(rawParams);
      } catch {
        revokeForCallId(context.toolCallId);
        return;
      }
      evictOldest();
      pending.set(context.toolCallId, {
        context: {
          agentId: context.agentId,
          runId: context.runId,
          sessionId: context.sessionId,
          sessionKey: context.sessionKey,
          toolCallId: context.toolCallId,
        },
        request: snapshotParams(request),
        deferred,
        used: false,
      });
    },

    take(callId, request, factory) {
      if (typeof callId !== 'string' || callId.length === 0) throw fail('readonly-exec-call-unbound');
      let entry = pending.get(callId);
      let parentId = callId;
      if (!entry) {
        const matches = [];
        for (const [parent, item] of pending) {
          if (item.deferred && isDeferredChild(callId, parent)) matches.push([parent, item]);
        }
        if (matches.length === 1) {
          parentId = matches[0][0];
          entry = matches[0][1];
        }
      }
      if (!entry || entry.used) throw fail('readonly-exec-call-unbound');
      if (!isDeepStrictEqual(entry.request, request)) throw fail('readonly-exec-call-unbound');
      if (!factoryMatches(entry, factory)) throw fail('readonly-exec-call-unbound');
      entry.used = true;
      pending.delete(parentId);
      return Object.freeze({...entry.context});
    },

    after(event, context) {
      const callId = context?.toolCallId ?? event?.toolCallId;
      if (!callId) return;
      pending.delete(callId);
      for (const [parentId, entry] of pending) {
        if (entry.deferred && isDeferredChild(callId, parentId)) pending.delete(parentId);
      }
    },

    clear() {
      pending.clear();
    },
  };
}

const DESCRIPTION = 'Run a read-only calculation or file analysis in the owner workspace. ' +
  'The shell starts in the owner workspace. No network access. Output is returned in the response. ' +
  'Cannot install packages. Owner files are not modified.';

const PARAMETERS = {
  type: 'object',
  additionalProperties: false,
  required: ['command'],
  properties: {
    // Large bounded string repetitions exceed llama.cpp's grammar limit.
    // Enforce the exact ceiling in normalizeReadonlyExec before admission.
    command: {type: 'string', minLength: 1, description: 'Shell command; maximum 16384 characters, enforced before execution.'},
    workdir: {type: 'string', description: 'Relative POSIX path within the owner workspace; defaults to ".".'},
    timeoutSeconds: {type: 'integer', minimum: 1, maximum: MAX_TIMEOUT, description: 'Defaults to 10.'},
  },
};

function sanitizeError(error) {
  const code = typeof error?.code === 'string' && /^[a-z][a-z0-9-]{0,79}$/.test(error.code) ? error.code : 'readonly-exec-unavailable';
  return code;
}

export function createReadonlyExecTool(context, {admission, execute, scopeForContext}) {
  if (context?.agentId !== 'pixel') return null;
  if (!admission || typeof admission.take !== 'function') throw fail('readonly-exec-admission-required');
  if (typeof execute !== 'function') throw fail('readonly-exec-runner-required');
  if (typeof scopeForContext !== 'function') throw fail('readonly-exec-scope-required');
  return {
    name: READONLY_EXEC_TOOL,
    label: 'Read-only execution',
    description: DESCRIPTION,
    parameters: PARAMETERS,
    async execute(id, args, signal) {
      let request;
      try {
        request = normalizeReadonlyExec(args);
      } catch (error) {
        return {
          isError: true,
          details: {status: 'failed', code: sanitizeError(error)},
          content: [{type: 'text', text: 'Read-only execution rejected: ' + sanitizeError(error) + '.'}],
        };
      }
      try {
        const active = admission.take(id, request, context);
        const scope = scopeForContext(active, context);
        // Preserve the runner's SDK result, including nonzero exits and errors.
        return await execute(scope, request, signal);
      } catch (error) {
        const code = sanitizeError(error);
        return {
          isError: true,
          details: {status: 'failed', code},
          content: [{type: 'text', text: 'Read-only execution failed: ' + code + '.'}],
        };
      }
    },
  };
}
