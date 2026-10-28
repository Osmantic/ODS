// Connected apps as the owner reads them. Pure: the custodian's status in,
// words and the one fix that resolves each state out. Every code the custodian
// can report has an entry; anything else reads as a real failure with help.

// Google Cloud pages the wizard and the fixes link to, as Google's own
// Workspace setup guides link them.
export const GOOGLE_CONSOLE = {
  createProject: 'https://console.cloud.google.com/projectcreate',
  enableGmail: 'https://console.cloud.google.com/apis/enableflow;apiid=gmail.googleapis.com',
  enableCalendar: 'https://console.cloud.google.com/apis/enableflow;apiid=calendar-json.googleapis.com',
  branding: 'https://console.developers.google.com/auth/branding',
  audience: 'https://console.developers.google.com/auth/audience',
  clients: 'https://console.developers.google.com/auth/clients',
}

const PROJECT_ID = /^[a-z][a-z0-9-]{4,28}[a-z0-9]$/
const CLIENT_ID = /^[0-9]{4,32}-[a-z0-9]{8,64}\.apps\.googleusercontent\.com$/
const CLIENT_SECRET = /^[\x21-\x7e]{8,256}$/
const AUTH_URI = 'https://accounts.google.com/o/oauth2/auth'
const TOKEN_URI = 'https://oauth2.googleapis.com/token'

// A console page opened on the owner's own project, once the wizard knows it.
export function consoleLink(url, projectId) {
  return projectId && PROJECT_ID.test(projectId) ? `${url}?project=${projectId}` : url
}

// A configured deployment is not a claim of Google brand/scope approval. An
// older catalog can still offer explicit Advanced setup without sending new
// fields to an older host. Unknown or partial managed frames fail closed.
export function googleSignInOptions(auth) {
  const legacy = auth?.type === 'oauth2-pkce' && auth?.client === 'owner-desktop-app'
    && auth.mode === undefined && auth.state === undefined && auth.advancedAvailable === undefined
  const managed = auth?.type === 'oauth2-pkce' && auth?.client === 'ods-managed-desktop-app'
    && auth.mode === 'managed' && ['configured', 'not-configured', 'unavailable'].includes(auth.state)
    && typeof auth.advancedAvailable === 'boolean'
  return {legacy, managed, ready: managed && auth.state === 'configured',
    state: managed ? auth.state : 'unavailable', advancedAvailable: managed ? auth.advancedAvailable : legacy}
}

export function googleConnectionMode(google, auth) {
  if (google?.authMode === 'advanced') return 'advanced'
  if (google?.authMode === 'managed') return 'managed'
  return googleSignInOptions(auth).legacy ? 'advanced' : 'managed'
}

// Google's downloaded client file, checked here before it leaves the browser.
// The custodian checks it again; this only names the fix sooner.
export function readClientFile(text) {
  let value
  try { value = JSON.parse(text) } catch { return {error: 'client-invalid'} }
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== 1) return {error: 'client-invalid'}
  if (value.web) return {error: 'client-not-desktop'}
  const installed = value.installed
  if (!installed || typeof installed !== 'object' || Array.isArray(installed)) return {error: 'client-invalid'}
  if (!CLIENT_ID.test(installed.client_id ?? '') || !CLIENT_SECRET.test(installed.client_secret ?? '')
      || !PROJECT_ID.test(installed.project_id ?? '')
      || installed.auth_uri !== AUTH_URI || installed.token_uri !== TOKEN_URI) return {error: 'client-invalid'}
  const redirects = installed.redirect_uris ?? ['http://localhost']
  if (!Array.isArray(redirects) || !redirects.includes('http://localhost')) return {error: 'client-not-desktop'}
  return {client: value, projectId: installed.project_id}
}

// [what happened, what to do, whether it is a real failure that gets the Discord link]
const FILE_PROBLEMS = {
  'client-not-desktop': ['That file is for another kind of Google client.',
    'Create an OAuth client of type Desktop app, download its JSON file and choose it here.', false],
  'client-invalid': ["That isn't a Google OAuth client file.",
    'Download the JSON file of your Desktop app client from Google Cloud and choose it here.', false],
}

