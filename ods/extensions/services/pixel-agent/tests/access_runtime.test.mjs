import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import childProcessApi from 'node:child_process';
import {syncBuiltinESMExports} from 'node:module';
import {createHash} from 'node:crypto';
import {once} from 'node:events';
import {createAccessRuntime, executionHostForAgent} from '../plugin/access-runtime.mjs';

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-access-runtime-test-'));
  fs.chmodSync(root, 0o700);
  return {directory: path.join(root, 'runtime'), runtimeVersion: '2026.6.33', hooksAllowed: true};
}
const token = 'a'.repeat(64), other = 'b'.repeat(64);
const runtimeUrl = new URL('../plugin/access-runtime.mjs', import.meta.url).href;
const linux = {skip: process.platform !== 'linux'};
function seed(options, lock, phase = 'idle') {
  fs.mkdirSync(options.directory, {mode: 0o700});
  fs.writeFileSync(path.join(options.directory, 'process.json'), JSON.stringify(lock), {mode: 0o600});
  fs.writeFileSync(path.join(options.directory, 'state.json'), JSON.stringify({version: 1, phase,
    revision: 'c'.repeat(64), tokenHash: phase === 'held' ? createHash('sha256').update(token).digest('hex') : null}), {mode: 0o600});
}
function identity(pid = process.pid) {
  const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
  return {version: 2, pid, bootId: fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim(),
    startTicks: stat.slice(stat.lastIndexOf(')') + 2).trim().split(/\s+/)[19]};
}
async function childProcess(t, script) {
  const child = spawn(process.execPath, ['--input-type=module', '-e', script],
    {stdio: ['ignore', 'ignore', 'pipe', 'ipc']});
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  t.after(async () => {
    if (child.exitCode === null && child.signalCode === null) {
      const exited = once(child, 'exit'); child.kill('SIGKILL'); await exited;
    }
  });
  const [message] = await once(child, 'message', {signal: AbortSignal.timeout(10000)});
  assert.equal(stderr, '');
  return {child, message};
}
function withInvocations(environments, callback) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-invocation-test-'));
  for (const [pid, contents] of Object.entries(environments)) fs.writeFileSync(path.join(root, pid), contents, {mode: 0o600});
  const read = fs.readFileSync, open = fs.openSync;
  fs.readFileSync = function (name, ...args) {
    if (name === '/proc/sys/kernel/random/boot_id') throw Object.assign(new Error('hidden'), {code: 'ENOENT'});
    return read.call(this, name, ...args);
  };
  fs.openSync = function (name, ...args) {
    const match = typeof name === 'string' && /^\/proc\/(\d+)\/environ$/.exec(name);
    if (match) {
      if (!Object.hasOwn(environments, match[1])) throw Object.assign(new Error('hidden'), {code: 'EACCES'});
      return open.call(this, path.join(root, match[1]), ...args);
    }
    return open.call(this, name, ...args);
  };
  try { return callback(); }
  finally { fs.readFileSync = read; fs.openSync = open; fs.rmSync(root, {recursive: true}); }
}

test('restricted proc uses bounded exact invocation identity and preserves a matching owner', linux, () => {
  const invocationId = 'd'.repeat(32), options = fixture();
  withInvocations({[process.pid]: `OTHER=${'x'.repeat(4080)}\0INVOCATION_ID=${invocationId}\0LAST=discarded\0`}, () => {
    const runtime = createAccessRuntime(options);
    assert.equal(runtime.status().available, true);
    const previous = JSON.parse(fs.readFileSync(path.join(options.directory, 'process.json')));
    assert.equal(previous.version, 3);
    assert.equal(previous.invocationId, invocationId);
    assert.equal(createAccessRuntime(options).status().available, false);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(options.directory, 'process.json'))), previous);
  });
});

test('different current invocation cannot displace a directly verified live previous owner', linux, async t => {
  const {child} = await childProcess(t, `process.send({ready:true}); setInterval(() => {}, 1000);`);
  const startTicks = identity(child.pid).startTicks, previousInvocation = 'd'.repeat(32);
  const previous = {version: 3, pid: child.pid, startTicks, invocationId: previousInvocation};
  for (const previousEnvironment of [`INVOCATION_ID=${previousInvocation}\0`, 'UNRELATED=discarded\0', null]) {
    const options = fixture(); seed(options, previous, 'held');
    const environments = {[process.pid]: `INVOCATION_ID=${'e'.repeat(32)}\0`};
    if (previousEnvironment !== null) environments[child.pid] = previousEnvironment;
    withInvocations(environments, () => {
      const runtime = createAccessRuntime(options);
      assert.equal(runtime.status().available, false);
      assert.equal(runtime.admit({}, {runId:'native'}).outcome, 'block');
      assert.deepEqual(JSON.parse(fs.readFileSync(path.join(options.directory, 'process.json'))), previous);
    });
  }
});

