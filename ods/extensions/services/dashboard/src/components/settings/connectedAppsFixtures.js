// Status replies and a client file in the exact shapes the dashboard API returns, for tests.

export const READ_BOTH = {'calendar.read': true, 'calendar.write': false, 'calendar.direct': false,
  'gmail.read': true, 'gmail.drafts': false}

export const INSTALLED = {client_id: '123456789012-abcdefghijklmnopqrstuvwxyz012345.apps.googleusercontent.com',
  client_secret: 'test-secret-test', project_id: 'ods-owner-calendar', auth_uri: 'https://accounts.google.com/o/oauth2/auth',
  token_uri: 'https://oauth2.googleapis.com/token', redirect_uris: ['http://localhost']}

export const GOOGLE_CATALOG = {id: 'google', name: 'Google', summary: 'Calendar and Gmail', kinds: ['source', 'actuator'],
  platforms: ['linux-systemd', 'wsl-systemd'], auth: {type: 'oauth2-pkce', client: 'owner-desktop-app', redirects: ['loopback', 'paste']},
  capabilities: [
    {id: 'calendar.read', label: 'Read your calendar', effect: 'read', scopes: [], tools: [], default: true, available: true, requires: []},
    {id: 'calendar.write', label: 'Suggest calendar changes you approve', effect: 'external-write', scopes: [], tools: [], default: false, available: true, requires: ['calendar.read']},
    {id: 'calendar.direct', label: 'Add or move your own private events without asking', effect: 'private-write', scopes: [], tools: [], default: false, available: false, requires: ['calendar.write']},
    {id: 'gmail.read', label: 'Read email summaries', effect: 'read', scopes: [], tools: [], default: true, available: true, requires: []},
    {id: 'gmail.drafts', label: 'Write Gmail drafts you send yourself', effect: 'draft', scopes: [], tools: [], default: false, available: false, requires: ['gmail.read']},
  ],
  data: {calendarId: 'primary', calendarPastDays: 7, calendarFutureDays: 90, gmailWindowDays: 180}}

const SOURCE = {state: 'ok', errorClass: null, httpStatus: null, lastSuccessAt: '2026-10-25T20:29:00Z', failingSince: null,
  recordCount: 12, complete: true}

export function statusFor(google = {}, top = {}) {
  const connected = Boolean(google.state) && google.state !== 'not-connected'
  return {schemaVersion: 1, available: true, surface: 'linux-systemd', reason: null, ownerTimeZone: 'America/Chicago', ...top,
    connectors: {google: {state: 'not-connected', reason: null, account: connected ? 'owner@example.com' : null,
      capabilities: connected ? READ_BOTH : null, cloudModels: connected ? true : null,
      connectedAt: connected ? '2026-10-25T20:00:00Z' : null, grantedScopes: null, checkedAt: connected ? '2026-10-25T20:29:30Z' : null,
      sources: connected && google.state !== 'first-sync' ? {email: SOURCE, calendar: SOURCE} : {email: null, calendar: null},
      pending: null, waitingChanges: connected ? 0 : null, ...google}},
    catalog: [GOOGLE_CATALOG]}
}

export const pendingStep = (phase, error = null, expiresAt = null) => ({
  action: phase === 'deactivating' ? 'disconnect' : 'connect', phase, error,
  expiresAt: phase === 'consent' ? expiresAt ?? new Date(Date.now() + 10 * 60 * 1000).toISOString().replace(/\.\d+Z$/, 'Z') : null,
  since: '2026-10-25T20:29:00Z'})

// Calendar changes Portal suggested, as GET /api/pixel/connections/google/changes lists them.
export const CHANGE = {proposalId: 'calendar-1761500000000-0badc0de', sha256: 'c'.repeat(64), state: 'waiting', action: 'create',
  createdAt: '2026-10-26T15:00:00.000Z', event: null, tooLarge: false,
  change: {summary: 'Dentist', start: '2026-10-27T15:00:00-05:00', end: '2026-10-27T16:00:00-05:00', allDay: false,
    timeZone: 'America/Chicago', location: '12 Main St', description: 'Bring the form', attendees: ['pat@example.com'],
    sendUpdates: 'all'}}

export const MOVE = {proposalId: 'calendar-1761500000001-0badc0de', sha256: 'd'.repeat(64), state: 'waiting', action: 'update',
  createdAt: '2026-10-26T15:01:00.000Z', tooLarge: false,
  change: {summary: null, start: '2026-10-28T09:00:00-05:00', end: '2026-10-28T09:30:00-05:00', allDay: null, timeZone: null,
    location: null, description: null, attendees: null, sendUpdates: 'none'},
  event: {title: 'Team sync', start: '2026-10-27T10:00:00-05:00', end: '2026-10-27T10:30:00-05:00', allDay: false}}

export const REMOVE = {proposalId: 'calendar-1761500000002-0badc0de', sha256: 'e'.repeat(64), state: 'unconfirmed', action: 'delete',
  createdAt: '2026-10-26T15:02:00.000Z', tooLarge: false,
  change: {summary: null, start: null, end: null, allDay: null, timeZone: null, location: null, description: null,
    attendees: null, sendUpdates: 'none'},
  event: {title: 'Lunch', start: '2026-10-29', end: '2026-10-30', allDay: true}}

export const UNREADABLE = {proposalId: 'calendar-1761500000003-0badc0de', sha256: null, state: 'invalid', action: null,
  createdAt: null, change: null, event: null, tooLarge: false}
