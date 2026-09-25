// Replays consecutive Pixel owner turns the way a local llama.cpp server sees
// them: the actual before_prompt_build / before_message_write / agent_end hook
// source from plugin/index.js, OpenClaw 2026.6.33's prompt composition and
// persistence, and the Qwen3.5 chat template (tests/qwen-chat-render.mjs).
// Shared by prompt_prefix_replay.test.mjs and the pinned llama.cpp check.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {ACTIVITY_CONTRACT} from '../plugin/activity-display.mjs';
import {executionContext, turnHostDate} from '../plugin/completion-assurance.mjs';
import {GOAL_CONTRACT} from '../plugin/goal-progress.mjs';
import {composePromptBuildResult, promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {createTurnGuidancePersistence, registerTurnGuidanceTextTransforms, retryGuidance, withoutPersistedTurnGuidance} from '../plugin/turn-guidance.mjs';
import {privateBrowserAccessForAgent} from '../plugin/tool-loop-guard.mjs';
import {executionHostForAgent} from '../plugin/access-runtime.mjs';
import {registeredPixelTools} from './tool-grammar-registration.mjs';

const DELIVERY = "\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]";
const MARKER = 'FLEET-63c79f3de4';
const DIRECTORY = 'Playground/fleet-qualification-4de6a2213f1b';

// The fleet journey's first three owner messages: they select the website
// preview contract, the visual-continuation contract and no contract.
export const OWNER_TURNS = [
  {prompt: `Create a polished responsive static event website in a new workspace directory ${DIRECTORY}. Actually write files and publish a verified Pixel workspace preview. Page title and one h1 must be exactly "Night Garden ${MARKER}". Include the preview URL in your final response. Do the work now.` + DELIVERY,
    path: `${DIRECTORY}/index.html`, html: `<!doctype html><title>Night Garden ${MARKER}</title><h1>Night Garden ${MARKER}</h1>`,
    answer: `Published the Night Garden ${MARKER} site in ${DIRECTORY}: http://127.0.0.1:9437/p/night-garden/`},
  {prompt: `Update that same website: change both page title and h1 to exactly "Night Garden ${MARKER} Revised" and change the accent to amber. Publish the updated preview and provide its new URL.` + DELIVERY,
    path: `${DIRECTORY}/index.html`, html: `<!doctype html><title>Night Garden ${MARKER} Revised</title><h1 style="color:#f59e0b">Night Garden ${MARKER} Revised</h1>`,
    answer: `Updated and republished: http://127.0.0.1:9437/p/night-garden-2/`},
  {prompt: 'Without changing any files or rereading files, tell me the exact unique FLEET marker and the directory of the website we just edited.' + DELIVERY,
    answer: `${MARKER}, in ${DIRECTORY}.`},
];

// Stands in for OpenClaw's base system prompt, which ends with the per-chat
// Runtime line (system-prompt.ts:1350-1356); constant within a chat.
const BASE_SYSTEM_PROMPT = 'You are a personal assistant running inside OpenClaw.\n\n## Tooling\nUse the tools you are given.\n\n## Runtime\nRuntime: agent=pixel | session=agent:pixel:openai-user:ods-aaaa | sessionId=4f1c | os=Linux (x64) | model=ods-local/ods/current | channel=webchat\nReasoning: off (hidden unless on/stream).';
const PLUGIN_HEADER = 'OpenClaw plugin-injected system context. This block is not workspace file content.';

// OpenClaw composeSystemPromptWithHookContext / wrapPluginSystemContextSection.
export const openClawSystemPrompt = appendSystemContext =>
  [BASE_SYSTEM_PROMPT, `---\n\n${PLUGIN_HEADER}\n\n${appendSystemContext.trim()}\n\n---`].join('\n\n');
// OpenClaw composeModelPromptContext (attempt.llm-boundary.ts:250-258).
export const openClawModelPrompt = ({prependContext, prompt, appendContext}) =>
  [prependContext, prompt, appendContext].filter(value => Boolean(value?.trim())).join('\n\n');

// The main-branch composition before this change, kept as a regression control:
// message-selected contracts, the goal contract and the host date in system space.
function legacyComposition(contract) {
  return {appendSystemContext: `${ACTIVITY_CONTRACT}  ${contract.appendSystemContext} ${executionContext()} ${turnHostDate([])} `};
}

// Extract and run the actual registration block for these hooks with only the
// unrelated runtime services faked. repositoryContext stands in for the
// plugin's README reader; cancelContextForRun for the Portal cancel note.
export function pixelPromptHooks({goalActive = false, repositoryContext = async () => '',
  cancelContextForRun = () => undefined} = {}) {
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8');
  const start = source.indexOf('    const turnGuidance = createTurnGuidancePersistence(');
  const endMarker = 'api.on("agent_end", (_event, context) => turnGuidance.forget(context));';
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0 && end > start, 'expected the prompt-build hook registration block');
  const callbacks = {};
  // Input text transforms the block registers, for a caller that applies them
  // (OpenClaw does, to every model request).
  const textTransforms = [];
  const config = {agents: {list: [{id: 'pixel', workspace: '/home/owner/.openclaw/workspace',
    sandbox: {mode: 'all'}, tools: {exec: {host: 'sandbox'}}}]}};
  vm.runInNewContext(source.slice(start, end + endMarker.length), {
    api: {config, on: (name, callback) => { callbacks[name] = callback; },
      registerTextTransforms: transforms => { textTransforms.push(transforms); }},
    AGENT_ID: 'pixel', configuredContextWindow: 65536, configuredLeanPrompt: false,
    createTurnGuidancePersistence, registerTurnGuidanceTextTransforms, retryGuidance, withoutPersistedTurnGuidance,
    privateBrowserAccessForAgent, executionHostForAgent,
    promptContractForAgent, composePromptBuildResult, ACTIVITY_CONTRACT, executionContext, turnHostDate, GOAL_CONTRACT,
    extensionRepositoryContext: repositoryContext,
    toolLoopGuard: {observeRun() {}, verificationStatus: () => undefined, observeRepositorySource() {},
      promptContextForRun: cancelContextForRun},
    accessRuntime: {isProbe: () => false},
    goalProgress: {begin() {}, active: () => goalActive},
    taskActivity: {begin() {}},
  });
  for (const name of ['before_prompt_build', 'before_message_write', 'agent_end']) {
    assert.equal(typeof callbacks[name], 'function', name);
  }
  return Object.assign(callbacks, {textTransforms});
}

