import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ConnectedApps from './ConnectedApps'
import { GOOGLE_CATALOG, pendingStep, statusFor } from './connectedAppsFixtures'

const DISCORD = 'https://discord.gg/4ntNp9MAwC'
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

// The dashboard API as the card reaches it: GET status and access mode, POST actions.
function api(routes) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${url}`
    calls.push([key, options.body === undefined ? undefined : JSON.parse(options.body)])
    const route = routes[key] ?? (key === 'GET /api/pixel/access-mode' ? [200, {configured_mode: 'sandboxed'}]
      : key === 'GET /api/pixel/connections/apps' ? [404, {}] : null)
    if (!route) throw new Error(`unexpected request ${key}`)
    const [status, body] = typeof route === 'function' ? route() : route
    return {ok: status >= 200 && status < 300, status, json: async () => body}
  }))
  return calls
}

const card = () => screen.getByRole('article', {name: 'Google'})

it('offers Connect when nothing is connected, and opens the wizard in place', async () => {
  api({'GET /api/pixel/connections': [200, statusFor()]})
  render(<ConnectedApps />)
  expect(await within(await screen.findByRole('article', {name: 'Google'})).findByText('Not connected')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name: 'Connect Google'}))
  expect(screen.getByRole('heading', {name: 'Connect Google'})).toBeVisible()
  expect(screen.queryByRole('button', {name: 'Connect Google'})).toBeNull()
})

it.each([
  ['first-sync', null, ['Disconnect'], /reading your calendar and email for the first time/],
  ['connected', null, ['Sync now', 'Change access', 'Disconnect'], /Connected as owner@example.com · Calendar: read · Email: summaries/],
  ['behind', 'network', ['Sync now', 'Disconnect'], /slow or unreachable/],
  ['needs-reconnect', 'invalid-grant', ['Reconnect', 'Disconnect'], /ends sign-ins after 7 days/],
  ['needs-reconnect', 'sign-in-missing', ['Reconnect', 'Disconnect'], /after a restore/],
  ['missing-permission', 'missing-scope', ['Grant access', 'Disconnect'], /didn't grant access/],
  ['setup-broken', 'api-disabled', ['Sync now', 'Reconnect', 'Disconnect'], /API is turned off/],
  ['blocked-by-admin', 'blocked-by-admin', ['Reconnect', 'Disconnect'], /administrator blocks this app/],
  ['unknown-error', 'status-unreadable', ['Sync now', 'Disconnect'], /couldn't read the sync status/],
])('%s (%s) shows what happened and its fix', async (state, reason, buttons, text) => {
  api({'GET /api/pixel/connections': [200, statusFor({state, reason})]})
  render(<ConnectedApps />)
  expect(await screen.findByText(text)).toBeVisible()
  for (const name of buttons) expect(within(card()).getByRole('button', {name})).toBeEnabled()
})

it('links straight to the console page a setup fix needs', async () => {
  api({'GET /api/pixel/connections': [200, statusFor({state: 'setup-broken', reason: 'api-disabled'})]})
  render(<ConnectedApps />)
  expect(await screen.findByRole('link', {name: 'Gmail API'})).toHaveAttribute('href', expect.stringContaining('apiid=gmail.googleapis.com'))
  expect(screen.getByRole('link', {name: 'Google Calendar API'})).toHaveAttribute('href', expect.stringContaining('apiid=calendar-json.googleapis.com'))
})

it('says why the last sign-in did not connect, with the Discord link only for a real failure', async () => {
  api({'GET /api/pixel/connections': [200, statusFor({pending: pendingStep('failed', 'access-denied')})]})
  const {unmount} = render(<ConnectedApps />)
  expect(await screen.findByText("You didn't allow access at Google.")).toBeVisible()
  expect(within(card()).getByRole('button', {name: 'Try again'})).toBeEnabled()
  expect(screen.queryByRole('link', {name: /Discord/})).toBeNull()
  unmount()
  api({'GET /api/pixel/connections': [200, statusFor({pending: pendingStep('failed', 'sign-in-exchange-unavailable')})]})
  render(<ConnectedApps />)
  expect(await screen.findByText("Google sign-in couldn't finish on this machine.")).toBeVisible()
  expect(screen.getByRole('link', {name: /Discord/})).toHaveAttribute('href', DISCORD)
})

it('finishes a step Portal was too busy for with one button', async () => {
  let status = statusFor({state: 'first-sync', pending: pendingStep('activating', 'runtime-busy')})
  const calls = api({'GET /api/pixel/connections': () => [200, status],
    'POST /api/pixel/connections/google/finish': () => { status = statusFor({state: 'first-sync'}); return [200, {outcome: 'applied'}] }})
  render(<ConnectedApps />)
  expect(await screen.findByText('Portal is busy with a task.')).toBeVisible()
  fireEvent.click(within(card()).getByRole('button', {name: 'Finish'}))
  await waitFor(() => expect(screen.queryByText('Portal is busy with a task.')).toBeNull())
  expect(calls.filter(([key]) => key.startsWith('POST'))).toEqual([['POST /api/pixel/connections/google/finish', {}]])
})

it.each(['consent', 'activating', 'deactivating'])('offers Finish instead of a new sign-in during a retained %s update', async phase => {
  const status = statusFor({state: 'connected', pending: pendingStep(phase)}, {reason: 'connection-admission-held'})
  api({'GET /api/pixel/connections': [200, status]})
  render(<ConnectedApps />)
  expect(await screen.findByRole('button', {name: 'Finish'})).toBeEnabled()
  expect(screen.queryByRole('button', {name: /Start again|Connect Google|Change access|Reconnect/})).toBeNull()
  expect(screen.queryByText(/interrupted|stopped/i)).toBeNull()
})

it('exposes recovery after a pasted callback fails without a connected notice or another code exchange', async () => {
  vi.spyOn(window, 'open').mockReturnValue({opener: {}, location: {replace: vi.fn()}, close: vi.fn()})
  let status = managedStatus()
  const flowId = 'a'.repeat(32)
  const calls = api({'GET /api/pixel/connections': () => [200, status],
    'POST /api/pixel/connections/google/connect': [200, {flowId,
      authUrl: 'https://accounts.google.com/o/oauth2/auth?client_id=x&state=y',
      expiresAt: new Date(Date.now() + 600000).toISOString(), redirect: 'loopback'}],
    'POST /api/pixel/connections/google/complete': () => {
      status = {...managedStatus({pending: pendingStep('activating', 'invalid-grant')}), reason: 'connection-admission-held'}
      return [200, {outcome: 'failed', errorClass: 'invalid-grant', account: null, capabilities: null, missing: []}]
    },
    'POST /api/pixel/connections/google/finish': () => {
      status = managedStatus()
      return [200, {outcome: 'applied'}]
    }})
  render(<ConnectedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Connect Google'}))
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Google'}))
  const box = await screen.findByLabelText(/Copy its whole address and paste it here/)
  fireEvent.change(box, {target: {value: `http://127.0.0.1:53682/?state=${'s'.repeat(43)}&code=4/pasted`}})
  fireEvent.click(screen.getByRole('button', {name: 'Finish connecting'}))
  expect(await screen.findByRole('button', {name: 'Finish'})).toBeEnabled()
  expect(screen.queryByRole('heading', {name: 'Connect Google'})).toBeNull()
  expect(screen.queryByText(/Google is connected/)).toBeNull()
  expect(screen.queryByRole('button', {name: /Try again|Connect Google|Reconnect/})).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: 'Finish'}))
  expect(await screen.findByRole('button', {name: 'Connect Google'})).toBeEnabled()
  expect(calls.filter(([key]) => key.startsWith('POST')).map(([key]) => key)).toEqual([
    'POST /api/pixel/connections/google/connect', 'POST /api/pixel/connections/google/complete',
    'POST /api/pixel/connections/google/finish'])
})