test('verified invocation or start mismatch recovers stale PID while retaining held and interrupted states', linux, () => {
  const invocationId = 'd'.repeat(32), startTicks = identity().startTicks;
  for (const phase of ['idle', 'held', 'busy', 'interrupted']) {
    for (const mismatch of ['invocationId', 'startTicks']) {
      const previous = {version: 3, pid: process.pid, startTicks, invocationId};
      previous[mismatch] = mismatch === 'invocationId' ? 'e'.repeat(32) : String(BigInt(startTicks) + 1n);
      const options = fixture(); seed(options, previous, phase);
      withInvocations({[process.pid]: `INVOCATION_ID=${invocationId}\0`}, () => {
        const runtime = createAccessRuntime(options);
        assert.equal(runtime.status().available, true);
        assert.equal(runtime.status().phase, phase === 'busy' ? 'interrupted' : phase);
        assert.equal(runtime.admit({}, {runId:'native'}).outcome, phase === 'idle' ? 'pass' : 'block');
        if (phase === 'held') assert.equal(runtime.owns(token), true);
      });
    }
  }
});

test('restricted proc rejects missing, ambiguous, malformed, or oversized invocation identity', linux, () => {
  const value = 'd'.repeat(32);
  for (const environment of ['UNRELATED=value\0', `PREFIX_INVOCATION_ID=${value}\0`, `INVOCATION_ID=${value}`,
    `INVOCATION_ID=${value}\0INVOCATION_ID=${value}\0`, 'INVOCATION_ID=unknown\0', `INVOCATION_ID=${value}extra\0`,
    `INVOCATION_ID=${value}\0LARGE=${'x'.repeat(1048576)}\0`]) {
    withInvocations({[process.pid]: environment}, () => {
      const runtime = createAccessRuntime(fixture());
      assert.equal(runtime.status().available, false);
      assert.equal(runtime.admit({}, {runId:'native'}).outcome, 'block');
    });
  }
});

test('reused PID recovers with unreadable foreign environment but preserves a matching unknown owner', linux, async t => {
  const {child} = await childProcess(t, `process.send({ready:true}); setInterval(() => {}, 1000);`);
  const startTicks = identity(child.pid).startTicks;
  for (const mismatch of [false, true]) {
    for (const phase of ['idle', 'held', 'busy', 'interrupted']) {
      const options = fixture();
      const previous = {version: 3, pid: child.pid, invocationId: 'd'.repeat(32),
        startTicks: mismatch ? String(BigInt(startTicks) + 1n) : startTicks};
      seed(options, previous, phase);
      withInvocations({[process.pid]: `INVOCATION_ID=${'e'.repeat(32)}\0`}, () => {
        const runtime = createAccessRuntime(options);
        assert.equal(runtime.status().available, mismatch);
        if (!mismatch) {
          assert.deepEqual(JSON.parse(fs.readFileSync(path.join(options.directory, 'process.json'))), previous);
          assert.equal(runtime.admit({}, {runId:'native'}).outcome, 'block');
        } else {
          assert.equal(runtime.status().phase, phase === 'busy' ? 'interrupted' : phase);
          assert.equal(runtime.admit({}, {runId:'native'}).outcome, phase === 'idle' ? 'pass' : 'block');
          if (phase === 'held') assert.equal(runtime.owns(token), true);
        }
      });
    }
  }
});

test('incarnation changing during invocation read fails closed', linux, () => {
  const options = fixture();
  withInvocations({[process.pid]: `INVOCATION_ID=${'d'.repeat(32)}\0`}, () => {
    const read = fs.readFileSync; let reads = 0;
    fs.readFileSync = function (name, ...args) {
      const contents = read.call(this, name, ...args);
      if (name !== `/proc/${process.pid}/stat` || ++reads < 2) return contents;
      const end = contents.lastIndexOf(')'), fields = contents.slice(end + 2).trim().split(/\s+/);
      fields[19] = String(BigInt(fields[19]) + 1n);
      return contents.slice(0, end + 2) + fields.join(' ');
    };
    try {
      const runtime = createAccessRuntime(options);
      assert.equal(runtime.status().available, false);
      assert.equal(runtime.admit({}, {runId:'native'}).outcome, 'block');
      assert.equal(fs.existsSync(path.join(options.directory, 'process.json')), false);
    } finally { fs.readFileSync = read; }
  });
});

