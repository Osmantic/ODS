import net from 'node:net';
import { noProposalTurns, chatSession, MEMORY_CHOICES, MEMORY_QUESTION_ID, memoryQuestion } from './integration-memory.mjs';

// Portal tools for the owner's integrations: an Obsidian vault, ntfy notifications, and MCP
// servers, among them the owner's apps. The gateway holds no token or vault path: every call
// goes to the host integrations service, which checks what the owner turned on and keeps "ask first"
// actions waiting for the owner's approval. Results are the owner's own data or a server's
// answer and are passed on as untrusted text.
// Tests that load the whole plugin point this elsewhere, so a service on the test machine never answers.
const socketPath = () => process.env.ODS_PIXEL_INTEGRATIONS_SOCKET || '/run/ods-pixel-integrations/control.sock';
const MAX_REPLY = 512 * 1024;
const TIMEOUT_MS = 130000;
const MCP_USAGE = 'Give the arguments too. To see the servers and their tools: {"action": "list"}. To run a tool: '
  + '{"action": "call", "server": "<server id>", "tool": "<tool name>", "arguments": {...}}, for example '
  + '{"action": "call", "server": "photos", "tool": "search", "arguments": {"place": "Lisbon"}}.';
const APPROVAL_TEXT ='Waiting for the owner. Tell the owner this needs their approval in Settings → Portal integrations (they may also get a notification). After they approve, call this tool again with exactly the same arguments; do not change them. In a scheduled task, do not wait: say it needs approval and finish; once approved, the next run within 24 hours with the same arguments goes ahead.';

export function submitIntegration(payload, {connect = net.createConnection, platform = process.platform, signal, timeoutMs = TIMEOUT_MS} = {}) {
  return new Promise((resolve, reject) => {
    if (platform !== 'linux') return reject(new Error(platform === 'darwin' ? "Portal integrations aren't available on macOS yet." : 'Portal integrations are not available on this system yet.'));
    const socket = connect({path: socketPath()});
    let buffer = Buffer.alloc(0), finished = false;
    const finish = (error, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      socket.destroy();
      if (error) reject(error); else resolve(value);
    };
    const unavailable = () => finish(new Error('The integrations service is not running.'));
    const timer = setTimeout(() => finish(new Error('The integration did not answer in time.')), timeoutMs);
    const onAbort = () => finish(new Error('Cancelled.'));
    signal?.addEventListener('abort', onAbort, {once: true});
    socket.on('connect', () => socket.write(JSON.stringify({schemaVersion: 1, ...payload}) + '\n'));
    socket.on('error', unavailable);
    socket.on('end', () => finish(new Error('The integrations service closed the connection.')));
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > MAX_REPLY) return finish(new Error('The integration answer was too large.'));
      if (!buffer.includes(10)) return;
      try {
        finish(null, JSON.parse(buffer.toString('utf8')));
      } catch {
        finish(new Error('The integration answer was not readable.'));
      }
    });
  });
}

const text = (value, details, isError = false) => ({content: [{type: 'text', text: value}], details, ...(isError ? {isError} : {})});

async function run(submit, payload, signal, render) {
  let reply;
  try {
    reply = await submit(payload, {signal});
  } catch (error) {
    return text(error.message, {status: 'unavailable'}, true);
  }
  if (!reply?.ok) return text(reply?.message || 'The integration request failed.', {status: 'refused', code: reply?.error}, true);
  if (reply.status === 'approval-required') {
    return text(`${reply.summary}\n\n${APPROVAL_TEXT}`, {status: 'approval-required', approvalId: reply.approvalId});
  }
  return text(render(reply), {status: 'done'});
}

const searchText = reply => reply.results.length
  ? reply.results.map(hit => `${hit.path} (line ${hit.line}, ${hit.matched}/${hit.of} words): ${hit.snippet}`).join('\n')
    + '\n\nRead a note (pixel_ods_notes action read) before quoting it.'
  : 'No note holds those words. Try other words.';

const toolsText = reply => reply.servers.length
  ? reply.servers.map(server => `Server "${server.id}" (${server.name}):\n` + (server.tools.length
    ? server.tools.map(tool => `- ${tool.name}${tool.needsApproval ? ' [needs owner approval]' : ''}: ${tool.description || 'no description'}\n  arguments: ${JSON.stringify(tool.inputSchema?.properties ?? {})}`).join('\n')
    : '- no tools are allowed')).join('\n\n')
  : 'No MCP servers are turned on. The owner can add one in Settings → Portal integrations.';

const str = value => (typeof value === 'string' ? value : '');

function notesRequest(params) {
  const action = params?.action;
  if (action === 'search') return {action: 'notes-search', query: str(params?.query)};
  if (action === 'read') return {action: 'notes-read', path: str(params?.path)};
  if (action === 'write') return {action: 'notes-write', path: str(params?.path), text: str(params?.text),
    mode: params?.mode === 'create' ? 'create' : 'append'};
  return null;
}

