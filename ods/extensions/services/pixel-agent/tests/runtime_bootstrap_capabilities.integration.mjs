// Explicit pinned-SDK lifecycle test, isolated files/state and no model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {registerBootstrapCapabilities, CALENDAR_TOOLS, FRONTIER_TOOLS} from '../plugin/bootstrap-capabilities.mjs';

const installed = process.argv[2] ?? process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(installed && path.isAbsolute(installed));
assert.equal(JSON.parse(fs.readFileSync(path.join(installed, 'package.json'))).version, '2026.6.33');
const hash = value => createHash('sha256').update(value).digest('hex');
const bootstrapModule = path.join(installed, 'dist/bootstrap-files-BBluxtSB.js');
assert.equal(hash(fs.readFileSync(bootstrapModule)), '054f712a6cfc7c1bb162f34885473abebecb632b75b740b96764b3564a1e8d37');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-bootstrap-capabilities-'));
process.env.OPENCLAW_STATE_DIR = path.join(temporary, 'state');
process.env.OPENCLAW_CONFIG_PATH = path.join(temporary, 'openclaw.json');
let registry;
try {
  const {o: resolveFiles} = await import(pathToFileURL(bootstrapModule));
  const {t: createRegistry} = await import(pathToFileURL(path.join(installed, 'dist/registry-BrwqdzU5.js')));
  const workspace = path.join(temporary, 'workspace'); fs.mkdirSync(workspace);
  const originals = Object.fromEntries(['AGENTS.md', 'TOOLS.md'].map(name => [name,
    fs.readFileSync(new URL(`../../../../vendor/pixel/workspace-template/${name}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n')]));
  for (const [name, value] of Object.entries(originals)) fs.writeFileSync(path.join(workspace, name), value);
  const config = {agents: {list: [{id: 'pixel', workspace, tools: {deny: [...CALENDAR_TOOLS, ...FRONTIER_TOOLS]}}]},
    plugins: {entries: {'pixel-ods': {enabled: true}, 'pixel-operations-broker': {enabled: true}}}};
  fs.writeFileSync(process.env.OPENCLAW_CONFIG_PATH, JSON.stringify(config));
  registry = createRegistry({activateGlobalSideEffects: true});
  const record = {id: 'pixel-ods', name: 'fixture', source: new URL('../plugin/bootstrap-capabilities.mjs', import.meta.url).pathname, hookNames: []};
  registerBootstrapCapabilities({registerHook: (events, handler, options) =>
    registry.registerHook(record, events, handler, options, config, {})});
  assert.equal(registry.registry.diagnostics.length, 0);
  const params = {workspaceDir: workspace, config, sessionKey: 'agent:pixel:isolated-fixture', agentId: 'pixel'};
  const text = (files, name) => files.find(file => file.name === name).content;
  const first = await resolveFiles(params), frozenFirst = structuredClone(first);
  assert.ok(!text(first, 'AGENTS.md').includes('copy its exact `etag`'));
  assert.ok(!text(first, 'TOOLS.md').includes('sanitizedPreview'));
  config.agents.list[0].tools.deny = [];
  config.plugins.entries['pixel-source-broker'] = {enabled: true};
  config.plugins.entries['pixel-frontier-broker'] = {enabled: true};
  const enabled = await resolveFiles(params);
  assert.ok(text(enabled, 'AGENTS.md').includes('copy its exact `etag`'));
  assert.equal(text(enabled, 'TOOLS.md'), originals['TOOLS.md']);
  assert.deepEqual(first, frozenFirst, 'later runs cannot mutate earlier returned file objects');
  config.agents.list[0].tools.deny = [...CALENDAR_TOOLS, ...FRONTIER_TOOLS];
  const disabledAgain = await resolveFiles(params);
  assert.equal(text(disabledAgain, 'AGENTS.md'), text(first, 'AGENTS.md'));
  const other = await resolveFiles({...params, agentId: 'other', sessionKey: 'agent:other:isolated-fixture'});
  assert.equal(text(other, 'AGENTS.md'), originals['AGENTS.md']);
  assert.equal(text(other, 'TOOLS.md'), originals['TOOLS.md']);
  for (const [name, value] of Object.entries(originals)) assert.equal(fs.readFileSync(path.join(workspace, name), 'utf8'), value);
  fs.appendFileSync(path.join(workspace, 'AGENTS.md'), '\nOwner customization.\n');
  const custom = await resolveFiles(params);
  assert.equal(text(custom, 'AGENTS.md'), originals['AGENTS.md'] + '\nOwner customization.\n');
  console.log(JSON.stringify({status: 'passed', modelCalls: 0, packageSha256: hash(fs.readFileSync(path.join(installed, 'package.json'))),
    bootstrapModuleSha256: hash(fs.readFileSync(bootstrapModule)),
    files: Object.entries(originals).map(([name, before]) => ({name, beforeChars: before.length,
      disabledChars: text(first, name).length, enabledChars: text(enabled, name).length,
      beforeSha256: hash(before), disabledSha256: hash(text(first, name)), enabledSha256: hash(text(enabled, name))})),
    assertions: ['actual plugin hook registration and bootstrap pipeline', 'disabled-enabled-disabled fresh config',
      'prior result object isolation', 'other-agent isolation', 'disk unchanged', 'owner customization preserved']}));
} finally {
  registry?.rollbackPluginGlobalSideEffects('pixel-ods');
  fs.rmSync(temporary, {recursive: true, force: true});
}
