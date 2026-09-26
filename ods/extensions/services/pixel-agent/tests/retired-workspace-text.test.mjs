// Retired template text: runtime prompt filter, vendored on-disk migration, the real
// apply workspace steps, and a privacy scan of every model-facing seeded file.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {filterBootstrapCapabilities, removeRetiredText, RETIRED_TEXT, CALENDAR_TOOLS, FRONTIER_TOOLS}
  from '../plugin/bootstrap-capabilities.mjs';
import {removeRetiredWorkspaceText, RETIRED_WORKSPACE_TEXT}
  from '../../../../vendor/pixel/scripts/lib/retired-workspace-text.mjs';

const ODS = fileURLToPath(new URL('../../../../', import.meta.url));
const PIXEL = path.join(ODS, 'vendor/pixel');
const TEMPLATE = path.join(PIXEL, 'workspace-template');
const sha256 = value => createHash('sha256').update(value).digest('hex');

// Owner-specific wording that must never reach a user's model context again.
const PRIVATE_TERMS = [/\bMichael\b/, /\bTower ?\d\b/i, /\bDSV4\b/, /Codex (?:scopes|supervis|plans)/i,
  /pixel-local-work-ledger/, /Qwen3\.6/, /Dream Fleet/i, /Local execution plan/i, /lightheart/i];
const privateTerms = text => PRIVATE_TERMS.filter(pattern => pattern.test(text)).map(String);

// Synthetic retired blocks keep these tests independent of the retired wording.
const SECTION = '## Retired sample section\n\nOwner-specific sample rule.\n\n### Detail\n1. First detail.\n2. Second “detail” — kept exact.\n';
const ENTRY = '- 2026-01-01: Retired sample entry.';
const TABLE = {
  'AGENTS.md': {sections: [sha256(SECTION)], lines: [], emptyHeadings: []},
  'MEMORY.md': {sections: [], lines: [sha256(ENTRY)], emptyHeadings: ['## Standing operating decisions']},
};
const HEAD = '# Operating contract\n\nIntro “quoted” text.\n\n## Web privacy\n\nPrefer private search.\n\n';
const TAIL = '## Memory\n\nKeep it short.\n';
const MEMORY_HEAD = '# Durable memory\n\nStore only facts.\n';
const crlf = text => text.replace(/\n/g, '\r\n');
const CASES = [
  ['section between headings', 'AGENTS.md', HEAD + SECTION + '\n' + TAIL, HEAD + TAIL, 1],
  ['section with CRLF line endings', 'AGENTS.md', crlf(HEAD + SECTION + '\n' + TAIL), crlf(HEAD + TAIL), 1],
  ['section with BOM and CRLF', 'AGENTS.md', '﻿' + crlf(HEAD + SECTION + '\n' + TAIL), '﻿' + crlf(HEAD + TAIL), 1],
  ['section at end of file', 'AGENTS.md', HEAD + SECTION, HEAD.trimEnd() + '\n', 1],
  ['section followed by extra empty lines', 'AGENTS.md', HEAD + SECTION + '\n\n\n' + TAIL, HEAD + TAIL, 1],
  ['owner edits elsewhere are kept', 'AGENTS.md', HEAD + 'Owner rule.\n\n' + SECTION + '\n' + TAIL + '\n## Owner\n\nMine.\n',
    HEAD + 'Owner rule.\n\n' + TAIL + '\n## Owner\n\nMine.\n', 1],
  ['one edited byte keeps the block', 'AGENTS.md', HEAD + SECTION.replace('First', 'first') + '\n' + TAIL, null, 0],
  ['owner line inside the block keeps it', 'AGENTS.md', HEAD + SECTION + 'Owner addition.\n\n' + TAIL, null, 0],
  ['trailing spaces are an edit', 'AGENTS.md', HEAD + SECTION.replace('rule.', 'rule. ') + '\n' + TAIL, null, 0],
  ['entry and its now-empty heading', 'MEMORY.md', `${MEMORY_HEAD}\n## Standing operating decisions\n\n${ENTRY}\n`, MEMORY_HEAD, 1],
  ['entry with CRLF', 'MEMORY.md', crlf(`${MEMORY_HEAD}\n## Standing operating decisions\n\n${ENTRY}\n`), crlf(MEMORY_HEAD), 1],
  ['entry without a final newline', 'MEMORY.md', `${MEMORY_HEAD}\n## Standing operating decisions\n\n${ENTRY}`, MEMORY_HEAD, 1],
  ['later owner entries keep the heading', 'MEMORY.md',
    `${MEMORY_HEAD}\n## Standing operating decisions\n\n${ENTRY}\n- 2026-09-01: Owner fact.\n`,
    `${MEMORY_HEAD}\n## Standing operating decisions\n\n- 2026-09-01: Owner fact.\n`, 1],
  ['a later section survives', 'MEMORY.md',
    `${MEMORY_HEAD}\n## Standing operating decisions\n\n${ENTRY}\n\n## Preferences\n\n- Tea.\n`,
    `${MEMORY_HEAD}\n## Preferences\n\n- Tea.\n`, 1],
  ['entry moved under another heading', 'MEMORY.md', `${MEMORY_HEAD}\n## Notes\n\n${ENTRY}\n- Kept.\n`,
    `${MEMORY_HEAD}\n## Notes\n\n- Kept.\n`, 1],
  ['edited entry is kept', 'MEMORY.md', `${MEMORY_HEAD}\n## Standing operating decisions\n\n${ENTRY} Owner note.\n`, null, 0],
  ['unrelated file name', 'TOOLS.md', HEAD + SECTION + '\n' + TAIL, null, 0],
];

