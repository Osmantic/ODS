import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildReadonlyExecutionConfig,
  readonlyShellCommand,
} from '../plugin/readonly-execution-config.mjs';

const nonce = 'a'.repeat(32);
const imageId = 'sha256:' + 'b'.repeat(64);
const workspaceRoot = '/srv/agent/workspace';
const privateRoot = '/srv/agent/private';

function baseConfig(overrides = {}) {
  return {
    agents: {
      defaults: {
        workspace: workspaceRoot,
        sandbox: { mode: 'off' },
      },
      list: [
        {
          id: 'pixel',
          workspace: workspaceRoot,
          tools: { exec: { host: 'gateway', security: 'full' }, elevated: { enabled: true } },
          skills: ['alpha', 'beta'],
        },
        {
          id: 'other',
          workspace: '/srv/agent/other',
          tools: { exec: { host: 'gateway', security: 'full' }, elevated: { enabled: true } },
          skills: ['gamma'],
        },
      ],
    },
    ...overrides,
  };
}

function build(config = baseConfig(), opts = {}) {
  return buildReadonlyExecutionConfig(config, {
    workspaceRoot,
    privateRoot,
    nonce,
    imageId,
    ...opts,
  });
}

test('builds a pinned sandbox for pixel without mutating the input', () => {
  const input = baseConfig();
  const snapshot = structuredClone(input);
  const { config, sandboxSessionKey, containerPrefix } = build(input);
  assert.deepEqual(input, snapshot, 'input config must not be mutated');
  assert.notEqual(config, input);
  assert.equal(sandboxSessionKey, 'agent:pixel:ods-readonly:' + nonce);
  assert.equal(containerPrefix, 'ods-pixel-ro-' + nonce.slice(0, 16) + '-');

  const pixel = config.agents.list.find(a => a.id === 'pixel');
  const sandbox = pixel.sandbox;
  assert.equal(sandbox.mode, 'all');
  assert.equal(sandbox.backend, 'docker');
  assert.equal(sandbox.scope, 'session');
  assert.equal(sandbox.workspaceAccess, 'ro');
  assert.equal(sandbox.workspaceRoot, privateRoot + '/sandboxes');
  assert.equal(sandbox.docker.image, imageId);
  assert.equal(sandbox.docker.containerPrefix, containerPrefix);
  assert.equal(sandbox.docker.workdir, '/workspace');
  assert.equal(sandbox.docker.network, 'none');
  assert.equal(sandbox.docker.user, 'sandbox');
  assert.equal(sandbox.docker.readOnlyRoot, true);
  assert.deepEqual(sandbox.docker.capDrop, ['ALL']);
  assert.deepEqual(sandbox.docker.tmpfs, [
    '/tmp:rw,nosuid,nodev,size=16m',
    '/run:rw,nosuid,nodev,size=4m',
  ]);
  assert.equal(sandbox.docker.memory, '512m');
  assert.equal(sandbox.docker.memorySwap, '512m');
  assert.equal(sandbox.docker.cpus, 1);
  assert.equal(sandbox.docker.pidsLimit, 128);
  assert.deepEqual(sandbox.docker.env, { LANG: 'C.UTF-8' });
  assert.equal(sandbox.browser.enabled, false);
  assert.deepEqual(sandbox.prune, { idleHours: 0, maxAgeDays: 0 });
  assert.deepEqual(pixel.skills, []);
});

test('enforces both exec and elevated scopes even when absent', () => {
  const input = baseConfig();
  delete input.tools;
  delete input.agents.list[0].tools;
  const { config } = build(input);
  const pixel = config.agents.list.find(a => a.id === 'pixel');
  for (const owner of [config, pixel]) {
    assert.equal(owner.tools.exec.host, 'sandbox');
    assert.equal(owner.tools.elevated.enabled, false);
  }
});

test('preserves allow/deny/ask/security and non-pixel agents', () => {
  const input = baseConfig();
  input.tools = {};
  input.tools.exec = {
    host: 'gateway', security: 'allowlist', ask: 'always',
  };
  input.tools.allow = ['exec','read'];input.tools.deny = ['write'];
  input.tools.elevated = { enabled: true, allowFrom: {webchat:['owner']} };
  input.agents.list[0].tools.exec = {
    host: 'gateway', security: 'allowlist', ask: 'always',
  };
  input.agents.list[0].tools.allow = ['exec','read'];input.agents.list[0].tools.deny = ['write'];
  input.agents.list[0].tools.elevated = { enabled: true, allowFrom: {webchat:['owner']} };
  const { config } = build(input);
  const pixel = config.agents.list.find(a => a.id === 'pixel');
  for (const owner of [config, pixel]) {
    assert.equal(owner.tools.exec.host, 'sandbox');
    assert.equal(owner.tools.exec.security, 'allowlist');
    assert.deepEqual(owner.tools.allow, ['exec','read']);
    assert.deepEqual(owner.tools.deny, ['write']);
    assert.equal(owner.tools.exec.ask, 'always');
    assert.equal(owner.tools.elevated.enabled, false);
    assert.deepEqual(owner.tools.elevated.allowFrom, {webchat:['owner']});
  }
  const other = config.agents.list.find(a => a.id === 'other');
  assert.equal(other.tools.exec.host, 'gateway');
  assert.equal(other.tools.elevated.enabled, true);
  assert.deepEqual(other.skills, ['gamma']);
  assert.equal(other.sandbox, undefined);
});

