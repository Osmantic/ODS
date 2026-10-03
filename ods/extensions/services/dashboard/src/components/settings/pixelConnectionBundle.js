// Bounded client-side validation; distinct basename from the component on Windows.
// No network, no secrets storage, no state mutations. Pure functions only.

const devicePattern = /^device-[a-f0-9]{16}$/
const apiKeyPattern = /^ods_infer_[a-f0-9]{64}$/

function _integer(value, low, high) {
  return Number.isSafeInteger(value) && value >= low && value <= high
}

function _text(value, max) {
  return typeof value === 'string' && value === value.trim() && value.length > 0 && value.length <= max && /^[\x20-\x7e]+$/.test(value)
}

function _exact(value, keys) {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return false
  const actual = Object.keys(value)
  return actual.length === keys.length && keys.every(k => Object.hasOwn(value, k))
}

/**
 * Validate the raw string is parseable JSON that matches the client-side connection bundle schema.
 * Returns { endpoint, label, model, deviceId, expiresAt, expected } or throws.
 * Never returns or exposes the credential apiKey.
 */
export function parseBundle(raw) {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > 32768 || new TextEncoder().encode(raw).length > 32768) {
    throw new Error('Paste a valid ODS connection bundle (up to 32 KB).')
  }
  let parsed
  try { parsed = JSON.parse(raw) } catch {
    throw new Error('The pasted text is not valid JSON.')
  }

  if (!_exact(parsed, ['schemaVersion', 'kind', 'label', 'baseUrl', 'model', 'deviceId', 'expiresAt', 'expected', 'credential', 'execution'])
    || !_integer(parsed.schemaVersion, 1, 1)
    || parsed.kind !== 'ods-inference-connection'
    || !_text(parsed.label, 256)
    || parsed.model !== 'ods/shared'
    || parsed.execution !== 'client-owned'
    || !(typeof parsed.deviceId === 'string' && devicePattern.test(parsed.deviceId))
    || !_integer(parsed.expiresAt, 0, Math.pow(2, 53) - 1)
    || parsed.expiresAt <= Date.now() / 1000
    || !_exact(parsed.expected, ['catalogId', 'runtimeModelId'])
    || !_text(parsed.expected.catalogId, 256)
    || !_text(parsed.expected.runtimeModelId, 256)
    || !_exact(parsed.credential, ['apiKey'])
    || !(typeof parsed.credential.apiKey === 'string')
    || !apiKeyPattern.test(parsed.credential.apiKey)) {
    throw new Error('Bundle schema does not match the expected ODS connection format.')
  }

  const endpoint = connectionEndpoint(parsed.baseUrl)

  return {
    endpoint,
    label: parsed.label,
    model: parsed.model,
    deviceId: parsed.deviceId,
    expiresAt: parsed.expiresAt,
    expected: parsed.expected,
  }
}

/**
 * Validate the server probe response matches the strict expected shape and identity constraints.
 * Returns the sanitized metadata object or throws.
 */
export function validateProbeResponse(response, bundleParsed) {
  if (!_exact(response, ['schemaVersion', 'endpoint', 'deviceId', 'expiresAt', 'expected', 'metadata'])
    || response.schemaVersion !== 1
    || typeof response.endpoint !== 'string'
    || typeof response.deviceId !== 'string'
    || !_integer(response.expiresAt, 0, Math.pow(2, 53) - 1)
    || response.expiresAt <= Date.now() / 1000
    || !_exact(response.expected, ['catalogId', 'runtimeModelId'])
    || !_exact(response.metadata, ['catalogId', 'routedModel', 'identitySource', 'routeSeq', 'contextLength', 'capabilities', 'maxOutputTokens', 'expiresAt', 'execution'])
    || response.metadata.identitySource !== 'ods-verified-route'
    || response.metadata.execution !== 'client-owned'
    || !_integer(response.metadata.routeSeq, 0, Math.pow(2, 53) - 1)
    || !_integer(response.metadata.contextLength, 4096, 10000000)
    || !_integer(response.metadata.maxOutputTokens, 256, Math.min(131072, response.metadata.contextLength))
    || response.metadata.maxOutputTokens > response.metadata.contextLength
    || response.metadata.expiresAt !== response.expiresAt
    || response.metadata.expiresAt !== bundleParsed.expiresAt
    || !_exact(response.metadata.capabilities, ['chat', 'tools', 'vision', 'agentViable'])
    || typeof response.metadata.capabilities.chat !== 'boolean'
    || response.metadata.capabilities.chat !== true
    || typeof response.metadata.capabilities.tools !== 'boolean'
    || typeof response.metadata.capabilities.vision !== 'boolean'
    || typeof response.metadata.capabilities.agentViable !== 'boolean') {
    throw new Error('The probe response does not match the expected format.')
  }

  // Identity assertions: server-returned values must match bundle expectations
  if (response.endpoint !== bundleParsed.endpoint
    || response.deviceId !== bundleParsed.deviceId
    || response.expected.catalogId !== bundleParsed.expected.catalogId
    || response.expected.runtimeModelId !== bundleParsed.expected.runtimeModelId
    || response.metadata.catalogId !== bundleParsed.expected.catalogId
    || response.metadata.routedModel !== bundleParsed.expected.runtimeModelId) {
    throw new Error('The probe response identity does not match the bundle.')
  }

  return response.metadata
}

/**
 * Derive a safe default provider ID from the bundle label.
 */
export function suggestProviderId(label, existingIds) {
  let base = label.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 60)
  if (!/^[a-z]/.test(base)) base = 'ods-import'
  let id = base.slice(0, 63)
  if (!existingIds.has(id)) return id
  let n = 2
  while (n < 100) {
    const candidate = `${base.slice(0, 63 - String(n).length)}-${n}`
    if (!existingIds.has(candidate)) return candidate
    n++
  }
  return 'ods-import-' + n
}

export function connectionEndpoint(value) {
  if (typeof value !== 'string' || value.length > 2048) throw new Error('Invalid connection endpoint.')
  const match = /^(https?):\/\/(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9.-]+)(?::([0-9]{1,5}))?\/v1\/?$/.exec(value)
  if (!match || match[3] && (+match[3] < 1 || +match[3] > 65535)) throw new Error('Invalid connection endpoint.')
  let url
  try { url = new URL(value) } catch { throw new Error('Invalid connection endpoint.') }
  let hostname = url.hostname
  if (match[1] === 'http') {
    if (hostname === 'localhost') hostname = '127.0.0.1'
    else if (hostname !== '[::1]' && (!/^127\.\d+\.\d+\.\d+$/.test(hostname) || hostname !== match[2])) {
      throw new Error('HTTP requires literal loopback or localhost. Use verified HTTPS for other hosts.')
    }
  }
  return `${match[1]}://${hostname}${match[3] ? ':' + Number(match[3]) : ''}/v1`
}
