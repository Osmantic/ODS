import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspacePreviewInspectTool, inspectionPlanHash, INSPECTION_KIND, INSPECTION_SCOPE,
  normalizeWorkspacePreviewInspectionParams as normalize}
  from '../plugin/workspace-preview-inspect.mjs';

// Workstation06 (2026-10-27): a generated memory game styled its win overlay with
// display: flex, overriding the overlay's hidden attribute, so the overlay covered
// the board from load. Every card click failed as click_failed with no reason,
// and the agent told the owner the inspector could not click while the game worked.
const digest = 'b'.repeat(64);
const request = () => ({siteId: `site-${digest.slice(0,24)}`, sha256:digest, viewport:{width:800,height:600},
  steps:[{action:'click',locator:{selector:'.card:nth-child(1)'}},
    {action:'assert-text',locator:{selector:'#moves'},expectedText:'1'}]});
const state = text => ({count:1,visible:true,display:'block',visibility:'visible',opacity:'1',hidden:false,
  hiddenUntilFound:false,rectCount:1,...(text === undefined ? {} : {text:{actual:text,truncated:false}})});
function receipt(params, {clickFails = false, renderedHidden = 0} = {}) {
  const steps = clickFails
    ? [{index:0,...structuredClone(params.steps[0]),before:state(),stable:true,status:'failed',errorCode:'click_failed'}]
    : [{index:0,...structuredClone(params.steps[0]),before:state(),after:state(),stable:true,status:'passed'},
      {index:1,...structuredClone(params.steps[1]),before:state('1'),stable:true,status:'passed'}];
  return {schemaVersion:1,kind:INSPECTION_KIND,status:clickFails ? 'failed' : 'passed',
    siteId:params.siteId,sha256:params.sha256,planSha256:inspectionPlanHash(normalize(params)),viewport:params.viewport,
    scope:INSPECTION_SCOPE,diagnostics:{renderedHiddenAttributeCount:renderedHidden,hiddenUntilFoundCount:0},
    blockedRequests:[],steps};
}
const summary = result => result.content[0].text.split(' Evidence: ')[0];
const inspect = (options, id = 'click') => {
  const params = request();
  return createWorkspacePreviewInspectTool({request:async () => receipt(params, options)}).execute(id, params);
};

test('a failed click is a page problem to find, not an inspector limit', async () => {
  const result = await inspect({clickFails:true});
  assert.equal(result.isError, true);
  const text = summary(result);
  assert.match(text, /Step 1 \(click\) found its target but could not click it/);
  assert.match(text, /covers that point.*person's click as well/);
  assert.match(text, /not an inspector limit/);
  assert.match(text, /disabled, or animates without pause/);
  assert.match(text, /fix the page, republish, and inspect the new snapshot/);
  assert.match(text, /Requested behavior remains unverified/);
  assert.doesNotMatch(text, /hidden attribute/);
});

test('a rendered hidden overlay is named beside the failed click', async () => {
  const text = summary(await inspect({clickFails:true, renderedHidden:1}));
  assert.match(text, /could not click it/);
  assert.match(text, /one element carries the hidden attribute but is rendered anyway/);
  assert.match(text, /display: flex overrides hidden/);
  assert.match(text, /\[hidden\] \{ display: none !important; \}/);
});

test('a passed inspection still notes hidden elements that are rendered', async () => {
  const noted = summary(await inspect({renderedHidden:2}, 'passed-noted'));
  assert.match(noted, /^Preview inspection passed\./);
  assert.match(noted, /2 elements carry the hidden attribute but are rendered anyway/);
  const clean = summary(await inspect({}, 'passed-clean'));
  assert.match(clean, /^Preview inspection passed\./);
  assert.doesNotMatch(clean, /hidden attribute|could not click/);
});