// Portal proposes a fact about the owner; unless the owner chose to save without asking, the
// chat shows a card with the choices below Portal's reply (see integration-memory.mjs). The reply
// itself still comes: told to wait, the small model never answered a question the owner asked
// (e2e: "Suggest one dinner..." ended with no answer after a proposal).
async function remember(submit, params, sessionKey, signal, shared) {
  const session = chatSession(sessionKey);
  if (!session) {
    // Only the owner's own chat can show the card and holds the "don't remember" mark: a
    // delegated helper or a scheduled task saves nothing.
    return text('Nothing was saved: Portal remembers only what the owner says in their own Portal chat.', {status: 'done'});
  }
  const blocked = noProposalTurns(shared).get(session);
  if (blocked === 'answered') {
    return text('The owner has just answered your memory question; this reply only acknowledges it. Save nothing else now.', {status: 'done'});
  }
  if (blocked === 'reminder') {
    // Pointing at the right call, so the turn still schedules what the owner asked for.
    return text('Nothing was saved: the owner asked for a reminder, not a memory. Schedule it with one call: '
      + 'tool_call with id "openclaw:pixel-ods:pixel_ods_notify" as described in the note on the owner\'s message.', {status: 'done'});
  }
  let reply;
  try {
    reply = await submit({action: 'memory-propose', text: str(params?.text), session}, {signal});
  } catch (error) {
    return text(error.message, {status: 'unavailable'}, true);
  }
  if (!reply?.ok) return text(reply?.message || 'The memory was not saved.', {status: 'refused', code: reply?.error}, true);
  if (reply.status === 'known') return text(`Already remembered: ${reply.text}`, {status: 'done'});
  if (reply.status === 'saved') return text(`Remembered: ${reply.text}`, {status: 'done'});
  if (blocked === 'card') {
    // A reply shows one card; the next fact waits in Settings.
    return text(`Also waiting for the owner: "${reply.text}". They can decide in Settings → Portal integrations → Memory; mention that, not a card.`,
      {status: 'done'});
  }
  noProposalTurns(shared).set(session, 'card');
  const questions = [{id: MEMORY_QUESTION_ID, question: memoryQuestion(reply.text), options: Object.keys(MEMORY_CHOICES)}];
  return text(`${questions[0].question}\n\nThe owner decides on a card shown below your reply; never answer it for them. Now answer the owner's message, using other tools only if it needs them.`,
    {status: 'awaiting_user', questions});
}

const notesText = reply => reply.results ? searchText(reply)
  : reply.text !== undefined ? `${reply.path}:\n\n${reply.text}`
    : `${reply.mode === 'create' ? 'Created' : 'Added to'} ${reply.written}.`;

// A reminder: the service checks it and works out when it is due, then the gateway's scheduler
// gets a one-off job for it; the service sends it through ntfy when that job starts.
async function remind(submit, scheduleReminder, params, signal) {
  let reply;
  try {
    reply = await submit({action: 'reminder-plan', title: str(params?.title), message: str(params?.message),
      inMinutes: params?.inMinutes, at: params?.at === undefined ? undefined : str(params.at)}, {signal});
  } catch (error) {
    return text(error.message, {status: 'unavailable'}, true);
  }
  if (!reply?.ok) return text(reply?.message || 'The reminder could not be set.', {status: 'refused', code: reply?.error}, true);
  try {
    await scheduleReminder(reply.plan);
  } catch (error) {
    return text(`The reminder was not set: ${error.message}`, {status: 'unavailable'}, true);
  }
  return text(`Reminder set for ${reply.plan.when}: "${reply.plan.text}". It will reach the owner's phone then; nothing else is needed.`,
    {status: 'done', reminderAt: reply.plan.atMs});
}

const noScheduler = () => Promise.reject(new Error('Reminders are not available here.'));

// isDigestRun(sessionKey): whether this session is a run of the daily news digest
// (integration-tasks.mjs). That run reads feeds anyone can write, with nobody watching: it may
// read the digest and nothing else, so a story cannot steer it to the owner's other apps.
const DIGEST_ONLY = 'The daily news digest run may only read the digest: tool_call with id "pixel_ods_mcp" and args '
  + '{"action": "call", "server": "news", "tool": "digest", "arguments": {}}.';
const notDigestRun = async () => false;

