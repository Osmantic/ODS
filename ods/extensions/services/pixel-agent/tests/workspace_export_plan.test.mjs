import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, symlinkSync, linkSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createWorkspaceExportPlanTool, workspaceExportPlan } from '../plugin/workspace-export-plan.mjs';
import { WORKSPACE_EXPORT_HELPER } from '../plugin/workspace-export-helper.mjs';

const hash = x => createHash('sha256').update(x).digest('hex');
function fixture() {
  const root = mkdtempSync(path.join(tmpdir(), 'ods-export-test-'));
  mkdirSync(path.join(root, 'src')); mkdirSync(path.join(root, 'out'));
  writeFileSync(path.join(root, 'src/a.txt'), Buffer.from('café 😀\r\nsecond line\n\n'));
  writeFileSync(path.join(root, 'src/b.txt'), Buffer.from('no final newline'));
  return root;
}
const request = () => ({ files: [
  { source: 'src/a.txt', destination: 'out/a.txt', key: 'alpha' },
  { source: 'src/b.txt', destination: 'out/b.txt', key: 'beta' },
], textMap: 'out/files.json' });
function run(root, input = request(), prefix = '') {
  if (prefix) return spawnSync('python3', ['-c', prefix + '\n' + WORKSPACE_EXPORT_HELPER,
    JSON.parse(workspaceExportPlan(input).content[0].text).exec.command.match(/ '([^']+)'$/)[1]], { cwd: root, encoding: 'utf8' });
  const plan = JSON.parse(workspaceExportPlan(input).content[0].text);
  return spawnSync('/bin/sh', ['-c', plan.exec.command], { cwd: root, encoding: 'utf8' });
}
function failedBeforeWrites(root, input, prefix = '') {
  const result = run(root, input, prefix);
  assert.equal(result.status, 2, result.stdout + result.stderr);
  assert.equal(result.stdout, '');
  assert.equal(JSON.parse(result.stderr).status, 'failed');
  return result;
}

test('planning has no filesystem effects and makes no execution claim', async () => {
  const root = fixture(); const before = readdirSync(path.join(root, 'out'));
  const result = await createWorkspaceExportPlanTool().execute('call', request());
  const plan = JSON.parse(result.content[0].text);
  assert.equal(plan.executed, false);
  assert.deepEqual(readdirSync(path.join(root, 'out')), before);
  assert.equal(plan.exec.workdir, undefined, 'ordinary exec retains its configured workspace root');
});

test('ordinary command preserves exact Unicode, CRLF and trailing-newline bytes and verifies hashes', () => {
  const root = fixture(); const r = run(root); assert.equal(r.status, 0, r.stderr);
  const receipt = JSON.parse(r.stdout); assert.equal(receipt.status, 'succeeded');
  const mapping = JSON.parse(readFileSync(path.join(root, 'out/files.json')));
  for (const [name, key] of [['a.txt', 'alpha'], ['b.txt', 'beta']]) {
    const source = readFileSync(path.join(root, 'src', name));
    assert.deepEqual(readFileSync(path.join(root, 'out', name)), source);
    assert.deepEqual(Buffer.from(mapping[key], 'utf8'), source);
    assert.equal(receipt.sources.find(x => x.source === 'src/' + name).sha256, hash(source));
  }
  for (const output of receipt.outputs) {
    const bytes = readFileSync(path.join(root, output.path));
    assert.equal(output.sha256, hash(bytes)); assert.equal(output.bytes, bytes.length);
    assert.equal(output.readbackVerified, true);
  }
  assert.match(receipt.boundary, /no tests, code execution, publication or task completion verified/);
});

test('binary copies work without a text map and are never executed', () => {
  const root = fixture(); const data = Buffer.from([0, 255, 128, 10]);
  writeFileSync(path.join(root, 'src/binary'), data);
  const r = run(root, { files: [{ source: 'src/binary', destination: 'out/binary' }] });
  assert.equal(r.status, 0, r.stderr); assert.deepEqual(readFileSync(path.join(root, 'out/binary')), data);
});

test('quotes, docstrings, escapes and mixed newlines round-trip without manual serialization', () => {
  const root = fixture();
  const bytes = Buffer.from('"""A docstring with \\"quotes\\" and \\n text."""\r\nvalue = "café 😀"\n\n');
  writeFileSync(path.join(root, 'src/a.txt'), bytes);
  const result = run(root); assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(readFileSync(path.join(root, 'out/a.txt')), bytes);
  assert.deepEqual(Buffer.from(JSON.parse(readFileSync(path.join(root, 'out/files.json'))).alpha), bytes);
});

test('workspace Python modules are data and cannot shadow serializer standard-library imports', () => {
  const root = fixture();
  writeFileSync(path.join(root, 'json.py'), "raise RuntimeError('workspace code executed')\n");
  const result = run(root); assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).status, 'succeeded');
});

