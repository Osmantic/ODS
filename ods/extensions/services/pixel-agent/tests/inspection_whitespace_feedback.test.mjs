import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspacePreviewInspectTool, inspectionPlanHash, INSPECTION_KIND, INSPECTION_SCOPE,
  normalizeWorkspacePreviewInspectionParams as normalize} from '../plugin/workspace-preview-inspect.mjs';

async function replay(names, {hidden = false} = {}) {
  const digest = 'a'.repeat(64);
  const params = {siteId: `site-${digest.slice(0, 24)}`, sha256: digest,
    viewport: {width: 800, height: 600}, steps: [{action: hidden ? 'assert-hidden' : 'assert-visible',
      locator: hidden ? {selector: '.answer p'} : {role: 'button', name: 'Question?▾'}}]};
  const receipt = {schemaVersion: 1, kind: INSPECTION_KIND, status: 'failed', siteId: params.siteId,
    sha256: digest, planSha256: inspectionPlanHash(normalize(params)), viewport: params.viewport,
    scope: INSPECTION_SCOPE, diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0},
    blockedRequests: [], controls: {count: names.length,
      items: names.map(name => ({name, role: 'button', source: 'content', visible: true}))},
    steps: [{index: 0, ...normalize(params).steps[0], before: hidden
      ? {count: 1, visible: true, display: 'block', visibility: 'visible', opacity: '1', hidden: false,
        hiddenUntilFound: false, rectCount: 1} : {count: 0}, stable: true, status: 'failed',
      errorCode: hidden ? 'visibility_mismatch' : 'no_match'}]};
  const original = structuredClone(receipt), plan = structuredClone(params);
  let forwarded;
  const result = await createWorkspacePreviewInspectTool({request: async value => {
    forwarded = structuredClone(value); return receipt;
  }}).execute('spacing', params);
  assert.equal(result.isError, true);
  assert.equal(result.details.status, 'failed');
  assert.deepEqual(receipt, original);
  assert.deepEqual(result.details, original);
  assert.deepEqual(params, plan);
  assert.deepEqual(forwarded, normalize(plan));
  return result.content[0].text.split(' Evidence: ')[0];
}

test('a unique whitespace-only name mismatch quotes the observed exact name without changing the locator', async () => {
  const text = await replay(['Question? ▾']);
  assert.match(text, /named "Question\? ▾"/);
  assert.match(text, /differs only in whitespace/);
  assert.match(text, /Requested behavior remains unverified/);
});

test('ambiguous whitespace matches do not choose a control', async () => {
  const text = await replay(['Question? ▾', 'Question ?▾']);
  assert.doesNotMatch(text, /differs only in whitespace/);
  assert.match(text, /matched no element/);
});

test('unrelated observed names do not become whitespace suggestions', async () => {
  const text = await replay(['Submit appointment']);
  assert.doesNotMatch(text, /differs only in whitespace|Submit appointment/);
  assert.match(text, /matched no element/);
});

test('a failed hidden-state assertion explains the clipping limit without treating it as a pass', async () => {
  const text = await replay([], {hidden: true});
  assert.match(text, /ancestor clips its overflow/);
  assert.match(text, /collapsed container itself/);
  assert.match(text, /Requested behavior remains unverified/);
});