const SIGN_IN = {
  'access-denied': ["You didn't allow access at Google.", "Try again, and choose Continue on Google's screens.", false],
  'consent-failed': ["Google couldn't finish the sign-in.", 'Try again.', false],
  'sign-in-flow-expired': ['That sign-in took too long and expired.', 'Start again.', false],
  'flow-expired': ['That sign-in took too long and expired.', 'Start again.', false],
  'sign-in-flow-unknown': ['That sign-in is no longer open.', 'Start again.', false],
  'state-mismatch': ['That address belongs to a different sign-in.', 'Paste the full address from the Google tab opened for this connection.', false],
  'sign-in-state-mismatch': ['That address belongs to a different sign-in.', 'Paste the full address from the Google tab opened for this connection.', false],
  'address-not-from-sign-in': ["That isn't the address Google sent you to.",
    "Copy the whole address from the tab that didn't load, then paste it here.", false],
  'invalid-callback': ["That isn't the address Google sent you to.",
    "Copy the whole address from the tab that didn't load, then paste it here.", false],
  'missing-scope': ["Google didn't grant access to anything you turned on.",
    "Try again, and leave every box checked on Google's screen.", false],
  'invalid-grant': ["Google didn't accept the sign-in. Its code works only once, for a few minutes.", 'Start again.', false],
  'redirect-mismatch': ["Google didn't accept the address it should return to.",
    'Make sure your client is a Desktop app, then choose its file again.', false],
  'client-invalid': ["Google didn't accept your app's client.",
    'Download the Desktop app client file again and choose it here.', false],
  'blocked-by-admin': ['Your Google Workspace administrator blocks this app.',
    'Ask your administrator to allow it, then try again.', false],
  'api-disabled': ['An API your app needs is turned off in Google Cloud.',
    'Turn on the Gmail and Calendar APIs, then try again.', false],
  network: ["ODS couldn't reach Google.", 'Check the internet connection, then try again.', false],
  'rate-limited': ['Google is limiting requests right now.', 'Wait a minute, then try again.', false],
  'no-refresh-token': ["Google didn't give ODS lasting access.",
    'Start again. If it keeps happening, remove the app from your Google account\'s third-party access first.', true],
  'account-unverified': ["Google didn't confirm which account signed in, so ODS removed the sign-in.",
    'Start again with an account whose email Google has verified.', true],
  'sign-in-exchange-unavailable': ["Google sign-in couldn't finish on this machine.", 'Try again.', true],
}
const SIGN_IN_FALLBACK = ["ODS couldn't finish connecting Google.", 'Try again.', true]
const MANAGED_SIGN_IN = {
  'redirect-mismatch': ["Google couldn't return to ODS.", 'Try again. If this continues, ask for help with ODS Google sign-in.', true],
  'client-invalid': ["Google isn't accepting ODS sign-in right now.", 'Try again later, or ask for help with ODS Google sign-in.', true],
  'api-disabled': ['An API this connection needs is turned off.', 'Ask for help with the Google sign-in provided by this ODS installation.', true],
  'invalid-grant': ["Google no longer accepts this connection.", 'Reconnect to choose your account and allow access again.', false],
  'google-app-not-configured': ['Standard Google sign-in is not available in this ODS installation yet.',
    'Wait for an ODS update that provides it.', false],
  'google-app-config-unavailable': ['ODS could not load its Google sign-in configuration.',
    'Try again, or ask for help with this ODS installation.', true],
}
for (const code of ['google-app-config-invalid', 'google-app-config-unsafe']) {
  MANAGED_SIGN_IN[code] = MANAGED_SIGN_IN['google-app-config-unavailable']
}

// Why Portal's gateway update stopped. Finish runs it again.
const UPDATE = {
  'runtime-busy': ['Portal is busy with a task.', 'Press Finish when it is done.', false],
  'transition-busy': ['ODS is finishing another Portal change.', 'Press Finish in a moment.', false],
  'model-lifecycle-busy': ['ODS is finishing another Portal change.', 'Press Finish in a moment.', false],
  'transition-recovery-required': ['ODS is finishing another Portal change.', 'Press Finish in a moment.', false],
  'gateway-change-rolled-back': ["Portal couldn't start with the new connection, so it's back on its previous setup.",
    'Press Finish to try again.', true],
  stalled: ["Portal hasn't picked up the change yet.", 'Press Finish to try again.', false],
}
const UPDATE_FALLBACK = ["Portal hasn't picked up the change yet.", 'Press Finish to try again.', true]
// The custodian gives a Portal update five minutes. One still silent after six has stopped, so Finish runs it again.
const STALLED_AFTER = 6 * 60 * 1000
const stalled = (pending, now) => !pending.error && now - Date.parse(pending.since) > STALLED_AFTER