test('live child incarnation keeps ownership, including comm with spaces and parentheses', linux, async t => {
  const options = fixture();
  const {child, message} = await childProcess(t, `
    import fs from 'node:fs';
    import {createAccessRuntime} from ${JSON.stringify(runtimeUrl)};
    fs.writeFileSync('/proc/self/comm', 'owner ) name');
    const r = createAccessRuntime(${JSON.stringify(options)});
    r.acquire('${token}', r.status().revision);
    process.send(r.status()); setInterval(() => {}, 1000);`);
  assert.equal(message.available, true);
  const lockPath = path.join(options.directory, 'process.json');
  const before = fs.readFileSync(lockPath, 'utf8');
  assert.deepEqual(JSON.parse(before), identity(child.pid));
  const second = createAccessRuntime(options);
  assert.equal(second.status().available, false);
  assert.equal(second.admit({}, {runId: 'duplicate'}).outcome, 'block');
  assert.equal(fs.readFileSync(lockPath, 'utf8'), before);
  assert.equal(JSON.parse(fs.readFileSync(path.join(options.directory, 'state.json'))).phase, 'held');
  const exited = once(child, 'exit'); child.kill('SIGKILL'); await exited;
  const restarted = createAccessRuntime(options);
  assert.equal(restarted.status().available, true);
  assert.equal(restarted.status().phase, 'held');
  assert.equal(restarted.owns(token), true);
  assert.equal(restarted.admit({}, {runId: 'after-crash'}).outcome, 'block');
});

test('reused live PID with mismatched boot or start identity recovers idle admission', linux, () => {
  for (const mismatch of ['bootId', 'startTicks']) {
    const options = fixture(), previous = identity();
    previous[mismatch] = mismatch === 'bootId' ? '00000000-0000-0000-0000-000000000000' : String(BigInt(previous.startTicks) + 1n);
    seed(options, previous);
    const runtime = createAccessRuntime(options);
    assert.equal(runtime.status().available, true, mismatch);
    assert.equal(runtime.status().phase, 'idle');
    assert.equal(runtime.status().proof, null);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(options.directory, 'process.json'))), identity());
    assert.equal(runtime.admit({}, {runId: 'native'}).outcome, 'pass');
  }
});

test('structured lock recovers from a foreign recycled PID while legacy lock stays conservative', linux, () => {
  const foreignPid = 424242, structured = {
    version: 3, pid: foreignPid, invocationId: 'd'.repeat(32), startTicks: '123',
  };
  const original = process.kill;
  process.kill = function (pid, signal) {
    if (pid === foreignPid && signal === 0) {
      throw Object.assign(new Error('not permitted'), {code: 'EPERM'});
    }
    return original.call(this, pid, signal);
  };
  try {
    const recovered = fixture(); seed(recovered, structured);
    const runtime = createAccessRuntime(recovered);
    assert.equal(runtime.status().available, true);
    assert.equal(runtime.status().phase, 'idle');
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(recovered.directory, 'process.json'))), identity());
    assert.equal(runtime.admit({}, {runId: 'after-foreign-reuse'}).outcome, 'pass');

    const legacy = fixture(); seed(legacy, {pid: foreignPid});
    const before = fs.readFileSync(path.join(legacy.directory, 'process.json'), 'utf8');
    assert.equal(createAccessRuntime(legacy).status().available, false);
    assert.equal(fs.readFileSync(path.join(legacy.directory, 'process.json'), 'utf8'), before);
  } finally { process.kill = original; }
});

test('PID reuse recovery preserves held and interrupted gates and interrupts busy work', linux, () => {
  for (const phase of ['held', 'busy', 'interrupted']) {
    const options = fixture(), previous = identity(); previous.startTicks = String(BigInt(previous.startTicks) + 1n);
    seed(options, previous, phase);
    const runtime = createAccessRuntime(options);
    assert.equal(runtime.status().available, true);
    assert.equal(runtime.status().phase, phase === 'busy' ? 'interrupted' : phase);
    if (phase === 'held') assert.equal(runtime.owns(token), true);
    assert.equal(runtime.admit({}, {runId: 'native'}).outcome, 'block');
    assert.equal(runtime.beforeTool({toolCallId: 'direct'}).block, true);
  }
});

