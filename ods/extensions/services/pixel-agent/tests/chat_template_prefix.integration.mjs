// Requires the pinned llama.cpp bridge (test-pixel-tool-grammar.yml). Renders
// the replayed owner turns with llama.cpp b9014's own Jinja engine and the
// Qwen3.5 templates ODS ships, and checks that
//  - tests/qwen-chat-render.mjs renders every request byte for byte the same,
//  - with Pixel's preserve_thinking switch each owner turn extends the
//    server's previous slot, and without it the break is the think block.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {commonPrefixLength, renderQwen35} from './qwen-chat-render.mjs';
import {replayOwnerTurns, slotAfter, systemBlockLength, OWNER_TURNS} from './prompt-prefix-replay.mjs';

const bridge = process.env.ODS_TEST_LLAMA_SCHEMA;
assert.ok(bridge, 'set ODS_TEST_LLAMA_SCHEMA to the pinned native test bridge');
const ORIGINAL = '        {%- if loop.index0 > ns.last_query_index %}\n';
const PRESERVE = '        {%- if (preserve_thinking is defined and preserve_thinking is true) or (loop.index0 > ns.last_query_index) %}\n';
const shipped = ['qwen3.5-preserve-thinking.jinja', 'qwen3.5-small-preserve-thinking.jinja'].map(name => ({name,
  text: readFileSync(new URL(`../../../../config/llama-server/templates/${name}`, import.meta.url), 'utf8')}));
const {requests} = await replayOwnerTurns();

function llamaRender(chatTemplate, request, chatTemplateKwargs) {
  const result = spawnSync(bridge, {input: JSON.stringify({chatTemplate, render: {
    messages: request.messages, tools: request.tools, enableThinking: false, chatTemplateKwargs}}),
    encoding: 'utf8', maxBuffer: 64 * 1024 * 1024});
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout).prompt;
}

for (const {name, text} of shipped) {
  const stock = text.replace(PRESERVE, ORIGINAL);
  test(`${name}: llama.cpp b9014 renders the replay exactly like the JavaScript mirror`, () => {
    for (const request of requests) {
      assert.equal(llamaRender(text, request, {enable_thinking: false, preserve_thinking: true}),
        renderQwen35({...request, preserveThinking: true}));
      assert.equal(llamaRender(text, request, {enable_thinking: false}), renderQwen35({...request, preserveThinking: false}));
      assert.equal(llamaRender(stock, request, {enable_thinking: false, preserve_thinking: true}),
        renderQwen35({...request, preserveThinking: false}), 'the GGUF template ignores the switch');
    }
  });

  test(`${name}: each owner turn extends the previous slot under llama.cpp b9014`, () => {
    for (const [preserve, kwargs] of [[true, {enable_thinking: false, preserve_thinking: true}], [false, {enable_thinking: false}]]) {
      const render = request => llamaRender(text, request, kwargs);
      for (const turn of OWNER_TURNS.keys()) {
        if (turn === 0) continue;
        const slot = slotAfter(render, requests.filter(request => request.turn === turn - 1).at(-1));
        const next = render(requests.find(request => request.turn === turn));
        const shared = commonPrefixLength(slot, next);
        assert.ok(shared >= systemBlockLength(next), 'system+tools block shared');
        assert.equal(shared === slot.length, preserve, `turn ${turn}: shared ${shared} of ${slot.length}`);
      }
    }
  });
}