const REASONS = {
  'sign-in-missing': ["Google's sign-in isn't stored on this machine, for example after a restore.",
    'Reconnect to turn your calendar and email back on.', false],
  'invalid-grant': ['Google no longer accepts this connection: access was removed, the password changed, or your Google app is still in Testing, where Google ends sign-ins after 7 days.',
    'Reconnect. To stop weekly sign-outs, publish your app on its Audience page in Google Cloud.', false],
  'not-connected': ["The connection's sign-in is missing.", 'Reconnect.', false],
  'blocked-by-admin': ['Your Google Workspace administrator blocks this app.',
    'Ask your administrator to allow it, then reconnect.', false],
  'api-disabled': ['The Gmail or Calendar API is turned off in your Google project.',
    'Turn both on in Google Cloud, then press Sync now.', false],
  'client-invalid': ["Google no longer accepts your app's client.",
    'Reconnect with a new Desktop app client file.', false],
  'missing-scope': ["Google didn't grant access to everything you turned on.",
    "Reconnect, and leave every box checked on Google's screen.", false],
  'rate-limited': ['Google is limiting requests right now. ODS keeps trying.', 'Sync now, or wait a few minutes.', false],
  network: ['Google is slow or unreachable. ODS keeps trying.', 'Sync now, or wait a few minutes.', false],
  'sync-stopped': ['The sync service stopped updating.', 'Press Sync now to restart it.', true],
  unknown: ["Syncing stopped for a reason ODS doesn't recognize.", 'Press Sync now to try again.', true],
  'status-unreadable': ["ODS couldn't read the sync status.", 'Press Sync now to try again.', true],
}
const REASON_FALLBACK = ["Syncing stopped for a reason ODS doesn't recognize.", 'Press Sync now to try again.', true]

function explain(table, fallback, code) {
  const [message, fix, help] = table[code] ?? fallback
  return {message, fix, help}
}

export const fileProblem = code => explain(FILE_PROBLEMS, FILE_PROBLEMS['client-invalid'], code)
export const signInProblem = (code, mode = 'advanced') => explain(mode === 'managed'
  ? {...SIGN_IN, ...MANAGED_SIGN_IN} : SIGN_IN, SIGN_IN_FALLBACK, code)
export const connectionProblem = (error, mode) => mode === 'managed' && MANAGED_SIGN_IN[error.code]
  ? signInProblem(error.code, mode) : {message: error.message, fix: null, help: Boolean(error.help)}

// [chip label, tone, the actions the card offers]
const STATES = {
  'not-connected': ['Not connected', 'neutral', ['connect']],
  'first-sync': ['Syncing', 'progress', ['disconnect']],
  connected: ['Connected', 'ok', ['sync', 'reconnect', 'disconnect']],
  behind: ['Behind', 'warn', ['sync', 'disconnect']],
  'needs-reconnect': ['Needs reconnecting', 'error', ['reconnect', 'disconnect']],
  'missing-permission': ['Missing permission', 'warn', ['reconnect', 'disconnect']],
  'setup-broken': ['Setup needs a fix', 'error', ['sync', 'reconnect', 'disconnect']],
  'blocked-by-admin': ['Blocked by your administrator', 'error', ['reconnect', 'disconnect']],
  'unknown-error': ['Sync problem', 'error', ['sync', 'disconnect']],
}

const CAPABILITY_WORDS = {'calendar.read': 'Calendar: read', 'calendar.write': 'Calendar: changes you approve',
  'calendar.direct': 'Calendar: your own private events', 'gmail.read': 'Email: summaries', 'gmail.drafts': 'Email: drafts'}