test('live legacy locks remain conservative and dead legacy locks migrate', linux, () => {
  const live = fixture(); seed(live, {pid: process.pid});
  const before = fs.readFileSync(path.join(live.directory, 'process.json'), 'utf8');
  assert.equal(createAccessRuntime(live).status().available, false);
  assert.equal(fs.readFileSync(path.join(live.directory, 'process.json'), 'utf8'), before);
  const child = spawnSync(process.execPath, ['-e', ''], {encoding: 'utf8'});
  assert.equal(child.status, 0, child.stderr);
  const dead = fixture(); seed(dead, {pid: child.pid});
  assert.equal(createAccessRuntime(dead).status().available, true);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(dead.directory, 'process.json'))), identity());
});

test('unknown or malformed process identity never overwrites the lock or state', linux, () => {
  const known = identity();
  for (const lock of [null, {pid: -1}, {...known, version: 3}, {...known, startTicks: 123},
    {...known, startTicks: 'unknown'}, {...known, bootId: 'unknown'}, {...known, extra: true}, {pid: process.pid, version: 1}]) {
    const options = fixture(); seed(options, lock);
    const before = ['process.json', 'state.json'].map(name => fs.readFileSync(path.join(options.directory, name), 'utf8'));
    const runtime = createAccessRuntime(options);
    assert.equal(runtime.status().available, false);
    assert.equal(runtime.admit({}, {runId: 'native'}).outcome, 'block');
    assert.deepEqual(['process.json', 'state.json'].map(name => fs.readFileSync(path.join(options.directory, name), 'utf8')), before);
  }
  for (const contents of ['{broken', ' '.repeat(4097)]) {
    const options = fixture(); seed(options, known);
    const lockPath = path.join(options.directory, 'process.json');
    fs.writeFileSync(lockPath, contents);
    assert.equal(createAccessRuntime(options).status().available, false);
    assert.equal(fs.readFileSync(lockPath, 'utf8'), contents);
  }
});

test('unreadable or malformed live proc identity fails closed', linux, () => {
  for (const target of ['/proc/sys/kernel/random/boot_id', `/proc/${process.pid}/stat`]) {
    for (const failure of ['unreadable', 'malformed', 'missing']) {
      const options = fixture(); seed(options, identity());
      const original = fs.readFileSync, lockPath = path.join(options.directory, 'process.json');
      const before = original(lockPath, 'utf8');
      fs.readFileSync = function (name, ...args) {
        if (name === target) {
          if (failure === 'malformed') return 'unknown';
          throw Object.assign(new Error('unavailable'), {code: failure === 'missing' ? 'ENOENT' : 'EACCES'});
        }
        return original.call(this, name, ...args);
      };
      try {
        const runtime = createAccessRuntime(options);
        assert.equal(runtime.status().available, false);
        assert.equal(runtime.admit({}, {runId: 'native'}).outcome, 'block');
        assert.equal(original(lockPath, 'utf8'), before);
      } finally { fs.readFileSync = original; }
    }
  }
});

test('missing or failing Linux flock helper cannot claim admission', linux, () => {
  for (const result of [{status: null, error: Object.assign(new Error('missing'), {code: 'ENOENT'})},
    {status: 1}, {status: null, signal: 'SIGTERM'}]) {
    const options = fixture(), previous = identity(); previous.startTicks = String(BigInt(previous.startTicks) + 1n);
    seed(options, previous);
    const lockPath = path.join(options.directory, 'process.json'), before = fs.readFileSync(lockPath, 'utf8');
    const original = childProcessApi.spawnSync;
    childProcessApi.spawnSync = (command, args, opts) => {
      assert.equal(command, '/usr/bin/flock');
      assert.deepEqual(args, ['--exclusive', '--nonblock', '3']);
      assert.equal(typeof opts.stdio[3], 'number');
      return result;
    };
    syncBuiltinESMExports();
    try {
      const runtime = createAccessRuntime(options);
      assert.equal(runtime.status().available, false);
      assert.equal(runtime.admit({}, {runId: 'native'}).outcome, 'block');
      assert.equal(fs.readFileSync(lockPath, 'utf8'), before);
    } finally { childProcessApi.spawnSync = original; syncBuiltinESMExports(); }
    assert.equal(createAccessRuntime(options).status().available, true);
  }
});

