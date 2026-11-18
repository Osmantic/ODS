import path from 'node:path';

// Read-only Docker lifecycle for ODS sandbox inspection/admission/cleanup.
// Never executes model code. Uses injected async docker(args) -> stdout string.
// Fixed argv only; no shell; no interpolation.

const HEX64 = /^[0-9a-f]{64}$/;
const IMAGE_ID = /^sha256:[0-9a-f]{64}$/;
const NAME_PREFIX = 'ods-pixel-ro-';
const NAME_RE = /^ods-pixel-ro-[a-z0-9._-]{1,50}$/;

function fail(msg) {
  const e = new Error(msg);
  e.code = 'readonly-docker-lifecycle-failed';
  throw e;
}

function isAbs(p) {
  return typeof p === 'string' && p.length > 0 && p[0] === '/';
}

function hasBadPathChars(p) {
  if (typeof p !== 'string') return true;
  if (p.indexOf('\u0000') !== -1) return true;
  if (p.indexOf('\\') !== -1) return true;
  for (let i = 0; i < p.length; i++) {
    const code = p.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

function hasDotDotSegment(p) {
  const parts = p.split('/');
  for (const seg of parts) {
    if (seg === '..') return true;
  }
  return false;
}

function validAbsPath(p) {
  return isAbs(p) && p !== '/' && !p.endsWith('/') && !hasBadPathChars(p) &&
    !hasDotDotSegment(p) && path.posix.normalize(p) === p;
}

function normBind(b) {
  if (!b || typeof b !== 'object') return null;
  if (b.RW !== false) return null;
  return {
    type: b.Type,
    source: b.Source,
    destination: b.Destination,
    rw: b.RW,
  };
}

function validateExpected(expected) {
  if (!expected || typeof expected !== 'object') fail('expected required');
  const e = expected;
  if (typeof e.containerName !== 'string' || !e.containerName.startsWith(NAME_PREFIX)) {
    fail('expected.containerName must start with ' + NAME_PREFIX);
  }
  if (!NAME_RE.test(e.containerName)) fail('expected.containerName malformed');
  if (e.containerName.length > 63) fail('expected.containerName too long');
  if (typeof e.expectedImageId !== 'string' || !IMAGE_ID.test(e.expectedImageId)) {
    fail('expected.expectedImageId must be sha256:64hex');
  }
  if (!validAbsPath(e.workspace)) fail('expected.workspace must be absolute POSIX path');
  if (!validAbsPath(e.bootstrap)) fail('expected.bootstrap must be absolute POSIX path');
  if (e.workspace === e.bootstrap || e.workspace.startsWith(e.bootstrap + '/') ||
      e.bootstrap.startsWith(e.workspace + '/')) fail('workspace and bootstrap must be distinct and disjoint');
  if (typeof e.createdAfterEpochMs !== 'number' || !Number.isFinite(e.createdAfterEpochMs) ||
      !Number.isInteger(e.createdAfterEpochMs) || e.createdAfterEpochMs < 0) {
    fail('expected.createdAfterEpochMs must be finite integer >= 0');
  }
  let controlSource = null;
  if (e.controlSource !== undefined) {
    if (!validAbsPath(e.controlSource)) fail('expected.controlSource must be absolute POSIX path');
    if (e.controlSource === e.workspace || e.controlSource === e.bootstrap) {
      fail('controlSource must be distinct from workspace/bootstrap');
    }
    controlSource = e.controlSource;
  }
  const extra = [];
  const destSeen = new Set(['/agent','/workspace','/run/pixel-ods-control']);
  if (e.extraReadonlyBinds !== undefined) {
    if (!Array.isArray(e.extraReadonlyBinds)) fail('extraReadonlyBinds must be array');
    for (const b of e.extraReadonlyBinds) {
      if (!b || typeof b !== 'object') fail('extraReadonlyBinds entry invalid');
      if (!validAbsPath(b.source) || !validAbsPath(b.destination)) {
        fail('extraReadonlyBinds source/destination must be absolute POSIX paths');
      }
      if (b.source === e.workspace || b.source === e.bootstrap) fail('extraReadonlyBinds source collides');
      if (destSeen.has(b.destination)) fail('extraReadonlyBinds duplicate destination');
      destSeen.add(b.destination);
      extra.push({ source: b.source, destination: b.destination });
    }
  }
  let maxMem = null;
  if (e.maximumExpectedMemoryBytes !== undefined) {
    if (typeof e.maximumExpectedMemoryBytes !== 'number' ||
        !Number.isFinite(e.maximumExpectedMemoryBytes) ||
        !Number.isInteger(e.maximumExpectedMemoryBytes) ||
        !(e.maximumExpectedMemoryBytes > 0)) {
      fail('maximumExpectedMemoryBytes must be finite positive integer');
    }
    maxMem = e.maximumExpectedMemoryBytes;
  }
  let requireNoNewPrivs = false;
  if (e.requireNoNewPrivileges !== undefined) {
    if (typeof e.requireNoNewPrivileges !== 'boolean') fail('requireNoNewPrivileges must be boolean');
    requireNoNewPrivs = e.requireNoNewPrivileges;
  }
  return Object.freeze({
    containerName: e.containerName,
    expectedImageId: e.expectedImageId,
    workspace: e.workspace,
    bootstrap: e.bootstrap,
    createdAfterEpochMs: e.createdAfterEpochMs,
    controlSource,
    extraReadonlyBinds: Object.freeze(extra.map((x) => Object.freeze(x))),
    maximumExpectedMemoryBytes: maxMem,
    requireNoNewPrivileges: requireNoNewPrivs,
  });
}

function parseInspect(stdout) {
  let arr;
  try {
    arr = JSON.parse(stdout);
  } catch {
    fail('inspect output not JSON');
  }
  if (!Array.isArray(arr)) fail('inspect output not array');
  if (arr.length === 0) fail('inspect returned empty array (unexpected)');
  if (arr.length !== 1) fail('inspect returned multiple entries');
  const c = arr[0];
  if (!c || typeof c !== 'object') fail('inspect entry invalid');
  return c;
}

function checkOwnership(c, exp) {
  if (c.Name !== '/' + exp.containerName) fail('ownership: name mismatch');
  if (typeof c.Id !== 'string' || !HEX64.test(c.Id)) fail('ownership: id not 64hex');
  const labels = c.Config && c.Config.Labels;
  if (!labels || labels['openclaw.sandbox'] !== '1') fail('ownership: label missing');
  const created = Date.parse(c.Created);
  if (!Number.isFinite(created)) fail('ownership: Created unparseable');
  if (created < exp.createdAfterEpochMs - 2000) fail('ownership: created too early');
  if (!Array.isArray(c.Mounts)) fail('ownership: Mounts missing or malformed');
  const mounts = c.Mounts.map(normBind);
  if (mounts.some((m) => m === null)) fail('ownership: mount entry invalid');
  const allowed = new Map();
  allowed.set(exp.workspace + '\u0000/agent', { source: exp.workspace, destination: '/agent' });
  allowed.set(exp.bootstrap + '\u0000/workspace', { source: exp.bootstrap, destination: '/workspace' });
  if (exp.controlSource) {
    allowed.set(exp.controlSource + '\u0000/run/pixel-ods-control', {
      source: exp.controlSource,
      destination: '/run/pixel-ods-control',
    });
  }
  for (const b of exp.extraReadonlyBinds) {
    allowed.set(b.source + '\u0000' + b.destination, { source: b.source, destination: b.destination });
  }
  const seen = new Set();
  for (const m of mounts) {
    if (m.type !== 'bind') fail('ownership: non-bind mount');
    const key = m.source + '\u0000' + m.destination;
    if (!allowed.has(key)) fail('ownership: unexpected bind ' + m.source + '->' + m.destination);
    if (seen.has(key)) fail('ownership: duplicate bind');
    seen.add(key);
  }
  if (!seen.has(exp.workspace + '\u0000/agent')) fail('ownership: workspace bind missing');
  if (!seen.has(exp.bootstrap + '\u0000/workspace')) fail('ownership: bootstrap bind missing');
  return c.Id;
}

function checkRestrictions(c, exp) {
  if (c.Image !== exp.expectedImageId) fail('restriction: image id mismatch');
  if (c.Config?.User !== 'sandbox' || c.Config?.WorkingDir !== '/workspace')
    fail('restriction: execution user or directory mismatch');
  if (!c.State || c.State.Running !== true) fail('restriction: not running');
  const hc = c.HostConfig || {};
  if (hc.ReadonlyRootfs !== true) fail('restriction: rootfs not readonly');
  if (hc.NetworkMode !== 'none') fail('restriction: network not none');
  if (!Array.isArray(hc.CapDrop) || !hc.CapDrop.includes('ALL')) fail('restriction: CapDrop missing ALL');
  if (hc.CapAdd !== null && hc.CapAdd !== undefined) {
    if (!Array.isArray(hc.CapAdd)) fail('restriction: CapAdd malformed');
    if (hc.CapAdd.length !== 0) fail('restriction: CapAdd not empty');
  }
  if (hc.Privileged !== false) fail('restriction: privileged must be false');
  if (hc.Devices !== null && hc.Devices !== undefined) {
    if (!Array.isArray(hc.Devices)) fail('restriction: Devices malformed');
    if (hc.Devices.length !== 0) fail('restriction: devices present');
  }
  if (hc.DeviceRequests !== null && hc.DeviceRequests !== undefined) {
    if (!Array.isArray(hc.DeviceRequests)) fail('restriction: DeviceRequests malformed');
    if (hc.DeviceRequests.length !== 0) fail('restriction: DeviceRequests present');
  }
  if (exp.maximumExpectedMemoryBytes !== null) {
    const mem = hc.Memory;
    if (typeof mem !== 'number' || !Number.isFinite(mem) || !Number.isInteger(mem) || !(mem > 0)) {
      fail('restriction: memory limit invalid');
    }
    if (mem > exp.maximumExpectedMemoryBytes) fail('restriction: memory exceeds max');
    if (!Number.isInteger(hc.MemorySwap) || hc.MemorySwap < mem ||
        hc.MemorySwap > exp.maximumExpectedMemoryBytes) fail('restriction: memory swap limit invalid');
  }
  if (!Number.isInteger(hc.NanoCpus) || hc.NanoCpus <= 0 || hc.NanoCpus > 1e9)
    fail('restriction: CPU limit invalid');
  const pids = hc.PidsLimit;
  if (typeof pids !== 'number' || !Number.isInteger(pids) || !(pids > 0) || pids > 128) {
    fail('restriction: PidsLimit invalid');
  }
  if (exp.requireNoNewPrivileges) {
    const opts = Array.isArray(hc.SecurityOpt) ? hc.SecurityOpt : [];
    const ok = opts.some((o) => o === 'no-new-privileges' || o === 'no-new-privileges:true');
    if (!ok) fail('restriction: no-new-privileges required');
  }
}

async function inspectRaw(docker, name) {
  let out;
  try {
    out = await docker(['inspect', '--type', 'container', name]);
  } catch (err) {
    if (err && err.code === 'DOCKER_NOT_FOUND') return null;
    fail('inspect transport error');
  }
  if (typeof out !== 'string') fail('inspect stdout not string');
  return parseInspect(out);
}

async function inspectById(docker, id) {
  let out;
  try {
    out = await docker(['inspect', '--type', 'container', id]);
  } catch (err) {
    if (err && err.code === 'DOCKER_NOT_FOUND') return null;
    fail('inspect transport error');
  }
  if (typeof out !== 'string') fail('inspect stdout not string');
  return parseInspect(out);
}

async function psById(docker, id) {
  let out;
  try {
    out = await docker(['ps', '-aq', '--no-trunc', '--filter', 'id=' + id]);
  } catch {
    fail('ps verify failed');
  }
  if (typeof out !== 'string') fail('ps stdout not string');
  return out.trim();
}

export function createReadonlyDockerLifecycle({ docker, expected } = {}) {
  if (typeof docker !== 'function') fail('docker function required');
  const exp = validateExpected(expected);
  let capturedId = null;
  async function verifyAbsent(id) {
    const remaining = await psById(docker,id);
    if (remaining !== '') fail('cleanup: container still present');
    if (await inspectRaw(docker,exp.containerName) !== null)
      fail('cleanup: container name still occupied');
  }
  function capture(container) {
    const id = checkOwnership(container, exp);
    if (capturedId !== null && id !== capturedId) fail('ownership: captured id changed');
    capturedId = id;
    return id;
  }

  async function inspect() {
    const c = await inspectRaw(docker, capturedId ?? exp.containerName);
    if (c === null) return { exists: false };
    const id = capture(c);
    return { exists: true, id, container: c };
  }

  async function admit() {
    const c = await inspectRaw(docker, capturedId ?? exp.containerName);
    if (c === null) fail('admit: container not found');
    const id = capture(c);
    checkRestrictions(c, exp);
    return { id };
  }

  async function cleanup() {
    if (capturedId === null) {
      const c = await inspectRaw(docker, exp.containerName);
      if (c === null) return { alreadyAbsent: true };
      const id = capture(c);
      try {
        await docker(['rm', '-f', id]);
      } catch {
        fail('cleanup: rm failed');
      }
      await verifyAbsent(id);
      return { removed: id };
    }
    const c = await inspectById(docker, capturedId);
    if (c === null) {
      await verifyAbsent(capturedId);
      return { alreadyAbsent: true };
    }
    const id = checkOwnership(c, exp);
    if (id !== capturedId) fail('cleanup: id mismatch');
    try {
      await docker(['rm', '-f', capturedId]);
    } catch {
      fail('cleanup: rm failed');
    }
    await verifyAbsent(capturedId);
    return { removed: capturedId };
  }

  return Object.freeze({ inspect, admit, cleanup });
}
