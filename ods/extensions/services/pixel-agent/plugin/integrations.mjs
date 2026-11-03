import net from 'node:net';

// Portal tools for the owner's integrations: Obsidian, ntfy, configured apps and MCP
// servers. Tool payloads contain no configuration token or vault root: calls go to
// the host service, which checks the owner's settings and keeps "ask first"
// actions waiting for the owner's approval. Results are the owner's own data or a server's
// answer and are passed on as untrusted text.
const SOCKET = '/run/ods-pixel-integrations/control.sock';
const MAX_REPLY = 512 * 1024;
const TIMEOUT_MS = 130000;
const freezeRole = value => {
  if (value && typeof value === 'object') {for (const child of Object.values(value)) freezeRole(child);Object.freeze(value);}
  return value;
};
// Private native policy, not an owner-intent classifier, availability report or
// permission grant. Normal model-facing tool definitions below stay unchanged.
export const NATIVE_REVIEW_ROLE = freezeRole({version:1,scope:'native-review-policy-not-owner-intent',
  limits:{distinctCanonicalOperations:4,askProposalFamilies:1},operations:[
  {canonicalOperation:'integration:catalog',action:'mcp-tools',maximumEffect:'catalog-metadata',
    role:'List saved integration tool metadata only; never start a custom server or read private source data.',
    constraints:{metadataOnly:true,customProgramStart:false}},
  {canonicalOperation:'obsidian:search',action:'notes-search',maximumEffect:'private-read',arguments:{query:'string'},
    role:'Search bounded names/content in the configured Obsidian vault; no file changes.',constraints:{queryMaxCodePoints:300}},
  {canonicalOperation:'obsidian:read',action:'notes-read',maximumEffect:'private-read',arguments:{path:'note-path'},
    role:'Read bounded contents of one Markdown note inside the configured Obsidian vault; no file changes.',constraints:{pathMaxCodePoints:512}},
  {canonicalOperation:'obsidian:append',action:'notes-write',mode:'append',maximumEffect:'approval-proposal',requiresCurrentAsk:true,
    arguments:{path:'note-path',text:'string',mode:'append'},role:'Only prepare or reuse an exact Ask request to append. Never write the note, consume approval or notify; Allowed/Off refuse.',
    constraints:{pathMaxCodePoints:512,textMaxCodePoints:20000,defaultMode:'append',preserveFullText:true}},
  {canonicalOperation:'obsidian:create',action:'notes-write',mode:'create',maximumEffect:'approval-proposal',requiresCurrentAsk:true,
    arguments:{path:'note-path',text:'string',mode:'create'},role:'Only prepare or reuse an exact Ask request to create a note. Never create it, consume approval or notify; Allowed/Off refuse.',
    constraints:{pathMaxCodePoints:512,textMaxCodePoints:20000,preserveFullText:true}},
  {canonicalOperation:'paperless:search',action:'mcp-call',server:'app:documents',tool:'search',maximumEffect:'private-read',arguments:{query:'string'},
    role:'Search bounded existing documents in the configured Paperless library; no document changes.',constraints:{normalizedQueryMaxCodePoints:200}},
  {canonicalOperation:'paperless:read',action:'mcp-call',server:'app:documents',tool:'read',maximumEffect:'private-read',arguments:{id:'document'},
    role:'Read bounded text of the selected Paperless document; no document changes.',constraints:{id:'positive safe integer or canonical positive decimal string <= 9223372036854775807'}},
  {canonicalOperation:'immich:search',action:'mcp-call',server:'app:photos',tool:'search',maximumEffect:'private-read',arguments:{query:'string',place:'string',after:'string',before:'string'},
    role:'Search configured Immich photos with the supplied query/place/date restrictions; no photo changes or query fallback.',constraints:{normalizedQueryMaxCodePoints:200,normalizedPlaceMaxCodePoints:100,hostValidatesDates:true}},
  {canonicalOperation:'watch:list',action:'mcp-call',server:'app:watch',tool:'list',maximumEffect:'private-read',arguments:{},
    role:'List configured webpage monitors; do not create or remove any monitor.',constraints:{readOnly:true}},
  {canonicalOperation:'watch:add',action:'mcp-call',server:'app:watch',tool:'add',maximumEffect:'approval-proposal',requiresCurrentAsk:true,arguments:{url:'string',title:'string',below:'number'},
    role:'Only prepare or reuse an exact Ask request for a webpage/price monitor. Never create the monitor, consume approval or notify; Allowed/Off refuse.',constraints:{strippedTitleMaxCodePoints:200,hostValidatesUrlAndPrice:true}},
  {canonicalOperation:'watch:remove',action:'mcp-call',server:'app:watch',tool:'remove',maximumEffect:'approval-proposal',requiresCurrentAsk:true,arguments:{id:'string'},
    role:'Only prepare or reuse an exact Ask request to remove the selected monitor. Never remove it, consume approval or notify; Allowed/Off refuse.',constraints:{preserveExactId:true}},
  {canonicalOperation:'freshrss:unread',action:'mcp-call',server:'app:news',tool:'unread',maximumEffect:'private-read',arguments:{},
    role:'Read bounded unread FreshRSS stories for this response; do not mark read or send a notification.',constraints:{delivery:false}},
  {canonicalOperation:'freshrss:digest_read',action:'mcp-call',server:'app:news',tool:'digest',maximumEffect:'private-read',arguments:{},
    role:'Read a bounded FreshRSS digest for this response; do not mark read, notify or arm later delivery.',constraints:{delivery:false}},
].map(entry=>({...entry,adapter:entry.action.startsWith('notes-')?'pixel_ods_notes':'pixel_ods_mcp',
  adapterAction:entry.action.startsWith('notes-')?entry.action.slice('notes-'.length):entry.action==='mcp-tools'?'list':'call',
  ...(entry.maximumEffect==='approval-proposal'?{adapterPreparationAction:'prepare'}:{})}))});