test('non-Linux POSIX keeps conservative legacy behavior and rejects Linux identities', linux, () => {
  const descriptor = Object.getOwnPropertyDescriptor(process, 'platform');
  const child = spawnSync(process.execPath, ['-e', ''], {encoding: 'utf8'});
  assert.equal(child.status, 0, child.stderr);
  const previous = identity();
  Object.defineProperty(process, 'platform', {...descriptor, value: 'darwin'});
  try {
    const fresh = fixture(), runtime = createAccessRuntime(fresh);
    assert.equal(runtime.status().available, true);
    assert.equal(runtime.admit({}, {runId: 'native'}).outcome, 'pass');
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(fresh.directory, 'process.json'))), {pid: process.pid});
    assert.equal(createAccessRuntime(fresh).status().available, false);
    const dead = fixture(); seed(dead, {pid: child.pid});
    assert.equal(createAccessRuntime(dead).status().available, true);
    const unknown = fixture(); seed(unknown, previous);
    assert.equal(createAccessRuntime(unknown).status().available, false);
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(unknown.directory, 'process.json'))), previous);
  } finally { Object.defineProperty(process, 'platform', descriptor); }
});

test('kernel claim survives helper exit, excludes recovery, and releases after owner death', linux, async t => {
  const options = fixture(), previous = identity(); previous.startTicks = String(BigInt(previous.startTicks) + 1n);
  seed(options, previous);
  const claim = path.join(options.directory, '.process-claim');
  const {child, message} = await childProcess(t, `
    import fs from 'node:fs'; import {spawnSync} from 'node:child_process';
    const fd = fs.openSync(${JSON.stringify(claim)}, 'wx', 0o600);
    const result = spawnSync('/usr/bin/flock', ['--exclusive', '--nonblock', '3'],
      {stdio: ['ignore', 'ignore', 'ignore', fd]});
    process.send({status: result.status}); setInterval(() => {}, 1000);`);
  assert.equal(message.status, 0);
  const before = fs.readFileSync(path.join(options.directory, 'process.json'), 'utf8');
  assert.equal(createAccessRuntime(options).status().available, false);
  assert.equal(fs.readFileSync(path.join(options.directory, 'process.json'), 'utf8'), before);
  const exited = once(child, 'exit'); child.kill('SIGKILL'); await exited;
  assert.equal(createAccessRuntime(options).status().available, true);
  assert.equal(fs.statSync(claim).isFile(), true);
});

test('simultaneous stale registrations elect one live owner without replacing it', linux, async t => {
  const options = fixture(), previous = identity(); previous.startTicks = String(BigInt(previous.startTicks) + 1n);
  seed(options, previous);
  const contenders = await Promise.all(Array.from({length: 6}, () => childProcess(t, `
    import {createAccessRuntime} from ${JSON.stringify(runtimeUrl)};
    process.once('message', () => {
      const r = createAccessRuntime(${JSON.stringify(options)});
      if (r.status().available) r.acquire('${token}', r.status().revision);
      process.send(r.status());
    }); process.send({ready: true}); setInterval(() => {}, 1000);`)));
  const responses = contenders.map(({child}) => once(child, 'message', {signal: AbortSignal.timeout(10000)}));
  contenders.forEach(({child}) => child.send('start'));
  const states = (await Promise.all(responses)).map(([state]) => state);
  const winners = states.filter(state => state.available);
  assert.equal(winners.length, 1);
  assert.equal(winners[0].phase, 'held');
  const lock = JSON.parse(fs.readFileSync(path.join(options.directory, 'process.json')));
  assert.equal(lock.pid, winners[0].pid);
  assert.deepEqual(lock, identity(winners[0].pid));
  assert.equal(createAccessRuntime(options).status().available, false);
  assert.equal(JSON.parse(fs.readFileSync(path.join(options.directory, 'state.json'))).phase, 'held');
});

