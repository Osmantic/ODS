import test from 'node:test';
import assert from 'node:assert/strict';
import {AGENT_SKILLS, createAgentSkillTool} from '../plugin/agent-skills.mjs';
import {createRunProgressBudget} from '../plugin/run-progress-budget.mjs';

test('guide lookup returns only the selected bounded topic', async () => {
  const tool = createAgentSkillTool();
  for (const topic of Object.keys(AGENT_SKILLS)) {
    const result = await tool.execute('guide', {topic});
    assert.equal(result.isError, undefined);
    assert.deepEqual(result.details, {kind: 'ods-operating-guide', topic, readOnly: true});
    assert.equal(result.content[0].text, AGENT_SKILLS[topic]);
    assert.ok(result.content[0].text.length < 2600);
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
