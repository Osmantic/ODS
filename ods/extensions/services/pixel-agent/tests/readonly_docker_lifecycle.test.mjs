import test from 'node:test';
import assert from 'node:assert/strict';
import { createReadonlyDockerLifecycle } from '../plugin/readonly-docker-lifecycle.mjs';

const ID = 'a'.repeat(64);
const IMG = 'sha256:' + 'b'.repeat(64);
const WS = '/tmp/ods-ws';
const BS = '/tmp/ods-bs';
const NAME = 'ods-pixel-ro-1234abcd-0000-4000-8000-000000000000';
const CREATED = '2024-01-01T00:00:10.000Z';
const AFTER = Date.parse('2024-01-01T00:00:00.000Z');

function baseExpected(over = {}) {
  return {
    containerName: NAME,
    expectedImageId: IMG,
    workspace: WS,
    bootstrap: BS,
    createdAfterEpochMs: AFTER,
    maximumExpectedMemoryBytes: 512 * 1024 * 1024,
    ...over,
  };
}

function baseContainer(over = {}) {
  return {
    Id: ID,
    Name: '/' + NAME,
    Image: IMG,
    Created: CREATED,
    Config: { Labels: { 'openclaw.sandbox': '1' },User:'sandbox',WorkingDir:'/workspace' },
    State: { Running: true },
    HostConfig: {
      ReadonlyRootfs: true,
      NetworkMode: 'none',
      CapDrop: ['ALL'],
      CapAdd: [],
      Privileged: false,
      Devices: [],
      Memory: 256 * 1024 * 1024,
      MemorySwap: 256 * 1024 * 1024,
      NanoCpus: 1e9,
      PidsLimit: 64,
    },
    Mounts: [
      { Type: 'bind', Source: WS, Destination: '/agent', RW: false },
      { Type: 'bind', Source: BS, Destination: '/workspace', RW: false },
    ],
    ...over,
  };
}

function mkDocker(container, opts = {}) {
  const calls = [];
  const docker = async (args) => {
    calls.push(args);
    if (args[0] === 'inspect') {
      if (opts.inspectError) throw opts.inspectError;
      if (container === null) {
        const e = new Error('not found');
        e.code = 'DOCKER_NOT_FOUND';
        throw e;
      }
      return JSON.stringify([container]);
    }
    if (args[0] === 'rm') {
      if (opts.rmError) throw opts.rmError;
      container = null;
      return '';
    }
    if (args[0] === 'ps') {
      if (opts.psError) throw opts.psError;
      return opts.psOut ?? '';
    }
    throw new Error('unexpected docker args: ' + args.join(' '));
  };
  return { docker, calls };
}

function assertFail(promise, re) {
  return assert.rejects(promise, (e) => {
    assert.equal(e.code, 'readonly-docker-lifecycle-failed');
    if (re) assert.match(e.message, re);
    return true;
  });
}

test('admit succeeds on correct config', async () => {
  const { docker } = mkDocker(baseContainer());
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  const r = await lc.admit();
  assert.equal(r.id, ID);
});

test('inspect reports not exists on trusted not-found', async () => {
  const { docker } = mkDocker(null);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  const r = await lc.inspect();
  assert.deepEqual(r, { exists: false });
});

test('inspect transport error is not absence', async () => {
  const { docker } = mkDocker(baseContainer(), { inspectError: new Error('boom') });
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.inspect(), /transport/);
});

test('wrong name rejected', async () => {
  const { docker } = mkDocker(baseContainer({ Name: '/other' }));
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.inspect(), /name/);
});

test('wrong id rejected', async () => {
  const { docker } = mkDocker(baseContainer({ Id: 'xyz' }));
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.inspect(), /id/);
});

test('missing label rejected', async () => {
  const { docker } = mkDocker(baseContainer({ Config: { Labels: {} } }));
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.inspect(), /label/);
});

test('created too early rejected', async () => {
  const { docker } = mkDocker(baseContainer({ Created: '2023-01-01T00:00:00.000Z' }));
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.inspect(), /created/);
});

test('rw mount rejected', async () => {
  const c = baseContainer();
  c.Mounts[0].RW = true;
  const { docker } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.inspect(), /mount/);
});

test('extra unexpected bind rejected', async () => {
  const c = baseContainer();
  c.Mounts.push({ Type: 'bind', Source: '/etc', Destination: '/etc', RW: false });
  const { docker } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.inspect(), /unexpected/);
});

test('extra readonly bind allowed when explicitly expected', async () => {
  const c = baseContainer();
  c.Mounts.push({ Type: 'bind', Source: '/opt/skill', Destination: '/skills/x', RW: false });
  const { docker } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({
    docker,
    expected: baseExpected({ extraReadonlyBinds: [{ source: '/opt/skill', destination: '/skills/x' }] }),
  });
  const r = await lc.admit();
  assert.equal(r.id, ID);
});

test('control source allowed when expected', async () => {
  const c = baseContainer();
  c.Mounts.push({ Type: 'bind', Source: '/run/ctl', Destination: '/run/pixel-ods-control', RW: false });
  const { docker } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected({ controlSource: '/run/ctl' }) });
  const r = await lc.admit();
  assert.equal(r.id, ID);
});

test('rootfs not readonly rejected', async () => {
  const c = baseContainer();
  c.HostConfig.ReadonlyRootfs = false;
  const { docker } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.admit(), /rootfs/);
});

test('network not none rejected', async () => {
  const c = baseContainer();
  c.HostConfig.NetworkMode = 'bridge';
  const { docker } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.admit(), /network/);
});

