import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {
  PIXEL_RUNTIME_LINE_PREFIX,
  PIXEL_RUNTIME_LINE_TRANSFORMS,
  PIXEL_RUNTIME_SESSION_FIELDS,
  registerStableRuntimeLine,
  stablePixelRuntimeLine,
} from '../plugin/runtime-line.mjs';

// The Runtime section exactly as the pinned OpenClaw 2026.6.33 renders it
// (system-prompt-config buildRuntimeLine; fields joined with " | ").
const runtimeSection = ({agent = 'pixel', key, id, processes = ''} = {}) => [
  '## Runtime',
  'Runtime: ' + [
    `agent=${agent}`,
    key ? `session=${key}` : '',
    id ? `sessionId=${id}` : '',
    'host=tower3', 'repo=/home/tower3/.openclaw/workspace-pixel', 'os=Linux 6.8.0-85-generic (x64)',
    'node=v22.23.0', 'model=ods-local/Qwen3.5-27B-Q4_K_M.gguf', 'default_model=ods-local/Qwen3.5-27B-Q4_K_M.gguf',
    'shell=bash', 'thinking=off',
  ].filter(Boolean).join(' | '),
  ...(processes ? [processes] : []),
  'Reasoning: off (hidden unless on/stream). Toggle /reasoning; /status shows Reasoning when enabled.',
].join('\n');

const prompt = runtime => `# Tools\n...\n## Workspace\nYour working directory is ~/workspace.\n${runtime}\n\n---\n\nOpenClaw plugin-injected system context.`;

// Portal chats are keyed agent:pixel:openai-user:ods-<64 hex> (pixel_ingress).
const MAIN = {key: 'agent:pixel:openai-user:ods-' + '3f'.repeat(32), id: '30ee9622-e5d2-48a4-90f2-96e73252ac6f'};
const SIDE = {key: 'agent:pixel:openai-user:ods-' + 'a9'.repeat(32), id: '6ca59fdf-47f7-4968-9086-9120c84de39e'};

test('the Runtime line keeps every field except the chat session key and id', () => {
  const before = prompt(runtimeSection(MAIN));
  const after = stablePixelRuntimeLine(before);
  assert.equal(after, prompt(runtimeSection({})));
  assert.match(after, /^Runtime: agent=pixel \| host=tower3 \| repo=/m);
  assert.doesNotMatch(after, /session=|sessionId=/);
  assert.ok(!after.includes(MAIN.id) && !after.includes(MAIN.key));
  assert.equal(before.length - after.length, ` | session=${MAIN.key} | sessionId=${MAIN.id}`.length,
    'only the two fields are removed');
});

test('a new chat and the resident chat get byte-identical system prompts', () => {
  const main = prompt(runtimeSection(MAIN)), side = prompt(runtimeSection(SIDE));
  assert.notEqual(main, side);
  let firstDifference = 0; while (main[firstDifference] === side[firstDifference]) firstDifference++;
  assert.ok(main.slice(0, firstDifference).endsWith('session=agent:pixel:openai-user:ods-'),
    'without the transform the chats diverge inside the session key');
  assert.equal(stablePixelRuntimeLine(main), stablePixelRuntimeLine(side));
});

test('the transform is idempotent and leaves text without the fields unchanged', () => {
  const once = stablePixelRuntimeLine(prompt(runtimeSection(SIDE)));
  assert.equal(stablePixelRuntimeLine(once), once);
  for (const text of ['', 'plain owner text', prompt(runtimeSection({})),
    'Runtime: agent=pixel | host=h | thinking=off', 'Runtime: agent=pixel']) {
    assert.equal(stablePixelRuntimeLine(text), text);
  }
  assert.equal(stablePixelRuntimeLine(undefined), undefined);
});

test('only a key field or only an id field is removed too', () => {
  assert.equal(stablePixelRuntimeLine(`Runtime: agent=pixel | session=${MAIN.key} | host=h`), 'Runtime: agent=pixel | host=h');
  assert.equal(stablePixelRuntimeLine(`Runtime: agent=pixel | sessionId=${MAIN.id} | host=h`), 'Runtime: agent=pixel | host=h');
  assert.equal(stablePixelRuntimeLine(`Runtime: agent=pixel | session=${MAIN.key} | sessionId=${MAIN.id}`), 'Runtime: agent=pixel');
});

