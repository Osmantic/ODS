import { normalizeDisplayName, readIdentity, readIdentityResponse } from './portalIdentity'

it.each([['', 'Assistant'], ['   ', 'Assistant'], ['\u00a0', 'Assistant'], [' Cafe\u0301 ', 'Café'], ['👩\u200d💻', '👩\u200d💻'], ['😀'.repeat(60), '😀'.repeat(60)]])('normalizes inert display text %j', (raw, expected) => {
  expect(normalizeDisplayName(raw)).toBe(expected)
})
it.each([null, true, 1, [], {}, '\nNova', 'Nova\t', '\u202eNova', '\ufeffNova', '\ud800', 'A\u2028B', 'A\u2029B', 'x'.repeat(61), ' '.repeat(241)])('rejects invalid name %j', value => {
  expect(() => normalizeDisplayName(value)).toThrow()
})
it.each([{ schemaVersion: true }, { revision: true }, { revision: -1 }, { revision: 2 ** 53 }, { displayName: ' Nova ' }, { displayName: '' }, { extra: 'private' }])('rejects invalid stored identity %j', change => {
  expect(() => readIdentity({ schemaVersion: 1, revision: 0, displayName: 'Assistant', ...change })).toThrow()
})
it('preserves a valid historical Portal display name as owner data', () => {
  expect(readIdentity({ schemaVersion: 1, revision: 4, displayName: 'Portal' })).toEqual({ schemaVersion: 1, revision: 4, displayName: 'Portal' })
})
it('bounds streamed responses, rejects malformed UTF8 and releases the reader', async () => {
  for (const bytes of [new TextEncoder().encode('x'.repeat(2049)), new Uint8Array([0xff])]) {
    const response = new globalThis.Response(bytes)
    await expect(readIdentityResponse(response)).rejects.toThrow()
    expect(response.body.locked).toBe(false)
  }
})