test('process and kernel claim files require private owned regular single-link entries', linux, () => {
  for (const name of ['process.json', '.process-claim']) {
    for (const kind of ['symlink', 'hardlink', 'public', 'directory', 'foreign-owner']) {
      const options = fixture(); seed(options, {pid: process.pid});
      const target = path.join(options.directory, name), source = path.join(options.directory, 'source');
      fs.writeFileSync(source, JSON.stringify({pid: process.pid}), {mode: 0o600});
      if (fs.existsSync(target)) fs.unlinkSync(target);
      if (kind === 'symlink') fs.symlinkSync(source, target);
      if (kind === 'hardlink') fs.linkSync(source, target);
      if (kind === 'public') { fs.copyFileSync(source, target); fs.chmodSync(target, 0o644); }
      if (kind === 'directory') fs.mkdirSync(target, {mode: 0o700});
      const lstat = fs.lstatSync;
      if (kind === 'foreign-owner') {
        fs.copyFileSync(source, target);
        fs.lstatSync = function (name, ...args) {
          const entry = lstat.call(this, name, ...args);
          if (name === target) entry.uid = process.getuid() + 1;
          return entry;
        };
      }
      try {
        const runtime = createAccessRuntime(options);
        assert.equal(runtime.status().available, false, `${name}: ${kind}`);
        assert.equal(runtime.admit({}, {runId: 'native'}).outcome, 'block');
        assert.equal(fs.readFileSync(source, 'utf8'), JSON.stringify({pid: process.pid}));
      } finally { fs.lstatSync = lstat; }
    }
  }
});

test('all agent IDs and cron/native turns retain admission until end', () => {
  const runtime = createAccessRuntime(fixture());
  assert.equal(runtime.admit(null, {runId: 'native', agentId: 'another'}).outcome, 'pass');
  assert.equal(runtime.admit(null, {runId: 'cron', agentId: 'pixel', trigger: 'cron'}).outcome, 'pass');
  assert.equal(runtime.status().active, 2);
  assert.throws(() => runtime.acquire(token, runtime.status().revision));
  runtime.finish({}, {runId: 'native'});
  assert.equal(runtime.status().active, 1);
  runtime.finish({runId: 'cron'}, {});
  assert.equal(runtime.status().phase, 'idle');
});

test('held lease blocks new native admission and direct tools', () => {
  const runtime = createAccessRuntime(fixture());
  runtime.acquire(token, runtime.status().revision);
  assert.equal(runtime.admit({}, {runId: 'later'}).outcome, 'block');
  assert.equal(runtime.beforeTool({toolCallId: 'direct'}).block, true);
  assert.throws(() => runtime.release(other));
  assert.equal(runtime.status().phase, 'held');
  runtime.release(token);
  assert.equal(runtime.admit({}, {runId: 'later'}).outcome, 'pass');
});

test('stale revision and missing run identity cannot gain admission', () => {
  const runtime = createAccessRuntime(fixture());
  const old = runtime.status().revision;
  runtime.admit({}, {runId: 'work'}); runtime.finish({}, {runId: 'work'});
  assert.throws(() => runtime.acquire(token, old));
  assert.equal(runtime.admit({}, {}).outcome, 'block');
});

test('direct tools and detached exec keep transition busy after agent end', () => {
  const runtime = createAccessRuntime(fixture());
  runtime.admit({}, {runId: 'work'});
  runtime.beforeTool({toolCallId: 'exec-1'}, {runId: 'work'});
  runtime.afterTool({toolCallId: 'exec-1', toolName: 'exec', result: {details: {status: 'running', sessionId: 'child'}}});
  runtime.finish({}, {runId: 'work'});
  assert.throws(() => runtime.acquire(token, runtime.status().revision));
  runtime.beforeTool({toolCallId: 'poll'});
  runtime.afterTool({toolCallId: 'poll', toolName: 'process', params: {sessionId: 'child'}, result: {details: {status: 'completed', exitCode: 0}}});
  assert.equal(runtime.status().phase, 'idle');
});

test('parallel process registration cannot replace the live owner gate', () => {
  const options = fixture(), runtime = createAccessRuntime(options);
  runtime.acquire(token, runtime.status().revision);
  const second = createAccessRuntime(options);
  assert.equal(second.status().available, false);
  assert.equal(runtime.status().phase, 'held');
  assert.equal(JSON.parse(fs.readFileSync(path.join(options.directory, 'state.json'))).phase, 'held');
});

