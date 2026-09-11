import { confirmOutcome, readOutcome, readRuntime, runtimeChangeRequest } from './pixelRuntimeStatus'
import { runtimeDoc } from './pixelRuntimeStatusFixtures'

it.each(['not-applied', 'applied', 'saved-changes', 'restored', 'pending', 'unavailable'])('validates %s without inventing state', status => {
  expect(readRuntime(runtimeDoc(status))).toEqual(runtimeDoc(status))
})

it('copies capability lists and preserves unknown capacity', () => {
  const value = runtimeDoc()
  readRuntime(value).capabilities.supportedThinkingLevels.push('high')
  expect(value.capabilities.supportedThinkingLevels).toEqual([])
  expect(readRuntime(value).capabilities.backendContextTokens).toBeNull()
})

it('rejects missing, malformed, private and contradictory metadata', () => {
  for (const value of [null, {}, [], { ...runtimeDoc(), credential: 'private' },
    { ...runtimeDoc(), schemaVersion: true }, { ...runtimeDoc(), settingsRevision: true },
    { ...runtimeDoc(), settingsRevision: Number.MAX_SAFE_INTEGER + 1 }, { ...runtimeDoc(), revision: 'bad' },
    { ...runtimeDoc('unavailable'), pending: false }, { ...runtimeDoc('unavailable'), reason: 'private/path' },
    { ...runtimeDoc('pending'), capabilities: runtimeDoc().capabilities }, { ...runtimeDoc('pending'), pending: false },
    { ...runtimeDoc('applied'), appliedRevision: 2 }, { ...runtimeDoc('saved-changes'), appliedRevision: 3 },
    ...['2026-99-01T00:00:00Z', '2026-02-30T00:00:00Z', '2026-09-08', null].map(lastVerifiedAt => ({ ...runtimeDoc('applied'), lastVerifiedAt })),
    ...[{ backendContextTokens: 0 }, { supportedThinkingLevels: ['high', 'high'] }, { supportedThinkingLevels: [true] },
      { samplingSupported: 1 }, { activeMaxOutputTokens: 999999 }, { capacitySource: 'backend-observed' }]
      .map(caps => ({ ...runtimeDoc(), capabilities: { ...runtimeDoc().capabilities, ...caps } })),
  ]) expect(() => readRuntime(value)).toThrow()
})

it('constructs only revision-bound fixed requests, including recovery without readable preferences', () => {
  expect(runtimeChangeRequest(runtimeDoc(), 'apply', 3)).toEqual({ operation: 'apply', revision: 'a'.repeat(64), settingsRevision: 3 })
  expect(runtimeChangeRequest(runtimeDoc('pending'), 'recover', null)).toEqual({ operation: 'recover', revision: 'a'.repeat(64), settingsRevision: 3 })
  for (const [value, op, revision] of [[runtimeDoc(), 'apply', 4], [runtimeDoc('applied'), 'apply', 3],
    [runtimeDoc('pending'), 'apply', 3], [runtimeDoc(), 'recover', 3], [runtimeDoc('unavailable'), 'apply', 3]]) {
    expect(() => runtimeChangeRequest(value, op, revision)).toThrow()
  }
})

it('requires fresh matching numeric readback, not any applied state or the opaque hash', () => {
  const result = { outcome: 'applied', appliedRevision: 3 }
  expect(confirmOutcome(result, { ...runtimeDoc('applied'), revision: 'b'.repeat(64) }, 3)).toMatch(/revision 3.*verified/)
  for (const value of [runtimeDoc('applied', 4), runtimeDoc('saved-changes', 4), runtimeDoc('pending'), runtimeDoc('unavailable')]) {
    expect(() => confirmOutcome(result, value, 3)).toThrow()
  }
  for (const result of [{ outcome: 'applied', appliedRevision: 4 }, { outcome: 'applied', appliedRevision: null },
    { outcome: 'rolled-back', appliedRevision: true }, { outcome: 'applied', appliedRevision: 3, secret: 'private' }]) {
    expect(() => readOutcome(result, 3)).toThrow()
  }
})

it('verifies both recovery outcomes without treating every recovery as rollback', () => {
  expect(confirmOutcome({ outcome: 'applied', appliedRevision: 3 }, runtimeDoc('applied'), 3)).toMatch(/is applied/)
  expect(confirmOutcome({ outcome: 'rolled-back', appliedRevision: 2 }, runtimeDoc('saved-changes'), 3)).toMatch(/restored/)
  expect(confirmOutcome({ outcome: 'rolled-back', appliedRevision: null }, runtimeDoc('restored'), 3)).toMatch(/restored/)
  expect(() => confirmOutcome({ outcome: 'rolled-back', appliedRevision: null }, runtimeDoc(), 3)).toThrow()
  expect(() => confirmOutcome({ outcome: 'rolled-back', appliedRevision: 2 }, runtimeDoc(), 3)).toThrow()
})
