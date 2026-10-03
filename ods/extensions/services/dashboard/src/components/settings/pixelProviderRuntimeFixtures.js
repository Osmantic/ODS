export const binding = (revision = 3) => ({ schemaVersion: 1, activationId: '123e4567-e89b-12d3-a456-426614174000', revision, allowCloud: false })
export function runtimeDoc(status = 'not-applied', providerRevision = 3) {
  const verified = ['applied', 'saved-changes', 'inactive'].includes(status)
  const value = { schemaVersion: 1, status, revision: 'a'.repeat(64), providerRevision,
    binding: ['applied', 'saved-changes'].includes(status) ? binding(status === 'saved-changes' ? 2 : providerRevision) : null,
    pending: status === 'pending', registrationVerified: verified, transportVerified: false,
    lastVerifiedAt: verified ? '2026-09-09T18:01:25.584Z' : null, reason: null }
  return status === 'unavailable' ? { ...Object.fromEntries(Object.keys(value).map(key => [key, null])),
    schemaVersion: 1, status, reason: 'provider-controller-unavailable' } : value
}
export const outcome = (active = binding(), result = 'applied') => ({ outcome: result, binding: active, registrationVerified: true, transportVerified: false })
