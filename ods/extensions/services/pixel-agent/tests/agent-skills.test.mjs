import test from 'node:test';
import assert from 'node:assert/strict';
import {AGENT_SKILLS, NAMED_ITEM_CONTRACT, createAgentSkillTool} from '../plugin/agent-skills.mjs';
import {promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {createRunProgressBudget} from '../plugin/run-progress-budget.mjs';

test('guide lookup returns only the selected bounded topic', async () => {
  const tool = createAgentSkillTool();
  for (const topic of Object.keys(AGENT_SKILLS)) {
    const result = await tool.execute('guide', {topic});
    assert.equal(result.isError, undefined);
    assert.deepEqual(result.details, {kind: 'ods-operating-guide', topic, readOnly: true});
    assert.equal(result.content[0].text, AGENT_SKILLS[topic]);
    assert.ok(result.content[0].text.length < (topic === 'workspace' ? 3400 : 2600));
    for (const other of Object.keys(AGENT_SKILLS).filter(name => name !== topic)) {
      assert.ok(!result.content[0].text.includes(AGENT_SKILLS[other]));
    }
  }
});

test('guide lookup rejects extra execution fields, path traversal and prototype names', async () => {
  for (const args of [null, {}, {topic: '../config'}, {topic: '__proto__'},
    {topic: 'constructor'}, {topic: 'extensions', command: 'install'}, {topic: ['extensions']}]) {
    assert.equal((await createAgentSkillTool().execute('guide', args)).isError, true);
  }
});

test('loading guides cannot erase repeated action failures', () => {
  const budget = createRunProgressBudget();
  for (let i = 0; i < 4; i++) {
    budget.observeResult({callId: `exec-${i}`, tool: 'exec', failed: true, params: {command: `failing-${i}`}});
    budget.observeResult({callId: `guide-${i}`, tool: 'pixel_ods_skill', failed: false, params: {topic: 'extensions'}});
  }
  assert.ok(budget.exhausted);
});

// Tower2 round 073 (Qwen3-Coder-Next): the owner listed three event cards by
// name and the model titled one "Dawn Jazz at the Rose Pavilion".
const ROUND_073 = 'Create a polished responsive static event website in a new workspace directory fleet-qualification-92d546f23872. Actually write files and publish a verified Pixel workspace preview. Page title and one h1 must be exactly "Night Garden FLEET-d88dfed64e". Include three event cards: Dawn jazz, River lantern walk, and Midnight sold-out concert. Initially hide the entire Midnight sold-out concert card. Provide an accessible button named exactly "Show sold out" that reveals that card when clicked. Mobile width 375px must not overflow horizontally. Use semantic HTML, attractive CSS, working JavaScript, no external libraries, no localStorage dependency. Include the preview URL in your final response. Do the work now.';
const ROUND_073_EDIT = 'Update that same website: change both page title and h1 to exactly "Night Garden FLEET-d88dfed64e Revised", change the accent to amber, add a visible footer "FLEET-d88dfed64e edited successfully". Preserve all three event cards and the working Show sold out behavior. Publish the updated preview and provide its new URL.';

test('owner-named items keep their names: one constant sentence, the same bytes every turn', () => {
  assert.equal(NAMED_ITEM_CONTRACT, "When the owner names items (cards, sections, pages, buttons), use each name verbatim as that item's heading or label; put extra detail in body text, not in the heading.");
  assert.equal(AGENT_SKILLS.workspace.split(NAMED_ITEM_CONTRACT).length, 2);
  for (const other of ['extensions', 'research', 'verification']) assert.ok(!AGENT_SKILLS[other].includes(NAMED_ITEM_CONTRACT));
  const turn = (prompt, {messages, contextTokenBudget = 65536} = {}) =>
    promptContractForAgent({agentId: 'pixel', contextTokenBudget}, 'pixel', {prompt, messages}).appendSystemContext;
  const create = turn(ROUND_073);
  // Other item names select the same contract, byte for byte: nothing from the
  // owner's list is interpolated.
  const renamed = turn(ROUND_073.replace('Dawn jazz, River lantern walk, and Midnight sold-out concert', 'Alpha, Beta, and Gamma')
    .replace('Midnight sold-out concert card', 'Gamma card'));
  assert.equal(renamed, create);
  assert.doesNotMatch(create, /Dawn jazz|River lantern walk|Alpha, Beta/);
  const edit = turn(ROUND_073_EDIT, {messages: [{role: 'user', content: ROUND_073}, {role: 'assistant', content: 'Published.'},
    {role: 'user', content: ROUND_073_EDIT}]});
  const lean = turn(ROUND_073, {contextTokenBudget: 16384});
  for (const contract of [create, edit, lean]) {
    assert.equal(contract.split(NAMED_ITEM_CONTRACT).length, 2, 'present exactly once');
    assert.ok(contract.includes(AGENT_SKILLS.workspace), 'inside the unchanged workspace guide');
  }
});
