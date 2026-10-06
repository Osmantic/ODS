import {LAYA_LIMITS, LayaProtocolError, prepareLayaRequest, readLayaResponse} from './laya-protocol.mjs';

export class LayaServiceError extends Error {
  constructor(code, message, submitted = false) {
    super(message); this.name = 'LayaServiceError'; this.code = code; this.submitted = submitted;
  }
}

// Deployment-owned address only. A prompt can never supply a destination,
// model repository, credential, local path, or another service's URL.
function endpoint(port) {
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new TypeError('Invalid Laya port.');
  return `http://127.0.0.1:${port}`;
}

async function jsonResponse(response, signal, maxBytes) {
  const declared = response.headers.get('content-length');
  if (declared && Number(declared) > maxBytes) {
    await response.body?.cancel();
    throw new LayaServiceError('invalid_response', 'Laya response exceeds the byte limit.');
  }
  const reader = response.body?.getReader();
  if (!reader) throw new LayaServiceError('invalid_response', 'Laya returned no response body.');
  const decoder = new TextDecoder('utf-8', {fatal: true});
  const chunks = []; let bytes = 0;
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener('abort', cancel, {once: true});
  try {
    for (;;) {
      signal.throwIfAborted();
      const {done, value} = await reader.read();
      signal.throwIfAborted();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > maxBytes) throw new LayaServiceError('invalid_response', 'Laya response exceeds the byte limit.');
      chunks.push(decoder.decode(value, {stream: true}));
    }
    chunks.push(decoder.decode());
    return JSON.parse(chunks.join(''));
  } catch (error) {
    if (error instanceof SyntaxError || error instanceof TypeError) {
      throw new LayaServiceError('invalid_response', 'Laya returned invalid JSON or text encoding.');
    }
    throw error;
  } finally {
    signal.removeEventListener('abort', cancel);
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

function httpError(status, submitted) {
  const errors = {
    401: ['authentication_failed', 'Laya authentication failed. Check the extension configuration.'],
    403: ['authentication_failed', 'Laya refused access. Check the extension configuration.'],
    400: ['request_rejected', 'Laya rejected the decision request.'],
    413: ['request_too_large', 'Split the decision request into smaller batches.'],
    422: ['request_rejected', 'Laya rejected the question schema or inference controls.'],
    429: ['busy', 'Laya is busy. Continue the task without it or try later.'],
    503: ['busy', 'Laya is unavailable or busy. Continue the task without it or try later.'],
  };
  const [code, message] = errors[status] ?? ['unavailable', 'Laya inference failed. Check the extension logs.'];
  return new LayaServiceError(code, message, submitted);
}

export function createLayaClient({port = 8017, token, fetch: request = globalThis.fetch,
  timeoutMs = 30_000, healthTimeoutMs = 2_000} = {}) {
  const base = endpoint(port);
  if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{32,256}$/.test(token)) {
    throw new TypeError('Laya requires a deployment-owned service token.');
  }
  for (const value of [timeoutMs, healthTimeoutMs]) {
    if (!Number.isInteger(value) || value < 1 || value > 120_000) throw new TypeError('Invalid Laya deadline.');
  }
  let busy = false;
  async function send(path, {body, signal, deadline}) {
    const controller = new AbortController();
    const cancel = () => controller.abort();
    signal?.addEventListener('abort', cancel, {once: true});
    if (signal?.aborted) controller.abort();
    const timer = setTimeout(cancel, deadline);
    let submitted = false;
    try {
      controller.signal.throwIfAborted();
      // One attempt only. An observation failure never replays inference.
      submitted = body !== undefined;
      const response = await request(`${base}${path}`, {method: body === undefined ? 'GET' : 'POST',
        redirect: 'error', signal: controller.signal,
        headers: {'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json'},
        ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      if (!response.ok) {
        await response.body?.cancel();
        throw httpError(response.status, submitted);
      }
      return await jsonResponse(response, controller.signal, body === undefined ? 16_000 : LAYA_LIMITS.responseBytes);
    } catch (error) {
      if (controller.signal.aborted) {
        throw new LayaServiceError(signal?.aborted ? 'cancelled' : 'timed_out',
          signal?.aborted ? 'Laya observation cancelled.' : 'Laya exceeded the decision deadline.', submitted);
      }
      if (error instanceof LayaServiceError) { error.submitted = submitted; throw error; }
      if (error instanceof TypeError || ['ECONNREFUSED', 'ECONNRESET', 'ENOTFOUND'].includes(error?.code)) {
        throw new LayaServiceError('unavailable', 'Cannot reach the configured Laya service.', submitted);
      }
      throw error;
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
    }
  }
  return {
    async status(signal) {
      const value = await send('/health', {signal, deadline: healthTimeoutMs});
      const known = ['english', 'multilingual', 'typed-decisions'];
      if (value?.status !== 'ok' || !Array.isArray(value.loaded)
          || !value.loaded.every(name => known.includes(name)) || typeof value.device !== 'string'
          || !/^(cpu|mps|cuda(?::[0-9]+)?)$/.test(value.device) || typeof value.device_is_preference !== 'boolean') {
        throw new LayaServiceError('invalid_response', 'Laya returned unverified readiness information.');
      }
      return {status: value.loaded.length && !value.device_is_preference ? 'loaded' : 'starting',
        checkpoints: value.loaded, device: value.device, inferenceVerified: false};
    },
    async decide(input, signal) {
      const prepared = prepareLayaRequest(input);
      if (busy) throw new LayaServiceError('busy', 'Another Portal decision is running. Continue or try later.');
      busy = true;
      try {
        const response = await send('/v1/systemone/batch', {body: prepared.body, signal, deadline: timeoutMs});
        try { return readLayaResponse(response, prepared); }
        catch (error) {
          if (error instanceof LayaProtocolError) error.submitted = true;
          throw error;
        }
      } finally { busy = false; }
    },
  };
}