const text = value => [{type: 'text', text: value}];

// OpenClaw openai-completions conversion of stored messages to request messages.
function requestMessage(message) {
  if (message.role === 'user') return {role: 'user', content: message.content.map(part => part.text).join('')};
  if (message.role === 'toolResult') {
    return {role: 'tool', tool_call_id: message.toolCallId, content: message.content.map(part => part.text).join('\n')};
  }
  const content = message.content.filter(part => part.type === 'text').map(part => part.text).join('');
  const calls = message.content.filter(part => part.type === 'toolCall');
  return calls.length
    ? {role: 'assistant', content: content || null, tool_calls: calls.map(call =>
      ({id: call.id, type: 'function', function: {name: call.name, arguments: JSON.stringify(call.arguments)}}))}
    : {role: 'assistant', content};
}

/**
 * Run the owner turns through the plugin hooks and OpenClaw's persistence.
 * Every run makes one tool call (except the last question) and one final
 * answer. Returns every model request and the text the server generated.
 *
 * mode 'current': this plugin. 'unstored': the guidance is not stored with
 * the owner message (OpenClaw's default for appendContext). 'legacy': the
 * earlier composition with message-selected text in system space.
 */
export async function replayOwnerTurns({mode = 'current', turns = OWNER_TURNS} = {}) {
  const hooks = pixelPromptHooks();
  const tools = (await registeredPixelTools()).map(tool => ({type: 'function', function: {
    name: tool.name, description: tool.description ?? '', parameters: tool.parameters}}));
  const transcript = [];
  const requests = [];
  const sessionKey = 'agent:pixel:openai-user:ods-aaaa';
  let timestamp = Date.parse('2026-09-25T13:48:00Z');
  for (const [index, turn] of turns.entries()) {
    const context = {agentId: 'pixel', sessionKey, runId: `run-${index}`, trigger: 'user'};
    const event = {prompt: turn.prompt, messages: structuredClone(transcript)};
    let result = await hooks.before_prompt_build(event, context);
    if (mode === 'legacy') {
      result = legacyComposition(promptContractForAgent(context, 'pixel', event, {configuredContextWindow: 65536}));
    }
    const system = openClawSystemPrompt(result.appendSystemContext);
    const modelPrompt = openClawModelPrompt({prompt: turn.prompt, prependContext: result.prependContext,
      appendContext: result.appendContext});
    // OpenClaw persists the owner message through before_message_write.
    let owner = {role: 'user', content: text(turn.prompt), timestamp: timestamp += 1000};
    const written = mode === 'current'
      ? hooks.before_message_write({message: owner}, {agentId: 'pixel', sessionKey}) : undefined;
    if (written?.message) owner = written.message;
    const ownerIndex = transcript.push(owner) - 1;
    const live = () => [
      {role: 'system', content: system},
      ...transcript.slice(0, ownerIndex).map(requestMessage),
      {role: 'user', content: modelPrompt},
      ...transcript.slice(ownerIndex + 1).map(requestMessage),
    ];
    if (turn.path) {
      requests.push({turn: index, system, tools, messages: live()});
      const call = {type: 'toolCall', id: `call-${index}`, name: 'write', arguments: {path: turn.path, content: turn.html}};
      transcript.push({role: 'assistant', content: [call], timestamp: timestamp += 1000});
      transcript.push({role: 'toolResult', toolCallId: call.id, toolName: 'write', timestamp: timestamp += 1000,
        content: text(`Successfully wrote ${turn.html.length} bytes to ${turn.path}\n\n[ODS Pixel next step] Publish the workspace preview for ${DIRECTORY}, then give the owner its URL.`)});
    }
    requests.push({turn: index, system, tools, messages: live(), generated: turn.answer});
    transcript.push({role: 'assistant', content: text(turn.answer), timestamp: timestamp += 1000});
    hooks.agent_end({}, context);
  }
  return {requests, transcript, tools};
}

/** The server's slot after a request: the rendered prompt plus the generated text. */
export const slotAfter = (render, request) => render(request) + (request.generated ?? '');
/** Characters before the first user message: the system+tools block. */
export const systemBlockLength = rendered => rendered.indexOf('<|im_start|>user\n');