test('memory over max rejected', async () => {
  const c = baseContainer();
  c.HostConfig.Memory = 1024 * 1024 * 1024;
  const { docker } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.admit(), /memory/);
});

test('restriction failure still allows cleanup of owned container', async () => {
  const c = baseContainer();
  c.HostConfig.NetworkMode = 'bridge';
  const { docker, calls } = mkDocker(c);
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await assertFail(lc.admit(), /network/);
  assert.equal((await lc.cleanup()).removed,ID);
  assert.deepEqual(calls.find(a=>a[0]==='rm'),['rm','-f',ID]);
});

test('cleanup removes captured id and verifies empty', async () => {
  const { docker, calls } = mkDocker(baseContainer());
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await lc.admit();
  const r = await lc.cleanup();
  assert.equal(r.removed, ID);
  const rm = calls.find((a) => a[0] === 'rm');
  assert.deepEqual(rm, ['rm', '-f', ID]);
  const ps = calls.find((a) => a[0] === 'ps');
  assert.deepEqual(ps, ['ps', '-aq', '--no-trunc', '--filter', 'id=' + ID]);
});

test('cleanup refuses when name points to different id', async () => {
  const { docker } = mkDocker(baseContainer());
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await lc.admit();
  const other = baseContainer({ Id: 'c'.repeat(64) });
  const { docker: d2 } = mkDocker(other);
  // swap docker by constructing new lifecycle is not valid; instead simulate via closure
  // Rebuild with a docker that returns different id on second inspect.
  let n = 0;
  const docker2 = async (args) => {
    if (args[0] === 'inspect') {
      n++;
      return JSON.stringify([n === 1 ? baseContainer() : other]);
    }
    if (args[0] === 'rm') throw new Error('should not rm');
    if (args[0] === 'ps') return '';
    throw new Error('unexpected');
  };
  const lc2 = createReadonlyDockerLifecycle({ docker: docker2, expected: baseExpected() });
  await lc2.admit();
  await assertFail(lc2.cleanup(), /id mismatch/);
});

test('cleanup rm failure surfaces', async () => {
  const { docker } = mkDocker(baseContainer(), { rmError: new Error('rm fail') });
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await lc.admit();
  await assertFail(lc.cleanup(), /rm failed/);
});

test('cleanup post-remove still present surfaces', async () => {
  const { docker } = mkDocker(baseContainer(), { psOut: ID + '\n' });
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await lc.admit();
  await assertFail(lc.cleanup(), /still present/);
});

test('expected validation rejects same workspace/bootstrap', () => {
  assert.throws(
    () => createReadonlyDockerLifecycle({ docker: async () => '', expected: baseExpected({ bootstrap: WS }) }),
    (e) => e.code === 'readonly-docker-lifecycle-failed',
  );
});

test('expected validation rejects bad image id', () => {
  assert.throws(
    () => createReadonlyDockerLifecycle({ docker: async () => '', expected: baseExpected({ expectedImageId: 'sha256:zz' }) }),
    (e) => e.code === 'readonly-docker-lifecycle-failed',
  );
});

test('expected validation rejects bad name prefix', () => {
  assert.throws(
    () => createReadonlyDockerLifecycle({ docker: async () => '', expected: baseExpected({ containerName: 'evil' }) }),
    (e) => e.code === 'readonly-docker-lifecycle-failed',
  );
});

test('no broader remove commands issued', async () => {
  const { docker, calls } = mkDocker(baseContainer());
  const lc = createReadonlyDockerLifecycle({ docker, expected: baseExpected() });
  await lc.admit();
  await lc.cleanup();
  for (const a of calls) {
    if (a[0] === 'rm') assert.deepEqual(a, ['rm', '-f', ID]);
    if (a[0] === 'ps') assert.ok(a.includes('id=' + ID));
  }
});

test('admission rejects unbounded swap, CPU and PID allocations', async () => {
  for (const [field,value] of [['MemorySwap',-1],['MemorySwap',0],['MemorySwap',1024**3],
    ['NanoCpus',0],['NanoCpus',2e9],['PidsLimit',129]]) {
    const container=baseContainer();container.HostConfig[field]=value;
    const {docker}=mkDocker(container);
    const lifecycle=createReadonlyDockerLifecycle({docker,expected:baseExpected()});
    await assertFail(lifecycle.admit(),/limit/i);
  }
});

test('ownership capture is immutable and a replacement name is never removed', async () => {
  let oldGone=false;
  const other=baseContainer({Id:'c'.repeat(64)}),calls=[];
  const docker=async args=>{
    calls.push(args);
    if(args[0]==='inspect'){
      if(args[3]===ID&&oldGone)throw Object.assign(new Error('missing'),{code:'DOCKER_NOT_FOUND'});
      return JSON.stringify([oldGone?other:baseContainer()]);
    }
    if(args[0]==='ps')return '';
    throw new Error('must not remove replacement');
  };
  const lifecycle=createReadonlyDockerLifecycle({docker,expected:baseExpected()});
  await lifecycle.admit();oldGone=true;
  await assertFail(lifecycle.cleanup(),/name still occupied/);
  assert(!calls.some(args=>args[0]==='rm'));
});

test('missing RW is not read-only proof and extra mounts cannot replace the workspace', async () => {
  const container=baseContainer();delete container.Mounts[0].RW;
  const {docker}=mkDocker(container);
  await assertFail(createReadonlyDockerLifecycle({docker,expected:baseExpected()}).admit(),/mount/);
  assert.throws(()=>createReadonlyDockerLifecycle({docker,expected:baseExpected({
    extraReadonlyBinds:[{source:'/other',destination:'/agent'}]})}),/destination/);
});
