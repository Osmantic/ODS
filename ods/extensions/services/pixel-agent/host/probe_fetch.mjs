// Optional owner-private measurement lease. Never changes model-visible input.
import * as odsProbeFs from 'node:fs';
import * as odsProbePath from 'node:path';
import * as odsProbeCrypto from 'node:crypto';
import * as odsProbeOs from 'node:os';

const odsProbeCounts = new Map();
const odsProbeSymbol = Symbol.for('ods.pixel.ownedProbeFetch.v1');

function odsProbeReadPrivate(path, maxBytes) {
  const parent = odsProbePath.dirname(path);
  if (!odsProbePath.isAbsolute(path) || odsProbeFs.realpathSync(parent) !== parent) return null;
  const folder = odsProbeFs.statSync(parent);
  if (folder.uid !== process.getuid() || (folder.mode & 0o077)) return null;
  const fd = odsProbeFs.openSync(path, odsProbeFs.constants.O_RDONLY | odsProbeFs.constants.O_NOFOLLOW);
  try {
    const stat = odsProbeFs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid() || (stat.mode & 0o077) || stat.size > maxBytes) return null;
    return JSON.parse(odsProbeFs.readFileSync(fd, 'utf8'));
  } finally { odsProbeFs.closeSync(fd); }
}

function odsProbeLease(context, now, leasePath) {
  if (!leasePath || typeof process.getuid !== 'function') return null;
  const lease = odsProbeReadPrivate(leasePath, 8192);
  if (!lease || lease.schemaVersion !== 1 || lease.agentId !== 'pixel' || context.agentId !== 'pixel' ||
      lease.ownerUid !== process.getuid() || !/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(lease.scopeId) ||
      !/^[a-f0-9]{64}$/.test(lease.signingKey) || !Number.isSafeInteger(lease.expiresAtMs) ||
      !(now < lease.expiresAtMs && lease.expiresAtMs <= now + 1800_000) ||
      !Number.isSafeInteger(lease.maxAttempts) || lease.maxAttempts < 1 || lease.maxAttempts > 16) return null;
  for (const key of ['runId', 'sessionId', 'sessionKey', 'provider', 'modelId']) {
    if (typeof lease[key] !== 'string' || !lease[key] || lease[key].length > 256 || lease[key] !== context[key]) return null;
  }
  const route = new URL(lease.baseUrl);
  if (route.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(route.hostname) || route.username || route.password || route.search || route.hash || route.pathname !== '/v1' || lease.baseUrl !== context.baseUrl) return null;
  return lease;
}

function odsProbeRecord(leasePath, value) {
  const path = leasePath + '.events.jsonl';
  const fd = odsProbeFs.openSync(path, odsProbeFs.constants.O_WRONLY | odsProbeFs.constants.O_CREAT | odsProbeFs.constants.O_APPEND | odsProbeFs.constants.O_NOFOLLOW, 0o600);
  try {
    const stat = odsProbeFs.fstatSync(fd);
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid() || (stat.mode & 0o077) || stat.size > 65536) return;
    odsProbeFs.writeSync(fd, JSON.stringify(value) + '\n');
  } finally { odsProbeFs.closeSync(fd); }
}

export function createOwnedProbeFetch(context, fetchImpl = globalThis.fetch, controls = {}) {
  const root = process.env.ODS_PIXEL_PROBE_ROOT || odsProbePath.join(process.env.OPENCLAW_STATE_DIR || odsProbePath.join(odsProbeOs.homedir(), '.openclaw'), 'ods-measurement');
  const leasePath = controls.leasePath ?? odsProbePath.join(root, 'run-' + odsProbeCrypto.createHash('sha256').update(String(context.runId)).digest('hex') + '.json');
  const clock = controls.clock ?? Date.now;
  return async (input, init = {}) => {
    // All caller-provided probe headers are removed, even with no valid lease.
    const headers = new Headers(init.headers ?? (input instanceof Request ? input.headers : undefined));
    for (const name of [...headers.keys()]) if (name.toLowerCase().startsWith('x-ods-probe')) headers.delete(name);
    const options = {...init, headers};
    let record;
    try {
      const lease = odsProbeLease(context, clock(), leasePath);
      const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url);
      const method = String(init.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      // Only final serialized OpenAI JSON bodies are eligible. No stream/body is
      // consumed or reconstructed merely to make a diagnostic observable.
      if (lease && method === 'POST' && url.href === lease.baseUrl + '/chat/completions' && typeof init.body === 'string' && Buffer.byteLength(init.body) <= 2 * 1024 * 1024) {
        const key = lease.scopeId + ':' + odsProbeCrypto.createHash('sha256').update(lease.signingKey).digest('hex');
        const count = odsProbeCounts.get(key) ?? 0;
        if (count < lease.maxAttempts) {
          odsProbeCounts.set(key, count + 1);
          if (odsProbeCounts.size > 32) odsProbeCounts.delete(odsProbeCounts.keys().next().value);
          const call = odsProbeCrypto.randomUUID().replaceAll('-', '');
          const raw = Buffer.from(init.body);
          const signed = Buffer.concat([Buffer.from('ods.probe-header.v1\0' + lease.scopeId + '\0' + call + '\0'), odsProbeCrypto.createHash('sha256').update(raw).digest()]);
          const signature = odsProbeCrypto.createHmac('sha256', lease.signingKey).update(signed).digest('base64url');
          headers.set('X-ODS-Probe', lease.scopeId + '.' + call + '.' + signature);
          record = {schemaVersion: 1, boundary: 'provider-fetch-invocation', scopeId: lease.scopeId, correlationId: call, runId: context.runId, sessionId: context.sessionId, modelCallId: context.callId ?? null, attempt: count + 1, bodyBytes: raw.length, atMs: clock(), startedMonotonicMs: performance.now()};
          odsProbeRecord(leasePath, record);
        }
      }
    } catch { /* Optional diagnostics cannot fail or retry a real request. */ }
    return fetchImpl(input, options);
  };
}

export {odsProbeSymbol};