test('rejects wrong, duplicate, or missing pixel agents', () => {
  const wrong = baseConfig();
  wrong.agents.list[0].id = 'notpixel';
  assert.throws(() => build(wrong), /readonly-config-scope-mismatch/);

  const dup = baseConfig();
  dup.agents.list.push({ id: 'pixel', workspace: workspaceRoot });
  assert.throws(() => build(dup), /readonly-config-scope-mismatch/);

  const missing = baseConfig();
  missing.agents.list = missing.agents.list.filter(a => a.id !== 'pixel');
  assert.throws(() => build(missing), /readonly-config-scope-mismatch/);
});

test('rejects pixel workspace that does not match the requested root', () => {
  const input = baseConfig();
  input.agents.list[0].workspace = '/srv/agent/elsewhere';
  assert.throws(() => build(input), /readonly-config-scope-mismatch/);
});

test('rejects invalid roots, nonce, and image id', () => {
  const cases = [
    { workspaceRoot: 'relative/path' },
    { workspaceRoot: '/srv/agent/workspace/' },
    { workspaceRoot: '/srv/agent/../agent/workspace' },
    { workspaceRoot: '/srv/agent\\workspace' },
    { workspaceRoot: '/srv/agent/\u0000workspace' },
    { workspaceRoot: '/srv/agent/\u001fworkspace' },
    { workspaceRoot: '/srv/agent/\u007fworkspace' },
    { workspaceRoot: '/' },
    { privateRoot: 'relative' },
    { privateRoot: '/srv/agent/private/' },
    { privateRoot: '/srv/agent/../private' },
    { nonce: 'short' },
    { nonce: 'A'.repeat(32) },
    { imageId: 'sha256:short' },
    { imageId: 'sha256:' + 'B'.repeat(64) },
    { imageId: 'md5:' + 'b'.repeat(64) },
  ];
  for (const opts of cases) {
    assert.throws(() => build(baseConfig(), opts), /readonly-config-invalid/, JSON.stringify(opts));
  }
});

test('rejects overlapping workspace and private roots', () => {
  assert.throws(
    () => build(baseConfig(), { workspaceRoot: '/srv/agent', privateRoot: '/srv/agent/private' }),
    /readonly-config-invalid/,
  );
  assert.throws(
    () => build(baseConfig(), { workspaceRoot: '/srv/agent/workspace', privateRoot: '/srv/agent' }),
    /readonly-config-invalid/,
  );
  assert.throws(
    () => build(baseConfig(), { workspaceRoot: '/srv/agent/x', privateRoot: '/srv/agent/x' }),
    /readonly-config-invalid/,
  );
});

test('rejects non-plain config objects', () => {
  class Cfg {}
  assert.throws(() => build(new Cfg()), /readonly-config-invalid/);
  assert.throws(() => build([]), /readonly-config-invalid/);
  assert.throws(() => build(null), /readonly-config-invalid/);
});

test('readonlyShellCommand preserves exact command including newlines, comments, heredocs', () => {
  const command = 'echo start\n# a comment\ncat <<EOF\nhello\nEOF\necho done';
  const out = readonlyShellCommand({ command, workdir: '.' });
  assert.equal(out, "cd -- '/agent' || exit $?\n" + command);
  assert.ok(out.includes('\n# a comment\n'));
  assert.ok(out.includes('cat <<EOF\nhello\nEOF'));
});

test('readonlyShellCommand safely quotes subdirectory with spaces and apostrophe', () => {
  const out = readonlyShellCommand({ command: 'ls', workdir: "sub dir/it's here" });
  assert.equal(out, "cd -- '/agent/sub dir/it'\"'\"'s here' || exit $?\nls");
});

test('readonlyShellCommand normalization rejects absolute and traversal workdirs', () => {
  assert.throws(() => readonlyShellCommand({ command: 'ls', workdir: '/etc' }));
  assert.throws(() => readonlyShellCommand({ command: 'ls', workdir: '../escape' }));
  assert.throws(() => readonlyShellCommand({ command: 'ls', workdir: 'a/../../b' }));
});
