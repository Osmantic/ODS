// Requires the exact installed/patched OpenClaw package and pinned llama.cpp
// bridge. Unlike unit tests, missing dependencies are a failure in this gate.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, randomUUID} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {spawnSync} from 'node:child_process';
import {registeredPixelTools, combinedToolSchema} from './tool-grammar-registration.mjs';

const bridge = process.env.ODS_TEST_LLAMA_SCHEMA;
const moduleFile = process.env.OPENCLAW_TOOL_SEARCH_MODULE;
const templateFile = process.env.ODS_TEST_LLAMA_CHAT_TEMPLATE;
assert.ok(bridge && moduleFile && templateFile, 'native bridge, actual runtime module and pinned template are required');
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-image-envelope.json', import.meta.url)));
assert.equal(createHash('sha256').update(readFileSync(moduleFile)).digest('hex'), manifest.patchedSha256);
const root = dirname(dirname(moduleFile));
assert.equal(JSON.parse(readFileSync(join(root, 'package.json'))).version, '2026.6.33');
const {u: applyCatalog, m: createCatalogRef} = await import(pathToFileURL(moduleFile));
const {t: createCoreTools} = await import(pathToFileURL(join(root, 'dist/agent-tools-D1DOpg6D.js')));
const {t: normalizeSchema} = await import(pathToFileURL(join(root, 'dist/agent-tools-parameter-schema-CnoL-cVw.js')));
const route = {modelProvider: 'ods-gateway', modelId: 'ods/current', modelApi: 'openai-completions'};
const pluginTools = (await registeredPixelTools()).map(tool =>
  ({...tool, parameters: normalizeSchema(tool.parameters, route)}));
const config = {plugins: {enabled: false}, models: {providers: {
  'ods-gateway': {baseUrl: 'http://127.0.0.1:1/v1', api: 'openai-completions',
    models: [{id: 'ods/current', name: 'Grammar fixture', input: ['text'], reasoning: false,
      contextWindow: 32768, maxTokens: 4096}]},
}}, tools: {
  toolSearch: {enabled: true, mode: 'tools'},
  web: {search: {enabled: true}, fetch: {enabled: true}},
}};
// No tool execute function is called. OpenClaw constructs its real core schemas
// and normalizes them just as it does for a model request. Provider/model IDs
// match the stable route in pixel-host-install.sh. With core capabilities on,
// this is a schema superset, not proof of a specific installed access policy.
const coreTools = createCoreTools({agentId: 'pixel', sessionKey: 'agent:pixel:grammar-test',
  workspaceDir: process.env.ODS_TEST_GRAMMAR_WORKSPACE ?? process.cwd(),
  ...route, config, includeToolSearchControls: true,
  toolConstructionPlan: {includeBaseCodingTools: true, includeShellTools: true,
    includeChannelTools: false, includeOpenClawTools: true, includePluginTools: false},
});
const allTools = [...coreTools, ...pluginTools];
const {tools: offered} = applyCatalog({tools: allTools, config, agentId: 'pixel',
  runId: randomUUID(), catalogRef: createCatalogRef()});
const names = tools => tools.map(tool => tool.name).sort();
const directNames = ['apply_patch', 'edit', 'exec', 'pixel_ods_ask_user', 'pixel_ods_extension_request_status',
  'pixel_ods_extensions', 'pixel_ods_skill', 'pixel_ods_web_extract', 'pixel_ods_workspace_preview', 'process', 'read', 'tool_call', 'tool_describe',
  'tool_search', 'web_fetch', 'web_search', 'write'];
const historicalNames = [...directNames.filter(name => !['pixel_ods_workspace_preview', 'pixel_ods_web_extract'].includes(name)), 'pixel_ods_extension_request_advance',
  'pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_retry',
  'pixel_ods_python_library_proposal', 'pixel_ods_source_proposal'];
const select = selected => selected.map(name => {
  const matches = allTools.filter(tool => tool.name === name);
  assert.equal(matches.length, 1, `exactly one actual schema for ${name}`);
  return matches[0];
});
const template = readFileSync(templateFile, 'utf8');
function run(input) {
  const result = spawnSync(bridge, [], {input: JSON.stringify({...input, compileOnly: true}),
    encoding: 'utf8', timeout: 30000, maxBuffer: 4 * 1024 * 1024});
  assert.ifError(result.error);
  return result;
}
function compile(tools) {
  assert.equal(new Set(names(tools)).size, tools.length, 'compile each offered tool exactly once');
  for (const input of [
    {schema: combinedToolSchema(tools)},
    {chatTemplate: template, tools: tools.map(({name, description, parameters}) => ({name, description, parameters}))},
  ]) {
    const result = run(input);
    assert.equal(result.status, 0, `${names(tools).join(', ')}\n${result.stderr.slice(0, 2000)}`);
    assert.ok(JSON.parse(result.stdout).grammarBytes > 0);
  }
}

test('actual catalog offers the reviewed general-purpose surface', () => {
  assert.deepEqual(names(offered), directNames);
});
test('actual directly offered tools compile together as JSON and Qwen3.5 template grammar', () => compile(offered));
test('workspace preview is present exactly once in the compiled ordinary surface', () => {
  assert.equal(offered.length, 17);
  assert.equal(offered.filter(tool => tool.name === 'pixel_ods_workspace_preview').length, 1);
});

test('targeted extraction is present exactly once in the compiled ordinary surface', () => {
  assert.equal(offered.filter(tool => tool.name === 'pixel_ods_web_extract').length, 1);
});

for (const specialist of pluginTools.filter(tool => !directNames.includes(tool.name))) {
  test(`discovered ${specialist.name} compiles alongside the ordinary surface`, () => compile([...offered, specialist]));
}
test('the former 20-tool surface also compiles with current specialist schemas', () => compile(select(historicalNames)));
test('native gate reproduces the observed predecessor 2048-bound grammar failure', () => {
  // This schema delta occurred in historical Mac and CPU-VM grammar logs; those
  // appended logs alone do not establish the schemas of a current live process.
  // It is a negative control, never the tool schema under test.
  const predecessor = select(historicalNames).map(({name, description, parameters}) =>
    ({name, description, parameters: structuredClone(parameters)}));
  const verification = predecessor.find(tool => tool.name === 'pixel_ods_python_library_proposal')
    .parameters.properties.pythonVerification.properties;
  assert.equal(verification.expression.maxLength, undefined);
  assert.equal(verification.expected.maxLength, undefined);
  verification.expression.maxLength = 2048;
  verification.expected.maxLength = 2048;
  const result = run({chatTemplate: template, tools: predecessor});
  assert.equal(result.status, 1, result.stderr.slice(0, 2000));
  assert.match(result.stderr, /number of repetitions exceeds sane defaults/);
});
