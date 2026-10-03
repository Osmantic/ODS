// Private pipe transport. The command/directory/request callback must come from
// owner-approved activation, never a model, HTTP body or incoming header.
import { spawn } from 'node:child_process';
import { isAbsolute } from 'node:path';

const MAX_REPLY = 8192;
const failure = () => new Error('ODS lease worker unavailable');
export function createLeaseWorkerAdapter({command, directory, request, ownerScopes = false}) {
  if (process.platform === 'win32' || !Array.isArray(command) || !command.length ||
      !isAbsolute(command[0]) || command.some(arg => typeof arg !== 'string' || arg.includes('\0')) ||
      typeof directory !== 'string' || !isAbsolute(directory) || typeof request !== 'function' || typeof ownerScopes !== 'boolean') throw failure();
  const executable = [...command];
  const entries = new Map();
  function stop(entry) {
    if (entry.closing || entry.exited) return entry.closed;
    entry.closing = true;
    entry.child.stdin.end();
    entry.killTimer = setTimeout(() => {
      if (!entry.exited) entry.child.kill('SIGKILL');
    }, 5000);
    return entry.closed;
  }
  async function acquireLease({runId, sessionId, sessionKey}) {
    if (typeof runId !== 'string' || runId.length > 64 || typeof sessionId !== 'string' ||
        !sessionId || sessionId.length > 256 || entries.has(runId) || ownerScopes &&
        (typeof sessionKey !== 'string' || !/^agent:pixel:openai-user:ods-[a-f0-9]{64}$/.test(sessionKey))) throw failure();
    let policy;
    try { policy = request(Object.freeze({runId, sessionId})); } catch { throw failure(); }
    const keys = policy && Object.keys(policy).sort().join(',');
    if (!policy || !['allowCloud,confirmed,expectedRevision,timeoutSeconds',
      'allowCloud,confirmed,expectedRevision,handoffProviderId,timeoutSeconds'].includes(keys) ||
        policy.confirmed !== true || typeof policy.allowCloud !== 'boolean' ||
        ('handoffProviderId' in policy && (typeof policy.handoffProviderId !== 'string' ||
          !/^[a-z][a-z0-9_-]{0,63}$/.test(policy.handoffProviderId))) ||
        !Number.isSafeInteger(policy.expectedRevision) || policy.expectedRevision < 0 ||
        !Number.isSafeInteger(policy.timeoutSeconds) || policy.timeoutSeconds < 1 || policy.timeoutSeconds > 3600) throw failure();
    if (ownerScopes && 'handoffProviderId' in policy) throw failure();
    const body = Buffer.from(JSON.stringify({schemaVersion: 1, runId, sessionId, ...policy,
      ...(ownerScopes ? {scopeSessionKey: sessionKey} : {})}));
    const prefix = Buffer.alloc(4); prefix.writeUInt32BE(body.length);
    const child = spawn(executable[0], [...executable.slice(1), '--provider-directory', directory], {
      stdio: ['pipe', 'pipe', 'pipe'], cwd: '/', detached: true,
      env: {PATH: '/usr/bin:/bin', LANG: 'C.UTF-8', PYTHONDONTWRITEBYTECODE: '1'},
    });
    const entry = {child, sessionId, exited: false, closing: false};
    entries.set(runId, entry);
    let settleClose;
    entry.closed = new Promise(resolve => {settleClose = resolve;});
    let rejectReady;
    const ready = new Promise((resolve, reject) => {
      rejectReady = reject;
      let bytes = Buffer.alloc(0), expected;
      const fail = () => {reject(failure()); void stop(entry);};
      entry.startTimer = setTimeout(fail, 15000);
      child.stdout.on('data', part => {
        bytes = Buffer.concat([bytes, part]);
        if (bytes.length > MAX_REPLY + 4) {fail(); return;}
        if (bytes.length >= 4 && expected === undefined) {
          expected = bytes.readUInt32BE(0);
          if (expected < 1 || expected > MAX_REPLY) {fail(); return;}
        }
        if (expected === undefined || bytes.length < expected + 4) return;
        if (bytes.length !== expected + 4) {fail(); return;}
        try {
          const value = JSON.parse(bytes.subarray(4).toString('utf8'));
          if (value.schemaVersion !== 1 || value.runId !== runId || value.sessionId !== sessionId ||
              Object.keys(value).sort().join(',') !== 'lease,runId,schemaVersion,sessionId' || entry.closing) throw failure();
          clearTimeout(entry.startTimer);
          resolve(value.lease);
        } catch {fail();}
      });
      child.stdin.on('error', fail);
      child.on('error', fail);
      let errorBytes = 0;
      child.stderr.on('data', part => {errorBytes += part.length; if (errorBytes > 8192) fail();});
    });
    child.on('close', () => {
      entry.exited = true;
      clearTimeout(entry.startTimer); clearTimeout(entry.killTimer); clearTimeout(entry.lifeTimer);
      if (entries.get(runId) === entry) entries.delete(runId);
      rejectReady(failure()); settleClose();
    });
    // Redundant host bound. The worker has its own startup/lifetime/EOF watchdog.
    entry.lifeTimer = setTimeout(() => {void stop(entry);}, (policy.timeoutSeconds + 15) * 1000);
    child.stdin.write(Buffer.concat([prefix, body]));
    try { return await ready; }
    catch { await stop(entry); throw failure(); }
  }
  async function releaseLease({runId, sessionId}) {
    const entry = entries.get(runId);
    if (!entry) return;
    if (entry.sessionId !== sessionId) throw failure();
    await stop(entry);
  }
  return {acquireLease, releaseLease, durableReplayGuard: true};
}
