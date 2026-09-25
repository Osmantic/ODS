import { createHash } from 'node:crypto';
// Metadata survives result compaction only with the actual native rendered
// body. This is transport preservation, not an independent acceptance oracle.
export function retainedFileReceipt(result, operation) {
  const receipt = result?.details?.fileReceipt;
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
