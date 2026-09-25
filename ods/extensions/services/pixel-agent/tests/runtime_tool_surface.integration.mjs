// Exercise the actual reviewed package module and its catalog implementation.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { createPublicWebExtractTool } from '../plugin/web-extract.mjs';
import { createSearchReadTool, SEARCH_READ_PARAMETERS } from '../plugin/search-read.mjs';
import { createWorkspacePreviewInspectTool, INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash } from '../plugin/workspace-preview-inspect.mjs';

const file = process.env.OPENCLAW_TOOL_SEARCH_MODULE;
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-image-envelope.json', import.meta.url)));
assert.equal(createHash('sha256').update(readFileSync(file)).digest('hex'), manifest.patchedSha256);
const { u: apply, m: createRef, h: createControls, v: resolveExact } = await import(pathToFileURL(file));
const { t: filterByPolicy } = await import(pathToFileURL(join(dirname(file), 'agent-tools.policy-Dyn4VuLn.js')));
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
    'web_fetch', 'web_search', 'pixel_ods_web_extract', 'pixel_ods_search_read', 'pixel_ods_skill', 'pixel_ods_ask_user',
    'pixel_ods_extensions', 'pixel_ods_extension_request_status', 'pixel_ods_workspace_preview', 'pixel_ods_workspace_preview_inspect'];
  const specialistNames = ['pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_advance',
    'pixel_ods_extension_request_retry', 'pixel_ods_source_proposal',
    'pixel_ods_python_library_proposal', 'pixel_ods_extension_proposal'];
  const result = run([...nativeNames, ...specialistNames].map(tool));
  assert.deepEqual(result.tools.map(t => t.name), [...controls.map(t => t.name), ...nativeNames]);
  assert.equal(result.tools.length, 19); // Sixteen native tools plus the three search controls.
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

test('targeted public extraction is directly offered with its exact bounded schema', async () => {
  let calls = 0;
  const extractor = createPublicWebExtractTool({
    guardedFetch: async ({url}) => {
      calls++;
      return {response: new Response('Navigation\n'.repeat(1000) + '\nBoard power 250 W\n',
        {headers: {'Content-Type':'text/plain'}}), finalUrl:url, release() {}};
    },
    readResponseText: async response => ({text:await response.text(), truncated:false}),
    extractBasicHtmlContent: async () => {throw new Error('plain-text fixture');},
  });
  const direct = run([extractor]).tools.find(t => t.name === extractor.name);
  assert.equal(direct, extractor, 'retain the policy-filtered implementation object');
  assert.deepEqual(direct.parameters, {type:'object', additionalProperties:false, required:['url'], properties:{
    url:{type:'string', minLength:10, maxLength:1024}, query:{type:'string', minLength:2, maxLength:200},
  }});
  const result = await direct.execute('extract', {url:'https://docs.example.org/specs', query:'Board power'});
  assert.equal(calls, 1);
  assert.equal(result.details.matched, true);
  assert.equal(result.details.evidence_truncated_before, true);
  assert.match(result.content[0].text, /Board power 250 W/);
  assert.match(result.content[0].text, /EXTERNAL_UNTRUSTED_CONTENT/);
  assert.equal((await direct.execute('private', {url:'http://127.0.0.1/specs'})).isError, true);
  assert.equal(calls, 1, 'native exposure cannot bypass public URL validation');
});

test('search-and-read is directly offered with its static schema and public-only reads', async () => {
  const reads = [];
  const searchRead = createSearchReadTool({
    search: async () => ({provider: 'fixture', result: {results: [{url: 'https://docs.example.org/specs', title: 'Specs'}]}}),
    readPage: async (url) => {
      reads.push(url);
      return {ok: true, status: 200, finalUrl: url, contentType: 'text/plain',
        text: ['Navigation', 'Board power 250 W', ''].join('\n'), truncated: false};
    },
  });
  const direct = run([searchRead]).tools.find(t => t.name === searchRead.name);
  assert.equal(direct, searchRead, 'retain the policy-filtered implementation object');
  assert.equal(direct.parameters, SEARCH_READ_PARAMETERS);
  const result = await direct.execute('search-read', {query: 'board power'});
  assert.deepEqual(reads, ['https://docs.example.org/specs']);
  assert.equal(result.details.receipts, 1);
  assert.match(result.content[0].text, /Board power 250 W/);
  assert.equal((await direct.execute('private', {urls: ['http://127.0.0.1/specs']})).isError, true);
  assert.deepEqual(reads, ['https://docs.example.org/specs'], 'native exposure cannot bypass public URL validation');
  const filtered = filterByPolicy([searchRead, tool('web_fetch')], {deny: ['pixel_ods_search_read']});
  assert.deepEqual(run(filtered).tools.map(t => t.name), [...controls.map(t => t.name), 'web_fetch']);
});