// The one line under the account: what Portal may use.
export function capabilitySummary(capabilities) {
  return Object.entries(capabilities ?? {}).filter(([, on]) => on).map(([id]) => CAPABILITY_WORDS[id]).filter(Boolean).join(' · ')
}

function pendingView(pending, now, mode) {
  if (pending.phase === 'consent') {
    // Only the wizard that opened a sign-in can cancel it; a new one replaces it.
    return Date.parse(pending.expiresAt) > now
      ? {kind: 'waiting', tone: 'progress', message: 'A Google sign-in is open. Finish it in its tab, or start again.', fix: null, help: false, actions: ['connect']}
      : {kind: 'expired', tone: 'neutral', message: 'That sign-in took too long and expired.', fix: 'Start again.', help: false, actions: ['connect']}
  }
  if (pending.phase === 'failed') {
    // The current account may still use a different app from this failed
    // attempt. With no attempt mode in public status, never prescribe a client
    // file/project based on the account's older authorization.
    const problem = mode === 'unknown' && ['client-invalid', 'redirect-mismatch', 'api-disabled'].includes(pending.error)
      ? {message: "Google couldn't finish this sign-in.", fix: 'Try again, or ask for help with this connection.', help: true}
      : signInProblem(pending.error, mode === 'unknown' ? 'advanced' : mode)
    return {kind: 'failed', tone: 'error', ...problem, actions: ['connect']}
  }
  const action = pending.phase === 'activating' ? "Bringing Portal's calendar and email tools up to date." : 'Removing your calendar and email from Portal.'
  if (!pending.error && !stalled(pending, now)) return {kind: 'updating', tone: 'progress', message: action, fix: null, help: false, actions: []}
  return {kind: 'update-stopped', tone: 'warn', ...explain(UPDATE, UPDATE_FALLBACK, pending.error ?? 'stalled'), actions: ['finish']}
}

// Everything the Google card shows, from one status reply.
export function googleCard(status, now = Date.now()) {
  if (!status?.available) {
    return {chip: 'Not available', tone: 'neutral', message: "Connected apps need ODS on Linux or WSL. They aren't available on this system yet.",
      fix: null, help: false, actions: [], step: null}
  }
  if (status.reason === 'record-unusable') {
    return {chip: 'Needs a fix', tone: 'error', message: "ODS couldn't read your saved connection, so connected apps are off.",
      fix: 'Disconnect Google, then connect it again.', help: true, actions: ['disconnect'], step: null}
  }
  const google = status.connectors.google
  const mode = googleConnectionMode(google, status.catalog?.find(entry => entry.id === 'google')?.auth)
  const known = Object.hasOwn(STATES, google.state)
  const [chip, tone, actions] = STATES[known ? google.state : 'unknown-error']
  const card = {chip, tone, message: null, fix: null, help: false, actions, step: null}
  const reason = google.reason ?? (known && google.state !== 'unknown-error' ? null : 'unknown')
  if (reason) Object.assign(card, mode === 'managed' && MANAGED_SIGN_IN[reason]
    ? signInProblem(reason, mode) : explain(REASONS, REASON_FALLBACK, reason))
  if (google.state === 'first-sync') card.message = 'Portal is reading your calendar and email for the first time. A large mailbox takes a while.'
  if (google.pending) {
    const auth = status.catalog?.find(entry => entry.id === 'google')?.auth
    card.step = pendingView(google.pending, now, googleSignInOptions(auth).legacy ? 'advanced' : 'unknown')
  } else if (status.reason === 'journal-unusable') {
    card.step = {kind: 'update-stopped', tone: 'warn', message: 'ODS lost track of an unfinished step.',
      fix: 'Press Finish to bring Portal up to date.', help: false, actions: ['finish']}
  }
  return card
}

// While a step runs or the first sync is underway, status changes on its own.
export function refreshDelay(status, now = Date.now()) {
  const google = status?.connectors?.google
  if (!status?.available || !google) return null
  if (google.pending?.phase === 'consent' && Date.parse(google.pending.expiresAt) > now) return 2000
  if (google.pending && !google.pending.error && google.pending.phase !== 'failed' && !stalled(google.pending, now)) return 3000
  if (google.state === 'first-sync') return 5000
  return null
}
