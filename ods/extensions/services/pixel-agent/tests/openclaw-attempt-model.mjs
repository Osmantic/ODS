// A small model of how OpenClaw 2026.6.33 turns one owner message into model
// requests and a stored transcript entry, reduced to what decides the owner
// message text. It drives the plugin's actual prompt hooks
// (tests/prompt-prefix-replay.mjs pixelPromptHooks). The real gateway checks in
// runtime_turn_guidance.integration.mjs pin the same behaviour against the
// pinned OpenClaw package.
//
//  - prompt-build context: prependContext / appendContext joined with a blank
//    line around the prompt (selection-BEwSQKM-.js:13229-13240);
//  - an unanswered stored owner message at the leaf is merged into, or
//    dropped as already contained in, the new prompt, separately for the
//    model prompt and for the transcript prompt (attempt.prompt-helpers
//    promptAlreadyIncludesQueuedUserMessage / mergeOrphanedTrailingUserPrompt,
//    applied at selection-BEwSQKM-.js:13304-13336);
//  - the transcript/model split, which turns model-prompt text found inside
//    the transcript prompt into an "OpenClaw runtime context" message sent
//    before the owner message (runtime-context-prompt resolveRuntimeContextPromptParts);
//  - before_message_write on the owner message, whose persistence a revision
//    pass suppresses after the hook ran (attempt.model-diagnostic-events:1250-1259);
//  - the in-run model prompt transform (selection-BEwSQKM-.js:7923-7950),
//    including an attempt that compacts and retries in place after a context
//    overflow: the session is reloaded, so the transform finds the stored
//    owner text instead of the transcript prompt and composes the
//    prompt-build context around it (attempt.llm-boundary.ts replace(),
//    agent-session.ts runAutoCompaction("overflow", true));
//  - registered input text transforms, applied to every model request
//    (plugin-text-transforms.ts wrapStreamFnTextTransforms);
//  - an owner cancel writes an aborted, empty assistant message (dropped from
//    requests) plus a prompt-error entry; a gateway crash writes nothing.

export const QUEUED_USER_MESSAGE_MARKER = '[Queued user message that arrived while the previous turn was still active]';
export const BEFORE_AGENT_FINALIZE_RETRY_PROMPT_PREFIX = 'Before accepting the previous final answer, apply this revision request and produce the revised final answer. Do not repeat completed work or rerun tools unless the request explicitly requires it.';
export const RUNTIME_CONTEXT_HEADER = 'OpenClaw runtime context for the immediately preceding user message.';

function extractUserMessagePromptText(content) {
  if (typeof content === 'string') return content.trim() || undefined;
  if (!Array.isArray(content)) return undefined;
  return content.flatMap(part => part?.type === 'text' && typeof part.text === 'string' && part.text.trim()
    ? [part.text.trim()] : []).join('\n').trim() || undefined;
}

function promptAlreadyIncludesQueuedUserMessage(prompt, orphanText) {
  const normalizedPrompt = prompt.replace(/\r\n/g, '\n');
  const normalizedOrphanText = orphanText.replace(/\r\n/g, '\n').trim();
  if (!normalizedOrphanText) return false;
  const queuedBlockPrefix = `${QUEUED_USER_MESSAGE_MARKER}\n${normalizedOrphanText}`;
  return normalizedPrompt === queuedBlockPrefix || normalizedPrompt.startsWith(`${queuedBlockPrefix}\n`) ||
    normalizedPrompt.includes(`\n${queuedBlockPrefix}\n`) || `\n${normalizedPrompt}\n`.includes(`\n${normalizedOrphanText}\n`);
}

function mergeOrphanedTrailingUserPrompt(prompt, leafMessage) {
  const orphanText = extractUserMessagePromptText(leafMessage.content);
  if (!orphanText || promptAlreadyIncludesQueuedUserMessage(prompt, orphanText)) return prompt;
  return [QUEUED_USER_MESSAGE_MARKER, orphanText, '', prompt].join('\n');
}

function removeLastPromptOccurrence(text, prompt) {
  const index = text.lastIndexOf(prompt);
  if (index === -1) return null;
  return [text.slice(0, index).trimEnd(), text.slice(index + prompt.length).trimStart()]
    .filter(part => part.length > 0).join('\n\n').trim();
}

const text = value => [{type: 'text', text: value}];
// OpenClaw composeModelPromptContext.
const composeModelPrompt = parts => parts.filter(value => Boolean(value?.trim())).join('\n\n');
// OpenClaw applyPluginTextReplacements over each registered input transform.
const applyInputTransforms = (content, transforms = []) => typeof content === 'string'
  ? transforms.flatMap(entry => entry.input ?? []).reduce((next, {from, to}) => next.replace(from, to), content)
  : content;
const plain = message => Array.isArray(message.content)
  ? message.content.filter(part => part.type === 'text').map(part => part.text).join('') : message.content;

