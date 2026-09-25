import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createWorkspacePreviewInspectTool, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams}
  from '../plugin/workspace-preview-inspect.mjs';

// Fleet evidence: windows-laptop-wsl-beta, round 081, Qwen3.5-9B,
// website_create. Step 3 asserted ".event-card.hidden" hidden (one element,
// display:none), the click on .reveal-button passed, and step 5 asserted the
// same selector visible: no element matched, because the click removed the
// "hidden" class. The reply said only "matched no element"; the model decided
// the page script was broken and spent 22 calls (9,192 generated tokens)
// rewriting a working site.
const LAPTOP = JSON.parse(fs.readFileSync(new URL('./fixtures/preview-locator-laptop-round081.json', import.meta.url), 'utf8'));

async function reply(args, receipt) {
  const result = await createWorkspacePreviewInspectTool({request: async () => receipt}).execute('inspect', args);
  return {result, text: result.content[0].text};
}
// A receipt for changed steps, rebound to the changed plan like the capsule's.
function variant(steps, receiptSteps, status = 'failed') {
  const args = {...structuredClone(LAPTOP.args), steps};
  const planSha256 = inspectionPlanHash(normalizeWorkspacePreviewInspectionParams(args));
  return {args, receipt: {...structuredClone(LAPTOP.receipt), planSha256, status, steps: receiptSteps}};
}
const step = (index, action, selector, before, extra = {}) =>
  ({index, action, locator: {selector}, before, stable: true, status: 'passed', ...extra});
const shown = {count: 1, display: 'block', hidden: false, hiddenUntilFound: false, opacity: '1', rectCount: 1, visibility: 'visible', visible: true};
const hidden = {...shown, display: 'none', rectCount: 0, visible: false};
const missing = {count: 0};

const LAPTOP_FEEDBACK = 'Step 5 (assert-visible) matched no element. Step 3 matched exactly one element with this same selector before the click in step 4, ' +
  'so the click changed which elements ".event-card.hidden" matches (for example by removing the class "hidden"). ' +
  'That is not evidence of a site defect; do not change the site for it. Retry the inspection on this same snapshot with a locator that does not depend on the toggled state, ' +
  "such as the element's id, in both the hidden and the visible assertion. Requested behavior remains unverified.";

test('laptop replay: the recorded receipt explains the state-dependent locator and stays a failure', async () => {
  const {result, text} = await reply(LAPTOP.args, LAPTOP.receipt);
  assert.equal(result.isError, true);
  assert.equal(result.details.status, 'failed');
  assert.ok(text.startsWith(`Preview inspection failed. ${LAPTOP_FEEDBACK} `), text);
  assert.doesNotMatch(text, /Copy the exact role and accessible name/);
  // Today's reply for the same receipt, recorded in the fleet transcript.
  assert.match(LAPTOP.persistedText, /^Preview inspection failed\. Step 5 \(assert-visible\) matched no element, so nothing was measured/);
});

test('without an earlier match of the same selector, or without a click between, the reply is unchanged', async () => {
  const click = step(1, 'click', '.reveal-button', shown, {after: shown});
  const noEarlier = variant(
    [{action: 'click', locator: {selector: '.reveal-button'}}, {action: 'assert-visible', locator: {selector: '.event-card.hidden'}}],
    [{...click, index: 0}, step(1, 'assert-visible', '.event-card.hidden', missing, {status: 'failed', errorCode: 'no_match'})]);
  const noClick = variant(
    [{action: 'assert-hidden', locator: {selector: '.event-card.hidden'}}, {action: 'assert-visible', locator: {selector: '.reveal-button'}},
      {action: 'assert-visible', locator: {selector: '.event-card.hidden'}}],
    [step(0, 'assert-hidden', '.event-card.hidden', hidden), step(1, 'assert-visible', '.reveal-button', shown),
      step(2, 'assert-visible', '.event-card.hidden', missing, {status: 'failed', errorCode: 'no_match'})]);
  const {text: first} = await reply(noEarlier.args, noEarlier.receipt);
  assert.match(first, /^Preview inspection failed\. Step 2 \(assert-visible\) matched no element, so nothing was measured and later steps did not run\. /);
  // A click is in the plan, so the generic branch names the state class.
  assert.match(first, / A state class that a click adds or removes, such as "hidden" in "\.event-card\.hidden", matches in only one state\. Copy the exact role/);
  assert.doesNotMatch(first, /not evidence of a site defect/);
  const {text: second} = await reply(noClick.args, noClick.receipt);
  assert.match(second, /^Preview inspection failed\. Step 3 \(assert-visible\) matched no element, so nothing was measured and later steps did not run\. Copy the exact role/);
  assert.doesNotMatch(second, /state class|not evidence of a site defect/);
});