test('map-only export supports explicit arbitrary keys', () => {
  const root = fixture(); const r = run(root, { files: [{ source: 'src/a.txt', key: '__proto__' }], textMap: 'out/map.json' });
  assert.equal(r.status, 0, r.stderr);
  assert.equal(JSON.parse(readFileSync(path.join(root, 'out/map.json')))['__proto__'], readFileSync(path.join(root, 'src/a.txt'), 'utf8'));
});

test('invalid UTF-8 for any map entry fails before creating raw copies', () => {
  const root = fixture(); writeFileSync(path.join(root, 'src/b.txt'), Buffer.from([255]));
  failedBeforeWrites(root, request()); assert.deepEqual(readdirSync(path.join(root, 'out')), []);
});

test('any existing destination fails before all writes and preserves its bytes', () => {
  const root = fixture(); writeFileSync(path.join(root, 'out/b.txt'), 'existing');
  failedBeforeWrites(root, request()); assert.deepEqual(readdirSync(path.join(root, 'out')), ['b.txt']);
  assert.equal(readFileSync(path.join(root, 'out/b.txt'), 'utf8'), 'existing');
});

for (const [name, mutate] of [
  ['source symlink', root => { symlinkSync('a.txt', path.join(root, 'src/link')); return { files: [{ source: 'src/link', destination: 'out/copy' }] }; }],
  ['source hardlink', root => { linkSync(path.join(root, 'src/a.txt'), path.join(root, 'src/link')); return request(); }],
  ['source directory', _ => ({ files: [{ source: 'src', destination: 'out/copy' }] })],
  ['destination parent symlink', root => { symlinkSync('out', path.join(root, 'alias')); return { files: [{ source: 'src/a.txt', destination: 'alias/copy' }] }; }],
  ['missing destination parent', _ => ({ files: [{ source: 'src/a.txt', destination: 'missing/copy' }] })],
]) test(`${name} is rejected without outputs`, () => {
  const root = fixture(); failedBeforeWrites(root, mutate(root)); assert.deepEqual(readdirSync(path.join(root, 'out')), []);
});

for (const [name, input] of [
  ['absolute source', { files: [{ source: '/tmp/a', destination: 'out/a' }] }],
  ['traversal', { files: [{ source: 'src/../a', destination: 'out/a' }] }],
  ['backslash', { files: [{ source: 'src\\a', destination: 'out/a' }] }],
  ['control character', { files: [{ source: 'src/a\n', destination: 'out/a' }] }],
  ['copy aliases', { files: [{ source: 'src/a', destination: 'out/A' }, { source: 'src/b', destination: 'out/a' }] }],
  ['source aliases', { files: [{ source: 'src/a', destination: 'out/a' }, { source: 'src/A', destination: 'out/b' }] }],
  ['map collision', { files: [{ source: 'src/a', destination: 'out/a', key: 'k' }], textMap: 'out/A' }],
  ['duplicate keys', { files: [{ source: 'src/a', key: 'k' }, { source: 'src/b', key: 'k' }], textMap: 'out/a' }],
  ['extra command', { ...request(), command: 'false' }],
  ['empty request', { files: [] }],
]) test(`invalid ${name} has no executable plan`, () => assert.throws(() => workspaceExportPlan(input)));

test('a destination appearing after preflight is not overwritten; prior new outputs are removed', () => {
  const root = fixture(); const prefix = String.raw`
import os
_open = os.open
def racing_open(name, flags, mode=0o777, *, dir_fd=None):
    if name == 'b.txt' and flags & os.O_CREAT:
        existing = _open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
        os.write(existing, b'racer'); os.close(existing)
    return _open(name, flags, mode, dir_fd=dir_fd)
os.open = racing_open
`;
  failedBeforeWrites(root, request(), prefix);
  assert.deepEqual(readdirSync(path.join(root, 'out')), ['b.txt']);
  assert.equal(readFileSync(path.join(root, 'out/b.txt'), 'utf8'), 'racer');
});

