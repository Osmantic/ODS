import { createHash, randomUUID } from 'node:crypto';
import { AsyncLocalStorage } from 'node:async_hooks';
import path from 'node:path';

// This module is embedded in the pinned SDK repair. Its only filesystem
// access is through the original SDK's confined operations. It does not
// manufacture receipts from hook parameters or run an independent host read.
const digest = value => createHash('sha256').update(value).digest('hex');
const textOf = message => (message?.content ?? []).filter(x => x?.type === 'text')
  .map(x => x.text).join('\n');
const MAX_BODY = 12_000;

function fileMessage(message, stored = false) {
  if (message?.role !== 'toolResult') return undefined;
  if (['read', 'write', 'edit'].includes(message.toolName)) return message;
  if (message.toolName !== 'tool_call') return undefined;
  const { tool, result } = message.details ?? {};
  const receipt = result?.details?.fileReceipt;
  if (tool?.source !== 'openclaw' || tool.sourceName !== 'core' ||
      !['read', 'write', 'edit'].includes(tool.name) || tool.id !== `openclaw:core:${tool.name}` ||
      typeof message.toolCallId !== 'string' || typeof receipt?.toolCallId !== 'string') return undefined;
  const prefix = `tool_search_code:${message.toolCallId}:${tool.name}:`;
  if (!receipt.toolCallId.startsWith(prefix) || !/^[1-9][0-9]*$/.test(receipt.toolCallId.slice(prefix.length))) return undefined;
  return { ...message, toolName: tool.name, toolCallId: receipt.toolCallId, details: result.details,
    // Stored envelope content may be JSON-encoded. At the provider boundary
    // only the actual outer body counts, never hidden metadata content.
    ...(stored ? { content: result.content } : {}) };
}

export function createFileReceiptContext({ agentId, sessionKey, workspace, enabled }) {
  if (!enabled || agentId !== 'pixel' || typeof sessionKey !== 'string' || !sessionKey ||
      typeof workspace !== 'string' || !path.isAbsolute(workspace)) return undefined;
  const scope = digest(JSON.stringify([agentId, sessionKey, path.resolve(workspace)]));
  const registered = new Map();
  let visible = new Map();
  function remember(receipt, rendered) {
    registered.set(receipt.id, { receipt, rendered });
    while (registered.size > 256) registered.delete(registered.keys().next().value);
  }
  return {
    scope,
    register(receipt, rendered) {
      remember(receipt, rendered);
    },
    // Only the runtime may call this with actual retained session messages.
    // Text that merely looks like a receipt is never parsed as authority.
    hydrateTrustedHistory(messages) {
      for (const original of messages ?? []) {
        const message = fileMessage(original, true);
        const receipt = message?.details?.fileReceipt;
        if (message?.role !== 'toolResult' || !receipt || receipt.scope !== scope ||
            receipt.schemaVersion !== 1 || !['read', 'write', 'edit'].includes(message.toolName) ||
            message.toolCallId !== receipt.toolCallId || message.toolName !== receipt.operation ||
            typeof receipt.id !== 'string' || !/^[a-f0-9-]{36}$/.test(receipt.id) ||
            typeof receipt.path !== 'string' || relativeFile(workspace, path.resolve(workspace, receipt.path)) !== receipt.path ||
            !/^[a-f0-9]{64}$/.test(receipt.version) || !/^[a-f0-9]{64}$/.test(receipt.identity) || receipt.status !== 'completed' ||
            receipt.observed !== true || receipt.bodyComplete !== true ||
            typeof receipt.rendered !== 'string' || receipt.rendered.length > MAX_BODY + 1024 ||
            typeof receipt.excerpt !== 'string' || receipt.excerpt.length > MAX_BODY ||
            digest(receipt.rendered) !== receipt.renderedSha256 || !textOf(message).includes(receipt.rendered)) continue;
        remember(receipt, receipt.rendered);
      }
    },
    // Called on the actual final provider-bound messages, after truncation.
    // A version header without its body does not authorize a mutation.
    updateVisible(messages) {
      visible = new Map();
      for (const original of messages ?? []) {
        const message = fileMessage(original);
        if (!message) continue;
        const text = textOf(message);
        for (const entry of registered.values()) {
          const { receipt, rendered } = entry;
          if (message.toolCallId !== receipt.toolCallId || message.toolName !== receipt.operation ||
              !receipt.observed) continue;
          let prior = visible.get(receipt.path);
          if (prior && (prior.version !== receipt.version || prior.identity !== receipt.identity)) {
            visible.delete(receipt.path); prior = undefined;
          }
          if (!text.includes(rendered) || !receipt.bodyComplete) continue;
          const coverage = prior?.coverage ?? [];
          const range = { start: receipt.start, end: receipt.end, excerpt: receipt.excerpt };
          if (!coverage.some(item => item.start === range.start && item.end === range.end)) coverage.push(range);
          while (coverage.length > 32) coverage.shift();
          visible.set(receipt.path, { ...receipt, coverage,
            fullFile: receipt.fullFile === true || prior?.fullFile === true });
        }
      }
    },
    visible(file) { return visible.get(file); },
    clear() { visible.clear(); registered.clear(); },
  };
}

