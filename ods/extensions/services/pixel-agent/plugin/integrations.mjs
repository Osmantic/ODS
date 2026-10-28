import net from 'node:net';

// Portal tools for the owner's integrations: an Obsidian vault, ntfy notifications and MCP
// servers. The gateway holds no token or vault path: every call goes to the host
// integrations service, which checks what the owner turned on and keeps "ask first"
// actions waiting for the owner's approval. Results are the owner's own data or a server's
// answer and are passed on as untrusted text.
const SOCKET = '/run/ods-pixel-integrations/control.sock';
const MAX_REPLY = 512 * 1024;
const TIMEOUT_MS = 130000;
const APPROVAL_TEXT = 'Waiting for the owner. Tell the owner this needs their approval in Settings → Portal integrations (they may also get a notification). After they approve, call this tool again with exactly the same arguments; do not change them.';

export function submitIntegration(payload, {connect = net.createConnection, platform = process.platform, signal} = {}) {
  return new Promise((resolve, reject) => {
    if (platform !== 'linux') return reject(new Error('Integrations are not available on this system yet.'));
    const socket = connect({path: SOCKET});
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
    const timer = setTimeout(() => finish(new Error('The integration did not answer in time.')), TIMEOUT_MS);
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

const notesText = reply => reply.results ? searchText(reply)
  : reply.text !== undefined ? `${reply.path}:\n\n${reply.text}`
    : `${reply.mode === 'create' ? 'Created' : 'Added to'} ${reply.written}.`;

export function createIntegrationTools({submit = submitIntegration} = {}) {
  return [
    {
      name: 'pixel_ods_notes',
      description: "The owner's Obsidian notes. action search: find notes holding words (query). action read: read a note (path from a search, such as Home/Lease.md). action write: add text to a note (mode append) or create a new note (mode create), only when the owner asks to save something; the owner may need to approve it first.",
      parameters: {type: 'object', additionalProperties: false, required: ['action'],
        properties: {action: {type: 'string', enum: ['search', 'read', 'write']},
          query: {type: 'string', description: 'For search: words to look for, such as "lease deposit".'},
          path: {type: 'string', description: 'For read and write: the note path inside the vault.'},
          text: {type: 'string', description: 'For write: Markdown text to add.'},
          mode: {type: 'string', enum: ['append', 'create'], description: 'For write: append to an existing note or create a new one.'}}},
      execute: (_id, params, signal) => {
        const request = notesRequest(params);
        return request ? run(submit, request, signal, notesText)
          : Promise.resolve(text('Use action search, read or write.', {status: 'invalid'}, true));
      },
    },
    {
      name: 'pixel_ods_notify',
      description: "Send a notification to the owner's phone or computer through their ntfy notifications. Use when the owner asks to be notified, or at the end of a scheduled task that should tell them its result.",
      parameters: {type: 'object', additionalProperties: false, required: ['message'],
        properties: {title: {type: 'string'}, message: {type: 'string'},
          priority: {type: 'string', enum: ['low', 'default', 'high']}}},
      execute: (_id, params, signal) => run(submit, {action: 'notify', title: str(params?.title) || 'Portal',
        message: str(params?.message), priority: params?.priority ?? 'default'}, signal, () => 'Notification sent.'),
    },
    {
      name: 'pixel_ods_mcp',
      description: "The owner's connected MCP servers, such as Home Assistant. action list: show each server id and the tools Portal may use, with their arguments; always list before the first call. action call: run one tool with server, tool and arguments. Some tools need the owner's approval first.",
      parameters: {type: 'object', additionalProperties: false, required: ['action'],
        properties: {action: {type: 'string', enum: ['list', 'call']},
          server: {type: 'string', description: 'For call: the server id from list, such as home.'},
          tool: {type: 'string', description: 'For call: the tool name from list.'},
          arguments: {type: 'object', description: 'For call: the tool arguments as an object.'}}},
      execute: (_id, params, signal) => {
        if (params?.action === 'list') return run(submit, {action: 'mcp-tools'}, signal, toolsText);
        if (params?.action !== 'call') return Promise.resolve(text('Use action list or call.', {status: 'invalid'}, true));
        const args = params?.arguments && typeof params.arguments === 'object' && !Array.isArray(params.arguments) ? params.arguments : {};
        return run(submit, {action: 'mcp-call', server: str(params?.server), tool: str(params?.tool), arguments: args}, signal,
          reply => `${reply.isError ? 'The tool reported an error:\n' : ''}${reply.text}`);
      },
    },
  ];
}

export const INTEGRATION_TOOL_NAMES = Object.freeze(['pixel_ods_mcp', 'pixel_ods_notes', 'pixel_ods_notify']);
