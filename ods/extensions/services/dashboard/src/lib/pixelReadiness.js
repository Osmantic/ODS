// Readiness v1 is deliberately never "ready": runtime identity v1 cannot
// establish release binding. This projection is not an admission permission.
const reasons = {
  'access-proof-unverified': 'Host access and installed-release readiness are unverified.',
  'access-probe-timeout': 'Access verification timed out. Effective permissions and installed-release readiness are unverified.',
  'access-probe-unavailable': 'Access verification is unavailable. Effective permissions and installed-release readiness are unverified.',
  'access-probe-invalid': 'The access response could not be verified. Effective permissions and installed-release readiness are unverified.',
  'access-inspection-failed': 'The host access inspection failed. Effective permissions are unverified. Review Access settings.',
  'access-verification-failed': 'The host could not verify its access boundary. Effective permissions are unverified. Review Access settings.',
  'access-transition-pending': 'An access transition is unfinished. Review the existing Access recovery controls.',
  'release-binding-unavailable': 'Host access is verified; installed-release readiness remains unverified.',
  'runtime-files-changed': 'Runtime files changed since initialization. Installed-release readiness is not verified.',
  'model-route-unavailable': 'The model route is unavailable.',
}

export function readPixelReadiness(value, routeAvailable, now = Date.now()) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.schemaVersion !== 1
    || typeof value.routeAvailable !== 'boolean' || value.routeAvailable !== routeAvailable
    || !['unverified', 'attention', 'unavailable'].includes(value.state)
    || !['verified', 'transitioning', 'failed', 'unverified'].includes(value.accessState)
    || !['sandboxed', 'full-access', 'unknown'].includes(value.effectiveMode)
    || (value.accessState === 'verified') !== (value.effectiveMode !== 'unknown')
    || !['unverified', 'mismatch'].includes(value.releaseState)
    || !Object.hasOwn(reasons, value.reasonCode)
    || typeof value.observedAt !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value.observedAt)
    || !Number.isFinite(Date.parse(value.observedAt)) || Math.abs(now - Date.parse(value.observedAt)) > 120000) return null
  const attention = ['failed', 'transitioning'].includes(value.accessState) || value.releaseState === 'mismatch'
  const state = !routeAvailable ? 'unavailable' : attention ? 'attention' : 'unverified'
  const expectedReasons = !routeAvailable ? ['model-route-unavailable']
    : value.accessState === 'failed' ? ['access-inspection-failed', 'access-verification-failed']
      : value.accessState === 'transitioning' ? ['access-transition-pending']
        : value.releaseState === 'mismatch' ? ['runtime-files-changed']
          : value.accessState === 'verified' ? ['release-binding-unavailable']
            : ['access-proof-unverified', 'access-probe-timeout', 'access-probe-unavailable', 'access-probe-invalid']
  if (value.state !== state || !expectedReasons.includes(value.reasonCode)) return null
  return {state, accessState:value.accessState, effectiveMode:value.effectiveMode,
    releaseState:value.releaseState, reasonCode:value.reasonCode, observedAt:value.observedAt}
}

export function pixelReadinessView(value, routeAvailable) {
  const verified = readPixelReadiness(value, routeAvailable)
  const attention = verified?.state === 'attention'
  return {
    attention,
    label: routeAvailable ? attention ? 'Needs attention' : 'Available · unverified' : 'Unavailable',
    detail: verified ? reasons[verified.reasonCode]
      : routeAvailable ? reasons['access-proof-unverified'] : reasons['model-route-unavailable'],
    access: verified?.accessState === 'verified' ? 'Verified' : verified?.accessState === 'failed'
      ? 'Failed' : verified?.accessState === 'transitioning' ? 'Transition unfinished' : 'Unverified',
    release: verified?.releaseState === 'mismatch' ? 'Files changed' : 'Unverified',
  }
}