test('a session key with separators inside is removed whole', () => {
  // Field boundaries are " | name=": a key cannot end the field early unless
  // it embeds that shape, which OpenClaw and ODS session keys never do.
  const odd = 'agent:pixel:custom | not a field | x y';
  assert.equal(stablePixelRuntimeLine(`Runtime: agent=pixel | session=${odd} | sessionId=${MAIN.id} | host=h | thinking=off`),
    'Runtime: agent=pixel | host=h | thinking=off');
});

test('other agents, other lines and later fields are not touched', () => {
  const other = prompt(runtimeSection({...MAIN, agent: 'pixel-reviewer'}));
  assert.equal(stablePixelRuntimeLine(other), other);
  const quoted = `The owner pasted:\n> Runtime: agent=pixel | session=${MAIN.key} | host=h`;
  assert.equal(stablePixelRuntimeLine(quoted), quoted, 'only a line that starts with the Runtime prefix');
  // OpenClaw also passes message text through input transforms. A pasted line
  // that starts with the exact prefix loses the same two fields, the same way
  // on every call, so the request stays append-only.
  const pasted = `Here is my prompt:\nRuntime: agent=pixel | session=${MAIN.key} | sessionId=${MAIN.id} | host=h\nthanks`;
  assert.equal(stablePixelRuntimeLine(pasted), 'Here is my prompt:\nRuntime: agent=pixel | host=h\nthanks');
  assert.equal(stablePixelRuntimeLine(stablePixelRuntimeLine(pasted)), stablePixelRuntimeLine(pasted));
  // Background exec sessions are model-facing (process log/poll) and stay.
  const processes = 'Active background exec sessions in this scope:\n- warm-otter running pid=41 :: npm run dev';
  const withProcesses = stablePixelRuntimeLine(prompt(runtimeSection({...MAIN, processes})));
  assert.ok(withProcesses.includes(processes));
  assert.equal(withProcesses, prompt(runtimeSection({processes})));
});

test('OpenClaw applies the registered replacement with String.prototype.replace', () => {
  // text-transforms.runtime: next = next.replace(replacement.from, replacement.to)
  const [replacement] = PIXEL_RUNTIME_LINE_TRANSFORMS.input;
  const text = prompt(runtimeSection(SIDE));
  assert.equal(text.replace(replacement.from, replacement.to), stablePixelRuntimeLine(text));
  assert.equal(replacement.from, PIXEL_RUNTIME_SESSION_FIELDS);
  assert.ok(!PIXEL_RUNTIME_SESSION_FIELDS.global && !PIXEL_RUNTIME_SESSION_FIELDS.sticky, 'stateless pattern');
  assert.equal(PIXEL_RUNTIME_LINE_TRANSFORMS.output, undefined, 'model output is never rewritten');
  assert.ok(PIXEL_RUNTIME_SESSION_FIELDS.source.startsWith('^(' + PIXEL_RUNTIME_LINE_PREFIX));
});

test('registration uses the plugin text-transform API and honours prompt-injection policy', () => {
  const calls = [];
  const api = config => ({config, registerTextTransforms: transforms => calls.push(transforms)});
  assert.equal(registerStableRuntimeLine(api({})), true);
  assert.deepEqual(calls, [PIXEL_RUNTIME_LINE_TRANSFORMS]);
  assert.equal(registerStableRuntimeLine(api({plugins: {entries: {'pixel-ods': {hooks: {allowPromptInjection: false}}}}})), false);
  assert.equal(registerStableRuntimeLine(api({plugins: {entries: {'pixel-ods': {hooks: {allowPromptInjection: true}}}}})), true);
  assert.equal(calls.length, 2);
  assert.equal(registerStableRuntimeLine({config: {}}), false, 'older runtimes without the API');
  assert.equal(registerStableRuntimeLine(undefined), false);
});

test('the plugin entry registers the stable Runtime line', () => {
  const source = readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  assert.match(source, /^import \{registerStableRuntimeLine\} from '\.\/runtime-line\.mjs';$/m);
  const register = source.slice(source.indexOf('  register(api) {'));
  assert.ok(register.length > 0 && /\n {4}registerStableRuntimeLine\(api\);\r?\n/.test(register));
});
