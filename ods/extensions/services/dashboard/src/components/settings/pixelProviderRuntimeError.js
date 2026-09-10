import { REASONS } from './pixelProviderRuntimeStatus'

const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))

// Error bodies are untrusted and may contain credentials or diagnostic text.
// Keep only an allowlisted code; never show the server's message verbatim.
export async function readProviderRuntimeError(response) {
  if (![400, 409, 413, 502, 503].includes(response.status) || !response.body?.getReader) return null
  const reader = response.body.getReader()
  const bytes = new Uint8Array(2048)
  let size = 0
  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      if (size + value.byteLength > bytes.length) return null
      bytes.set(value, size)
      size += value.byteLength
    }
    const body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, size)))
    if (!exact(body, ['detail']) || !exact(body.detail, ['reason', 'message'])
      || typeof body.detail.message !== 'string' || !REASONS.includes(body.detail.reason)) return null
    return body.detail.reason
  } catch {
    return null
  } finally {
    // Do not wait for an untrusted stream to acknowledge cancellation.
    void reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export function providerRuntimeErrorMessage(reason) {
  if (!REASONS.includes(reason)) return null
  const description = reason === 'provider-inspection-changed'
    ? 'Runtime changed since it was inspected' : 'Provider controller reported a problem'
  return `${description} (${reason}). Refresh runtime status before any further change.`
}