test('native extraction preserves actual runtime policy denials and ambiguity deferral', () => {
  const extractor = tool('pixel_ods_web_extract');
  const filtered = filterByPolicy([extractor, tool('web_fetch')], {deny:['pixel_ods_web_extract']});
  assert.deepEqual(filtered.map(t => t.name), ['web_fetch']);
  const denied = run(filtered);
  assert.deepEqual(denied.tools.map(t => t.name), [...controls.map(t => t.name), 'web_fetch']);
  assert.equal(resolveExact({agentId:'pixel', catalogRef:denied.catalogRef}, extractor.name), undefined);
  const ambiguous = run([extractor, tool(extractor.name)]);
  assert.deepEqual(ambiguous.tools, controls);
  assert.equal(ambiguous.catalogToolCount, 2);
  assert.deepEqual(run([extractor], {agentId:'another-agent'}).tools, controls);
});

test('native preview inspection retains exact snapshot validation and bounded broker execution', async () => {
  let calls = 0;
  const inspector = createWorkspacePreviewInspectTool({request: async request => {
    calls++;
    return {schemaVersion:1, kind:INSPECTION_KIND, status:'failed', errorCode:'unavailable',
      siteId:request.siteId, sha256:request.sha256, planSha256:inspectionPlanHash(request), scope:INSPECTION_SCOPE};
  }});
  const direct = run([inspector]).tools.find(tool => tool.name === inspector.name);
  assert.equal(direct, inspector);
  const args = {siteId:'site-' + 'a'.repeat(24), sha256:'a'.repeat(64), viewport:{width:800,height:600},
    steps:[{action:'assert-hidden',locator:{selector:'#details'}},
      {action:'click',locator:{role:'button',name:'Show details',exact:true}},
      {action:'assert-visible',locator:{selector:'#details'}}]};
  assert.equal((await direct.execute('wrong-snapshot',{...args,sha256:'b'.repeat(64)})).details.errorCode,'invalid_request');
  assert.equal(calls,0);
  const result = await direct.execute('inspect',args);
  assert.equal(calls,1);
  assert.equal(result.isError,true);
  assert.equal(result.details.errorCode,'unavailable');
  assert.match(result.content[0].text,/not pixel paint/);
});

test('native inspection cannot expose absent, denied or ambiguous capabilities', () => {
  const inspector = tool('pixel_ods_workspace_preview_inspect');
  assert.deepEqual(run([]).tools,controls);
  const filtered = filterByPolicy([inspector,tool('read')],{deny:[inspector.name]});
  assert.deepEqual(filtered.map(t=>t.name),['read']);
  const denied = run(filtered);
  assert.equal(resolveExact({agentId:'pixel',catalogRef:denied.catalogRef},inspector.name),undefined);
  assert.ok(!denied.tools.some(t=>t.name===inspector.name));
  assert.deepEqual(run([inspector,tool(inspector.name)]).tools,controls);
  assert.deepEqual(run([inspector],{agentId:'another-agent'}).tools,controls);
});

