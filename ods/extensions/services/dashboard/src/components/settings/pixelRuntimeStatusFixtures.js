// Synthetic public envelopes, not installed runtime evidence.
export const runtimeDoc = (status = 'not-applied', settingsRevision = 3) => {
  const value = { schemaVersion: 1, status, revision: 'a'.repeat(64), settingsRevision, appliedRevision: null,
    capabilities: { providerContextTokens: 65536, providerMaxOutputTokens: 8192, activeContextTokens: 32768,
      activeMaxOutputTokens: 4096, backendContextTokens: null, capacitySource: 'owner-declared',
      supportedThinkingLevels: [], samplingSupported: false, pixelOnlyRuntime: true }, pending: false, lastVerifiedAt: null, reason: null }
  if (['applied', 'saved-changes'].includes(status)) Object.assign(value, {
    appliedRevision: status === 'applied' ? settingsRevision : settingsRevision - 1, lastVerifiedAt: '2026-09-08T17:00:00Z' })
  if (status === 'pending') Object.assign(value, { pending: true, capabilities: null })
  if (status === 'restored') value.lastVerifiedAt = '2026-09-08T17:00:00Z'
  if (status === 'unavailable') Object.keys(value).filter(key => !['schemaVersion', 'status'].includes(key)).forEach(key => { value[key] = null })
  if (status === 'unavailable') value.reason = 'settings-controller-unavailable'
  return value
}
