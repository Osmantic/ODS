import { AsyncLocalStorage } from 'node:async_hooks';
import { isDeepStrictEqual } from 'node:util';

const ALLOWED_TOOLS = new Set(['exec', 'process']);
const REQUIRED_SCOPE = ['agentId', 'runId', 'sessionId', 'sessionKey'];

function isNonEmptyString(v) {
  return typeof v === 'string' && v.length > 0;
}

function isPlainObject(v) {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}

function snapshotParams(params) {
  if (!isPlainObject(params)) throw new TypeError('params must be a plain object');
  const seen = new Set();
  const validate = value => {
    if (value === null || ['string','boolean'].includes(typeof value)) return;
    if (typeof value === 'number' && Number.isFinite(value)) return;
    if (!Array.isArray(value) && !isPlainObject(value)) throw new TypeError('params must contain only JSON values');
    if (seen.has(value)) throw new TypeError('params must not be cyclic');
    seen.add(value);
    for (const key of Reflect.ownKeys(value)) {
      if (Array.isArray(value) && key === 'length') continue;
      const descriptor = Object.getOwnPropertyDescriptor(value,key);
      if (typeof key !== 'string' || !descriptor.enumerable || !('value' in descriptor)) throw new TypeError('params must contain JSON data properties');
      validate(descriptor.value);
    }
    if (Array.isArray(value)) for(let i=0;i<value.length;i++) if(!Object.hasOwn(value,i)) throw new TypeError('params must not contain sparse arrays');
    seen.delete(value);
  };
  validate(params);
  return structuredClone(params);
}

function validateScope(scope) {
  if (!isPlainObject(scope)) throw new TypeError('scope must be a plain object');
  for (const key of REQUIRED_SCOPE) {
    if (!isNonEmptyString(scope[key])) throw new TypeError(`scope.${key} must be a non-empty string`);
  }
  if (scope.agentId !== 'pixel') throw new TypeError('scope.agentId must be pixel');
  return Object.freeze({
    agentId: scope.agentId,
    runId: scope.runId,
    sessionId: scope.sessionId,
    sessionKey: scope.sessionKey
  });
}

function scopeMatches(expected, actual) {
  if (!actual || typeof actual !== 'object') return false;
  for (const key of REQUIRED_SCOPE) {
    if (actual[key] !== expected[key]) return false;
  }
  return true;
}

export function createReadonlyExecutionAuthority() {
  const als = new AsyncLocalStorage();

  async function run(scope, toolName, toolCallId, params, operation) {
    if (als.getStore() !== undefined) throw new Error('nested readonly execution authority is not allowed');
    if (!ALLOWED_TOOLS.has(toolName)) throw new TypeError('toolName not allowed');
    if (!isNonEmptyString(toolCallId)) throw new TypeError('toolCallId must be a non-empty string');
    if (typeof operation !== 'function') throw new TypeError('operation must be a function');
    const frozenScope = validateScope(scope);
    const frozenParams = snapshotParams(params);
    const record = { scope: frozenScope, toolName, toolCallId, params: frozenParams, active: true };
    try {
      return await als.run(record, () => operation());
    } finally {
      record.active = false;
    }
  }

  function consume(event, context) {
    const record = als.getStore();
    if (!record || !record.active) return false;
    if (!event || typeof event !== 'object') return false;
    if (!context || typeof context !== 'object') return false;
    if (context.toolName !== undefined && context.toolName !== record.toolName) return false;
    if (event.toolName !== record.toolName) return false;
    // The SDK supplies full scope in context, but only runId on the event.
    if (REQUIRED_SCOPE.some(key => event[key] !== undefined && event[key] !== record.scope[key])) return false;
    if (!scopeMatches(record.scope, context)) return false;
    if (event.toolCallId !== record.toolCallId) return false;
    if (context.toolCallId !== undefined && context.toolCallId !== record.toolCallId) return false;
    if (!isDeepStrictEqual(event.params, record.params)) return false;
    record.active = false;
    return true;
  }

  return Object.freeze({ run, consume });
}
