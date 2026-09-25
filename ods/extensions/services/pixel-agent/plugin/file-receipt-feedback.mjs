import { createHash } from 'node:crypto';
// Rehydrate text only from the single complete, hash-matching native body.
// Receipt metadata remains the authority; model-authored headers grant nothing.
export function materializeFileReceipt(result, operation) {
  const receipt = result?.details?.fileReceipt;
  if (!receipt || receipt.operation !== operation) return undefined;
  if (receipt.bodyStorage !== 'content') return receipt;
  if (receipt.rendered !== undefined || receipt.excerpt !== undefined ||
      !Number.isSafeInteger(receipt.start) || !Number.isSafeInteger(receipt.end) ||
      !Number.isSafeInteger(receipt.totalLines) || receipt.start < 1 ||
      receipt.end < receipt.start || receipt.end > receipt.totalLines ||
      receipt.end - receipt.start > 12000 || !/^[a-f0-9]{64}$/.test(receipt.renderedSha256)) return undefined;
  const matches = (result.content ?? []).filter(block => block?.type === 'text' &&
    typeof block.text === 'string' && block.text.length <= 13024 &&
    createHash('sha256').update(block.text).digest('hex') === receipt.renderedSha256);
  if (matches.length !== 1) return undefined;
  const rendered = matches[0].text, lines = rendered.split('\n'), values = [];
  for (let line = receipt.start; line <= receipt.end; line++) {
    const actual = lines[line - receipt.start + 1], prefix = `${line}|`;
    if (typeof actual !== 'string' || !actual.startsWith(prefix)) return undefined;
    values.push(actual.slice(prefix.length));
  }
  const excerpt = values.join('\n');
  const header = `[File ${receipt.operation}: ${receipt.path}; sha256=${receipt.version}; bytes=${receipt.bytes}; lines=${receipt.start}-${receipt.end}/${receipt.totalLines}]`;
  const body = values.map((value, index) => `${receipt.start + index}|${value}\n`).join('');
  const omitted = receipt.end < receipt.totalLines ? `\n[More content: read path=${JSON.stringify(receipt.path)} offset=${receipt.end + 1}.]` : '';
  if (excerpt.length > 12000 || rendered !== `${header}\n${body}${omitted}`) return undefined;
  return { ...receipt, rendered, excerpt };
}

export function compactFileReceipt(receipt) {
  const { body, rendered, excerpt, ...metadata } = receipt;
  return { ...metadata, bodyStorage: 'content' };
}

// Metadata survives result compaction only with the actual native rendered
// body. This is transport preservation, not an independent acceptance oracle.
export function retainedFileReceipt(result, operation) {
  const receipt = materializeFileReceipt(result, operation);
  if (result?.isError === true || !['read', 'write', 'edit'].includes(operation) ||
      !receipt || receipt.schemaVersion !== 1 || receipt.operation !== operation ||
      receipt.status !== 'completed' || receipt.observed !== true ||
      typeof receipt.rendered !== 'string' || receipt.rendered.length > 13_024 ||
      typeof receipt.excerpt !== 'string' || receipt.excerpt.length > 12_000 ||
      typeof receipt.path !== 'string' || receipt.path.length > 4096 ||
      typeof receipt.toolCallId !== 'string' || receipt.toolCallId.length > 512 ||
      !/^[a-f0-9]{64}$/.test(receipt.version) || !/^[a-f0-9]{64}$/.test(receipt.scope) ||
      !/^[a-f0-9]{64}$/.test(receipt.renderedSha256) ||
      !(result.content ?? []).some(part => part?.type === 'text' && part.text === receipt.rendered)) return undefined;
  if (JSON.stringify(receipt).length > 64_000) return undefined;
  return structuredClone(receipt);
}

export function observedCompleteFileContent(result, operation, path) {
  const receipt = retainedFileReceipt(result, operation);
  if (!receipt || receipt.path !== path || receipt.fullFile !== true ||
      receipt.bytes !== Buffer.byteLength(receipt.excerpt, 'utf8') ||
      receipt.version !== createHash('sha256').update(receipt.excerpt, 'utf8').digest('hex')) return undefined;
  return receipt.excerpt;
}