export function nativeReviewOperation(payload) {
  const action=payload?.action==='notes-prepare'?'notes-write':payload?.action==='mcp-prepare'?'mcp-call':payload?.action;
  const operation=NATIVE_REVIEW_ROLE.operations.find(entry=>entry.action===action
    && (entry.action!=='notes-write'||entry.mode===payload.mode)
    && (entry.action!=='mcp-call'||entry.server===payload.server&&entry.tool===payload.tool));
  return payload?.action==='mcp-prepare' && operation?.maximumEffect!=='approval-proposal'?undefined:operation;
}
const REVIEW_READS = new Set(NATIVE_REVIEW_ROLE.operations.filter(entry=>entry.action!=='mcp-call'
  &&entry.maximumEffect!=='approval-proposal').map(entry=>entry.action));
const REVIEW_APP_READS = Object.fromEntries([...new Set(NATIVE_REVIEW_ROLE.operations.filter(entry=>entry.action==='mcp-call')
  .map(entry=>entry.server))].map(server=>[server,NATIVE_REVIEW_ROLE.operations.filter(entry=>entry.server===server
    &&entry.maximumEffect==='private-read').map(entry=>entry.tool)]));
const MCP_USAGE = 'Give the arguments too. To see the servers and their tools: {"action": "list"}. To run a tool: '
  + '{"action": "call", "server": "<server id>", "tool": "<tool name>", "arguments": {...}}, for example '
  + '{"action": "call", "server": "app:photos", "tool": "search", "arguments": {"place": "Lisbon"}}.';
