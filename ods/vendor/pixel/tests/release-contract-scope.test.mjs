import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFile, readdir, mkdtemp, mkdir, cp, writeFile, chmod, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, basename} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));
const checker = await readFile(join(root, 'scripts/check-release-contract.mjs'), 'utf8');
const start = checker.indexOf('const allowedGeneratedPins = new Set([');
const finish = checker.indexOf('await findAuthoredPinDrift();', start);
assert.ok(start >= 0 && finish > start);
// Exercise the actual contiguous production evidence validation + scanner,
// without executing the checker's unrelated whole-repository top level.
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const checkEvidence = new AsyncFunction('root', 'manifest', 'read', 'sha256', 'readdir', 'join', 'readFile',
  checker.slice(start, finish) + 'await findAuthoredPinDrift();');
const manifest = JSON.parse(await readFile(join(root, 'RELEASE-MANIFEST.json'), 'utf8'));
const sha256 = value => createHash('sha256').update(value).digest('hex');
const evidence = (await readdir(root)).filter(name => /^ODS-QUALIFICATION-[0-9.]+\.md$/.test(name)).sort();
assert.equal(evidence.length, 16);

async function world(t) {
  const directory = await mkdtemp(join(tmpdir(), 'pixel-release-scope-'));
  await chmod(directory, 0o700);
  t.after(() => rm(directory, {recursive:true, force:true}));
  for (const name of evidence) await cp(join(root, name), join(directory, name));
  return {directory, check: () => checkEvidence(directory, manifest,
    path => readFile(join(directory, path), 'utf8'), sha256, readdir, join, readFile)};
}

test('exact frozen ODS history is evidence, including the two unreferenced records', async t => {
  const w = await world(t);
  assert.ok(evidence.includes('ODS-QUALIFICATION-4.3.30.md'));
  assert.ok(evidence.includes('ODS-QUALIFICATION-4.3.31.md'));
  await w.check();
});

test('known historical bytes changed even without a pin are refused before classification', async t => {
  const w = await world(t);
  await writeFile(join(w.directory, evidence[0]), 'Invented changed history without any release pin.\n');
  await assert.rejects(w.check(), /frozen ODS qualification evidence changed/);
});

test('missing known evidence is not silently skipped', async t => {
  const w = await world(t);
  await rm(join(w.directory, evidence[0]));
  await assert.rejects(w.check(), /ENOENT/);
});

test('unknown same-prefix evidence still undergoes the ordinary authored-pin scan', async t => {
  const w = await world(t);
  await writeFile(join(w.directory, 'ODS-QUALIFICATION-99.99.99.md'), manifest.openclaw);
  await assert.rejects(w.check(), /ODS-QUALIFICATION-99\.99\.99\.md duplicates an authored release pin/);
});

test('ordinary authored release pins still refuse', async t => {
  const w = await world(t);
  await writeFile(join(w.directory, 'ordinary.mjs'), JSON.stringify(manifest.openclaw));
  await assert.rejects(w.check(), /ordinary\.mjs duplicates an authored release pin/);
});

test('actual configure keeps optional Gmail windows and rejects invalid bounds', {timeout:30000}, async t => {
  const directory = await mkdtemp(join(tmpdir(), 'pixel-query-window-'));
  await chmod(directory, 0o700);
  t.after(() => rm(directory, {recursive:true, force:true}));
  const source = join(directory, 'source');
  const ignored = new Set(['.git', '.env', '.generated', '.runtime', 'dist', 'node_modules', '__pycache__']);
  await cp(root, source, {recursive:true, filter:path => !ignored.has(basename(path))});
  const home = join(directory, 'home');
  await mkdir(home, {mode:0o700});
  // Reuse the existing non-live launcher fixture, not a real OpenClaw install.
  const launcher = join(home, 'fake-openclaw');
  await cp(join(root, 'tests/fixtures/bin/openclaw'), launcher);
  await chmod(launcher, 0o755);
  const answersPath = join(directory, 'answers.json');
  const answers = {deploymentProfile:'prepared', capabilityProfile:'minimal',
    ownerName:'Invented Window Owner', organization:'Invented client', deploymentName:'window-fixture',
    timeZone:'America/New_York', agentId:'pixel', agentName:'Pixel', openclawBin:launcher,
    openclawHome:join(home,'.openclaw'), installDir:join(home,'install'), workspace:join(home,'workspace'),
    modelProvider:'local', modelId:'fixture', modelName:'Fixture', modelBaseUrl:'http://127.0.0.1:9/v1',
    modelApiKey:'fixture-only', modelContextWindow:8192, modelMaxTokens:1024,
    searxngBaseUrl:'http://127.0.0.1:9', embeddingModel:'fixture.gguf', embeddingCache:join(home,'cache'),
    googleAccount:'invented@example.invalid', calendarId:'primary', gatewayPort:18789, webCourierEnabled:false};
  async function configure(window) {
    await writeFile(answersPath, JSON.stringify({...answers, ...(window === undefined ? {} : {gmailWindowDays:window})}));
    return spawnSync(process.execPath, [join(source,'scripts/configure.mjs'), '--answers', answersPath, '--force'],
      {cwd:source, encoding:'utf8', timeout:10000,
        env:{PATH:dirname(process.execPath)+':/usr/bin:/bin', HOME:home, XDG_CONFIG_HOME:join(home,'.config'), LANG:'C.UTF-8'}});
  }
  const brokerEnv = join(source,'.generated/source-broker.env');
  for (const window of [undefined, null, '', 180, 3650]) {
    const result = await configure(window);
    assert.equal(result.status, 0, result.stderr);
    const body = await readFile(brokerEnv, 'utf8');
    const suffix = typeof window === 'number' ? ` newer_than:${window}d` : '';
    assert.ok(body.includes(`PIXEL_SOURCE_GMAIL_QUERY='in:inbox${suffix}'`));
    assert.ok(body.includes(`PIXEL_SOURCE_GMAIL_SENT_QUERY='in:sent${suffix}'`));
  }
  const before = await readFile(brokerEnv);
  for (const window of [0, -1, 3651, 1.5]) {
    const result = await configure(window);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /gmailWindowDays must be/);
    assert.deepEqual(await readFile(brokerEnv), before, 'invalid input must not rewrite generated queries');
  }
});
