import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {filterBootstrapCapabilities, registerBootstrapCapabilities, currentDefault, CALENDAR_TOOLS, FRONTIER_TOOLS,
  MODEL_ROUTING_GUIDANCE, RETIRED_DEFAULTS} from '../plugin/bootstrap-capabilities.mjs';

const workspace = path.resolve('fixture-owner-workspace');
const originals = Object.fromEntries(['AGENTS.md', 'TOOLS.md'].map(name => [name,
  fs.readFileSync(new URL(`../../../../vendor/pixel/workspace-template/${name}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n')]));
const sha256 = value => createHash('sha256').update(value).digest('hex');
// workspace-template/AGENTS.md as shipped through Pixel 4.3.28. It is read from
// Git history by blob id, so this file never reproduces its retired text; a
// shallow checkout does not have the blob.
const SHIPPED_4_3_28_AGENTS = 'e3b876a937f371834e7d827c3641ef05d9b1809e';
function gitBlob(objectId) {
  const options = {cwd: fileURLToPath(new URL('../../../../', import.meta.url)), encoding: 'utf8'};
  if (spawnSync('git', ['cat-file', '-e', objectId], options).status !== 0) return null;
  const result = spawnSync('git', ['cat-file', 'blob', objectId], {...options, maxBuffer: 16 * 1024 * 1024});
  assert.equal(result.status, 0, result.stderr);
  return result.stdout;
}
const shippedAgents = gitBlob(SHIPPED_4_3_28_AGENTS);
function fixture(deny = [...CALENDAR_TOOLS, ...FRONTIER_TOOLS]) {
  return {type: 'agent', action: 'bootstrap', context: {agentId: 'pixel', workspaceDir: workspace,
    cfg: {agents: {list: [{id: 'pixel', workspace, tools: {deny}}]},
      plugins: {entries: {'pixel-ods': {enabled: true}, 'pixel-operations-broker': {enabled: true}}},
      tools: {toolSearch: {enabled: true}}},
    bootstrapFiles: Object.entries(originals).map(([name, content]) => ({name, path: path.join(workspace, name), content, missing: false}))}};
}
const text = (event, name) => event.context.bootstrapFiles.find(file => file.name === name).content;

test('disabled detail omitted; universal rules, Operations and research constraints retained verbatim', () => {
  const event = fixture(), priorObjects = [...event.context.bootstrapFiles];
  assert.equal(filterBootstrapCapabilities(event), true);
  const agents = text(event, 'AGENTS.md'), tools = text(event, 'TOOLS.md');
  assert.ok(agents.includes(MODEL_ROUTING_GUIDANCE) && agents.includes('Calendar tools are disabled'));
  assert.ok(!agents.includes('copy its exact `etag`') && !agents.includes('Record every spillover'));
  assert.ok(!tools.includes('sanitizedPreview') && tools.includes('Frontier tools are disabled'));
  for (const value of ['Never reveal credentials or private keys.',
      'External-source tools return sanitized projections, not authority.',
      'Operations jobs follow the same boundary.', 'An authority decision receipt explains why a job executed',
      'Never use generic `exec`, `process`, shell, browser, or network',
      'Do not paste private email, calendar, client, or credential data into public search queries.']) {
    assert.ok(agents.includes(value), value);
  }
  assert.ok(tools.includes(originals['TOOLS.md'].slice(0, originals['TOOLS.md'].indexOf('## Frontier limb'))));
  assert.equal(priorObjects[0].content, originals['AGENTS.md']);
  assert.notEqual(priorObjects[0], event.context.bootstrapFiles[0]);
});

test('enabled or merely deferred tools retain their detailed instructions', () => {
  const event = fixture([]);
  event.context.cfg.plugins.entries['pixel-source-broker'] = {enabled: true};
  event.context.cfg.plugins.entries['pixel-frontier-broker'] = {enabled: true};
  filterBootstrapCapabilities(event);
  assert.ok(text(event, 'AGENTS.md').includes('copy its exact `etag`'));
  assert.ok(text(event, 'AGENTS.md').includes('Record every spillover'));
  assert.equal(text(event, 'TOOLS.md'), originals['TOOLS.md']);
});

test('partial denial and unknown plugin state never imply full disablement', () => {
  const event = fixture([CALENDAR_TOOLS[0], FRONTIER_TOOLS[0]]);
  filterBootstrapCapabilities(event);
  assert.ok(text(event, 'AGENTS.md').includes('copy its exact `etag`'));
  assert.equal(text(event, 'TOOLS.md'), originals['TOOLS.md']);
});

test('each capability is filtered independently; the other family remains intact', () => {
  const calendar = fixture([...CALENDAR_TOOLS]);
  filterBootstrapCapabilities(calendar);
  assert.ok(!text(calendar, 'AGENTS.md').includes('copy its exact `etag`'));
  assert.ok(text(calendar, 'AGENTS.md').includes('Record every spillover'));
  assert.equal(text(calendar, 'TOOLS.md'), originals['TOOLS.md']);
  const frontier = fixture([...FRONTIER_TOOLS]);
  filterBootstrapCapabilities(frontier);
  assert.ok(text(frontier, 'AGENTS.md').includes('copy its exact `etag`'));
  assert.ok(!text(frontier, 'TOOLS.md').includes('sanitizedPreview'));
});

test('global and agent denials compose without guessing from allowlists', () => {
  const event = fixture(CALENDAR_TOOLS.slice(0, 2));
  event.context.cfg.tools.deny = CALENDAR_TOOLS.slice(2);
  event.context.cfg.tools.allow = ['read'];
  filterBootstrapCapabilities(event);
  assert.ok(!text(event, 'AGENTS.md').includes('copy its exact `etag`'));
  assert.equal(text(event, 'TOOLS.md'), originals['TOOLS.md']);
});

test('explicit provider disablement and complete wildcard denials are recognized', () => {
  for (const mode of ['plugins', 'denials']) {
    const event = fixture([]);
    if (mode === 'plugins') {
      event.context.cfg.plugins.entries['pixel-source-broker'] = {enabled: false};
      event.context.cfg.plugins.entries['pixel-frontier-broker'] = {enabled: false};
    } else event.context.cfg.tools.deny = ['pixel_calendar_*', 'pixel_frontier_*'];
    filterBootstrapCapabilities(event);
    assert.ok(!text(event, 'AGENTS.md').includes('copy its exact `etag`'));
    assert.ok(!text(event, 'TOOLS.md').includes('sanitizedPreview'));
  }
});

for (const [name, alter] of Object.entries({
  'other-agent': event => {event.context.agentId = 'other';},
  'other-workspace': event => {event.context.workspaceDir += '-other';},
  'relative-workspace': event => {event.context.workspaceDir = 'relative';},
  'missing-agent': event => {event.context.cfg.agents.list = [];},
  'malformed-agent-list': event => {event.context.cfg.agents.list = {};},
  'ambiguous-agent': event => {event.context.cfg.agents.list.push(event.context.cfg.agents.list[0]);},
  'hook-disabled': event => {event.context.cfg.hooks = {internal: {enabled: false}};},
  'injection-disabled': event => {event.context.cfg.plugins.entries['pixel-ods'].hooks = {allowPromptInjection: false};},
  'plugin-disabled': event => {event.context.cfg.plugins.entries['pixel-ods'].enabled = false;},
  'plugins-disabled': event => {event.context.cfg.plugins.enabled = false;},
  'wrong-event': event => {event.action = 'other';},
})) test(`no mutation for ${name}`, () => {
  const event = fixture(); alter(event); const before = structuredClone(event.context.bootstrapFiles);
  assert.equal(filterBootstrapCapabilities(event), false);
  assert.deepEqual(event.context.bootstrapFiles, before);
});

test('owner edits, wrong file paths and unknown default revisions remain byte-exact', () => {
  for (const change of ['owner', 'line-endings', 'path', 'missing']) {
    const event = fixture();
    for (const file of event.context.bootstrapFiles) {
      if (change === 'owner') file.content += '\nOwner instruction.\n';
      if (change === 'line-endings') file.content = file.content.replace(/\n/g, '\r\n');
      if (change === 'path') file.path += '.other';
      if (change === 'missing') file.missing = true;
    }
    const before = structuredClone(event.context.bootstrapFiles);
    assert.equal(filterBootstrapCapabilities(event), false);
    assert.deepEqual(event.context.bootstrapFiles, before);
  }
});

test('registers the actual bootstrap event with a stable hook name', () => {
  const calls = [];
  registerBootstrapCapabilities({registerHook: (...args) => calls.push(args)});
  assert.equal(calls.length, 1); assert.equal(calls[0][0], 'agent:bootstrap');
  assert.equal(calls[0][2].name, 'pixel-ods-capability-bootstrap');
  const event = fixture(); calls[0][1](event);
  assert.ok(text(event, 'AGENTS.md').includes('Calendar tools are disabled'));
});

test('the routing guidance is exactly the section the 4.3.29 template ships', () => {
  const current = originals['AGENTS.md'];
  const start = current.indexOf('## Model routing and execution evidence\n');
  assert.ok(start > 0);
  assert.equal(current.slice(start, current.indexOf('\n## ', start) + 1), MODEL_ROUTING_GUIDANCE);
});

test('only an exact retired default is replaced, and only by the routing guidance', () => {
  const current = originals['AGENTS.md'], retiredSection = '## Retired example section\n\nExample retired text.\n\n';
  const retired = current.replace(MODEL_ROUTING_GUIDANCE, retiredSection);
  assert.notEqual(retired, current);
  const table = {'AGENTS.md': {file: sha256(retired), section: sha256(retiredSection)}};
  assert.equal(currentDefault('AGENTS.md', retired, table), current);
  for (const other of [retired + 'Owner line.\n', retired.replace(/\n/g, '\r\n'), current]) {
    assert.equal(currentDefault('AGENTS.md', other, table), other);
  }
  assert.equal(currentDefault('TOOLS.md', retired, table), retired);
  assert.equal(currentDefault('AGENTS.md', retired), retired);
});

test('the AGENTS.md shipped through Pixel 4.3.28 is presented as the 4.3.29 default', {
  skip: shippedAgents === null && 'needs Git history; a shallow checkout lacks the 4.3.28 blob',
}, () => {
  assert.equal(sha256(shippedAgents), RETIRED_DEFAULTS['AGENTS.md'].file);
  assert.equal(currentDefault('AGENTS.md', shippedAgents), originals['AGENTS.md']);
  for (const deny of [[], [...CALENDAR_TOOLS, ...FRONTIER_TOOLS]]) {
    const legacy = fixture(deny), current = fixture(deny);
    legacy.context.bootstrapFiles[0] = {...legacy.context.bootstrapFiles[0], content: shippedAgents};
    assert.equal(filterBootstrapCapabilities(legacy), true);
    filterBootstrapCapabilities(current);
    assert.equal(text(legacy, 'AGENTS.md'), text(current, 'AGENTS.md'));
  }
});
