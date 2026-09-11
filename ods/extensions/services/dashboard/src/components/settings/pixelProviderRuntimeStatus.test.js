import { confirmOutcome, readOutcome, readRuntime, runtimeChangeRequest } from './pixelProviderRuntimeStatus'
import { binding, outcome, runtimeDoc } from './pixelProviderRuntimeFixtures'

const change = (operation = 'apply') => ({ operation, revision: 'a'.repeat(64), providerRevision: 3 })
it.each(['not-applied', 'applied', 'saved-changes', 'inactive', 'pending', 'unavailable'])('validates %s', state => {
  expect(readRuntime(runtimeDoc(state))).toEqual(runtimeDoc(state))
})
it('detaches bindings and accepts all canonical UUID versions, not uppercase', () => {
  const raw = runtimeDoc('applied')
  readRuntime(raw).binding.revision = 99
  expect(raw.binding.revision).toBe(3)
  expect(() => readRuntime({ ...raw, binding: { ...binding(), activationId: binding().activationId.toUpperCase() } })).toThrow()
})
it('rejects private, malformed and contradictory fields', () => {
  for (const value of [null, [], {}, { ...runtimeDoc(), secret: 'private' }, { ...runtimeDoc(), schemaVersion: true },
    { ...runtimeDoc(), providerRevision: true }, { ...runtimeDoc(), providerRevision: 2 ** 53 },
    { ...runtimeDoc(), revision: 'A'.repeat(64) }, { ...runtimeDoc(), transportVerified: true },
    { ...runtimeDoc('pending'), binding: binding() }, { ...runtimeDoc('pending'), pending: false },
    { ...runtimeDoc('applied'), binding: null }, { ...runtimeDoc('applied'), providerRevision: 4 },
    { ...runtimeDoc('saved-changes'), binding: binding() }, { ...runtimeDoc('inactive'), binding: binding() },
    { ...runtimeDoc('unavailable'), pending: false }, { ...runtimeDoc('unavailable'), reason: 'private-path' },
    ...['2026-02-31T01:00:00Z', '0000-01-01T00:00:00Z', '2026-01-01T24:00:00Z', '2026-01-01T00:00:00+00:00']
      .map(lastVerifiedAt => ({ ...runtimeDoc('applied'), lastVerifiedAt }))]) expect(() => readRuntime(value)).toThrow()
  const raw = runtimeDoc(); delete raw.schemaVersion; delete raw.reason
  expect(() => readRuntime(raw)).toThrow()
})
it('sends only revision authority and permits recovery with unreadable saved configuration', () => {
  expect(runtimeChangeRequest(runtimeDoc(), 'apply', 3)).toEqual(change())
  expect(runtimeChangeRequest(runtimeDoc('saved-changes'), 'deactivate', 3)).toEqual(change('deactivate'))
  expect(runtimeChangeRequest(runtimeDoc('pending'), 'recover', null)).toEqual(change('recover'))
  for (const [state, operation, saved] of [['applied', 'apply', 3], ['inactive', 'deactivate', 3],
    ['not-applied', 'apply', 4], ['not-applied', 'apply', null], ['inactive', 'recover', 3]]) {
    expect(() => runtimeChangeRequest(runtimeDoc(state), operation, saved)).toThrow()
  }
})
it('requires exact binding readback, not just the same revision', () => {
  expect(confirmOutcome(outcome(), runtimeDoc('applied'), change())).toMatch(/registration is verified/)
  for (const modified of [{ activationId: '00000000-0000-0000-0000-000000000000' }, { allowCloud: true }]) {
    expect(() => confirmOutcome(outcome(), { ...runtimeDoc('applied'), binding: { ...binding(), ...modified } }, change())).toThrow()
  }
  expect(() => readOutcome(outcome(null), change())).toThrow()
  expect(() => readOutcome(outcome(binding(), 'rolled-back'), change())).toThrow()
  expect(() => readOutcome(outcome(), { ...change(), binding: binding() })).toThrow()
  expect(() => readOutcome(outcome(), change('deactivate'))).toThrow()
  expect(() => confirmOutcome(outcome(), runtimeDoc('saved-changes', 4), change())).toThrow()
})
it('corroborates deactivate and both recovery outcomes, including older active bindings', () => {
  expect(confirmOutcome(outcome(null), runtimeDoc('inactive'), change('deactivate'))).toMatch(/deactivated/)
  expect(confirmOutcome(outcome(binding(2), 'rolled-back'), runtimeDoc('saved-changes'), change('recover'))).toMatch(/restored/)
  expect(confirmOutcome(outcome(null, 'rolled-back'), runtimeDoc('inactive'), change('recover'))).toMatch(/restored/)
  expect(confirmOutcome(outcome(), runtimeDoc('applied'), change('recover'))).toMatch(/recovery completed/)
  expect(() => confirmOutcome(outcome(null), runtimeDoc('pending'), change('recover'))).toThrow()
})
