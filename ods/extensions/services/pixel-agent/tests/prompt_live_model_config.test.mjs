import test from 'node:test';
import assert from 'node:assert/strict';
import {realPromptBuild} from './prompt-build-harness.mjs';
import {ODS_SMALL_CONTEXT_CONVERSATION_CONTRACT, ODS_COMPACT_CONVERSATION_CONTRACT,
  ODS_CONVERSATION_CONTRACT} from '../plugin/prompt-contract.mjs';

const prompt = 'What color is a ripe banana? Answer in one short sentence without tools.';
const agentConfig = {id: 'pixel', sandbox: {mode: 'all'}, tools: {exec: {host: 'sandbox'}}};
const config = (modelContextWindow, leanPrompt) => ({
  plugins: {entries: {'pixel-ods': {config: {modelContextWindow, leanPrompt}}}},
});

test('one registered prompt hook follows live 64K -> 8K -> 32K -> 64K model switches', async t => {
  let current = config(65536, false);
  const build = await realPromptBuild(t, {agentConfig,
    pluginConfig: current.plugins.entries['pixel-ods'].config, currentConfig: () => current});
  // The pinned native before_prompt_build context supplies no token budget.
  // Supplying a synthetic 8K context field would hide a stale registration.
  for (const [window, lean, expected] of [[65536, false, ODS_CONVERSATION_CONTRACT],
    [8192, true, ODS_SMALL_CONTEXT_CONVERSATION_CONTRACT],
    [32768, true, ODS_COMPACT_CONVERSATION_CONTRACT], [65536, false, ODS_CONVERSATION_CONTRACT]]) {
    current = config(window, lean);
    const result = await build(prompt);
    assert.ok(result.appendSystemContext.includes(expected), `live ${window} tier reaches the complete hook output`);
  }
});

test('small registration expands after a live model switch', async t => {
  const build = await realPromptBuild(t, {agentConfig,
    pluginConfig: {modelContextWindow: 8192, leanPrompt: true}, currentConfig: () => config(65536, false)});
  assert.ok((await build(prompt)).appendSystemContext.includes(ODS_CONVERSATION_CONTRACT));
});

test('registration settings remain the fallback without live plugin configuration', async t => {
  for (const currentConfig of [undefined, () => ({})]) {
    const build = await realPromptBuild(t, {agentConfig,
      pluginConfig: {modelContextWindow: 8192, leanPrompt: true}, currentConfig});
    assert.ok((await build(prompt)).appendSystemContext.includes(ODS_SMALL_CONTEXT_CONVERSATION_CONTRACT));
  }
});

// An explicitly empty live config is authoritative, not a partial overlay.
test('cleared live prompt settings do not inherit the registration tier', async t => {
  const build = await realPromptBuild(t, {agentConfig,
    pluginConfig: {modelContextWindow: 8192, leanPrompt: true}, currentConfig: () => config(undefined, false)});
  assert.ok((await build(prompt)).appendSystemContext.includes(ODS_CONVERSATION_CONTRACT));
});