// openai-completions conversion of stored messages to request messages.
function requestMessages(transcript) {
  return transcript.flatMap(message => {
    if (message.role === 'user') return [{role: 'user', content: plain(message)}];
    if (message.role === 'toolResult') return [{role: 'tool', tool_call_id: message.toolCallId, content: plain(message)}];
    if (message.stopReason === 'aborted' && !message.content.length) return [];
    const calls = message.content.filter(part => part.type === 'toolCall');
    return [calls.length
      ? {role: 'assistant', content: null, tool_calls: calls.map(call => ({id: call.id, type: 'function',
        function: {name: call.name, arguments: JSON.stringify(call.arguments)}}))}
      : {role: 'assistant', content: plain(message)}];
  });
}

/**
 * One chat. attempt() runs one owner message (or a revision pass) and returns
 * what the model received; transcript holds what OpenClaw stored.
 */
export function createOpenClawChat({hooks, sessionKey = 'agent:pixel:openai-user:ods-aaaa'}) {
  const transcript = [];
  const requests = [];
  let leafIsMessage = true;
  const chat = {
    transcript,
    requests,
    hooks,
    // A gateway restart: the plugin (and its pending state) is new.
    restart(nextHooks) { chat.hooks = nextHooks; },
    async attempt({prompt, runId, answer = 'Done.', toolCall, interrupt, revision = false, overflow = false}) {
      const context = {agentId: 'pixel', sessionKey, runId, trigger: 'user'};
      const result = await chat.hooks.before_prompt_build({prompt, messages: structuredClone(transcript)}, context) ?? {};
      let effectivePrompt = prompt;
      if (result.prependContext) effectivePrompt = `${result.prependContext}\n\n${effectivePrompt}`;
      if (result.appendContext) effectivePrompt = `${effectivePrompt}\n\n${result.appendContext}`;
      const hasPromptBuildContext = Boolean(result.prependContext?.trim()) || Boolean(result.appendContext?.trim());
      let transcriptPrompt = prompt;
      const leaf = transcript.at(-1);
      if (leafIsMessage && leaf?.role === 'user') {
        effectivePrompt = mergeOrphanedTrailingUserPrompt(effectivePrompt, leaf);
        transcriptPrompt = mergeOrphanedTrailingUserPrompt(transcriptPrompt, leaf);
        transcript.pop();
      }
      const modelPrompt = hasPromptBuildContext ? effectivePrompt : undefined;
      const runtimeContext = modelPrompt ? removeLastPromptOccurrence(transcriptPrompt, modelPrompt)?.trim() || undefined : undefined;
      const promptForSession = transcriptPrompt;
      const promptForModel = modelPrompt ?? transcriptPrompt;
      const owner = {role: 'user', content: text(promptForSession)};
      const written = chat.hooks.before_message_write({message: owner}, {agentId: 'pixel', sessionKey});
      const stored = written?.message ?? owner;
      const history = requestMessages(transcript);
      if (!revision) transcript.push(stored);
      leafIsMessage = true;
      const ownerIndex = revision ? -1 : transcript.length - 1;
      let ownerForModel = promptForModel;
      const request = () => ({
        runId, revision,
        messages: [
          ...history,
          ...(runtimeContext ? [{role: 'user', content: `${RUNTIME_CONTEXT_HEADER}\n${runtimeContext}`}] : []),
          {role: 'user', content: ownerForModel},
          ...requestMessages(ownerIndex === -1 ? [] : transcript.slice(ownerIndex + 1)),
        ].map(message => ({...message, content: applyInputTransforms(message.content, chat.hooks.textTransforms)})),
      });
      requests.push(request());
      if (overflow && !revision) {
        // The provider rejected that call with a context overflow. OpenClaw
        // compacts (the summary replaces older history; the owner message is
        // kept) and retries with the owner message as stored.
        const reloaded = plain(stored);
        ownerForModel = reloaded === promptForSession ? promptForModel
          : composeModelPrompt([result.prependContext, reloaded, result.appendContext]);
        requests.push(request());
      }
      if (interrupt === 'crash') return {request: requests.at(-1), stored};
      if (interrupt === 'abort') {
        transcript.push({role: 'assistant', content: [], stopReason: 'aborted'});
        leafIsMessage = false;
        chat.hooks.agent_end({}, context);
        return {request: requests.at(-1), stored};
      }
      if (toolCall) {
        const call = {type: 'toolCall', id: `call-${runId}`, name: 'write', arguments: toolCall};
        transcript.push({role: 'assistant', content: [call]});
        transcript.push({role: 'toolResult', toolCallId: call.id, content: text(`Successfully wrote ${toolCall.path}`)});
        requests.push(request());
      }
      transcript.push({role: 'assistant', content: text(answer), stopReason: 'stop'});
      return {request: requests.at(-1), stored};
    },
    end(runId) { chat.hooks.agent_end({}, {agentId: 'pixel', sessionKey, runId}); },
  };
  return chat;
}

/** Texts of the stored owner messages. */
export const storedOwnerTexts = transcript => transcript.filter(message => message.role === 'user').map(plain);