test('an id or tag that stops matching after the click keeps the ordinary reply (the element itself is gone)', async () => {
  for (const selector of ['#details', 'dialog']) {
    const plan = variant(
      [{action: 'assert-visible', locator: {selector}}, {action: 'click', locator: {selector: '.close'}},
        {action: 'assert-visible', locator: {selector}}],
      [step(0, 'assert-visible', selector, shown), step(1, 'click', '.close', shown, {after: shown}),
        step(2, 'assert-visible', selector, missing, {status: 'failed', errorCode: 'no_match'})]);
    const {text} = await reply(plan.args, plan.receipt);
    assert.match(text, /^Preview inspection failed\. Step 3 \(assert-visible\) matched no element, so nothing was measured/, selector);
    assert.doesNotMatch(text, /not evidence of a site defect/, selector);
  }
});

test('a real visibility failure and a non-unique selector keep their replies', async () => {
  // Laptop A#9: an inline style kept the card hidden after the class changed;
  // the stable selector matched and the capsule reported visibility_mismatch.
  const mismatch = variant(LAPTOP.args.steps.map(item => item.locator.selector === '.event-card.hidden'
    ? {...item, locator: {selector: '#midnight-card'}} : item),
  [...LAPTOP.receipt.steps.slice(0, 4).map(item => item.locator.selector === '.event-card.hidden'
    ? {...item, locator: {selector: '#midnight-card'}} : item),
  step(4, 'assert-visible', '#midnight-card', hidden, {status: 'failed', errorCode: 'visibility_mismatch'})]);
  const {result, text} = await reply(mismatch.args, mismatch.receipt);
  assert.equal(result.isError, true);
  assert.equal(text.startsWith('Preview inspection failed. Requested behavior remains unverified; a failed inspection does not establish a visibility transition.'), true, text);
  assert.doesNotMatch(text, /not evidence of a site defect/);

  const several = variant(LAPTOP.args.steps,
    [...LAPTOP.receipt.steps.slice(0, 4), step(4, 'assert-visible', '.event-card.hidden', {count: 3}, {status: 'failed', errorCode: 'selector_not_unique'})]);
  const {text: many} = await reply(several.args, several.receipt);
  assert.match(many, /^Preview inspection failed\. Step 5 \(assert-visible\) matched 3 elements; a locator must match exactly one/);
  assert.doesNotMatch(many, /not evidence of a site defect/);
});

test('role/name locators keep their reply', async () => {
  const button = {role: 'button', name: 'Show details', exact: true};
  const args = {...structuredClone(LAPTOP.args), steps: [
    {action: 'assert-visible', locator: button}, {action: 'click', locator: {selector: '.reveal-button'}},
    {action: 'assert-visible', locator: button}]};
  const planSha256 = inspectionPlanHash(normalizeWorkspacePreviewInspectionParams(args));
  const receipt = {...structuredClone(LAPTOP.receipt), planSha256, steps: [
    {index: 0, action: 'assert-visible', locator: button, before: shown, stable: true, status: 'passed'},
    step(1, 'click', '.reveal-button', shown, {after: shown}),
    {index: 2, action: 'assert-visible', locator: button, before: missing, stable: true, status: 'failed', errorCode: 'no_match'}]};
  const {text} = await reply(args, receipt);
  assert.match(text, /^Preview inspection failed\. Step 3 \(assert-visible\) matched no element, so nothing was measured and later steps did not run\. For assert-visible, role\/name locators match only rendered elements/);
  assert.doesNotMatch(text, /not evidence of a site defect|state class/);
});

test('the same selector asserted visible twice around a click names both assertions', async () => {
  const plan = variant(
    [{action: 'assert-visible', locator: {selector: '.menu.open'}}, {action: 'click', locator: {selector: '.toggle'}},
      {action: 'assert-visible', locator: {selector: '.menu.open'}}],
    [step(0, 'assert-visible', '.menu.open', shown), step(1, 'click', '.toggle', shown, {after: shown}),
      step(2, 'assert-visible', '.menu.open', missing, {status: 'failed', errorCode: 'no_match'})]);
  const {text} = await reply(plan.args, plan.receipt);
  assert.match(text, /the click changed which elements "\.menu\.open" matches \(for example by removing the class "open"\)\./);
  assert.match(text, /such as the element's id, in both assertions\. Requested behavior remains unverified\./);
});
