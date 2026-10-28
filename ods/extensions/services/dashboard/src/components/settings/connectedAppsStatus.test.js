import { describe, expect, it } from 'vitest'
import { GOOGLE_CONSOLE, capabilitySummary, connectionProblem, consoleLink, googleCard, googleSignInOptions, readClientFile, refreshDelay, signInProblem } from './connectedAppsStatus'
import { GOOGLE_CATALOG, INSTALLED, READ_BOTH, statusFor } from './connectedAppsFixtures'

const NOW = Date.parse('2026-10-25T20:30:00Z')

describe("the owner's client file", () => {
  it('accepts the Desktop app file Google downloads and keeps its project', () => {
    expect(readClientFile(JSON.stringify({installed: INSTALLED}))).toEqual({client: {installed: INSTALLED}, projectId: 'ods-owner-calendar'})
    const minimal = {...INSTALLED}
    delete minimal.redirect_uris
    expect(readClientFile(JSON.stringify({installed: minimal})).projectId).toBe('ods-owner-calendar')
  })

  it('names the fix for every other file', () => {
    for (const [value, code] of [
      [{web: INSTALLED}, 'client-not-desktop'],
      [{installed: {...INSTALLED, redirect_uris: ['https://example.com/callback']}}, 'client-not-desktop'],
      [{installed: {...INSTALLED, token_uri: 'https://example.com/token'}}, 'client-invalid'],
      [{installed: {...INSTALLED, client_id: 'not-a-client'}}, 'client-invalid'],
      [{installed: INSTALLED, web: INSTALLED}, 'client-invalid'],
      [{type: 'service_account'}, 'client-invalid'],
    ]) expect(readClientFile(JSON.stringify(value))).toEqual({error: code})
    expect(readClientFile('{not json')).toEqual({error: 'client-invalid'})
  })
})

describe('console links', () => {
  it("open the owner's own project once it is known", () => {
    expect(consoleLink(GOOGLE_CONSOLE.audience, 'ods-owner-calendar')).toBe('https://console.developers.google.com/auth/audience?project=ods-owner-calendar')
    expect(consoleLink(GOOGLE_CONSOLE.audience, null)).toBe(GOOGLE_CONSOLE.audience)
    expect(consoleLink(GOOGLE_CONSOLE.audience, 'x"><script>')).toBe(GOOGLE_CONSOLE.audience)
  })
})

describe('ordinary and Advanced Google sign-in', () => {
  const auth = (state, advancedAvailable = true) => ({...GOOGLE_CATALOG.auth,
    client: 'ods-managed-desktop-app', mode: 'managed', state, advancedAvailable})

  it('uses only the complete advertised managed status to enable ordinary sign-in', () => {
    expect(googleSignInOptions(auth('configured'))).toMatchObject({managed: true, ready: true, advancedAvailable: true, legacy: false})
    for (const state of ['not-configured', 'unavailable']) {
      expect(googleSignInOptions(auth(state, false))).toMatchObject({managed: true, ready: false, advancedAvailable: false})
    }
    for (const value of [null, {}, {...auth('configured'), advancedAvailable: 'true'},
      {...auth('configured'), state: 'verified'}, {...auth('configured'), mode: 'advanced'},
      {...auth('configured'), client: 'owner-desktop-app'}]) {
      expect(googleSignInOptions(value)).toMatchObject({managed: false, ready: false, advancedAvailable: false})
    }
  })

  it('recognizes the exact legacy host as explicit Advanced only', () => {
    expect(googleSignInOptions(GOOGLE_CATALOG.auth)).toMatchObject({legacy: true, ready: false, advancedAvailable: true})
  })

  it('does not send ordinary owners to create or publish a Google Cloud project for a managed failure', () => {
    const catalog = [{...GOOGLE_CATALOG, auth: auth('configured')}]
    const card = googleCard(statusFor({state: 'needs-reconnect', reason: 'invalid-grant'}, {catalog}), NOW)
    expect(card.message).toBe('Google no longer accepts this connection.')
    expect(card.fix).not.toMatch(/publish|Cloud|file|project/)
    const pending = googleCard(statusFor({pending: {phase: 'failed', error: 'client-invalid'}}, {catalog}), NOW)
    expect(pending.step.message).toBe("Google couldn't finish this sign-in.")
    expect(pending.step.fix).not.toMatch(/file|project/)
    expect(connectionProblem({code: 'client-invalid', message: 'Upload the client JSON file.'}, 'managed').message)
      .toBe("Google isn't accepting ODS sign-in right now.")
    expect(connectionProblem({code: 'client-invalid', message: 'Upload the client JSON file.'}, 'advanced').message)
      .toBe('Upload the client JSON file.')
  })

  it('does not confuse the old Advanced account with a newly failed sign-in attempt', () => {
    const catalog = [{...GOOGLE_CATALOG, auth: auth('configured')}]
    for (const error of ['client-invalid', 'redirect-mismatch', 'api-disabled']) {
      const card = googleCard(statusFor({state: 'connected', authMode: 'advanced',
        pending: {phase: 'failed', error}}, {catalog}), NOW)
      expect(card.chip).toBe('Connected')
      expect(card.step.message).toBe("Google couldn't finish this sign-in.")
      expect(card.step.fix).not.toMatch(/file|project|Cloud/)
      expect(card.step.help).toBe(true)
      expect(card.actions).toContain('sync')
    }
  })
})

