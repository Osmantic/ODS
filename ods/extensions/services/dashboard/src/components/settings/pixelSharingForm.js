export function readSharing(value) {
  const config = value?.configuration
  if (!config || config.schemaVersion !== 1 || !Number.isSafeInteger(config.revision) || config.revision < 0
      || typeof config.enabled !== 'boolean' || !Array.isArray(config.devices) || config.devices.length > 64
      || !['not-probed', 'starting', 'ready', 'stopped', 'error', 'unavailable'].includes(value.runtime?.status)
      || value.transport?.mode !== 'loopback-only' || !Number.isInteger(value.transport.port)
      || value.transport.port < 1024 || value.transport.port > 65535) throw new Error('Invalid sharing response')
  return { configuration: config, activeRoute: value.activeRoute, transport: value.transport, runtime: value.runtime }
}

export function connectionBaseUrl(value) {
  const url = new URL(value)
  const local = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
  if (url.username || url.password || url.search || url.hash || !url.hostname
      || (url.protocol !== 'https:' && !(url.protocol === 'http:' && local))
      || !['/v1', '/v1/'].includes(url.pathname)) throw new Error('Use an HTTPS or loopback /v1 URL')
  return url.href.replace(/\/$/, '')
}

export function connectionBundle(issued, baseUrl) {
  const snapshot = readSharing(issued)
  const credential = issued.credential
  const device = snapshot.configuration.devices.find(item => item.id === credential?.id && !item.revoked)
  if (!device || !/^ods_infer_[a-f0-9]{64}$/.test(credential?.key || '') || issued.model !== 'ods/shared') {
    throw new Error('Invalid issued credential')
  }
  return {
    schemaVersion: 1, kind: 'ods-inference-connection', label: device.label,
    baseUrl: connectionBaseUrl(baseUrl), model: 'ods/shared', deviceId: device.id, expiresAt: device.expiresAt,
    expected: { catalogId: device.catalogId, runtimeModelId: device.runtimeModelId },
    credential: { apiKey: credential.key }, execution: 'client-owned',
  }
}
