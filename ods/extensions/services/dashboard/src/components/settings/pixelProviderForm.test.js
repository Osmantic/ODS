import { configurationError, createProvider, prepareSave, readConfiguration } from './pixelProviderForm'
const doc = () => ({ schemaVersion: 1, revision: 0, enabled: false,
  providers: [{ ...createProvider('tower', 'Tower', []), model: 'glm', enabled: true }],
  roles: { leader: null, backups: [], advisor: null, handoff: null },
  policy: { allowCloud: false, maxAttempts: 3, deadlineSeconds: 120 } })

it('validates disabled configurations and preserves blank numeric drafts as errors', () => {
  const value = doc()
  value.providers[0].contextTokens = ''
  expect(configurationError(value)).toContain('whole numbers')
  value.providers[0].contextTokens = 32768
  value.providers[0].model = ''
  expect(configurationError(value)).toContain('model')
})
it.each([null, {}, { providers: [] }, { ...doc(), roles: null },
  { ...doc(), policy: [] }, { ...doc(), revision: true }])('rejects malformed public response %#', value => {
  expect(() => readConfiguration({ configuration: value })).toThrow('invalid-response')
})
it('rejects unknown server fields and missing role fields', () => {
  const value = doc()
  value.providers[0].credentialRef = 'must-not-be-public'
  expect(() => readConfiguration({ configuration: value })).toThrow()
  delete value.providers[0].credentialRef
  delete value.roles.advisor
  expect(configurationError(value)).toBeTruthy()
})
it('keeps blank keys unchanged but distinguishes removal from replacement', () => {
  const value = doc()
  value.providers[0].hasCredential = true
  expect(prepareSave(value, value, { tower: '' }, {}).credentialChanges).toEqual({})
  expect(prepareSave(value, value, {}, { tower: true }).credentialChanges).toEqual({ tower: { action: 'remove' } })
  expect(prepareSave(value, value, { tower: 'synthetic-key' }, {}).credentialChanges)
    .toEqual({ tower: { action: 'set', value: 'synthetic-key' } })
})
it('accepts a pending new cloud key and rejects removal from an enabled cloud profile', () => {
  const value = doc()
  value.providers[0].kind = 'cloud'
  expect(() => prepareSave(value, value, { tower: 'synthetic-key' }, {})).not.toThrow()
  value.providers[0].hasCredential = true
  expect(() => prepareSave(value, value, {}, { tower: true })).toThrow('Disable it')
})
it.each(['secret\nvalue', 'secret value', 'x'.repeat(8193)])('rejects invalid key characters/size without echoing them', key => {
  try { prepareSave(doc(), doc(), { tower: key }, {}) } catch (error) {
    expect(error.message).not.toContain(key)
    return
  }
  throw new Error('Expected validation error')
})
it('enforces explicit cloud opt-in for role selection when enabled', () => {
  const value = doc()
  value.providers[0].kind = 'cloud'
  value.providers[0].hasCredential = true
  value.roles.leader = 'tower'
  value.enabled = true
  expect(configurationError(value)).toContain('opt-in')
  value.policy.allowCloud = true
  expect(configurationError(value)).toBeNull()
})
it('requires a fresh revision in successful POST responses', () => {
  expect(() => readConfiguration({ configuration: doc() }, 1)).toThrow()
})
it('rejects duplicate/invalid IDs before adding providers', () => {
  expect(() => createProvider('tower', 'Again', doc().providers)).toThrow('unique')
  expect(() => createProvider('bad\n', 'Invalid', [])).toThrow('unique')
})