export function createIntegrationTools({submit = submitIntegration, scheduleReminder = noScheduler, sessionKey = '', shared = globalThis,
  isDigestRun = notDigestRun} = {}) {
  return [
    {
      name: 'pixel_ods_notes',
      description: "The owner's Obsidian notes, and Portal's memory of the owner. action search: find notes holding words (query). action read: read a note (path from a search, such as Home/Lease.md). action write: add text to a note (mode append) or create a new note (mode create), only when the owner asks to save something; the owner may need to approve it first. action remember: keep a lasting fact about the owner or a preference they stated (text, one short sentence such as \"The owner is vegetarian.\"); the owner confirms it in the chat.",
      parameters: {type: 'object', additionalProperties: false, required: ['action'],
        properties: {action: {type: 'string', enum: ['search', 'read', 'write', 'remember']},
          query: {type: 'string', description: 'For search: words to look for, such as "lease deposit".'},
          path: {type: 'string', description: 'For read and write: the note path inside the vault.'},
          text: {type: 'string', description: 'For write: Markdown text to add. For remember: the fact, one short sentence.'},
          mode: {type: 'string', enum: ['append', 'create'], description: 'For write: append to an existing note or create a new one.'}}},
      execute: (_id, params, signal) => {
        if (params?.action === 'remember') return remember(submit, params, sessionKey, signal, shared);
        const request = notesRequest(params);
        return request ? run(submit, request, signal, notesText)
          : Promise.resolve(text('Use action search, read, write or remember.', {status: 'invalid'}, true));
      },
    },
    {
      name: 'pixel_ods_notify',
      description: "Send a notification to the owner's phone or computer through their ntfy notifications, now or later. "
        + "Use when the owner asks to be notified, at the end of a scheduled task that should tell them its result, "
        + "and for reminders: to remind the owner later (\"remind me in 10 minutes to stretch\"), give inMinutes or at "
        + "and Portal schedules it; it reaches their phone at that time. Never wait with sleep or a background command, "
        + "since nothing of this turn keeps running.",
      parameters: {type: 'object', additionalProperties: false, required: ['message'],
        properties: {title: {type: 'string'}, message: {type: 'string', description: 'The text the owner gets, such as "Stretch".'},
          priority: {type: 'string', enum: ['low', 'default', 'high']},
          inMinutes: {type: 'number', description: 'For a reminder: send it this many minutes from now, such as 10.'},
          at: {type: 'string', description: 'For a reminder: send it at this local time, such as 17:30, or date and time, such as 2026-11-04T09:00.'}}},
      execute: (_id, params, signal) => {
        // A small model fills every field: null or "" is no reminder time (the service reads them so too).
        const given = value => value !== undefined && value !== null && value !== '';
        if (given(params?.inMinutes) || given(params?.at)) return remind(submit, scheduleReminder, params, signal);
        return run(submit, {action: 'notify', title: str(params?.title) || 'Portal',
          message: str(params?.message), priority: params?.priority ?? 'default'}, signal, () => 'Notification sent.');
      },
    },
    {
      name: 'pixel_ods_mcp',
      description: "The owner's apps and connected MCP servers: documents (Paperless-ngx), photos (Immich), watched web pages and price alerts, news feeds, Home Assistant and others. action list: show each server id and the tools Portal may use, with their arguments; always list before the first call. action call: run one tool with server, tool and arguments. Some tools need the owner's approval first.",
      parameters: {type: 'object', additionalProperties: false, required: ['action'],
        properties: {action: {type: 'string', enum: ['list', 'call']},
          server: {type: 'string', description: 'For call: the server id from list, such as home.'},
          tool: {type: 'string', description: 'For call: the tool name from list.'},
          arguments: {type: 'object', description: 'For call: the tool arguments as an object.'}}},
      execute: async (_id, params, signal) => {
        if (await isDigestRun(sessionKey) && !(params?.action === 'call' && params?.server === 'news' && params?.tool === 'digest')) {
          return text(DIGEST_ONLY, {status: 'refused', code: 'digest-only'}, true);
        }
        if (params?.action === 'list') return run(submit, {action: 'mcp-tools'}, signal, toolsText);
        // A small model sometimes sends no arguments at all; show it the exact shape to retry with.
        if (params?.action !== 'call') return Promise.resolve(text(MCP_USAGE, {status: 'invalid'}, true));
        const args = params?.arguments && typeof params.arguments === 'object' && !Array.isArray(params.arguments) ? params.arguments : {};
        // The digest takes no arguments; in its run, none reach the service.
        if (await isDigestRun(sessionKey)) return run(submit, {action: 'mcp-call', server: 'news', tool: 'digest', arguments: {}}, signal,
          reply => `${reply.isError ? 'The tool reported an error:\n' : ''}${reply.text}`);
        return run(submit, {action: 'mcp-call', server: str(params?.server), tool: str(params?.tool), arguments: args}, signal,
          reply => `${reply.isError ? 'The tool reported an error:\n' : ''}${reply.text}`);
      },
    },
  ];
}

export const INTEGRATION_TOOL_NAMES = Object.freeze(['pixel_ods_mcp', 'pixel_ods_notes', 'pixel_ods_notify']);
