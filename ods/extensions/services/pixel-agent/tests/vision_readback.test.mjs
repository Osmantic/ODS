// What the plugin does once ODS has read llama-server's vision report back
// (bin/ods-host-agent.py _pixel_image_input_readback). The host agent sends
// the switch's target to the native coordinator, which renders Pixel's config
// with pixel_model_contract.plan; this runs that renderer from the install
// default through a vision model to a text-only one, then checks the preview
// screenshot gate and the owner-image route check against each result.
import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {previewScreenshotCapability} from '../plugin/preview-screenshot.mjs';
import {createContextCompaction} from '../plugin/context-compaction.mjs';
import {validateImageRoute} from '../host/chat_image_transport.mjs';

const ACTIVE = Object.freeze({provider: 'ods-gateway', modelId: 'ods/current', modelRef: 'ods-gateway/ods/current'});
const DIRECTIVE = Object.freeze({maxSide: 1200, maxPixels: 1024 * 1024, maxBytes: 128 * 1024});
const user = `ods-${'a'.repeat(64)}`;

// The Portal route an install writes: the gateway alias, image input unknown,
// whose row lists image so an owner-consented test can reach the model.
const installed = () => ({
  agents: {defaults: {compaction: {reserveTokens: 20000, reserveTokensFloor: 0, keepRecentTokens: 4096}},
    list: [{id: 'pixel', model: {primary: 'ods-gateway/ods/current'}, contextTokens: 65536, params: {maxTokens: 8192}}]},
  models: {providers: {'ods-gateway': {baseUrl: 'http://127.0.0.1:4000/v1', models: [{id: 'ods/current',
    name: 'ODS Current (old-model.gguf)', input: ['text', 'image'], contextWindow: 65536, maxTokens: 8192, reasoning: false}]}}},
  plugins: {entries: {'pixel-ods': {enabled: true, config: {modelContextWindow: 65536, modelImageInput: 'unknown'}}}},
});
// pixel_target from a Models-page switch (local route: no routeFingerprint).
const target = (model, imageInput) => ({model, contextLength: 65536, maxTokens: 8192, reasoning: false, imageInput});

// The coordinator's own renderer, applied in order; one config per target.
function render(config, targets) {
  const script = 'import json,sys\nsys.path.insert(0,sys.argv[1])\nfrom pixel_model_contract import plan\n' +
    'value=json.load(sys.stdin)\nconfig=value["config"]\nout=[]\n' +
    'for proposed in value["targets"]:\n config=plan(config,proposed)\n out.append(config)\nprint(json.dumps(out))\n';
  const result = spawnSync(process.platform === 'win32' ? 'python' : 'python3', ['-c', script,
    fileURLToPath(new URL('../../../../bin', import.meta.url))], {input: JSON.stringify({config, targets}), encoding: 'utf8', windowsHide: true});
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

// The model Portal's owner-image path checks (the plugin's context route).
function portalModel(t, config) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'ods-vision-readback-'));
  t.after(() => fs.rmSync(directory, {recursive: true, force: true}));
  const entry = {sessionId: 'session-one', modelProvider: 'ods-gateway', model: 'ods/current', contextTokens: 65536,
    totalTokens: 1000, totalTokensFresh: true, updatedAt: Date.now(), compactionCount: 0};
  const admission = {status: () => ({available: true, phase: 'idle', revision: '0', active: 0}),
    acquire() {}, release() {}, owns: () => false};
  return createContextCompaction({directory: path.join(directory, 'ledger'), requireModelObservation: false,
    readSession: () => entry, readConfig: () => config, callGateway: async () => ({}), admission}).context(user).model;
}
const route = (model, unknownConsent = false) => ({routeFingerprint: model.imageRouteFingerprint, unknownConsent});

test('the readback opens the screenshot gate for the vision model only, with the same bounds', () => {
  const [vision, text] = render(installed(), [target('vision.gguf', 'supported'), target('text-model.gguf', 'unknown')]);
  assert.deepEqual(previewScreenshotCapability(installed(), ACTIVE), {supported: false, reason: 'unknown'});
  assert.deepEqual(previewScreenshotCapability(vision, ACTIVE), {supported: true, directive: DIRECTIVE});
  assert.equal(vision.plugins.entries['pixel-ods'].config.modelImageInput, 'supported');
  assert.deepEqual(vision.models.providers['ods-gateway'].models[0].input, ['text', 'image']);
  // A run that does not name the configured model still gets none.
  assert.deepEqual(previewScreenshotCapability(vision, {...ACTIVE, modelRef: 'ods-gateway/other'}), {supported: false, reason: 'other-model'});
  // The next switch, to a model without a projector, closes it again.
  assert.deepEqual(previewScreenshotCapability(text, ACTIVE), {supported: false, reason: 'unknown'});
  assert.equal(text.plugins.entries['pixel-ods'].config.modelImageInput, 'unknown');
});

test('a catalog text-only model after the readback is text-only again', () => {
  const [, text] = render(installed(), [target('vision.gguf', 'supported'), target('text-model.gguf', 'unsupported')]);
  assert.deepEqual(previewScreenshotCapability(text, ACTIVE), {supported: false, reason: 'unsupported'});
  assert.deepEqual(text.models.providers['ods-gateway'].models[0].input, ['text']);
});

test('owner images need no image-test consent after the readback, and need it again after the switch back', t => {
  const [vision, text] = render(installed(), [target('vision.gguf', 'supported'), target('text-model.gguf', 'unknown')]);
  const before = portalModel(t, installed()), seeing = portalModel(t, vision), blind = portalModel(t, text);
  assert.equal(before.imageInput, 'unknown');
  assert.throws(() => validateImageRoute(route(before), before), /image-input-unverified/);

  assert.equal(seeing.imageInput, 'supported');
  assert.deepEqual(validateImageRoute(route(seeing), seeing),
    {imageInput: 'supported', routeFingerprint: seeing.imageRouteFingerprint, unknownConsent: false});

  // Ingress checks every turn whose history holds images against the model
  // serving it now: the vision route's binding no longer matches, and the
  // text model's own route needs the owner's explicit test again.
  assert.equal(blind.imageInput, 'unknown');
  assert.notEqual(blind.imageRouteFingerprint, seeing.imageRouteFingerprint);
  assert.throws(() => validateImageRoute(route(seeing), blind), /image-route-changed/);
  assert.throws(() => validateImageRoute(route(blind), blind), /image-input-unverified/);
  assert.equal(validateImageRoute(route(blind, true), blind).unknownConsent, true);
});
