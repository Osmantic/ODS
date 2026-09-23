// Exercise the actual reviewed package module and its catalog implementation.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const file = process.env.OPENCLAW_TOOL_SEARCH_MODULE;
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-image-envelope.json', import.meta.url)));
assert.equal(createHash('sha256').update(readFileSync(file)).digest('hex'), manifest.patchedSha256);
const { u: apply, m: createRef, h: createControls, v: resolveExact } = await import(pathToFileURL(file));
const tool = name => ({ name, label: name, description: name, parameters: { type: 'object', properties: {} }, execute: async () => ({ content: [] }) });
const controls = ['tool_search', 'tool_describe', 'tool_call'].map(tool);
function run(tools, extra = {}) {
  const catalogRef = createRef();
  const result = apply({ tools: [...controls, ...tools], agentId: 'pixel',
    runId: randomUUID(), catalogRef,
    config: { tools: { toolSearch: { enabled: true, mode: 'tools' } } }, ...extra });
  return { ...result, catalogRef };
}

test('working tools are direct while specialist tools remain in the real catalog', () => {
  const read = tool('read'), status = tool('pixel_ods_extension_request_status');
  const advance = tool('pixel_ods_extension_request_advance');
  const prepare = tool('pixel_ods_extension_request_prepare');
  const retry = tool('pixel_ods_extension_request_retry');
  const proposal = tool('pixel_ods_source_proposal');
  const advanced = tool('pixel_ods_extension_proposal');
  const library = tool('pixel_ods_python_library_proposal');
  const preview = tool('pixel_ods_workspace_preview');
  const result = run([read, status, prepare, advance, retry, proposal, library, preview, advanced]);
  assert.deepEqual(result.tools, [...controls, read, status, preview]);
  assert.equal(result.catalogToolCount, 9);
  assert.equal(result.catalogRegistered, true);
  assert.ok(result.catalogRef.current);
});

test('the ordinary direct surface stays small while every specialist stays catalogued', () => {
  const nativeNames = ['read', 'write', 'edit', 'apply_patch', 'exec', 'process',
    'web_fetch', 'web_search', 'pixel_ods_skill', 'pixel_ods_ask_user',
    'pixel_ods_extensions', 'pixel_ods_extension_request_status', 'pixel_ods_workspace_preview'];
  const specialistNames = ['pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_advance',
    'pixel_ods_extension_request_retry', 'pixel_ods_source_proposal',
    'pixel_ods_python_library_proposal', 'pixel_ods_extension_proposal'];
  const result = run([...nativeNames, ...specialistNames].map(tool));
  assert.deepEqual(result.tools.map(t => t.name), [...controls.map(t => t.name), ...nativeNames]);
  assert.equal(result.tools.length, 16); // Thirteen native tools plus the three search controls.
  assert.equal(result.catalogToolCount, nativeNames.length + specialistNames.length);
});

test('policy-denied tools are not synthesized and duplicate names stay deferred', () => {
  const result = run([tool('read'), tool('read'), tool('web_fetch')]);
  assert.deepEqual(result.tools.map(t => t.name), [...controls.map(t => t.name), 'web_fetch']);
  assert.equal(result.catalogToolCount, 3);
  assert.ok(!result.tools.some(t => t.name === 'exec'));
});

test('deferred specialists remain searchable, describable and callable through the normal dispatcher', async () => {
  const names = ['pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_advance',
    'pixel_ods_extension_request_retry', 'pixel_ods_source_proposal',
    'pixel_ods_python_library_proposal', 'pixel_ods_extension_proposal', 'pixel_ods_workspace_preview'];
  const specialists = names.map(tool);
  const { catalogRef } = run(specialists);
  const dispatched = [];
  const ctx = { agentId: 'pixel', catalogRef,
    config: { tools: { toolSearch: { enabled: true, mode: 'tools' } } },
    executeTool: async params => {
      dispatched.push(params);
      return { content: [{ type: 'text', text: 'dispatcher-receipt' }] };
    },
  };
  const controlsByName = Object.fromEntries(createControls(ctx).map(t => [t.name, t]));
  for (const specialist of specialists) {
    assert.equal(resolveExact(ctx, specialist.name), specialist);
    const search = await controlsByName.tool_search.execute('search', { query: specialist.name, limit: 100 });
    assert.ok(search.details.some(entry => entry.name === specialist.name));
    const described = await controlsByName.tool_describe.execute('describe', { id: specialist.name });
    assert.equal(described.details.name, specialist.name);
    assert.deepEqual(described.details.parameters, specialist.parameters);
    await controlsByName.tool_call.execute('invoke', { id: specialist.name, args: { marker: 'test-only' } });
    assert.equal(dispatched.at(-1).tool, specialist);
    assert.deepEqual(dispatched.at(-1).input, { marker: 'test-only' });
  }
  assert.equal(dispatched.length, specialists.length);
  await assert.rejects(controlsByName.tool_call.execute('denied', { id: 'policy_denied_tool', args: {} }));
  assert.equal(dispatched.length, specialists.length);
});

test('other agents retain the original catalog behavior', () => {
  const read = tool('read');
  assert.deepEqual(run([read], { agentId: 'another-agent' }).tools, controls);
  assert.deepEqual(run([read], { agentId: 'another-agent', isVisibleCatalogTool: () => true }).tools, [...controls, read]);
});

test('disabled search preserves the input tools', () => {
  const read = tool('read');
  const result = run([read], { config: { tools: { toolSearch: { enabled: false, mode: 'tools' } } } });
  assert.equal(result.compacted, false);
  assert.deepEqual(result.tools, [...controls, read]);
});

test('tool visibility is independent of owner language and request content', () => {
  const tools = [tool('read'), tool('pixel_ods_skill'), tool('specialist')];
  for (const prompt of ['instale a extensÃ£o', 'do not install; research only', 'sim', 'éŸ³æ¥½']) {
    assert.deepEqual(run(tools, { prompt }).tools, [...controls, ...tools.slice(0, 2)]);
  }
});


test('preview is directly callable through the exact policy-filtered object', async () => {
  const {createWorkspacePreviewTool} = await import('../plugin/workspace-preview.mjs');
  let calls=0;
  const preview=createWorkspacePreviewTool({request:async () => {calls++; return {status:'failed'};}});
  const result=run([preview]);
  const direct=result.tools.find(t => t.name === preview.name);
  assert.equal(direct,preview);
  assert.deepEqual(direct.parameters.required,['relativeDirectory']);
  assert.equal(direct.parameters.additionalProperties,false);
  // Direct exposure changes no execution/receipt policy: an unverified response
  // is still a failure, never a fabricated published URL.
  const response=await direct.execute('preview',{relativeDirectory:'Playground/example'});
  assert.equal(calls,1); assert.equal(response.isError,true);
  assert.equal(result.catalogToolCount,1);
});

test('denied and ambiguous preview tools remain unavailable directly', () => {
  assert.ok(!run([]).tools.some(t => t.name === 'pixel_ods_workspace_preview'));
  const result=run([tool('pixel_ods_workspace_preview'),tool('pixel_ods_workspace_preview')]);
  assert.deepEqual(result.tools,controls); assert.equal(result.catalogToolCount,2);
  assert.deepEqual(run([tool('pixel_ods_workspace_preview')],{agentId:'another-agent'}).tools,controls);
});