describe('the Google card', () => {
  it.each([
    ['not-connected', null, 'Not connected', ['connect'], false],
    ['first-sync', null, 'Syncing', ['disconnect'], false],
    ['connected', null, 'Connected', ['sync', 'reconnect', 'disconnect'], false],
    ['behind', 'network', 'Behind', ['sync', 'disconnect'], false],
    ['behind', 'sync-stopped', 'Behind', ['sync', 'disconnect'], true],
    ['needs-reconnect', 'invalid-grant', 'Needs reconnecting', ['reconnect', 'disconnect'], false],
    ['needs-reconnect', 'sign-in-missing', 'Needs reconnecting', ['reconnect', 'disconnect'], false],
    ['missing-permission', 'missing-scope', 'Missing permission', ['reconnect', 'disconnect'], false],
    ['setup-broken', 'api-disabled', 'Setup needs a fix', ['sync', 'reconnect', 'disconnect'], false],
    ['setup-broken', 'client-invalid', 'Setup needs a fix', ['sync', 'reconnect', 'disconnect'], false],
    ['blocked-by-admin', 'blocked-by-admin', 'Blocked by your administrator', ['reconnect', 'disconnect'], false],
    ['unknown-error', 'status-unreadable', 'Sync problem', ['sync', 'disconnect'], true],
    ['unknown-error', null, 'Sync problem', ['sync', 'disconnect'], true],
  ])('%s (%s) shows its fix', (state, reason, chip, actions, help) => {
    const card = googleCard(statusFor({state, reason}), NOW)
    expect(card.chip).toBe(chip)
    expect(card.actions).toEqual(actions)
    expect(card.help).toBe(help)
    if (reason || state === 'unknown-error') expect(card.fix).toBeTruthy()
  })

  it('explains invalid-grant, including the 7-day Testing sign-out and how to stop it', () => {
    const card = googleCard(statusFor({state: 'needs-reconnect', reason: 'invalid-grant'}), NOW)
    expect(card.message).toMatch(/Testing, where Google ends sign-ins after 7 days/)
    expect(card.fix).toMatch(/publish your app/)
  })

  it('shows each step in progress, and why the last sign-in did not connect', () => {
    const step = pending => googleCard(statusFor({pending}), NOW).step
    expect(step({action: 'connect', phase: 'consent', expiresAt: '2026-10-25T20:40:00Z', error: null, since: '2026-10-25T20:25:00Z'}))
      .toMatchObject({kind: 'waiting', actions: ['connect']})
    expect(step({action: 'connect', phase: 'consent', expiresAt: '2026-10-25T20:29:00Z', error: null, since: '2026-10-25T20:14:00Z'}))
      .toMatchObject({kind: 'expired', actions: ['connect']})
    expect(step({action: 'connect', phase: 'activating', expiresAt: null, error: null, since: '2026-10-25T20:29:00Z'}))
      .toMatchObject({kind: 'updating', tone: 'progress', actions: []})
    expect(step({action: 'connect', phase: 'activating', expiresAt: null, error: 'runtime-busy', since: '2026-10-25T20:29:00Z'}))
      .toMatchObject({kind: 'update-stopped', actions: ['finish'], help: false})
    // An update that never reported back has stopped: Finish runs it again.
    expect(step({action: 'connect', phase: 'activating', expiresAt: null, error: null, since: '2026-10-25T20:23:00Z'}))
      .toMatchObject({kind: 'update-stopped', actions: ['finish'], help: false, message: "Portal hasn't picked up the change yet."})
    expect(step({action: 'disconnect', phase: 'deactivating', expiresAt: null, error: 'gateway-change-rolled-back', since: '2026-10-25T20:29:00Z'}))
      .toMatchObject({kind: 'update-stopped', actions: ['finish'], help: true})
    expect(step({action: 'connect', phase: 'failed', expiresAt: null, error: 'access-denied', since: '2026-10-25T20:29:00Z'}))
      .toMatchObject({kind: 'failed', actions: ['connect'], help: false, message: "You didn't allow access at Google."})
    expect(step({action: 'connect', phase: 'failed', expiresAt: null, error: 'sign-in-exchange-unavailable', since: '2026-10-25T20:29:00Z'}))
      .toMatchObject({kind: 'failed', help: true})
  })

  it('offers Finish when ODS lost track of a step, and Disconnect for an unreadable record', () => {
    expect(googleCard(statusFor({state: 'connected'}, {reason: 'journal-unusable'}), NOW).step).toMatchObject({actions: ['finish']})
    expect(googleCard(statusFor({}, {reason: 'record-unusable'}), NOW)).toMatchObject({actions: ['disconnect'], help: true})
  })

  it('offers a check of a held callback without inferring a stopped exchange or another sign-in', () => {
    const pending = {action: 'connect', phase: 'consent', expiresAt: '2026-10-25T20:29:00Z',
      error: null, since: '2026-10-25T20:14:00Z'}
    const frame = statusFor({state: 'connected', pending}, {reason: 'connection-admission-held'})
    expect(googleCard(frame, NOW).step).toMatchObject({kind: 'connection-update', tone: 'progress',
      actions: ['finish'], message: 'ODS is finishing a Google connection update.', help: false})
    frame.connectors.google.pending.error = 'invalid-grant'
    expect(googleCard(frame, NOW).step).toMatchObject({kind: 'connection-update', tone: 'warn',
      actions: ['finish'], message: "Google didn't accept the sign-in. Its code works only once, for a few minutes."})
    expect(googleCard(frame, NOW).step.fix).toBe('Press Finish to check this update before starting another sign-in.')
  })

  it('says plainly where connected apps are not available yet', () => {
    const card = googleCard({...statusFor(), available: false, surface: 'darwin', reason: 'not-available-on-this-system'}, NOW)
    expect(card).toMatchObject({chip: 'Not available', actions: []})
    expect(card.message).toMatch(/Linux or WSL/)
  })

  it('treats every unknown sign-in outcome as a real failure with help', () => {
    expect(signInProblem('something-new')).toMatchObject({help: true})
    expect(signInProblem('access-denied').help).toBe(false)
  })
})