for (const [label, name, before, after, count] of CASES) {
  test(`retired text: ${label}`, () => {
    const expected = after ?? before;
    const runtime = removeRetiredText(name, before, TABLE);
    assert.equal(runtime, expected);
    const input = Buffer.from(before), migrated = removeRetiredWorkspaceText(name, input, TABLE);
    assert.equal(migrated.removed, count);
    assert.equal(migrated.bytes.toString('utf8'), expected, 'vendored migration and runtime filter agree');
    if (count === 0) assert.equal(migrated.bytes, input, 'unchanged input is returned as is');
    assert.equal(removeRetiredText(name, runtime, TABLE), runtime, 'idempotent');
    assert.equal(removeRetiredWorkspaceText(name, migrated.bytes, TABLE).removed, 0, 'idempotent on disk');
  });
}

test('runtime filter and on-disk migration share one retired-text table', () => {
  assert.deepEqual(JSON.parse(JSON.stringify(RETIRED_TEXT)), JSON.parse(JSON.stringify(RETIRED_WORKSPACE_TEXT)));
  for (const entry of Object.values(RETIRED_TEXT)) {
    for (const digest of [...entry.sections, ...entry.lines]) assert.match(digest, /^[0-9a-f]{64}$/);
  }
});

test('the migration keeps invalid UTF-8 bytes outside the removed block', () => {
  const before = Buffer.concat([Buffer.from(HEAD), Buffer.from([0xff, 0xfe, 0x0a, 0x0a]), Buffer.from(SECTION + '\n' + TAIL)]);
  const result = removeRetiredWorkspaceText('AGENTS.md', before, TABLE);
  assert.equal(result.removed, 1);
  assert.deepEqual(result.bytes, Buffer.concat([Buffer.from(HEAD), Buffer.from([0xff, 0xfe, 0x0a, 0x0a]), Buffer.from(TAIL)]));
});

test('the current template carries no retired text and no owner-specific wording', () => {
  for (const name of ['AGENTS.md', 'MEMORY.md']) {
    const bytes = fs.readFileSync(path.join(TEMPLATE, name));
    assert.equal(removeRetiredWorkspaceText(name, bytes).removed, 0, name);
  }
});

// Every file copied into a model's startup context by an ODS installer.
function seededFiles() {
  const files = [];
  const walk = directory => {
    for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(full); else files.push(full);
    }
  };
  walk(TEMPLATE);
  walk(path.join(ODS, 'memory-shepherd/baselines'));
  files.push(path.join(ODS, 'extensions/services/hermes/SOUL.md.template'));
  return files;
}

test('model-facing seeded files contain no owner-specific names, hosts, or workflow', () => {
  const findings = seededFiles().flatMap(file => privateTerms(fs.readFileSync(file, 'utf8'))
    .map(term => `${path.relative(ODS, file)}: ${term}`));
  assert.deepEqual(findings, []);
});

// The workspace steps `pixel apply` runs: configure's template copy and placeholder
// substitution, `cp -n`, then the three workspace migrations in apply.sh order.
function generatedWorkspace(root) {
  const generated = path.join(root, 'generated');
  fs.cpSync(TEMPLATE, generated, {recursive: true});
  const values = {'{{OWNER_NAME}}': 'ODS Owner', '{{ORGANIZATION}}': 'Example Org', '{{TIME_ZONE}}': 'UTC',
    '{{DEPLOYMENT_NAME}}': 'Test'};
  const customize = directory => {
    for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) { customize(full); continue; }
      let body = fs.readFileSync(full, 'utf8');
      for (const [token, value] of Object.entries(values)) body = body.replaceAll(token, value);
      fs.writeFileSync(full, body);
    }
  };
  customize(generated);
  return generated;
}

