import { CONTROLS } from './pixelRuntimeSettingsForm'

const KEYS = ['schemaVersion', 'status', 'revision', 'settingsRevision', 'appliedRevision', 'capabilities', 'pending', 'lastVerifiedAt', 'reason']
const CAP_KEYS = ['providerContextTokens', 'providerMaxOutputTokens', 'activeContextTokens', 'activeMaxOutputTokens',
  'backendContextTokens', 'capacitySource', 'supportedThinkingLevels', 'samplingSupported', 'pixelOnlyRuntime']
const STATES = ['not-applied', 'applied', 'saved-changes', 'restored', 'pending', 'unavailable']
const safeRevision = value => Number.isSafeInteger(value) && value >= 0
const exact = (value, keys) => value !== null && typeof value === 'object' &&
  [Object.prototype, null].includes(Object.getPrototypeOf(value)) &&
  Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key))
const requireValid = valid => { if (!valid) throw new Error('Invalid settings runtime response') }

function readCapabilities(value) {
  requireValid(exact(value, CAP_KEYS))
  for (const key of CAP_KEYS.slice(0, 5)) {
    if (key === 'backendContextTokens' && value[key] === null) continue
    requireValid(Number.isInteger(value[key]) && value[key] >= 1 && value[key] <= 10000000)
  }
  requireValid(value.providerMaxOutputTokens <= value.providerContextTokens && value.activeMaxOutputTokens <= value.activeContextTokens)
  requireValid(['provider-declared', 'owner-declared', 'backend-observed'].includes(value.capacitySource) &&
    (value.capacitySource !== 'backend-observed' || value.backendContextTokens !== null))
  requireValid(typeof value.samplingSupported === 'boolean' && typeof value.pixelOnlyRuntime === 'boolean')
  const levels = value.supportedThinkingLevels
  requireValid(Array.isArray(levels) && levels.every(level => CONTROLS.thinking.choices.includes(level)) && new Set(levels).size === levels.length)
  return { ...value, supportedThinkingLevels: [...levels] }
}

export function readRuntime(value) {
  requireValid(exact(value, KEYS) && value.schemaVersion === 1 && STATES.includes(value.status))
  if (value.status === 'unavailable') {
    requireValid(typeof value.reason === 'string' && /^[a-z][a-z0-9-]{0,95}$/.test(value.reason))
    requireValid(KEYS.filter(key => !['schemaVersion', 'status', 'reason'].includes(key)).every(key => value[key] === null))
    return { ...value }
  }
  requireValid(typeof value.revision === 'string' && /^[a-f0-9]{64}$/.test(value.revision) &&
    safeRevision(value.settingsRevision) && typeof value.pending === 'boolean' && value.reason === null)
  if (value.status === 'pending') {
    requireValid(value.pending && ['appliedRevision', 'capabilities', 'lastVerifiedAt'].every(key => value[key] === null))
    return { ...value }
  }
  requireValid(!value.pending)
  const capabilities = readCapabilities(value.capabilities)
  if (value.status === 'not-applied') requireValid(value.appliedRevision === null && value.lastVerifiedAt === null)
  else {
    requireValid(value.status === 'restored' ? value.appliedRevision === null
      : safeRevision(value.appliedRevision) && ((value.appliedRevision === value.settingsRevision) === (value.status === 'applied')))
    const at = value.lastVerifiedAt
    requireValid(typeof at === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$/.test(at) && Number.isFinite(Date.parse(at)))
    requireValid(new Date(at).toISOString().slice(0, 19) === at.slice(0, 19))
  }
  return { ...value, capabilities }
}

export function readOutcome(value, requestedRevision) {
  requireValid(safeRevision(requestedRevision) && exact(value, ['outcome', 'appliedRevision']) && ['applied', 'rolled-back'].includes(value.outcome))
  requireValid(value.appliedRevision === null || safeRevision(value.appliedRevision))
  requireValid(value.outcome !== 'applied' || value.appliedRevision === requestedRevision)
  return { ...value }
}

export function runtimeChangeRequest(value, operation, savedRevision) {
  const state = readRuntime(value)
  if (operation === 'recover' && state.status === 'pending') {
    return { operation, revision: state.revision, settingsRevision: state.settingsRevision }
  }
  if (operation === 'apply' && ['not-applied', 'saved-changes', 'restored'].includes(state.status) && state.settingsRevision === savedRevision) {
    return { operation, revision: state.revision, settingsRevision: savedRevision }
  }
  throw new Error('Refresh runtime status before changing settings')
}

export function confirmOutcome(value, inspected, requestedRevision) {
  const outcome = readOutcome(value, requestedRevision)
  const state = readRuntime(inspected)
  if (state.pending !== false || state.settingsRevision !== requestedRevision || state.appliedRevision !== outcome.appliedRevision ||
      (outcome.outcome === 'applied' && state.status !== 'applied') ||
      (outcome.outcome === 'rolled-back' && !['restored', 'applied', 'saved-changes'].includes(state.status))) {
    throw new Error('Runtime readback does not confirm this settings change')
  }
  return outcome.outcome === 'applied'
    ? `Saved revision ${requestedRevision} is applied and verified.`
    : 'Previous runtime configuration restored and verified. Saved preferences were not changed.'
}
