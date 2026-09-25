import { CONTROLS, prepareSettingsSave, readSettings } from './pixelRuntimeSettingsForm'

const envelope = (preferences = {}, revision = 0) => ({
  configuration: { schemaVersion: 1, revision, preferences },
  runtime: { status: 'not-applied', reason: 'settings-runtime-not-integrated' },
})

it('reads sparse preferences without defaults and copies them independently', () => {
  const input = envelope({ temperature: 1, reasoningVisibility: null }, Number.MAX_SAFE_INTEGER)
  const snapshot = readSettings(input)
  expect(snapshot).toEqual({ revision: Number.MAX_SAFE_INTEGER, preferences: input.configuration.preferences })
  snapshot.preferences.temperature = 2
  expect(input.configuration.preferences.temperature).toBe(1)
  expect(Object.keys(CONTROLS)).toHaveLength(20)
})

it('reads new persistence-only marker without asserting runtime state', () => {
  const value = envelope({ contextTokens: 65536 }, 3)
  value.runtime = { status: 'not-inspected', reason: 'runtime-status-separate' }
  expect(readSettings(value)).toEqual({ revision: 3, preferences: { contextTokens: 65536 } })
  value.runtime.reason = 'settings-runtime-not-integrated'
  expect(() => readSettings(value)).toThrow()
})

it('rejects incomplete, private, malformed or non-plain envelopes', () => {
  for (const input of [null, [], {}, { configuration: envelope().configuration },
    { ...envelope(), secret: 'private' },
    { ...envelope(), configuration: { ...envelope().configuration, auth: {} } },
    { ...envelope(), runtime: { ...envelope().runtime, private: true } },
    { ...envelope(), runtime: { status: 'applied', reason: 'settings-runtime-not-integrated' } },
    envelope([], 0), envelope(new Date(), 0), envelope({}, true), envelope({}, -1),
    envelope({}, Number.MAX_SAFE_INTEGER + 1), envelope({ constructor: null }),
    envelope(JSON.parse('{"__proto__":null}')), envelope({ contextTokens: true }),
    envelope({ temperature: NaN }), envelope({ temperature: Infinity }),
    envelope({ temperature: '0.5' }), envelope({ topP: 0 }),
    envelope({ compactionNotify: 1 }), envelope({ thinking: 'invented' }),
    envelope({ contextTokens: 4096.1 }), envelope({ maxOutputTokens: 0 }),
  ]) expect(() => readSettings(input)).toThrow()
  expect(() => readSettings(envelope({}, 4), 3)).toThrow()
})

it('saves only explicit string edits and preserves null reset intent', () => {
  const snapshot = readSettings(envelope({ temperature: 0.5, verbosity: 'off' }, 4))
  expect(prepareSettingsSave(snapshot, { reasoningVisibility: 'stream', compactionNotify: 'false', temperature: '' }))
    .toEqual({ expectedRevision: 4, changes: { reasoningVisibility: 'stream', compactionNotify: false, temperature: null } })
  expect(snapshot.preferences).toEqual({ temperature: 0.5, verbosity: 'off' })
})

it('rejects nonstring, blank, nonfinite, unsupported, unsafe and protected edits', () => {
  const snapshot = readSettings(envelope())
  for (const changes of [null, [], {}, { temperature: ' ' }, { temperature: '\t' },
    { temperature: false }, { temperature: 1 }, { temperature: 'Infinity' },
    { temperature: 'NaN' }, { temperature: '0x1' }, { temperature: '3' },
    { contextTokens: '4096.5' }, { contextTokens: '9007199254740992' },
    { compactionNotify: 'yes' }, { toolProgress: 'imaginary' },
    ...['model', 'provider', 'auth', 'ODS_MODE', 'constructor', 'toString'].map(key => ({ [key]: 'x' })),
  ]) expect(() => prepareSettingsSave(snapshot, changes)).toThrow()
  for (const revision of [-1, true, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER]) {
    expect(() => prepareSettingsSave({ revision, preferences: {} }, { temperature: '1' })).toThrow()
  }
  for (const snapshot of [{ revision: 0, preferences: [], }, { revision: 0, preferences: {}, auth: 'private' },
    { revision: 0 }, { revision: 0, preferences: { temperature: false } }]) {
    expect(() => prepareSettingsSave(snapshot, { temperature: '1' })).toThrow()
  }
})