function applyWorkspace(generated, workspace) {
  fs.mkdirSync(workspace, {recursive: true});
  fs.cpSync(generated, workspace, {recursive: true, force: false, errorOnExist: false});
  const node = (script, ...args) => execFileSync(process.execPath, [path.join(PIXEL, 'scripts', script), ...args],
    {encoding: 'utf8'});
  node('migrate-portal-identity.mjs', workspace, generated);
  node('migrate-workspace-source-boundary.mjs', workspace);
  return node('migrate-retired-workspace-text.mjs', workspace).trim();
}

const STARTUP_FILES = ['AGENTS.md', 'SOUL.md', 'TOOLS.md', 'IDENTITY.md', 'USER.md', 'HEARTBEAT.md', 'MEMORY.md'];
function odsBootstrap(workspace) {
  // ODS onboarding enables only the Operations limb.
  const event = {type: 'agent', action: 'bootstrap', context: {agentId: 'pixel', workspaceDir: workspace,
    cfg: {agents: {list: [{id: 'pixel', workspace, tools: {deny: [...CALENDAR_TOOLS, ...FRONTIER_TOOLS]}}]},
      plugins: {entries: {'pixel-ods': {enabled: true}, 'pixel-operations-broker': {enabled: true}}}},
    bootstrapFiles: STARTUP_FILES.map(name => ({name, path: path.join(workspace, name),
      content: fs.readFileSync(path.join(workspace, name), 'utf8'), missing: false}))}};
  filterBootstrapCapabilities(event);
  return event.context.bootstrapFiles;
}

test('a fresh install through the real apply workspace steps reaches the model without owner-specific text', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-retired-text-fresh-'));
  try {
    const generated = generatedWorkspace(root), workspace = path.join(root, 'workspace');
    assert.equal(applyWorkspace(generated, workspace), 'Retired workspace text: AGENTS.md current; MEMORY.md current');
    const files = odsBootstrap(workspace);
    assert.deepEqual(files.flatMap(file => privateTerms(file.content).map(term => `${file.name}: ${term}`)), []);
    assert.equal(applyWorkspace(generated, workspace), 'Retired workspace text: AGENTS.md current; MEMORY.md current');
    assert.deepEqual(fs.readdirSync(workspace).filter(name => name.endsWith('.bak')), []);
  } finally {
    fs.rmSync(root, {recursive: true, force: true});
  }
});

// The exact bytes every ODS Pixel bundle shipped from #6156 until this change. They are
// read from Git history, never committed again; shallow or exported trees skip.
const LEGACY = {
  'AGENTS.md': ['e3b876a937f371834e7d827c3641ef05d9b1809e', 'c5fe405d7c9a243ef21660aea711de14164dd1b245613f0970026f1881f4a225'],
  'MEMORY.md': ['358d3020fd5a6912457bebb17bf2b66da38c8548', 'fd6d893f4a19fc81fc1b2fc52df7abe90362d1c882e4ca694cc59a6563969213'],
};
function legacyTemplate(name) {
  try {
    const bytes = execFileSync('git', ['-C', ODS, 'cat-file', 'blob', LEGACY[name][0]], {stdio: ['ignore', 'pipe', 'ignore']});
    return sha256(bytes) === LEGACY[name][1] ? bytes : null;
  } catch {
    return null;
  }
}
const legacy = {'AGENTS.md': legacyTemplate('AGENTS.md'), 'MEMORY.md': legacyTemplate('MEMORY.md')};
const noHistory = !legacy['AGENTS.md'] || !legacy['MEMORY.md'] ? 'retired template bytes need full Git history' : false;

test('the shipped legacy template loses exactly its retired blocks', {skip: noHistory}, () => {
  for (const name of ['AGENTS.md', 'MEMORY.md']) {
    const result = removeRetiredWorkspaceText(name, legacy[name]);
    assert.equal(result.removed, 1, name);
    assert.deepEqual(result.bytes, fs.readFileSync(path.join(TEMPLATE, name)), `${name} equals the current template`);
    assert.equal(removeRetiredText(name, legacy[name].toString('utf8')), fs.readFileSync(path.join(TEMPLATE, name), 'utf8'));
    for (const variant of [crlf(legacy[name].toString('utf8')), '﻿' + legacy[name].toString('utf8')]) {
      assert.equal(removeRetiredWorkspaceText(name, Buffer.from(variant)).removed, 1, `${name} variant`);
    }
  }
});