it('lets the final failed status win after Google authorizes but the owner update rolls back', async () => {
  vi.spyOn(window, 'open').mockReturnValue({opener: {}, location: {replace: vi.fn()}, close: vi.fn()})
  let status = managedStatus()
  const calls = api({'GET /api/pixel/connections': () => [200, status],
    'POST /api/pixel/connections/google/connect': () => {
      status = managedStatus({pending: pendingStep('consent')})
      return [200, {flowId: 'a'.repeat(32), authUrl: 'https://accounts.google.com/o/oauth2/auth?state=x',
        expiresAt: new Date(Date.now() + 600000).toISOString(), redirect: 'loopback'}]
    },
    'POST /api/pixel/connections/google/complete': () => {
      status = managedStatus({pending: pendingStep('failed', 'gateway-change-rolled-back')})
      return [200, {outcome: 'connected', errorClass: null, account: 'owner@example.com',
        capabilities: statusFor({state: 'connected'}).connectors.google.capabilities, missing: []}]
    }})
  render(<ConnectedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Connect Google'}))
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Google'}))
  fireEvent.change(await screen.findByLabelText(/Copy its whole address and paste it here/),
    {target: {value: 'http://127.0.0.1:53682/?state=x&code=PRIVATE-code'}})
  fireEvent.click(screen.getByRole('button', {name: 'Finish connecting'}))
  expect(await screen.findByRole('button', {name: 'Try again'})).toBeEnabled()
  expect(within(card()).getByText('Not connected')).toBeVisible()
  expect(screen.queryByText(/Google is connected/)).toBeNull()
  expect(screen.queryByText(/Connected as/)).toBeNull()
  expect(screen.queryByRole('heading', {name: 'Connect Google'})).toBeNull()
  expect(calls.filter(([key]) => key.startsWith('POST'))).toHaveLength(2)
})

it('asks before disconnecting, then disconnects', async () => {
  const calls = api({'GET /api/pixel/connections': [200, statusFor({state: 'connected'})],
    'POST /api/pixel/connections/google/disconnect': [200, {outcome: 'disconnected', revoked: true}]})
  render(<ConnectedApps />)
  fireEvent.click(await within(await screen.findByRole('article', {name: 'Google'})).findByRole('button', {name: 'Disconnect'}))
  const dialog = screen.getByRole('dialog', {name: 'Disconnect Google?'})
  expect(calls.some(([key]) => key.startsWith('POST'))).toBe(false)
  fireEvent.click(within(dialog).getByRole('button', {name: 'Keep connected'}))
  expect(screen.queryByRole('dialog')).toBeNull()
  fireEvent.click(within(card()).getByRole('button', {name: 'Disconnect'}))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', {name: 'Disconnect'}))
  expect(await screen.findByText('Google is disconnected.')).toBeVisible()
  expect(calls.filter(([key]) => key.startsWith('POST'))).toEqual([['POST /api/pixel/connections/google/disconnect', {}]])
})

it('starts a sync, and shows a refusal with its reason and help', async () => {
  let sync = [200, {outcome: 'started'}]
  api({'GET /api/pixel/connections': [200, statusFor({state: 'connected'})],
    'POST /api/pixel/connections/google/sync': () => sync})
  render(<ConnectedApps />)
  fireEvent.click(await within(await screen.findByRole('article', {name: 'Google'})).findByRole('button', {name: 'Sync now'}))
  expect(await screen.findByText(/Sync started/)).toBeVisible()
  sync = [503, {detail: {code: 'connections-unavailable', message: "ODS couldn't reach the service that connects apps to Portal.", help: DISCORD}}]
  fireEvent.click(within(card()).getByRole('button', {name: 'Sync now'}))
  expect(await screen.findByRole('alert')).toHaveTextContent("ODS couldn't reach the service that connects apps to Portal.")
  expect(within(screen.getByRole('alert')).getByRole('link', {name: /Discord/})).toHaveAttribute('href', DISCORD)
})

it('warns on the card while Full Access is on', async () => {
  api({'GET /api/pixel/connections': [200, statusFor({state: 'connected'})],
    'GET /api/pixel/access-mode': [200, {configured_mode: 'full-access'}]})
  render(<ConnectedApps />)
  const note = await screen.findByText(/Full Access is on. Connected apps keep working/)
  expect(note).toHaveTextContent('approving its own calendar changes')
  expect(note).not.toHaveTextContent('by itself')
})

it('says private changes wait for approval while Full Access is on', async () => {
  const capabilities = {'calendar.read': true, 'calendar.write': true, 'calendar.direct': true, 'gmail.read': true,
    'gmail.drafts': false}
  api({'GET /api/pixel/connections': [200, statusFor({state: 'connected', capabilities})],
    'GET /api/pixel/access-mode': [200, {configured_mode: 'full-access'}]})
  render(<ConnectedApps />)
  expect(await screen.findByText(/Calendar changes Portal would make by itself wait for your approval until then/)).toBeVisible()
})

it('says plainly that connected apps are not available on this system', async () => {
  api({'GET /api/pixel/connections': [200, {...statusFor(), available: false, surface: 'darwin', reason: 'not-available-on-this-system'}]})
  render(<ConnectedApps />)
  expect(await screen.findByText(/aren't available on this system yet/)).toBeVisible()
  expect(within(card()).queryByRole('button')).toBeNull()
})

it('offers to try again when status is unavailable', async () => {
  let reply = [503, {detail: 'Connected apps status is unavailable right now. Try again in a moment.'}]
  api({'GET /api/pixel/connections': () => reply})
  render(<ConnectedApps />)
  expect(await screen.findByText('Connected apps status is unavailable right now.')).toBeVisible()
  reply = [200, statusFor()]
  fireEvent.click(screen.getByRole('button', {name: 'Try again'}))
  expect(await screen.findByRole('button', {name: 'Connect Google'})).toBeVisible()
})

function managedStatus(google = {}, state = 'configured') {
  return statusFor(google, {catalog: [{...GOOGLE_CATALOG, auth: {...GOOGLE_CATALOG.auth,
    client: 'ods-managed-desktop-app', mode: 'managed', state, advancedAvailable: true}}]})
}

it('opens the ordinary Google journey without client-file or Cloud-project steps', async () => {
  api({'GET /api/pixel/connections': [200, managedStatus()]})
  render(<ConnectedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Connect Google'}))
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeEnabled()
  expect(screen.queryByLabelText('Google client file')).toBeNull()
  expect(screen.queryByRole('link', {name: 'Create a Google Cloud project'})).toBeNull()
})

it('shows available partial email history without calling it up to date or holding back Calendar', async () => {
  const sources = statusFor({state: 'connected'}).connectors.google.sources
  api({'GET /api/pixel/connections': [200, managedStatus({state: 'first-sync', sources: {
    ...sources, email: {...sources.email, complete: false, recordCount: 23}}})]})
  render(<ConnectedApps />)
  expect(await screen.findByText('Partially synced · 23 email summaries available')).toBeVisible()
  expect(within(card()).getAllByText(/^Up to date/)).toHaveLength(1)
  expect(screen.getByText('Calendar')).toBeVisible()
})

it('does not infer active fetching or completed history from an empty incomplete snapshot', async () => {
  const sources = statusFor({state: 'connected'}).connectors.google.sources
  api({'GET /api/pixel/connections': [200, managedStatus({state: 'connected', sources: {
    ...sources, email: {...sources.email, complete: false, recordCount: 0}}})]})
  render(<ConnectedApps />)
  expect(await screen.findByText('Waiting for the first sync')).toBeVisible()
  expect(within(card()).getAllByText(/^Up to date/)).toHaveLength(1)
  expect(screen.queryByText(/Syncing history/)).toBeNull()
})

it('keeps an existing account usable when standard sign-in is not configured', async () => {
  const calls = api({'GET /api/pixel/connections': [200, managedStatus({state: 'connected'}, 'not-configured')],
    'POST /api/pixel/connections/google/sync': [200, {outcome: 'started'}]})
  render(<ConnectedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Sync now'}))
  expect(await screen.findByText(/Sync started/)).toBeVisible()
  expect(screen.getByRole('button', {name: 'Disconnect'})).toBeEnabled()
  expect(calls.filter(([key]) => key.startsWith('POST'))).toEqual([['POST /api/pixel/connections/google/sync', {}]])
})

it.each(['invalid-grant', 'api-disabled'])('does not turn a managed %s failure into per-user Cloud setup', async reason => {
  api({'GET /api/pixel/connections': [200, managedStatus({state: reason === 'invalid-grant' ? 'needs-reconnect' : 'setup-broken', reason})]})
  render(<ConnectedApps />)
  await screen.findByRole('article', {name: 'Google'})
  expect(screen.queryByRole('link', {name: /Audience page|Gmail API|Google Calendar API/})).toBeNull()
  expect(screen.queryByText(/publish your app|your Google project/)).toBeNull()
  expect(screen.getByRole('button', {name: 'Reconnect'})).toBeEnabled()
  expect(screen.getByRole('button', {name: 'Disconnect'})).toBeEnabled()
})

it('uses the connected account mode for Advanced repairs even when standard sign-in is available', async () => {
  api({'GET /api/pixel/connections': [200, managedStatus({state: 'setup-broken', reason: 'api-disabled', authMode: 'advanced'})]})
  render(<ConnectedApps />)
  expect(await screen.findByRole('link', {name: 'Gmail API'})).toHaveAttribute('href', expect.stringContaining('apiid=gmail.googleapis.com'))
  expect(screen.getByRole('link', {name: 'Google Calendar API'})).toHaveAttribute('href', expect.stringContaining('apiid=calendar-json.googleapis.com'))
})
