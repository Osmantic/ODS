const phases = new Set(['preparing', 'loading', 'profiling', 'verifying', 'rolling_back', 'rollback_verifying'])
const outcomes = new Set(['activated', 'rolled_back', 'rollback_unconfirmed'])
const failures = new Set(['runtime_load_failed', 'runtime_readiness_failed', 'consumer_verification_failed', 'rollback_unconfirmed'])

export function modelActivationStatus(value) {
  if (!value || typeof value.active !== 'boolean'
    || value.failureCode != null && !failures.has(value.failureCode)) return null
  if (value.active && phases.has(value.phase))
    return {active: true, phase: value.phase, failureCode: value.failureCode ?? null}
  if (!value.active && outcomes.has(value.outcome))
    return {active: false, outcome: value.outcome, failureCode: value.failureCode ?? null}
  return null
}
