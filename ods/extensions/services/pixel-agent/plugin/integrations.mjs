import net from 'node:net';

// Portal tools for the owner's integrations: an Obsidian vault, ntfy notifications and MCP
// servers. Their tool payloads contain no configuration token or vault root: calls
// go to the host integrations service, which checks the owner's settings and keeps "ask first"
// actions waiting for the owner's approval. Results are the owner's own data or a server's
// answer and are passed on as untrusted text.
const SOCKET = '/run/ods-pixel-integrations/control.sock';
const MAX_REPLY = 512 * 1024;
const TIMEOUT_MS = 130000;
const APPROVAL_TEXT = 'Waiting for the owner. Tell the owner this needs their approval in Settings → Portal integrations (they may also get a notification). After they approve, call this tool again with exactly the same arguments; do not change them. In a scheduled task, do not wait: say it needs approval and finish; once approved, the next run within 24 hours with the same arguments goes ahead.';

export function submitIntegration(payload, {connect = net.createConnection, platform = process.platform, signal} = {}) {
  return new Promise((resolve, reject) => {
    if (platform !== 'linux') return reject(new Error(platform === 'darwin' ? "Portal integrations aren't available on macOS yet." : 'Portal integrations are not available on this system yet.'));
    if (signal?.aborted) return reject(new Error('Cancelled.'));
    const socket = connect({path: SOCKET});
    let buffer = Buffer.alloc(0), finished = false, dispatched = false;
    const finish = (error, value) => {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      socket.destroy();
      if (error) {
        if (dispatched) error.code = 'integration-outcome-unknown';
        reject(error);
      } else resolve(value);
    };
    const unavailable = () => finish(new Error('The integrations service is not running.'));
    const timer = setTimeout(() => finish(new Error('The integration did not answer in time.')), TIMEOUT_MS);
    // Before submission cancellation means no action. Once submitted, retain
    // native admission until the service settles: closing a client socket does
    // not cancel an already accepted MCP/notes/notification operation.
    const onAbort = () => { if (!dispatched) finish(new Error('Cancelled.')); };
    signal?.addEventListener('abort', onAbort, {once: true});
    socket.on('connect', () => {
      if (finished) return;
      if (signal?.aborted) return finish(new Error('Cancelled.'));
      dispatched = true;
      socket.write(JSON.stringify({schemaVersion: 1, ...payload}) + '\n');
    });
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
    if (error?.code === 'integration-outcome-unknown') {
      return text('The integration was submitted, but its result could not be verified. Check the service or destination before trying again; do not repeat the action automatically.',
        {status: 'outcome-unknown'}, true);
    }
    return text('The integrations service is unavailable.', {status: 'unavailable'}, true);
  }
  if (!reply?.ok) return text(reply?.message || 'The integration request failed.', {status: 'refused', code: reply?.error}, true);
  if (reply.status === 'approval-required') {
    return text(`${reply.summary}\n\n${APPROVAL_TEXT}`, {status: 'approval-required', approvalId: reply.approvalId});
  }
  const failed = reply.isError === true;
  return text(render(reply), {status: failed ? 'failed' : 'done'}, failed);
}

const searchText = reply => reply.results.length
  ? reply.results.map(hit => `${hit.path} (line ${hit.line}, ${hit.matched}/${hit.of} words): ${hit.snippet}`).join('\n')
    + '\n\nRead a note (pixel_ods_notes action read) before quoting it.'
  : 'No note holds those words. Try other words.';

const MCP_CATALOG_SCOPE = 'This catalog covers only custom MCP servers. Obsidian notes use pixel_ods_notes; ntfy notifications use pixel_ods_notify. Their connection state is not reported here.';
const toolsText = reply => MCP_CATALOG_SCOPE + '\n\n' + (reply.servers.length
  ? reply.servers.map(server => `Server "${server.id}" (${server.name}):\n` + (server.tools.length
    ? server.tools.map(tool => `- ${tool.name}${tool.needsApproval ? ' [needs owner approval]' : ''}: ${tool.description || 'no description'}\n  arguments: ${JSON.stringify(tool.inputSchema ?? {type: 'object'})}`).join('\n')
    : '- no tools are allowed')).join('\n\n')
  : 'No MCP servers are turned on. The owner can add one in Settings → Portal integrations.');

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
      description: "Send a notification to the owner's phone or computer through ntfy configured in Settings → Portal integrations. This is a native notification connection, separate from custom MCP servers; it does not require an ntfy MCP server. Use when the owner asks to be notified, or at the end of a scheduled task that should tell them its result. The result confirms delivery to the configured ntfy server, not receipt on a physical device.",
      parameters: {type: 'object', additionalProperties: false, required: ['message'],
        properties: {title: {type: 'string'}, message: {type: 'string'},
          priority: {type: 'string', enum: ['low', 'default', 'high']}}},
      execute: (_id, params, signal) => run(submit, {action: 'notify', title: str(params?.title) || 'Portal',
        message: str(params?.message), priority: params?.priority ?? 'default'}, signal, () => 'Notification sent.'),
    },
    {
      name: 'pixel_ods_mcp',
      description: "Only the owner's custom MCP servers, such as Home Assistant; not the native Obsidian, ntfy or Google connections. action list: show each server id and the tools Portal may use, with their arguments; always list before the first call. action call: run one tool with server, tool and arguments. Some tools need the owner's approval first. Use pixel_ods_notes for notes and pixel_ods_notify for notifications.",
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