// pixel_ods_research is offered only while the owner's Perplexica is
// configured. It is deferred, so toggling it changes only the server-side
// catalog: the visible tools, and so the prompt, stay byte-identical.
test('offering or hiding the optional Perplexica tool leaves the visible surface unchanged', () => {
  const names = ['read', 'web_fetch', 'web_search', 'pixel_ods_web_extract'];
  const without = run(names.map(tool));
  const offered = run([...names, 'pixel_ods_research'].map(tool));
  assert.equal(JSON.stringify(offered.tools), JSON.stringify(without.tools));
  assert.equal(offered.catalogToolCount, without.catalogToolCount + 1);
  assert.equal(resolveExact({agentId:'pixel',catalogRef:offered.catalogRef},'pixel_ods_research')?.name,'pixel_ods_research');
  assert.equal(resolveExact({agentId:'pixel',catalogRef:without.catalogRef},'pixel_ods_research'),undefined);
});

// Tool Search is the research tool's only path. Its tool_call result is one
// text block holding the catalog entry and the whole result, escaped again.
// OpenClaw keeps a block of at most the tool-result cap unchanged and cuts
// the middle of a longer one, which drops sources and the closing marker.
test('a Perplexica result fits the tool-result cap as the real tool_call returns it', async () => {
  const { createPerplexicaResearchTool, researchOutputChars } = await import('../plugin/perplexica-research.mjs');
  const { o: setPluginToolMeta } = await import(pathToFileURL(join(dirname(file), 'tools-D5HS8Q_I.js')));
  const configured = {values: {preferences: {defaultChatProvider: 'c', defaultChatModel: 'm',
    defaultEmbeddingProvider: 'e', defaultEmbeddingModel: 'x'}}};
  const sources = Array.from({length: 25}, (_, i) => ({
    content: 'The festival runs from "October 3" to October 12 at venues across Center City.\n'.repeat(6),
    metadata: {title: `Event listing ${i + 1} "official" ${'T'.repeat(120)}`,
      url: `https://www.visitphilly.com/events/2026/event-slug-${i + 1}/?utm_source=search&x="q"`}}));
  const answer = Array.from({length: 40}, (_, i) =>
    `- **Event ${i}**: A "description" [${(i % 25) + 1}]. See https://invented-${i}.example.org/events/${i}/details.`).join('\n');
  for (const cap of [4000, 8000, 12000]) {
    let requests = 0;
    const research = createPerplexicaResearchTool({env: {},
      outputChars: () => researchOutputChars({agents: {list: [{id: 'pixel', contextLimits: {toolResultMaxChars: cap}}]}}, 'pixel'),
      fetch: async () => ++requests === 1 ? Response.json(configured) : new Response([{type: 'sources', data: sources},
        {type: 'response', data: answer}, {type: 'done'}].map(event => JSON.stringify(event)).join('\n'))});
    // As in production: the plugin tool OpenClaw builds from its cached
    // descriptor, labelled with its name and owned by the pixel-ods plugin.
    const catalogued = {...research, label: research.name};
    setPluginToolMeta(catalogued, {pluginId: 'pixel-ods'});
    const { catalogRef } = run([catalogued]);
    const ctx = { agentId: 'pixel', catalogRef, config: { tools: { toolSearch: { enabled: true, mode: 'tools' } } } };
    const call = createControls(ctx).find(t => t.name === 'tool_call');
    const delivered = await call.execute('research', { id: 'pixel_ods_research', args: { query: 'Philadelphia public events' } });
    assert.equal(requests, 2);
    assert.equal(delivered.content.length, 1);
    const text = delivered.content[0].text;
    assert.ok(text.length <= cap, `${cap}: ${text.length} characters`);
    const payload = JSON.parse(text);
    assert.equal(payload.tool.id, 'openclaw:pixel-ods:pixel_ods_research');
    assert.equal(payload.tool.label, 'pixel_ods_research');
    assert.equal(payload.tool.description, research.description);
    assert.equal(Object.hasOwn(payload.result.details, 'unsourcedLinkHosts'), false);
    const inner = payload.result.content[0].text;
    assert.match(inner, /\n<\/perplexica_evidence_[0-9a-f]{24}>$/);
    const evidence = JSON.parse(inner.slice(inner.indexOf('>\n') + 2, inner.lastIndexOf('\n</perplexica_evidence_')));
    assert.ok(JSON.stringify(evidence.answer).length >= 900, `${cap}: answer ${evidence.answer.length}`);
  }
});
