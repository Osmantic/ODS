// Uses the pinned provider serializer, not a simulated provider request.
// This proves byte transport only; visual understanding requires a real model.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {isAbsolute, join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createChatImageReadTool} from '../plugin/chat-image-read.mjs';

const root = process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root && isAbsolute(root), 'OPENCLAW_PACKAGE_DIR must identify the pinned runtime');
assert.equal(JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version, '2026.6.33');
const {t: convertMessages} = await import(pathToFileURL(join(root, 'dist/openai-completions-DTj6G8AI.js')).href);
const envelopeManifest = JSON.parse(readFileSync(new URL('../host/openclaw-image-envelope.json', import.meta.url), 'utf8'));
const searchSource = readFileSync(join(root, 'dist/tool-search-BInRpkE3.js'), 'utf8');
assert.equal(createHash('sha256').update(searchSource).digest('hex'), envelopeManifest.patchedSha256,
  'Tool Search must contain the reviewed image-preserving adapter');
const envelopeStart = 'function toolCallResultEnvelope(payload) {';
const envelopeEnd = 'async function runCodeMode(params) {';
assert.equal(searchSource.split(envelopeStart).length, 2);
assert.equal(searchSource.split(envelopeEnd).length, 2);
const wrapToolResult = new Function('isRecord', 'jsonResult',
  searchSource.slice(searchSource.indexOf(envelopeStart), searchSource.indexOf(envelopeEnd)) + '\nreturn toolCallResultEnvelope;')(
  value => value !== null && typeof value === 'object' && !Array.isArray(value),
  value => ({content:[{type:'text',text:JSON.stringify(value)}],details:value}),
);
const data = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR9kAAAAASUVORK5CYII=', 'base64');
const ref = {id: `img-${'1'.repeat(32)}`, sha256: createHash('sha256').update(data).digest('hex')};
const user = `ods-${'a'.repeat(64)}`;
const model = {id:'ods/current', provider:'ods-gateway', api:'openai-completions', input:['text','image'], reasoning:false};

test('reread image reaches the real provider serializer unchanged after text-only history', async () => {
  const tool = createChatImageReadTool({agentId:'pixel', sessionKey:`agent:pixel:openai-user:${user}`, sessionId:'owned-session'}, {
    getSessionEntry: async () => ({sessionId:'owned-session'}),
    readConfig: () => ({session:{store:'/isolated-test/session-store'}}),
    resolveStorePath: value => value,
    imagePolicyForContext: async () => ({imageInput:'supported',routeFingerprint:'d'.repeat(64),unknownConsent:false}),
    readImage: async () => ({schemaVersion:1,image:{...ref,mimeType:'image/png',bytes:data.length,data:data.toString('base64')}}),
  });
  const result = await tool.execute('image-reread', ref);
  assert.equal(result.isError, undefined);
  const wrapped = wrapToolResult({tool:{id:'pixel-image-read',source:'openclaw',sourceName:'pixel',name:'pixel_ods_image_read'},result});
  const messages = [
    {role:'user',content:'Re-examine the earlier attachment.',timestamp:1},
    {role:'assistant',content:[{type:'toolCall',id:'image-reread',name:'pixel_ods_image_read',arguments:ref}],api:model.api,provider:model.provider,model:model.id,stopReason:'toolUse',timestamp:2},
    {role:'toolResult',toolCallId:'image-reread',toolName:'pixel_ods_image_read',content:wrapped.content,isError:false,timestamp:3},
  ];
  const wire = convertMessages(model, {messages}, {});
  const images = wire.flatMap(message => Array.isArray(message.content) ? message.content : []).filter(block => block.type === 'image_url');
  assert.equal(images.length, 1);
  assert.equal(images[0].image_url.url, `data:image/png;base64,${data.toString('base64')}`);
  const text = wire.flatMap(message => Array.isArray(message.content) ? message.content.filter(block => block.type === 'text').map(block => block.text) : [message.content]).join('\n');
  assert.ok(!text.includes(data.toString('base64')), 'image bytes must not become prompt text');
});