const APPROVAL_TEXT = 'This call created or found a pending request; the action has not run. Tell the owner it needs approval in Settings → Portal integrations (they may also get a notification). After they approve, call this tool again with exactly the same arguments so the service can check and use the approval; do not just repeat this notice. Approval is for one use. For any later requested action, including an identical one, call the tool again to submit that new request. In a scheduled task, do not wait: report this returned status and finish; a later run within 24 hours can check the approval with exactly the same arguments.';
export function submitIntegration(payload, {connect = net.createConnection, platform = process.platform, signal, admitted} = {}) {
  return new Promise((resolve, reject) => {
    if (platform !== 'linux' && platform !== 'darwin') return reject(new Error('Portal integrations are not available on this system yet.'));
    if (signal?.aborted) return reject(new Error('Cancelled.'));
    const currentAdmission = () => {
      try { return admitted === undefined || typeof admitted === 'function' && admitted() === true; }
      catch { return false; }
    };
    if (!currentAdmission()) return reject(Object.assign(new Error('Native admission is no longer current.'), {code:'integration-call-unbound'}));
    const socket = connect({path: platform === 'darwin' ? '/private/var/lib/ods-pixel-integrations/control.sock' : SOCKET});
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
      if (!currentAdmission()) return finish(Object.assign(new Error('Native admission is no longer current.'), {code:'integration-call-unbound'}));
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

async function run(submit, payload, signal, render, observed, admitted) {
  const report = (status, references, code, completion) => { try { return observed?.(status, references, code, completion); } catch { return false; } };
  const reviewRead = payload.recoveryReviewOnly === true && (REVIEW_READS.has(payload.action) || payload.action==='mcp-call'
    && Object.hasOwn(REVIEW_APP_READS,payload.server) && REVIEW_APP_READS[payload.server].includes(payload.tool));
  let reply;
  try {
    reply = await submit(payload, {signal, ...(admitted ? {admitted} : {})});
  } catch (error) {
    if (error?.code === 'integration-call-unbound') {
      report('refused',undefined,'integration-call-unbound');
      return text('No integration action was submitted: its native admission is no longer current.',
        {status:'blocked',code:'integration-call-unbound'},true);
    }
    if (error?.code === 'integration-outcome-unknown') {
      report('unknown');
      return text('The integration was submitted, but its result could not be verified. Check the service or destination before trying again; do not repeat the action automatically.',
        {status: 'outcome-unknown'}, true);
    }
    return text('The integrations service is unavailable.', {status: 'unavailable'}, true);
  }
  // Host-owned typed references are retained only by the exact reservation.
  // They never enter render(), tool details, model text or ordinary callers.
  const references = reply?.reviewReferences;
  if (reply && typeof reply === 'object') {
    reply = {...reply}; delete reply.reviewReferences; delete reply.recoveryReferenceContext;
  }
  if (reviewRead && (!reply || typeof reply !== 'object' || typeof reply.ok !== 'boolean')) {
    report('unknown');
    return text('The integration read result could not be verified. No approval request or action completion was confirmed.',
      {status:'outcome-unknown',code:'review-only-read-result-invalid'},true);
  }
  if (!reply?.ok) {
    if (payload.recoveryReviewOnly === true && ['invalid-review-reference','stale-review-reference'].includes(reply?.error)) report('refused',undefined,reply.error);
    if (reply?.ok === false && ['notification-outcome-unknown', 'mcp-outcome-unknown', 'app-outcome-unknown'].includes(reply.error)) {
      report('unknown');
      return text('Its result could not be verified; check the destination before retrying. Do not repeat the action automatically.',
        {status:'outcome-unknown', code:reply.error}, true);
    }
    const scope = reply?.error === 'notes-off' && payload.action?.startsWith('notes-')
      ? ' This result concerns only Obsidian notes, not the Paperless document connection. Paperless documents use pixel_ods_mcp action list. Respect the owner\'s requested source.' : '';
    if (reviewRead && reply?.ok === false) report('refused');
    return text((reply?.message || 'The integration request failed.') + scope, {status: 'refused', code: reply?.error}, true);
  }
  if (reviewRead) {
    const valid = reply.recoveryReviewOnly === true && reply.isError !== true
      && !['approval-required','review-only-ready'].includes(reply.status)
      && (payload.action === 'notes-read' ? typeof reply.path === 'string' && typeof reply.text === 'string'
        : payload.action === 'notes-search' ? Array.isArray(reply.results)
          : payload.action === 'mcp-call' ? typeof reply.text==='string' : Array.isArray(reply.servers));
    if (valid) {
      try {
        const rendered = render(reply);
        const contentRead=payload.action==='notes-read'||payload.action==='mcp-call'&&payload.server==='app:documents'&&payload.tool==='read';
        if (report('settled', references,undefined,contentRead?{readCompleted:true}:undefined)===false) return text('The read returned no verified reference for another private call. Automatic review stopped.',
          {status:'outcome-unknown',code:'review-reference-result-invalid'},true);
        return text(rendered, {status:'done'});
      } catch { /* A malformed read reply cannot claim a settled result. */ }
    }
    report('unknown');
    return text('The integration read result could not be verified. No approval request or action completion was confirmed.',
      {status:'outcome-unknown',code:'review-only-read-result-invalid'},true);
  }
  const approvalIdValid = typeof reply.approvalId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(reply.approvalId);
  if (payload.action==='notes-prepare'||payload.action==='mcp-prepare') {
    if (reply.preparationOnly!==true || reply.actionExecuted!==false || reply.approvalConsumed!==false || !approvalIdValid
        || !['approval-required','approval-ready'].includes(reply.status)
        || payload.recoveryReviewOnly===true && reply.recoveryReviewOnly!==true) {
      report('unknown');
      return text('The preparation receipt could not be verified. No action completion or approval consumption was confirmed.',
        {status:'outcome-unknown',code:'preparation-result-invalid'},true);
    }
    report(reply.status==='approval-ready'?'ready':'pending');
    return text(reply.status==='approval-ready'
      ? 'This exact action already has owner approval. Preparation did not run it or use the approval. Only a separate owner-requested normal write/call with exactly the approved arguments can execute it.'
      : `${reply.summary}\n\nApproval was prepared only: no action ran, no approval was used and no notification was sent. Review Settings → Portal integrations. After approval, a separate owner-requested normal write/call with exactly the same arguments lets the service check the approval.`,
      {status:reply.status,approvalId:reply.approvalId,preparationOnly:true,actionExecuted:false,approvalConsumed:false});
  }
  const reviewedNoEffect = payload.recoveryReviewOnly === true && reply.recoveryReviewOnly === true &&
    reply.actionExecuted === false && reply.approvalConsumed === false && approvalIdValid;
  if (reply.status === 'review-only-ready') {
    if (!reviewedNoEffect) return text('The review-only action status could not be verified. No additional action was submitted.',
      {status:'refused',code:'review-only-result-invalid'},true);
    report('ready');
    return text('This exact action already has owner approval, but this review-only recovery did not run it or use the approval. Send a new request to execute it.',
      {status:'review-only-ready',approvalId:reply.approvalId});
  }
  if (reply.status === 'approval-required') {
    if (payload.recoveryReviewOnly === true) {
      if (!reviewedNoEffect) return text('The review-only action status could not be verified. No additional action was submitted.',
        {status:'refused',code:'review-only-result-invalid'},true);
      report('pending');
      return text('This review-only recovery requested approval; no action ran and no approval was used. Review Settings → Portal integrations, then send a new request for this exact action.',
        {status:'approval-required',approvalId:reply.approvalId});
    }
    if (reply.ok === true && approvalIdValid) report('pending');
    return text(`${reply.summary}\n\n${APPROVAL_TEXT}`, {status: 'approval-required', approvalId: reply.approvalId});
  }
  const failed = reply.isError === true;
  if (reply.ok === true && !failed && (
    payload.action === 'notify' && reply.sent === true ||
    payload.action === 'notes-write' && typeof reply.written === 'string' && reply.mode === payload.mode ||
    payload.action === 'mcp-call' && typeof reply.text === 'string')) report('settled', references);
  return text(render(reply), {status: failed ? 'failed' : 'done'}, failed);
}

const searchText = reply => reply.results.length
  ? reply.results.map(hit => `${hit.path} (line ${hit.line}, ${hit.matched}/${hit.of} words): ${hit.snippet}`).join('\n')
    + '\n\nRead a note (pixel_ods_notes action read) before quoting it.'
  : 'No note holds those words. Try other words.';

const MCP_CATALOG_SCOPE = 'This catalog covers configured Documents, Photos, Watched pages and News apps, plus custom MCP servers. Obsidian notes use pixel_ods_notes; ntfy notifications use pixel_ods_notify; Google uses the connected-app tools. Their connection state is not reported here.';
const toolsText = reply => MCP_CATALOG_SCOPE + '\n\n' + (reply.servers.length
  ? reply.servers.map(server => `Server "${server.id}" (${server.name}):\n` + (server.tools.length
    ? server.tools.map(tool => `- ${tool.name}${tool.needsApproval ? ' [needs owner approval]' : ''}: ${tool.description || 'no description'}\n  arguments: ${JSON.stringify(tool.inputSchema ?? {type: 'object'})}`).join('\n')
    : '- no tools are allowed')).join('\n\n')
  : 'No apps or custom MCP servers are turned on. The owner can configure them in Settings → Portal integrations.');

const str = value => (typeof value === 'string' ? value : '');

// A private bounded JSON snapshot is used for both admission identity and the
// bytes later submitted after the asynchronous Unix socket connects.
export function snapshotIntegrationRequest(payload) {
  let nodes = 0, bytes = 0;
  const textBound = value => {
    bytes += Buffer.byteLength(value);
    if (bytes > 65536) throw new Error('request-bound');
    return value;
  };
  function copy(value, depth = 0) {
    if (++nodes > 4096 || depth > 20) throw new Error('request-bound');
    if (typeof value === 'string') return textBound(value);
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (Array.isArray(value)) return value.map(item => copy(item, depth + 1));
    if (value && typeof value === 'object' && [Object.prototype, null].includes(Object.getPrototypeOf(value))) {
      const keys = Object.keys(value);
      if (keys.length > 4096) throw new Error('request-bound');
      return Object.fromEntries(keys.sort().map(key => [textBound(key), copy(value[key], depth + 1)]));
    }
    throw new Error('request-bound');
  }
  const snapshot = copy(payload);
  if (Buffer.byteLength(JSON.stringify(snapshot)) > 65536) throw new Error('request-bound');
  return snapshot;
}

function notesRequest(params) {
  const action = params?.action;
  if (action === 'search') return {action: 'notes-search', query: str(params?.query)};
  if (action === 'read') return {action: 'notes-read', path: str(params?.path)};
  if (action === 'write') return {action: 'notes-write', path: str(params?.path), text: str(params?.text),
    mode: params?.mode === 'create' ? 'create' : 'append'};
  if (action === 'prepare') {
    if (Object.keys(params).some(key=>!['action','path','text','mode'].includes(key))
        || typeof params.path!=='string' || typeof params.text!=='string'
        || params.mode!==undefined&&!['append','create'].includes(params.mode)) return null;
    return {action:'notes-prepare',path:params.path,text:params.text,mode:params.mode??'append'};
  }
  return null;
}

const notesText = reply => reply.results ? searchText(reply)
  : reply.text !== undefined ? `${reply.path}:\n\n${reply.text}`
    : `${reply.mode === 'create' ? 'Created' : 'Added to'} ${reply.written}.`;

export function integrationRequest(name, params, context) {
  if (name === 'pixel_ods_notes') return notesRequest(params);
  if (name === 'pixel_ods_notify') return {action: 'notify', title: str(params?.title) || 'Portal',
    message: str(params?.message), priority: params?.priority ?? 'default'};
  if (name !== 'pixel_ods_mcp') return null;
  if (params?.action === 'list') return {action: 'mcp-tools'};
  if (params?.action !== 'call' && params?.action !== 'prepare') return null;
  if (params.action==='prepare' && (Object.keys(params).some(key=>!['action','server','tool','arguments'].includes(key))
      || typeof params.server!=='string' || typeof params.tool!=='string'
      || params.arguments!==undefined && (!params.arguments || typeof params.arguments!=='object' || Array.isArray(params.arguments)))) return null;
  const args = params?.arguments && typeof params.arguments === 'object' && !Array.isArray(params.arguments) ? params.arguments : {};
  const server = str(params?.server), tool = str(params?.tool);
  const identityString = value => typeof value === 'string' && value.length > 0
    && value.length <= 512 && !/[\x00-\x20\x7f]/.test(value);
  const runContext = context?.agentId === 'pixel' && identityString(context.sessionId)
    && identityString(context.sessionKey) && context.sessionKey.startsWith('agent:pixel:')
    ? {sessionId: context.sessionId, sessionKey: context.sessionKey} : undefined;
  return {action: params.action==='prepare'?'mcp-prepare':'mcp-call', server, tool, arguments: args,
    ...(params.action==='call' && server === 'app:news' && tool === 'digest' && runContext ? {runContext} : {})};
}

// This is a permission-reducing check for the latched review path only. Keep
// the raw leaf parameters as well as the host payload: normalization must not
// turn a later invalid mode or ignored restriction into an admitted append.
export function reviewIntegrationCandidate(name, params, context) {
  if (!params || typeof params !== 'object' || Array.isArray(params)) throw new Error('invalid-review-arguments');
  const ownKeys = allowed => {
    if (Object.keys(params).some(key => !allowed.includes(key))) throw new Error('invalid-review-arguments');
  };
  let maximumEffect;
  if (name === 'pixel_ods_notes') {
    const operation=nativeReviewOperation({action:'notes-'+params.action,mode:params.mode??'append'});
    if (!operation) throw new Error('invalid-review-operation');
    if (params.action === 'search') {ownKeys(['action','query']); if (typeof params.query !== 'string' || !params.query.trim()
      || [...params.query].length>operation.constraints.queryMaxCodePoints) throw new Error('invalid-review-arguments');}
    else if (params.action === 'read') {ownKeys(['action','path']); if (!canonicalReviewNotePath(params.path)) throw new Error('invalid-review-arguments');}
    else if (params.action === 'write' || params.action === 'prepare') {
      ownKeys(['action','path','text','mode']);
      if (!canonicalReviewNotePath(params.path) || typeof params.text !== 'string' || !params.text.trim()
          || [...params.text].length > operation.constraints.textMaxCodePoints
          || params.mode !== undefined && !['append','create'].includes(params.mode)) throw new Error('invalid-review-arguments');
    } else throw new Error('invalid-review-arguments');
    maximumEffect = operation.maximumEffect;
  } else if (name === 'pixel_ods_mcp') {
    if (params.action === 'list') {ownKeys(['action']); maximumEffect = nativeReviewOperation({action:'mcp-tools'}).maximumEffect;}
    else if (params.action === 'call' || params.action === 'prepare') {
      ownKeys(['action','server','tool','arguments']);
      const operation = nativeReviewOperation({action:'mcp-call',server:params.server,tool:params.tool});
      if (params.action==='prepare' && operation?.maximumEffect!=='approval-proposal') throw new Error('invalid-review-operation');
      const schema = operation?.arguments;
      const args = params.arguments ?? {};
      if (!schema || !args || typeof args !== 'object' || Array.isArray(args) || Object.keys(args).some(key => !Object.hasOwn(schema,key))) throw new Error('invalid-review-arguments');
      for (const [key,value] of Object.entries(args)) {
        if (schema[key] === 'document' ? !canonicalReviewDocumentId(value)
          : schema[key] === 'number' ? typeof value !== 'number' || !Number.isFinite(value)
            : typeof value !== 'string') throw new Error('invalid-review-arguments');
      }
      // Match the host's existing truncating fields before the native view is
      // constructed. Refuse a lost restriction; never shorten it on the model's
      // behalf. Python str.split() also treats the four ASCII separators as
      // whitespace, unlike JavaScript's \s.
      const whitespace=/[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+/u;
      const words=value=>value.split(whitespace).filter(Boolean).join(' ');
      const stripped=value=>value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'');
      const bounded=(value,maximum)=>value===undefined || [...words(value)].length<=maximum;
      if (params.server==='app:documents' && params.tool==='search' && !bounded(args.query,operation.constraints.normalizedQueryMaxCodePoints)
        || params.server==='app:photos' && (!bounded(args.query,operation.constraints.normalizedQueryMaxCodePoints)
          || !bounded(args.place,operation.constraints.normalizedPlaceMaxCodePoints))
        || params.server==='app:watch' && params.tool==='add' && args.title!==undefined
          && [...stripped(args.title)].length>operation.constraints.strippedTitleMaxCodePoints)
        throw new Error('invalid-review-arguments');
      if (params.server === 'app:documents' && params.tool === 'read' && !canonicalReviewDocumentId(args.id)) throw new Error('invalid-review-arguments');
      maximumEffect = operation.maximumEffect;
    } else throw new Error('invalid-review-arguments');
  } else throw new Error('invalid-review-arguments');
  const payload=snapshotIntegrationRequest(integrationRequest(name,params,context));
  const role=nativeReviewOperation(payload);
  if (!role || role.maximumEffect!==maximumEffect) throw new Error('invalid-review-operation');
  return {raw:snapshotIntegrationRequest({name,params}),payload,maximumEffect:role.maximumEffect};
}

export function canonicalReviewNotePath(value) {
  if (typeof value !== 'string' || [...value].length > 512 || value.includes('\0')) return undefined;
  // Match Python str.strip(), including its four additional ASCII separators.
  const stripped = value.replace(/^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu,'');
  if (!stripped || stripped.startsWith('/') || stripped.includes('\\')) return undefined;
  const path = stripped.toLowerCase().endsWith('.md') ? stripped : stripped+'.md';
  return path.split('/').every(part => part && !part.startsWith('.')) ? path : undefined;
}

export function canonicalReviewDocumentId(value) {
  if (typeof value === 'number') return Number.isSafeInteger(value) && value > 0 ? String(value) : undefined;
  if (typeof value !== 'string' || !/^[1-9][0-9]{0,18}$/.test(value)) return undefined;
  return BigInt(value) <= 9223372036854775807n ? value : undefined;
}

export function createIntegrationTools({submit = submitIntegration, context, outcomes, requireBound, onReviewRefused} = {}) {
  // Factory context comes from the native runtime, never model arguments. Each
  // isolated cron run has its own session identity; keep its read snapshot apart
  // from an interactive digest or another task. This metadata grants no action.
  const dispatch = async (id, name, params, payload, signal, render) => {
    // An old native definition retains its original combined abort signal. Do
    // not let a cancelled callback inspect or consume a newer owner's ticket,
    // even when a provider reuses the same call ID and arguments.
    if (signal?.aborted) return text('This integration call was cancelled before submission.',
      {status:'cancelled',code:'integration-call-cancelled'},true);
    let reservation, boundRequired = false;
    try {
      if (requireBound !== undefined) {
        if (typeof requireBound !== 'function') throw new Error('Invalid native binding predicate');
        const required = requireBound(context);
        if (typeof required !== 'boolean') throw new Error('Invalid native binding result');
        boundRequired = required;
      }
      payload = snapshotIntegrationRequest(payload);
      const raw = boundRequired ? reviewIntegrationCandidate(name,params,context).raw : undefined;
      const refusal = outcomes?.refusal(payload, context);
      if (refusal) {
        outcomes?.refuseReviewCall?.(id,context,'integration-outcome-unresolved');
        return text(refusal, {status:'blocked',code:'integration-outcome-unresolved'}, true);
      }
      reservation = outcomes?.take(id, payload, context, raw ?? {name,params});
      if ((boundRequired || outcomes && (payload.action === 'mcp-call' || outcomes.effectful(payload))) && !reservation) {
        outcomes?.refuseReviewCall?.(id,context,'integration-call-unbound');
        if (boundRequired) onReviewRefused?.(context,'integration-call-unbound');
        return text('No integration action was submitted: this call has no matching current native admission.',
          {status:'blocked',code:'integration-call-unbound'}, true);
      }
    } catch {
      outcomes?.refuseReviewCall?.(id,context,'integration-call-unbound');
      if (boundRequired) onReviewRefused?.(context,'integration-call-unbound');
      return text('No integration action was submitted: its native admission could not be verified.',
        {status:'blocked',code:'integration-call-unbound'}, true);
    }
    const control = outcomes?.control?.(reservation);
    if (reservation && !control || boundRequired && control?.recoveryReviewOnly !== true) {
      outcomes?.finish?.(reservation);outcomes?.refuseReviewCall?.(id,context,'integration-call-unbound');
      return text('This integration call is no longer admitted for this response.',{status:'blocked',code:'integration-call-unbound'},true);
    }
    const reviewOnly = control?.recoveryReviewOnly === true;
    const nativePayload = reviewOnly ? {...payload,...control} : payload;
    if (reviewOnly && Buffer.byteLength(JSON.stringify({schemaVersion:1,...nativePayload})+'\n')>65536) {
      outcomes?.refuseReviewCall?.(id,context,'integration-request-too-large');outcomes?.finish?.(reservation);
      onReviewRefused?.(context,'integration-request-too-large');
      return text('No integration action was submitted: its bounded request was too large.',{status:'blocked',code:'integration-request-too-large'},true);
    }
    try {return await run(submit, nativePayload, signal, render,
      (status,references,code,completion) => {const accepted=outcomes?.complete(reservation,status,references,code,completion);
        if(reviewOnly && (accepted===false || ['invalid-review-reference','stale-review-reference','integration-call-unbound'].includes(code)))
          onReviewRefused?.(context,accepted===false?'review-reference-result-invalid':code);
        return accepted;},
      reviewOnly ? () => Boolean(outcomes?.control?.(reservation)) : undefined);
    } finally {outcomes?.finish?.(reservation);}
  };
  return [
    {
      name: 'pixel_ods_notes',
      description: "Markdown notes in the owner's Obsidian vault; Paperless docs/PDFs use pixel_ods_mcp action list. action search: find notes holding query words. action read: read a note by path from search. action write: append text or create a note with exact path/text/mode, only when the owner asks to save; may write or request approval under policy. action prepare: only prepare the same exact request, never write or use approval, even Allowed; Off refuses. Use prepare for preparation without execution. Call the normal tool for each new save/retry, including identical arguments; only its typed receipt establishes pending/done/refused. Portal approval IDs have no tool action.",
      parameters: {type: 'object', additionalProperties: false, required: ['action'],
        properties: {action: {type: 'string', enum: ['search', 'read', 'write', 'prepare']},
          query: {type: 'string', description: 'For search: words to look for, such as "lease deposit".'},
          path: {type: 'string', description: 'For read, write or prepare: the note path inside the vault.'},
          text: {type: 'string', description: 'For write or prepare: the exact Markdown text to add.'},
          mode: {type: 'string', enum: ['append', 'create'], description: 'For write or prepare: append to an existing note or create a new one.'}}},
      execute: (_id, params, signal) => {
        const request = integrationRequest('pixel_ods_notes', params, context);
        return request ? dispatch(_id, 'pixel_ods_notes', params, request, signal, notesText)
          : Promise.resolve(text('Use action search, read, write or prepare with its exact arguments.', {status: 'invalid'}, true));
      },
    },
    {
      name: 'pixel_ods_notify',
      description: "Send a notification to the owner's phone or computer through ntfy configured in Settings → Portal integrations. This is a native notification connection, separate from custom MCP servers; it does not require an ntfy MCP server. Use when the owner asks to be notified, or at the end of a scheduled task that should tell them its result. Call for each new requested notification; describing it does not send it. Only a successful result confirms delivery to the configured ntfy server, not receipt on a physical device.",
      parameters: {type: 'object', additionalProperties: false, required: ['message'],
        properties: {title: {type: 'string'}, message: {type: 'string'},
          priority: {type: 'string', enum: ['low', 'default', 'high']}}},
      execute: (_id, params, signal) => {
        const payload = integrationRequest('pixel_ods_notify', params, context);
        return dispatch(_id, 'pixel_ods_notify', params, payload, signal, () => 'Notification sent.');
      },
    },
    {
      name: 'pixel_ods_mcp',
      description: "Configured apps/custom MCP: Paperless documents, Immich photos, watched web pages/price alerts, FreshRSS news, Home Assistant and others; not native notes/ntfy/Google. action list: show server IDs/tools/arguments; list before the first call. action call: submit server/tool/arguments; may execute under policy. action prepare: only prepare an exact custom MCP call or watch add/remove, never execute/use approval, even Allowed; Off refuses. Watch preparation reads current metadata needed to bind the target. Use prepare for preparation without execution. Call for every new action/retry, including identical arguments or needsApproval; only a typed receipt confirms pending/done/refused. Notes use pixel_ods_notes; notifications use pixel_ods_notify. Portal approval IDs have no tool action.",
      parameters: {type: 'object', additionalProperties: false, required: ['action'],
        properties: {action: {type: 'string', enum: ['list', 'call', 'prepare']},
          server: {type: 'string', description: 'For call or prepare: the server id from list, such as home.'},
          tool: {type: 'string', description: 'For call or prepare: the tool name from list.'},
          arguments: {type: 'object', description: 'For call or prepare: the exact tool arguments as an object.'}}},
      execute: (_id, params, signal) => {
        if (params?.action === 'list') return dispatch(_id, 'pixel_ods_mcp', params, {action: 'mcp-tools'}, signal, toolsText);
        // A small model sometimes sends no arguments at all; show it the exact shape to retry with.
        if (params?.action !== 'call' && params?.action !== 'prepare') return Promise.resolve(text(MCP_USAGE, {status: 'invalid'}, true));
        const payload = integrationRequest('pixel_ods_mcp', params, context);
        if (!payload) return Promise.resolve(text('Prepare needs the exact server, tool and argument object.',{status:'invalid'},true));
        return dispatch(_id, 'pixel_ods_mcp', params, payload, signal,
          reply => `${reply.isError ? 'The tool reported an error:\n' : ''}${reply.text}`);
      },
    },
  ];
}

export const INTEGRATION_TOOL_NAMES = Object.freeze(['pixel_ods_mcp', 'pixel_ods_notes', 'pixel_ods_notify']);
