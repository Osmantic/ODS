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

export function createFileReceiptContext({ agentId, sessionKey, workspace, enabled }) {
  if (!enabled || agentId !== 'pixel' || !sessionKey || !path.isAbsolute(workspace)) return undefined;
  const scope = digest(JSON.stringify([agentId, sessionKey, path.resolve(workspace)]));
  const registered = new Map();
  let visible = new Map();
  return {
    scope,
    register(receipt, rendered) {
      registered.set(receipt.id, { receipt, rendered });
      while (registered.size > 256) registered.delete(registered.keys().next().value);
    },
    // Called on the actual final provider-bound messages, after truncation.
    // A version header without its body does not authorize a mutation.
    updateVisible(messages) {
      visible = new Map();
      for (const message of messages ?? []) {
        if (message?.role !== 'toolResult' || !['read', 'write', 'edit'].includes(message.toolName)) continue;
        const text = textOf(message);
        for (const entry of registered.values()) {
          const { receipt, rendered } = entry;
          if (message.toolCallId !== receipt.toolCallId || message.toolName !== receipt.operation ||
              !text.includes(rendered) || !receipt.observed || !receipt.bodyComplete) continue;
          visible.set(receipt.path, receipt);
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
  wrapped.readFile = async (...args) => {
    const bytes = Buffer.from(await operations.readFile(...args));
    const state = local.getStore();
    if (state) {
      state.path = relativeFile(root, args[0]);
      state.observed = bytes;
      if (operation === 'edit' && state.path && !state.checked) {
        state.checked = true;
        const proof = context.visible(state.path);
        if (!proof || proof.version !== digest(bytes) || !proof.bodyComplete ||
            (!proof.fullFile && !state.edits.every(edit => typeof edit.oldText === 'string' &&
              proof.excerpt.replace(/\r\n/g, '\n').includes(edit.oldText.replace(/\r\n/g, '\n'))))) {
          state.failure = versionMismatch(state.path);
          throw state.failure;
        }
      }
    }
    return bytes;
  };
  if (operations.writeFile) wrapped.writeFile = async (absolutePath, content, ...args) => {
    const state = local.getStore();
    const file = relativeFile(root, absolutePath);
    // Read immediately before the existing write. This detects stale content,
    // but there remains the SDK's existing external-writer check/write race.
    let before;
    try { before = Buffer.from(await operations.readFile(absolutePath)); }
    catch (error) { if (error?.code !== 'ENOENT') throw error; }
    if (file && before !== undefined) {
      const proof = context.visible(file);
      if (!proof || proof.version !== digest(before) ||
          (operation === 'write' && !proof.fullFile) ||
          (state?.observed && !state.observed.equals(before))) {
        const error = versionMismatch(file);
        if (state) state.failure = error;
        throw error;
      }
    }
    if (state) { state.path = file; state.writeAttempted = true; }
    await operations.writeFile(absolutePath, content, ...args);
    // The observed result may differ from intended bytes. Never report it as
    // verified success; do not recover a partial write merely from intent.
    const after = Buffer.from(await operations.readFile(absolutePath));
    if (state) state.observed = after;
    if (!after.equals(Buffer.from(content, 'utf8'))) {
      const error = new Error(`File readback differs after ${operation}: ${file ?? 'workspace file'}. Read the file before retrying.`);
      error.code = 'ODS_FILE_READBACK_MISMATCH';
      if (state) state.failure = error;
      throw error;
    }
  };
  return { operations: wrapped, wrap: tool => ({ ...tool, async execute(toolCallId, args, ...rest) {
    const state = { args, edits: args.edits ?? (typeof args.oldText === 'string' ? [{ oldText: args.oldText }] : []) };
    return local.run(state, async () => {
      const result = await tool.execute(toolCallId, args, ...rest);
      // Some SDK recovery paths infer success after catching a write error.
      // A failed version check/readback must never be converted into success.
      if (state.failure) throw state.failure;
      if (!state.path || !state.observed || result?.isError === true) return result;
      const range = rangeBody(state.observed, args, operation);
      if (!range || !range.body) return result;
      const receipt = { schemaVersion: 1, id: randomUUID(), scope: context.scope, toolCallId,
        operation, path: state.path, version: digest(state.observed), bytes: state.observed.length,
        status: 'completed', observed: true, ...range };
      const header = `[File ${operation}: ${receipt.path}; sha256=${receipt.version}; bytes=${receipt.bytes}; lines=${range.start}-${range.end}/${range.totalLines}]`;
      const omitted = range.end < range.totalLines ? `\n[More content: read path=${JSON.stringify(receipt.path)} offset=${range.end + 1}.]` : '';
      const rendered = `${header}\n${range.body}${omitted}`;
      context.register(receipt, rendered);
      return { ...result, content: operation === 'read' ? [{ type: 'text', text: rendered }] :
        [...(result.content ?? []), { type: 'text', text: rendered }],
        details: { ...result.details, fileReceipt: { ...receipt, body: undefined, excerpt: undefined } } };
    });
  } }) };
}
