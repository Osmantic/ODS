// A model reply that reaches its output-token limit ends with stopReason
// 'length'. The pinned harness runs a reply's tool calls only after a
// 'toolUse' stop, so a call the limit cut off never runs and the response
// ends there. Before this the owner saw only "No detailed failure reason was
// returned" (fleet 2026-10-18: an API model wrote a 17,662-token game file in
// one write call against an 8,192-token limit).
//
// The guard names the limit to the owner and tells the next turn in that chat
// how to write the file in smaller parts. Pure functions.
import { assistantMessageText } from './progress-finalization.mjs';

const DISCORD = 'https://discord.gg/4ntNp9MAwC';
const MAX_PATH_CHARS = 200;
const PATH_KEYS = ['path', 'file_path', 'filePath'];
const TOOL_NAME = /^[A-Za-z0-9_.:-]{1,64}$/;

function boundedPath(value) {
  if (typeof value !== 'string') return undefined;
  const text = value.replace(/[\u0000-\u001f\u007f`]/g, '').trim();
  return text ? text.slice(0, MAX_PATH_CHARS) : undefined;
}

function callPath(args) {
  if (!args || typeof args !== 'object') return undefined;
  for (const key of PATH_KEYS) {
    const path = boundedPath(args[key]);
    if (path) return path;
  }
  return undefined;
}

// How one assistant message ended at the limit, or undefined when it ended
// any other way. Only the last tool call can be the one cut off.
export function outputLimitCut(message) {
  if (message?.role !== 'assistant' || String(message.stopReason ?? '').trim().toLowerCase() !== 'length') {
    return undefined;
  }
  const calls = Array.isArray(message.content) ? message.content.filter(block => block?.type === 'toolCall') : [];
  const call = calls.at(-1);
  const tokens = message.usage?.output;
  return {
    tokens: Number.isSafeInteger(tokens) && tokens > 0 ? tokens : undefined,
    // Model-supplied names reach owner markdown only as plain identifiers.
    tool: call ? (TOOL_NAME.test(call.name ?? '') ? call.name : 'tool') : undefined,
    path: call ? callPath(call.arguments) : undefined,
    answered: !call && Boolean(assistantMessageText(message)),
  };
}

function limitPhrase(cut) {
  return cut.tokens ? `its ${cut.tokens.toLocaleString('en-US')}-token output limit` : 'its output limit';
}

function remoteAdvice(remote) {
  return remote ? 'If your API allows longer replies, raise Max output tokens in Settings → Remote model → Advanced. ' : '';
}

// Owner copy for a response that ended on a cut-off tool call or before any
// answer. A cut-off text answer keeps its text (outputLimitNotice).
export function outputLimitStopText(cut, {remote = false} = {}) {
  const help = `If it keeps happening, [get help on Discord](${DISCORD}).`;
  if (!cut.tool) {
    return `**Portal stopped: the model used ${limitPhrase(cut)} before it wrote an answer.** ` +
      'Send "continue" to try again. ' + remoteAdvice(remote) + help;
  }
  const target = cut.path ? ` while writing \`${cut.path}\`` : ` in the middle of a \`${cut.tool}\` call`;
  return `**Portal stopped: the model reached ${limitPhrase(cut)}${target}.** ` +
    (cut.path ? `That call did not run, so it did not change \`${cut.path}\`. ` : 'That call did not run. ') +
    'Send "continue" and Portal will write it in smaller parts. ' + remoteAdvice(remote) + help;
}

// One line after a cut-off text answer, which is delivered as it is.
export function outputLimitNotice(cut, {remote = false} = {}) {
  return (`This reply was cut off at the model's ${cut.tokens ? `${cut.tokens.toLocaleString('en-US')}-token ` : ''}` +
    'output limit. Send "continue" for the rest. ' + remoteAdvice(remote)).trim();
}

// Model-only context for the chat's next turn after a cut-off tool call.
export function outputLimitContext(cut) {
  const limit = cut.tokens ? `the model's ${cut.tokens}-token output limit` : "the model's output limit";
  const target = cut.path ? ` while writing ${JSON.stringify(cut.path)}` : ` in the middle of a ${cut.tool} call`;
  const lines = cut.tokens ? ` (about ${Math.max(50, Math.round(cut.tokens / 200) * 10)} lines of code)` : '';
  return `[ODS Pixel output limit] Your previous reply reached ${limit}${target}, so that call never ran ` +
    'and changed nothing. If the owner still wants that work, do not send it as one call again: split the program ' +
    'into several files, or write a short first version and add the rest with edit calls. Keep each call under ' +
    `half of that limit${lines}.`;
}
