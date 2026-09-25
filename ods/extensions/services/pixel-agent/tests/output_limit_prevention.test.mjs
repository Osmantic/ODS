import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {configuredMaxOutputTokens, outputBudgetContract} from '../plugin/output-limit-recovery.mjs';
import {ODS_COMPACT_CONVERSATION_CONTRACT, ODS_CONVERSATION_CONTRACT, ODS_WORKSPACE_PREVIEW_CONTRACT,
  promptContractForAgent} from '../plugin/prompt-contract.mjs';

// strixy 2026-09-25: Qwen3.6-35B-A3B with an 8192-token output limit wrote the
// whole requested page in one write call; the reply was cut at 8192 tokens and
// nothing was saved. Pixel now states the per-reply limit so the model plans
// several smaller writes from the start.
const STRIXY_PROMPT = 'as a demo of your capabilities, make me a cool looking webpage with a forest theme and cool forest ' +
  "type effects.  Best you can do.\n\n[ODS Portal delivery requirement: Answer the owner's complete message above.]";
const TEXT_PROMPT = 'Write me the longest, most detailed guide you can about how old-growth forests store carbon.';

// The shape pixel_model_contract.plan() writes for an ODS-managed local model.
function managedConfig({rowMax = 8192, agentParams = {maxTokens: 8192}, defaults = {}} = {}) {
  return {
    agents: {defaults, list: [{id: 'pixel', model: {primary: 'ods-local/Qwen3.6-35B-A3B'}, contextTokens: 65536,
      params: agentParams}]},
    models: {providers: {'ods-local': {models: [
      {id: 'Qwen3.6-35B-A3B', name: 'ODS Local Qwen3.6-35B-A3B', contextWindow: 65536, maxTokens: rowMax},
      {id: 'small', name: 'ODS Local small', contextWindow: 16384, maxTokens: 2048},
    ]}}},
  };
}

test('the output limit is read the way OpenClaw applies it', () => {
  assert.equal(configuredMaxOutputTokens(managedConfig(), 'pixel'), 8192, 'strixy managed config');
  assert.equal(configuredMaxOutputTokens(managedConfig({agentParams: {}}), 'pixel'), 8192, 'row limit alone');
  assert.equal(configuredMaxOutputTokens(managedConfig({agentParams: {maxTokens: 3000}}), 'pixel'), 3000);
  assert.equal(configuredMaxOutputTokens(managedConfig({agentParams: {maxTokens: 20000}}), 'pixel'), 8192,
    'a larger params limit is clamped to the model row');
  assert.equal(configuredMaxOutputTokens(managedConfig({agentParams: {max_tokens: 6000},
    defaults: {params: {maxTokens: 2048}}}), 'pixel'), 6000, 'the agent layer overrides defaults, any alias');
  assert.equal(configuredMaxOutputTokens(managedConfig({agentParams: {},
    defaults: {params: {max_completion_tokens: 2048}}}), 'pixel'), 2048);
  assert.equal(configuredMaxOutputTokens(managedConfig(), 'pixel',
    {modelProviderId: 'ods-local', modelId: 'small'}), 2048, 'the model the run resolved decides');
  const gateway = {agents: {list: [{id: 'pixel', model: 'ods-gateway/ods/current'}]},
    models: {providers: {'ods-gateway': {models: [{id: 'ods/current', name: 'ODS Current (x)', maxTokens: 4096}]}}}};
  assert.equal(configuredMaxOutputTokens(gateway, 'pixel'), 4096, 'a model id containing a slash');
  for (const config of [undefined, {}, managedConfig({rowMax: null, agentParams: {}}),
    managedConfig({rowMax: 'lots', agentParams: {maxTokens: -1}})]) {
    assert.equal(configuredMaxOutputTokens(config, 'pixel'), undefined, JSON.stringify(config));
  }
  assert.equal(configuredMaxOutputTokens(managedConfig(), 'pixel', {modelProviderId: 'ods-policy', modelId: 'managed'}),
    undefined, 'a dynamically registered model has no configured row, so its clamp is unknown');
});

test('the budget line names the limit, the split and no creative ceiling', () => {
  const line = outputBudgetContract(8192);
  assert.match(line, /at most about 8192 output tokens/);
  assert.match(line, /including its thinking and tool-call arguments/);
  assert.match(line, /unfinished tool call does not run/);
  assert.match(line, /not the size of your work/);
  assert.match(line, /separate index\.html, styles\.css and script\.js/);
  assert.match(line, /each well under 4096 tokens \(about 12288 characters\)/);
  assert.doesNotMatch(line, /under 7000 characters|self-contained/);
  assert.equal(outputBudgetContract(8192), line, 'byte-stable for a host');
  for (const value of [undefined, null, 0, -1, 8192.5, NaN, '8192']) assert.equal(outputBudgetContract(value), '', String(value));
});

test('the budget line is host-constant and precedes every per-request section', () => {
  const line = outputBudgetContract(8192);
  for (const [lean, conversation] of [[true, ODS_COMPACT_CONVERSATION_CONTRACT], [false, ODS_CONVERSATION_CONTRACT]]) {
    const contract = prompt => promptContractForAgent({agentId: 'pixel', contextTokenBudget: 65536}, 'pixel', {prompt},
      {configuredLeanPrompt: lean, maxOutputTokens: 8192}).appendSystemContext;
    const site = contract(STRIXY_PROMPT), text = contract(TEXT_PROMPT);
    assert.equal(text, `${conversation} ${line}`);
    assert.ok(site.startsWith(`${conversation} ${line} `), 'same prefix as any other turn');
    assert.ok(site.includes(ODS_WORKSPACE_PREVIEW_CONTRACT), 'the strixy request keeps its website route');
    assert.equal(contract(STRIXY_PROMPT), site, 'byte-stable across turns');
  }
  // Without a known limit nothing changes (a dynamic provider, older configs).
  assert.equal(promptContractForAgent({agentId: 'pixel', contextTokenBudget: 65536}, 'pixel', {prompt: TEXT_PROMPT},
    {configuredLeanPrompt: true}).appendSystemContext, ODS_COMPACT_CONVERSATION_CONTRACT);
});

test('the plugin passes the running model limit into the prompt contract', () => {
  const entry = readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  assert.match(entry, /maxOutputTokens: configuredMaxOutputTokens\(api\.runtime\?\.config\?\.current\?\.\(\) \?\? api\.config, AGENT_ID, context\)/);
});