it('summarizes only what Portal may use', () => {
  expect(capabilitySummary(READ_BOTH)).toBe('Calendar: read · Email: summaries')
  expect(capabilitySummary(null)).toBe('')
})

it('polls only while a step runs or the first sync is underway', () => {
  const consent = {action: 'connect', phase: 'consent', expiresAt: '2026-10-25T20:40:00Z', error: null, since: '2026-10-25T20:25:00Z'}
  expect(refreshDelay(statusFor({pending: consent}), NOW)).toBe(2000)
  expect(refreshDelay(statusFor({pending: {...consent, phase: 'activating', expiresAt: null}}), NOW)).toBe(3000)
  expect(refreshDelay(statusFor({pending: {...consent, phase: 'activating', expiresAt: null, since: '2026-10-25T20:23:00Z'}}), NOW)).toBeNull()
  expect(refreshDelay(statusFor({pending: {...consent, phase: 'activating', expiresAt: null, error: 'runtime-busy'}}), NOW)).toBeNull()
  expect(refreshDelay(statusFor({pending: {...consent, phase: 'failed', expiresAt: null, error: 'access-denied'}}), NOW)).toBeNull()
  expect(refreshDelay(statusFor({pending: {...consent, expiresAt: '2026-10-25T20:00:00Z'}},
    {reason: 'connection-admission-held'}), NOW)).toBe(3000)
  expect(refreshDelay(statusFor({pending: {...consent, error: 'invalid-grant'}},
    {reason: 'connection-admission-held'}), NOW)).toBeNull()
  expect(refreshDelay(statusFor({state: 'first-sync'}), NOW)).toBe(5000)
  expect(refreshDelay(statusFor({state: 'connected'}), NOW)).toBeNull()
  expect(refreshDelay({...statusFor(), available: false}, NOW)).toBeNull()
})
