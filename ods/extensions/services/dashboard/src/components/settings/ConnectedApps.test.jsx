import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import ConnectedApps from './ConnectedApps'
import { pendingStep, statusFor } from './connectedAppsFixtures'

const DISCORD = 'https://discord.gg/4ntNp9MAwC'
afterEach(() => vi.unstubAllGlobals())

// The dashboard API as the card reaches it: GET status and access mode, POST actions.
function api(routes) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${url}`
    calls.push([key, options.body === undefined ? undefined : JSON.parse(options.body)])
    const route = routes[key] ?? (key === 'GET /api/pixel/access-mode' ? [200, {configured_mode: 'sandboxed'}] : null)
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
  expect(await screen.findByText(/Full Access is on. Connected apps keep working/)).toBeVisible()
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