test('held gate survives gateway process restart; busy crash requires recovery', () => {
  for (const phase of ['held', 'busy']) {
    const options = fixture();
    const script = `import {createAccessRuntime} from ${JSON.stringify(new URL('../plugin/access-runtime.mjs', import.meta.url).href)};
      const r=createAccessRuntime(${JSON.stringify(options)});
      ${phase === 'held' ? `r.acquire('${token}',r.status().revision)` : `r.admit({}, {runId:'active'})`};`;
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], {encoding: 'utf8'});
    assert.equal(child.status, 0, child.stderr);
    const runtime = createAccessRuntime(options);
    assert.equal(runtime.status().phase, phase === 'held' ? 'held' : 'interrupted');
    assert.equal(runtime.admit({}, {runId: 'new'}).outcome, 'block');
    runtime.acquire(token, runtime.status().revision);
    runtime.release(token);
    assert.equal(runtime.status().phase, 'idle');
  }
});

test('unsafe state directory and malformed state fail closed', () => {
  const options = fixture(); fs.mkdirSync(options.directory, {mode: 0o755});
  // Exercise unsafe permissions even when the test runner uses umask 0077.
  fs.chmodSync(options.directory, 0o755);
  const runtime = createAccessRuntime(options);
  assert.equal(runtime.status().available, false);
  assert.equal(runtime.admit({}, {runId: 'anything'}).outcome, 'block');
});

test('execution cancellation host follows exact loaded agent override', () => {
  const config = {agents: {defaults: {sandbox: {mode: 'all'}}, list: [{id: 'pixel'}]}};
  assert.equal(executionHostForAgent(config), 'sandbox');
  config.agents.list[0] = {id: 'pixel', sandbox: {mode: 'off'}, tools: {exec: {host: 'gateway'}}};
  assert.equal(executionHostForAgent(config), 'gateway');
  config.agents.list[0].tools.exec.host = 'node';
  assert.throws(() => executionHostForAgent(config));
});

test('missing admission permission or an unqualified SDK cannot enable changes', () => {
  for (const overrides of [{hooksAllowed: false}, {runtimeVersion: 'future'}]) {
    const runtime = createAccessRuntime({...fixture(), ...overrides});
    assert.equal(runtime.status().available, false);
    assert.throws(() => runtime.acquire(token, runtime.status().revision));
  }
});

test('settings readback requires the exact current held lease before reading config', linux, () => {
  let reads = 0;
  let config = {agents: {list: [{id: 'pixel', contextTokens: 32768}]}};
  const runtime = createAccessRuntime({...fixture(),
    config: () => { throw new Error('startup config must not be used for settings readback'); },
    settingsConfig: () => { reads++; return config; }});
  const idle = runtime.status();
  assert.throws(() => runtime.readSettings(token, idle.revision));
  assert.equal(reads, 0);
  const held = runtime.acquire(token, idle.revision);
  for (const [candidate, revision] of [[other, held.revision], [token, idle.revision], [token, undefined]]) {
    assert.throws(() => runtime.readSettings(candidate, revision));
  }
  assert.equal(reads, 0);
  const result = runtime.readSettings(token, held.revision);
  assert.equal(result.pid, process.pid);
  assert.equal(result.revision, held.revision);
  assert.deepEqual(result.fields.contextTokens, {present: true, value: 32768});
  assert.equal(reads, 1);
  assert.equal(runtime.status().revision, held.revision);
  config = {agents: {list: [{id: 'pixel', contextTokens: 65536}]}};
  assert.equal(runtime.readSettings(token, held.revision).fields.contextTokens.value, 65536);
  assert.equal(reads, 2);
  runtime.release(token);
  assert.throws(() => runtime.readSettings(token, held.revision));
  assert.equal(reads, 2);
});

test('settings readback refuses missing current-config support without reading startup data', linux, () => {
  const runtime = createAccessRuntime({...fixture(), config: () => { throw new Error('startup config reached'); }});
  const held = runtime.acquire(token, runtime.status().revision);
  assert.throws(() => runtime.readSettings(token, held.revision), /runtime settings snapshot unavailable/);
  assert.equal(runtime.status().phase, 'held');
});

test('settings readback on an inherited hold refuses unqualified runtime versions', linux, () => {
  const options = fixture();
  seed(options, {version: 2, pid: 2147483647, bootId: '0'.repeat(8) + '-0000-0000-0000-' + '0'.repeat(12), startTicks: '1'}, 'held');
  const runtime = createAccessRuntime({...options, runtimeVersion: 'future',
    config: () => { throw new Error('config must not be read'); }});
  assert.equal(runtime.status().available, false);
  assert.throws(() => runtime.readSettings(token, runtime.status().revision), /runtime lease mismatch/);
});
