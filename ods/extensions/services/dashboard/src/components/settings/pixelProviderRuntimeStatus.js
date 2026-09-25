const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value)
  && [Object.prototype, null].includes(Object.getPrototypeOf(value))
  && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const revision = value => Number.isSafeInteger(value) && value >= 0
const hex = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const invalid = () => { throw new Error('Invalid provider runtime response') }
const keys = ['schemaVersion', 'status', 'revision', 'providerRevision', 'binding', 'pending',
  'registrationVerified', 'transportVerified', 'lastVerifiedAt', 'reason']
const states = ['not-applied', 'applied', 'saved-changes', 'inactive', 'pending', 'unavailable']
export const REASONS = [
  'provider-controller-unavailable', 'provider-transition-unavailable', 'provider-transition-uncertain',
  'provider-inspection-changed', 'provider-policy-disabled', 'provider-not-managed',
  'provider-recovery-unavailable', 'provider-recovery-conflict', 'provider-recovery-journal-missing',
  'provider-owner-state-changed', 'provider-runtime-custody-unqualified',
  'provider-runtime-descriptor-unqualified', 'provider-runtime-custody-changed',
  'provider-source-changed', 'provider-service-baseline-conflict',
  'provider-worker-runtime-not-ready',
  'settings-store-not-initialized', 'settings-store-busy', 'settings-data-directory-unqualified',
  'transition-recovery-required', 'runtime-busy', 'runtime-busy-or-unqualified',
  'model-lifecycle-busy', 'macos-launchd-adapter-missing', 'native-windows-adapter-missing',
]
function binding(value) {
  if (value === null) return null
  if (!exact(value, ['schemaVersion', 'activationId', 'revision', 'allowCloud']) || value.schemaVersion !== 1
    || !revision(value.revision) || typeof value.allowCloud !== 'boolean' || typeof value.activationId !== 'string'
    || !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(value.activationId)) invalid()
  return { ...value }
}
function timestamp(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(value)
    || Number(value.slice(0, 4)) < 1 || !Number.isFinite(Date.parse(value))
    || new Date(value).toISOString().slice(0, 19) !== value.slice(0, 19)) invalid()
}
export function readRuntime(value) {
  if (!exact(value, keys) || value.schemaVersion !== 1 || !states.includes(value.status)) invalid()
  if (value.status === 'unavailable') {
    if (!REASONS.includes(value.reason) || keys.filter(key => !['schemaVersion', 'status', 'reason'].includes(key))
      .some(key => value[key] !== null)) invalid()
    return { ...value }
  }
  if (!hex(value.revision) || !revision(value.providerRevision) || typeof value.pending !== 'boolean'
    || typeof value.registrationVerified !== 'boolean' || value.transportVerified !== false || value.reason !== null) invalid()
  const active = binding(value.binding)
  if (['pending', 'not-applied'].includes(value.status)) {
    if (value.pending !== (value.status === 'pending') || active !== null || value.registrationVerified !== false
      || value.lastVerifiedAt !== null) invalid()
  } else {
    if (value.pending || value.registrationVerified !== true) invalid()
    timestamp(value.lastVerifiedAt)
    if (value.status === 'inactive' ? active !== null
      : active === null || (active.revision === value.providerRevision) !== (value.status === 'applied')) invalid()
  }
  return { ...value, binding: active }
}
function readRequest(value) {
  if (!exact(value, ['operation', 'revision', 'providerRevision']) || !['apply', 'deactivate', 'recover'].includes(value.operation)
    || !hex(value.revision) || !revision(value.providerRevision)) invalid()
  return value
}
export function runtimeChangeRequest(value, operation, savedRevision) {
  const runtime = readRuntime(value)
  const allowed = operation === 'recover' ? runtime.status === 'pending'
    : revision(savedRevision) && runtime.providerRevision === savedRevision && (operation === 'apply'
      ? ['not-applied', 'inactive', 'saved-changes'].includes(runtime.status)
      : operation === 'deactivate' && ['applied', 'saved-changes'].includes(runtime.status))
  if (!allowed) invalid()
  return readRequest({ operation, revision: runtime.revision, providerRevision: runtime.providerRevision })
}
export function readOutcome(value, request) {
  const checked = readRequest(request)
  if (!exact(value, ['outcome', 'binding', 'registrationVerified', 'transportVerified'])
    || !['applied', 'rolled-back'].includes(value.outcome) || value.registrationVerified !== true || value.transportVerified !== false) invalid()
  const active = binding(value.binding)
  if ((checked.operation !== 'recover' && value.outcome !== 'applied')
    || (checked.operation === 'apply' && (!active || active.revision !== checked.providerRevision))
    || (checked.operation === 'deactivate' && active !== null)) invalid()
  return { ...value, binding: active }
}
export function confirmOutcome(value, readback, request) {
  const outcome = readOutcome(value, request)
  const runtime = readRuntime(readback)
  if (!['applied', 'saved-changes', 'inactive'].includes(runtime.status)
    || (outcome.binding === null ? runtime.binding !== null
      : runtime.binding === null || ['schemaVersion', 'activationId', 'revision', 'allowCloud']
        .some(key => outcome.binding[key] !== runtime.binding[key]))
    || (request.operation !== 'recover' && runtime.providerRevision !== request.providerRevision)
    || (request.operation === 'apply' && runtime.status !== 'applied')
    || (request.operation === 'deactivate' && runtime.status !== 'inactive')) invalid()
  if (request.operation === 'apply') return `Provider revision ${request.providerRevision} is applied and registration is verified.`
  if (request.operation === 'deactivate') return 'Managed providers are deactivated; the original inference configuration is registered. Saved settings and keys are retained.'
  return outcome.outcome === 'rolled-back'
    ? 'The recorded prior provider configuration is restored and registered. Saved settings remain separate.'
    : 'Provider recovery completed and current registration is verified. Saved settings remain separate.'
}