test('source mutation during export is detected and new outputs are removed', () => {
  const root = fixture(); const prefix = String.raw`
import os
_open = os.open
_changed = False
def racing_open(name, flags, mode=0o777, *, dir_fd=None):
    global _changed
    if flags & os.O_CREAT and not _changed:
        _changed = True
        with open('src/a.txt', 'ab') as f: f.write(b'changed')
    return _open(name, flags, mode, dir_fd=dir_fd)
os.open = racing_open
`;
  const r = failedBeforeWrites(root, request(), prefix); assert.match(r.stderr, /source-changed/);
  assert.deepEqual(readdirSync(path.join(root, 'out')), []);
  assert.ok(readFileSync(path.join(root, 'src/a.txt')).includes(Buffer.from('changed')));
});

test('source size failures occur before output creation', () => {
  const root = fixture(); writeFileSync(path.join(root, 'src/b.txt'), Buffer.alloc(1024 * 1024 + 1));
  failedBeforeWrites(root, request()); assert.deepEqual(readdirSync(path.join(root, 'out')), []);
});

test('bounded plan output fits native and deferred sanitizer limits without duplicate command in details', () => {
  for (let length = 1; length <= 125; length += 4) {
    const files = Array.from({ length: 6 }, (_, i) => ({ source: `src/${'s'.repeat(length)}${i}`, destination: `out/${'d'.repeat(length)}${i}`, key: `k${i}` }));
    let result;
    try { result = workspaceExportPlan({ files, textMap: 'out/map.json' }); }
    catch (e) { assert.match(e.message, /large|budget/); continue; }
    assert.ok(result.content[0].text.length <= 3500);
    const envelope = { tool: { id: 'openclaw:pixel-ods:pixel_ods_workspace_export_plan', name: 'pixel_ods_workspace_export_plan', source: 'openclaw', sourceName: 'pixel-ods', description: createWorkspaceExportPlanTool().description }, result };
    assert.ok(JSON.stringify(envelope, null, 2).length < 8000);
    assert.equal(result.details, undefined);
  }
});

test('cancelled planning does not produce a command', async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(createWorkspaceExportPlanTool().execute('call', request(), controller.signal), /cancelled/);
});

test('an earlier output changed while a later output is written cannot receive a success receipt', () => {
  const root=fixture();
  const prefix=String.raw`
import os
_open=os.open
_changed=False
def racing_open(name,flags,mode=0o777,*,dir_fd=None):
    global _changed
    if name == 'b.txt' and flags & os.O_CREAT and not _changed:
        _changed=True
        with open('out/a.txt','wb') as f: f.write(b'changed')
    return _open(name,flags,mode,dir_fd=dir_fd)
os.open=racing_open
`;
  failedBeforeWrites(root,request(),prefix);
  assert.deepEqual(readdirSync(path.join(root,'out')),[]);
});

test('a swapped output parent is rejected and cannot redirect writes through a symlink', () => {
  const root=fixture(); mkdirSync(path.join(root,'other'));
  const prefix=String.raw`
import os
_open=os.open
_changed=False
def racing_open(name,flags,mode=0o777,*,dir_fd=None):
    global _changed
    if flags & os.O_CREAT and not _changed:
        _changed=True
        os.rename('out','old-out')
        os.symlink('other','out')
    return _open(name,flags,mode,dir_fd=dir_fd)
os.open=racing_open
`;
  failedBeforeWrites(root,request(),prefix);
  assert.deepEqual(readdirSync(path.join(root,'other')),[]);
  assert.deepEqual(readdirSync(path.join(root,'old-out')),[]);
});

test('interrupted writes clean up only the invocation-owned outputs', () => {
  const root=fixture();
  const prefix=String.raw`
import os,signal
_write=os.write
_interrupted=False
def interrupted_write(fd,data):
    global _interrupted
    if not _interrupted:
        _interrupted=True
        os.kill(os.getpid(),signal.SIGTERM)
    return _write(fd,data)
os.write=interrupted_write
`;
  const r=failedBeforeWrites(root,request(),prefix);
  assert.match(r.stderr,/cancelled/);
  assert.deepEqual(readdirSync(path.join(root,'out')),[]);
});

test('receipt budget overflow fails before creating any output', () => {
  const root=fixture();
  const files=Array.from({length:6},(_,i)=>({source:`src/${'long-name-'.repeat(8)}${i}`,destination:`out/${'long-name-'.repeat(8)}${i}`,key:`k${i}`}));
  for(const item of files) writeFileSync(path.join(root,item.source),'quoted "data"\n');
  const result=failedBeforeWrites(root,{files,textMap:'out/map.json'});
  assert.match(result.stderr,/receipt-too-large/);
  assert.deepEqual(readdirSync(path.join(root,'out')),[]);
});