function relativeFile(root, absolutePath) {
  const relative = path.relative(path.resolve(root), path.resolve(absolutePath));
  if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) return undefined;
  return relative.split(path.sep).join('/');
}

function rangeBody(buffer, args, operation) {
  // Invalid UTF-8 and images are not silently normalized into text evidence.
  const text = buffer.toString('utf8');
  if (!Buffer.from(text, 'utf8').equals(buffer) || text.includes('\0')) return undefined;
  const lines = text.split('\n');
  const start = operation === 'read' && Number.isSafeInteger(args.offset) && args.offset > 0 ? args.offset : 1;
  const requested = operation === 'read' && Number.isSafeInteger(args.limit) && args.limit > 0 ? args.limit : lines.length;
  const end = Math.min(lines.length, start + requested - 1);
  if (start > end) return undefined;
  let body = '', last = start - 1;
  for (let line = start; line <= end; line++) {
    const next = `${line}|${lines[line - 1]}\n`;
    if (body.length + next.length > MAX_BODY) break;
    body += next;
    last = line;
  }
  return { body, start, end: last, totalLines: lines.length,
    bodyComplete: last >= start, requestedRangeComplete: last === end,
    fullFile: start === 1 && last === lines.length,
    excerpt: lines.slice(start - 1, last).join('\n') };
}

function versionMismatch(file) {
  const error = new Error(`File changed or its required content is no longer visible: ${file}. Read the affected range again before editing; read the full file before replacing it.`);
  error.code = 'ODS_FILE_VERSION_REFRESH_REQUIRED';
  return error;
}

const identityOf = buffer => typeof buffer?.odsFileIdentity === 'string' &&
  /^[a-f0-9]{64}$/.test(buffer.odsFileIdentity) ? buffer.odsFileIdentity : undefined;

/** Wrap original SDK operations per invocation, never bypass their confinement.
 * Reads are real reads even when an equivalent body is already visible. This
 * deliberately does not implement filesystem caching or atomic compare/swap.
 */