test('the ODS bootstrap filter keeps retired blocks out of an unmigrated workspace prompt', {skip: noHistory}, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-retired-text-runtime-'));
  try {
    const generated = generatedWorkspace(root);
    const current = Object.fromEntries(['AGENTS.md', 'MEMORY.md'].map(name => [name, fs.readFileSync(path.join(generated, name), 'utf8')]));
    for (const shape of ['byte-exact legacy copy', 'legacy after source-boundary rewrite', 'owner-edited legacy']) {
      const workspace = path.join(root, shape.replaceAll(' ', '-'));
      fs.cpSync(generated, workspace, {recursive: true});
      for (const [name, bytes] of Object.entries(legacy)) fs.writeFileSync(path.join(workspace, name), bytes);
      if (shape !== 'byte-exact legacy copy') {
        execFileSync(process.execPath, [path.join(PIXEL, 'scripts/migrate-workspace-source-boundary.mjs'), workspace]);
      }
      if (shape === 'owner-edited legacy') {
        fs.appendFileSync(path.join(workspace, 'AGENTS.md'), '\n## Owner rule\n\nKeep replies short.\n');
        fs.appendFileSync(path.join(workspace, 'MEMORY.md'), '- 2026-09-24: Owner prefers metric units.\n');
      }
      const files = Object.fromEntries(odsBootstrap(workspace).map(file => [file.name, file.content]));
      assert.deepEqual(privateTerms(Object.values(files).join('\n')), [], shape);
      if (shape === 'byte-exact legacy copy') {
        // Removal yields the reviewed current default, so capability trimming still applies.
        assert.ok(files['AGENTS.md'].includes('Calendar tools are disabled'), shape);
        assert.equal(files['MEMORY.md'], current['MEMORY.md'], shape);
      }
      if (shape === 'owner-edited legacy') {
        assert.ok(files['AGENTS.md'].endsWith('\n## Owner rule\n\nKeep replies short.\n'), shape);
        assert.ok(files['MEMORY.md'].endsWith('## Standing operating decisions\n\n- 2026-09-24: Owner prefers metric units.\n'), shape);
      }
      for (const name of Object.keys(legacy)) {
        assert.ok(privateTerms(fs.readFileSync(path.join(workspace, name), 'utf8')).length > 0, 'the filter never writes files');
      }
    }
  } finally {
    fs.rmSync(root, {recursive: true, force: true});
  }
});

test('an upgraded install matches a fresh install and keeps one backup per changed file', {skip: noHistory}, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-retired-text-upgrade-'));
  try {
    const generated = generatedWorkspace(root);
    const fresh = path.join(root, 'fresh'), upgraded = path.join(root, 'upgraded');
    applyWorkspace(generated, fresh);
    // A workspace installed from a legacy bundle, including the source-boundary rewrite
    // that its own apply ran, as found on a deployed Linux host.
    fs.cpSync(generated, upgraded, {recursive: true});
    for (const [name, bytes] of Object.entries(legacy)) fs.writeFileSync(path.join(upgraded, name), bytes);
    execFileSync(process.execPath, [path.join(PIXEL, 'scripts/migrate-workspace-source-boundary.mjs'), upgraded]);
    const before = Object.fromEntries(Object.keys(legacy).map(name => [name, fs.readFileSync(path.join(upgraded, name))]));
    assert.ok(privateTerms(odsBootstrap(fresh).map(file => file.content).join('\n')).length === 0);
    const filteredBefore = STARTUP_FILES.map(name => name in before ? removeRetiredText(name, before[name].toString('utf8')) : null);
    assert.ok(filteredBefore.every(text => text === null || privateTerms(text).length === 0), 'runtime filter covers an unmigrated install');
    const log = applyWorkspace(generated, upgraded);
    assert.match(log, /^Retired workspace text: AGENTS\.md removed \(backup AGENTS\.md\.before-retired-text-removal\.[0-9a-f]{12}\.bak\); MEMORY\.md removed \(backup MEMORY\.md\.before-retired-text-removal\.[0-9a-f]{12}\.bak\)$/);
    for (const name of STARTUP_FILES) {
      assert.deepEqual(fs.readFileSync(path.join(upgraded, name)), fs.readFileSync(path.join(fresh, name)), name);
    }
    const backups = fs.readdirSync(upgraded).filter(name => name.endsWith('.bak')).sort();
    assert.equal(backups.length, 2);
    for (const backup of backups) {
      assert.deepEqual(fs.readFileSync(path.join(upgraded, backup)), before[backup.slice(0, backup.indexOf('.md') + 3)]);
    }
    assert.equal(applyWorkspace(generated, upgraded), 'Retired workspace text: AGENTS.md current; MEMORY.md current');
    assert.equal(fs.readdirSync(upgraded).filter(name => name.endsWith('.bak')).length, 2);
  } finally {
    fs.rmSync(root, {recursive: true, force: true});
  }
});