export function createFileReceiptAdapter({ root, operation, operations, context }) {
  if (!context || !['read', 'edit', 'write'].includes(operation)) {
    return { operations, wrap: tool => tool };
  }
  const local = new AsyncLocalStorage();
  const wrapped = { ...operations };
  const receiptPath = absolutePath => {
    if (!operations.receiptPath) return relativeFile(root, absolutePath);
    const relative = operations.receiptPath(absolutePath);
    return typeof relative === 'string' && relativeFile(root, path.resolve(root, relative)) === relative ? relative : undefined;
  };
  wrapped.readFile = async (...args) => {
    const raw = await operations.readFile(...args);
    const bytes = Buffer.from(raw), identity = identityOf(raw);
    const state = local.getStore();
    if (state) {
      state.path = receiptPath(args[0]);
      state.observed = bytes;
      state.identity = identity;
      if (operation === 'edit' && !state.path) {
        state.failure = new Error('File mutation is outside the bound receipt workspace.');
        state.failure.code = 'ODS_FILE_RECEIPT_SCOPE'; throw state.failure;
      }
      if (operation === 'edit' && state.path && !state.checked) {
        state.checked = true;
        const proof = context.visible(state.path);
        if (!identity || !proof || proof.identity !== identity || proof.version !== digest(bytes) || !proof.bodyComplete ||
            (!proof.fullFile && !state.edits.every(edit => typeof edit.oldText === 'string' &&
              proof.coverage.some(range => range.excerpt.replace(/\r\n/g, '\n').includes(edit.oldText.replace(/\r\n/g, '\n')))))) {
          state.failure = versionMismatch(state.path);
          throw state.failure;
        }
      }
    }
    return bytes;
  };
  if (operations.writeFile) wrapped.writeFile = async (absolutePath, content, ...args) => {
    const state = local.getStore();
    const file = receiptPath(absolutePath);
    if (!file) {
      const error = new Error('File mutation is outside the bound receipt workspace.');
      error.code = 'ODS_FILE_RECEIPT_SCOPE';
      if (state) state.failure = error;
      throw error;
    }
    // Read immediately before the existing write. This detects stale content,
    // but there remains the SDK's existing external-writer check/write race.
    let before, beforeIdentity;
    try { const raw = await operations.readFile(absolutePath); before = Buffer.from(raw); beforeIdentity = identityOf(raw); }
    catch (error) { if (error?.code !== 'ENOENT' && !(error?.name === 'FsSafeError' && error.code === 'not-found')) throw error; }
    if (file && before === undefined && state?.observed) {
      state.failure = versionMismatch(file);
      throw state.failure;
    }
    if (file && before !== undefined) {
      const proof = context.visible(file);
      if (!beforeIdentity || !proof || proof.identity !== beforeIdentity || proof.version !== digest(before) ||
          (operation === 'write' && !proof.fullFile) ||
          (state?.observed && (!state.observed.equals(before) || state.identity !== beforeIdentity))) {
        const error = versionMismatch(file);
        if (state) state.failure = error;
        throw error;
      }
    }
    if (state) { state.path = file; state.writeAttempted = true; }
    try { await operations.writeFile(absolutePath, content, ...args); }
    catch (error) { if (state) state.failure = error; throw error; }
    // The observed result may differ from intended bytes. Never report it as
    // verified success; do not recover a partial write merely from intent.
    let afterRaw;
    try { afterRaw = await operations.readFile(absolutePath); }
    catch (error) { if (state) state.failure = error; throw error; }
    const after = Buffer.from(afterRaw);
    if (state) { state.observed = after; state.identity = identityOf(afterRaw); }
    if (!identityOf(afterRaw) || !after.equals(Buffer.from(content, 'utf8'))) {
      const error = new Error(`File readback differs after ${operation}: ${file ?? 'workspace file'}. Read the file before retrying.`);
      error.code = 'ODS_FILE_READBACK_MISMATCH';
      if (state) state.failure = error;
      throw error;
    }
  };
  return { operations: wrapped, wrap: tool => ({ ...tool, async execute(toolCallId, args, ...rest) {
    const state = { args, edits: args.edits ?? (typeof args.oldText === 'string' ? [{ oldText: args.oldText }] : []) };
    return local.run(state, async () => {
      let result;
      try { result = await tool.execute(toolCallId, args, ...rest); }
      catch (error) {
        if (!state.failure) state.failure = error;
      }
      // Some SDK recovery paths infer success after catching a write error.
      // A failed version check/readback must never be converted into success.
      if (state.failure) {
        const failure = state.failure;
        const range = state.observed && rangeBody(state.observed, args, operation);
        if (range?.body && failure instanceof Error) failure.message +=
          `\n[Last observed bytes during failed ${operation}: ${state.path}; sha256=${digest(state.observed)}; this is not mutation approval]\n${range.body.slice(0, 2000)}`;
        throw failure;
      }
      if (rest[0]?.aborted) throw new Error('Operation aborted; file effects require a fresh read.');
      if (!state.path || !state.observed || !state.identity || result?.isError === true) return result;
      const changedLine = result.details?.firstChangedLine;
      const range = operation === 'edit' && Number.isSafeInteger(changedLine) && changedLine > 0
        ? rangeBody(state.observed, { offset: Math.max(1, changedLine - 3), limit: 12 }, 'read')
        : rangeBody(state.observed, args, operation);
      if (!range || !range.body) return result;
      const receipt = { schemaVersion: 1, id: randomUUID(), scope: context.scope, toolCallId,
        operation, path: state.path, version: digest(state.observed), identity: state.identity, bytes: state.observed.length,
        status: 'completed', observed: true, ...range };
      const header = `[File ${operation}: ${receipt.path}; sha256=${receipt.version}; bytes=${receipt.bytes}; lines=${range.start}-${range.end}/${range.totalLines}]`;
      const omitted = range.end < range.totalLines ? `\n[More content: read path=${JSON.stringify(receipt.path)} offset=${range.end + 1}.]` : '';
      const rendered = `${header}\n${range.body}${omitted}`;
      receipt.rendered = rendered;
      receipt.renderedSha256 = digest(rendered);
      context.register(receipt, rendered);
      return { ...result, content: operation === 'read' ? [{ type: 'text', text: rendered }] :
        [...(result.content ?? []), { type: 'text', text: rendered }],
        details: { ...result.details, fileReceipt: { ...receipt, body: undefined } } };
    });
  } }) };
}
