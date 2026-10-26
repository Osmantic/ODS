import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import GoogleConnectWizard from './GoogleConnectWizard'
import { GOOGLE_CATALOG, INSTALLED, READ_BOTH, pendingStep, statusFor } from './connectedAppsFixtures'

const FLOW = 'a'.repeat(32)
const BEGUN = {flowId: FLOW, authUrl: 'https://accounts.google.com/o/oauth2/auth?client_id=x&state=y',
  expiresAt: '2026-10-25T20:45:00Z', redirect: 'loopback'}
const DISCORD = 'https://discord.gg/4ntNp9MAwC'
let tab
beforeEach(() => {
  tab = {opener: {}, location: {replace: vi.fn()}, close: vi.fn()}
  vi.spyOn(window, 'open').mockReturnValue(tab)
  localStorage.clear()
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function api(routes) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${url}`
    calls.push([key, options.body === undefined ? undefined : JSON.parse(options.body)])
    const route = routes[key]
    if (!route) throw new Error(`unexpected request ${key}`)
    const [status, body] = typeof route === 'function' ? route() : route
    return {ok: status >= 200 && status < 300, status, json: async () => body}
  }))
  return calls
}

function wizard({status = statusFor(), ...props} = {}) {
  const handlers = {refresh: vi.fn(async () => null), onClose: vi.fn(), onConnected: vi.fn()}
  const view = render(<GoogleConnectWizard catalog={GOOGLE_CATALOG} {...handlers} {...props} status={status} />)
  const update = next => view.rerender(<GoogleConnectWizard catalog={GOOGLE_CATALOG} {...handlers} {...props} status={next} />)
  return {...view, ...handlers, update}
}

async function chooseFile(content) {
  const file = new File([typeof content === 'string' ? content : JSON.stringify(content)], 'client.json', {type: 'application/json'})
  fireEvent.change(screen.getByLabelText('Google client file'), {target: {files: [file]}})
}

async function begin(calls) {
  await chooseFile({installed: INSTALLED})
  expect(await screen.findByText('Desktop app client for the project ods-owner-calendar.')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Google'}))
  expect(await screen.findByText(/Sign in to Google in the tab that opened/)).toBeVisible()
  return calls.find(([key]) => key === 'POST /api/pixel/connections/google/connect')[1]
}

it('walks through creating the Google app with links to the exact console pages', () => {
  api({})
  wizard()
  for (const [name, href] of [['Create a Google Cloud project', 'https://console.cloud.google.com/projectcreate'],
    ['Gmail API', 'https://console.cloud.google.com/apis/enableflow;apiid=gmail.googleapis.com'],
    ['Google Calendar API', 'https://console.cloud.google.com/apis/enableflow;apiid=calendar-json.googleapis.com'],
    ['Branding page', 'https://console.developers.google.com/auth/branding'],
    ['Audience page', 'https://console.developers.google.com/auth/audience'],
    ['Clients page', 'https://console.developers.google.com/auth/clients']]) {
    const link = screen.getByRole('link', {name})
    expect(link).toHaveAttribute('href', href)
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  }
  expect(screen.getByText(/Publish app, or Google ends the sign-in after 7 days/)).toBeVisible()
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeDisabled()
})

it('checks the client file before anything leaves the browser', async () => {
  const calls = api({})
  wizard()
  await chooseFile({web: INSTALLED})
  expect(await screen.findByText('That file is for another kind of Google client.')).toBeVisible()
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeDisabled()
  await chooseFile('{not json')
  expect(await screen.findByText("That isn't a Google OAuth client file.")).toBeVisible()
  expect(screen.queryByRole('link', {name: /Discord/})).toBeNull()
  expect(calls).toEqual([])
})

it("sends exactly the owner's choices, opens Google, and keeps no secret afterwards", async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const {container, refresh} = wizard()
  fireEvent.click(screen.getByRole('checkbox', {name: 'Also while Portal runs on a cloud model'}))
  fireEvent.change(screen.getByLabelText('Google account to use (optional)'), {target: {value: 'owner@example.com'}})
  const sent = await begin(calls)
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
  expect(sent).toEqual({client: {installed: INSTALLED}, capabilities: READ_BOTH, cloudModels: false,
    ownerTimeZone: /^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$/.test(zone) ? zone : null, loginHint: 'owner@example.com'})
  expect(window.open).toHaveBeenCalledWith('', '_blank')
  expect(tab.opener).toBeNull()
  expect(tab.location.replace).toHaveBeenCalledWith(BEGUN.authUrl)
  expect(screen.getByRole('link', {name: 'Open the Google sign-in again'})).toHaveAttribute('href', BEGUN.authUrl)
  expect(container.innerHTML).not.toContain(INSTALLED.client_secret)
  expect(localStorage.getItem('ods.connectedApps.googleProject')).toBe('ods-owner-calendar')
  expect(refresh).toHaveBeenCalled()
})

it('defaults to the capabilities ODS can deliver and allows cloud models, as decided', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard()
  for (const name of [/Suggest calendar changes/, /Write Gmail drafts/]) {
    expect(screen.getByRole('checkbox', {name})).toBeDisabled()
  }
  const sent = await begin(calls)
  expect(sent.capabilities).toEqual(READ_BOTH)
  expect(sent.cloudModels).toBe(true)
})

it('clears the client file when Google sign-in cannot start, and says why', async () => {
  api({'POST /api/pixel/connections/google/connect': [409, {detail: {code: 'runtime-busy',
    message: 'Portal is busy right now. Try again when it has finished.', help: null}}]})
  wizard()
  await chooseFile({installed: INSTALLED})
  fireEvent.click(await screen.findByRole('button', {name: 'Continue to Google'}))
  expect(await screen.findByText('Portal is busy right now. Try again when it has finished.')).toBeVisible()
  expect(tab.close).toHaveBeenCalled()
  expect(screen.getByLabelText('Google client file')).toHaveValue('')
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeDisabled()
  expect(screen.queryByRole('link', {name: /Discord/})).toBeNull()
})

it("finishes when Google's redirect reaches ODS, and says what Google left out", async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const {update, onConnected} = wizard()
  await begin(calls)
  update(statusFor({pending: pendingStep('consent')}))
  expect(onConnected).not.toHaveBeenCalled()
  update(statusFor({state: 'first-sync', capabilities: {...READ_BOTH, 'gmail.read': false},
    pending: pendingStep('activating')}))
  expect(onConnected).toHaveBeenCalledWith(['Read email summaries'])
})

it('goes back to the form with the reason when the sign-in did not connect', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const {update} = wizard()
  await begin(calls)
  update(statusFor({pending: pendingStep('consent')}))
  update(statusFor({pending: pendingStep('failed', 'access-denied')}))
  expect(await screen.findByText("You didn't allow access at Google.")).toBeVisible()
  expect(screen.getByLabelText('Google client file')).toHaveValue('')
  expect(screen.queryByRole('link', {name: /Discord/})).toBeNull()
})

it("does not let the last attempt's failure end the new sign-in", async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const earlier = statusFor({pending: pendingStep('failed', 'access-denied')})
  const {update} = wizard({status: earlier})
  await begin(calls)
  update(earlier)
  expect(screen.getByText(/Sign in to Google in the tab that opened/)).toBeVisible()
  update(statusFor({pending: pendingStep('consent')}))
  expect(screen.getByText(/Sign in to Google in the tab that opened/)).toBeVisible()
})

it('finishes with an address pasted from another computer, and keeps it no longer', async () => {
  const address = `http://127.0.0.1:53682/?state=${'s'.repeat(43)}&code=4/pasted`
  let complete = [400, {detail: {code: 'address-not-from-sign-in', message: "That isn't the address Google sent you to. Copy the whole address from the tab that didn't load, then paste it here.", help: null}}]
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN],
    'POST /api/pixel/connections/google/complete': () => complete})
  const {onConnected} = wizard()
  await begin(calls)
  const box = screen.getByLabelText(/Copy its whole address and paste it here/)
  fireEvent.change(box, {target: {value: 'https://example.com/elsewhere'}})
  fireEvent.click(screen.getByRole('button', {name: 'Finish connecting'}))
  expect(await screen.findByText(/That isn't the address Google sent you to/)).toBeVisible()
  expect(box).toHaveValue('')
  complete = [200, {outcome: 'connected', errorClass: null, account: 'owner@example.com', capabilities: READ_BOTH, missing: []}]
  fireEvent.change(screen.getByLabelText(/Copy its whole address and paste it here/), {target: {value: address}})
  fireEvent.click(screen.getByRole('button', {name: 'Finish connecting'}))
  await waitFor(() => expect(onConnected).toHaveBeenCalledWith([]))
  expect(calls.filter(([key]) => key.endsWith('/complete')).map(([, body]) => body)).toEqual([
    {flowId: FLOW, address: 'https://example.com/elsewhere'}, {flowId: FLOW, address}])
})

it('cancels the open sign-in', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN],
    'POST /api/pixel/connections/google/cancel': [200, {outcome: 'cancelled'}]})
  const {onClose} = wizard()
  await begin(calls)
  fireEvent.click(screen.getByRole('button', {name: 'Cancel sign-in'}))
  await waitFor(() => expect(onClose).toHaveBeenCalled())
  expect(calls.at(-1)).toEqual(['POST /api/pixel/connections/google/cancel', {flowId: FLOW}])
})

it('shows the Discord link for a real failure only', async () => {
  api({'POST /api/pixel/connections/google/connect': [503, {detail: {code: 'sign-in-exchange-unavailable',
    message: "Google sign-in couldn't finish on this machine. Try again.", help: DISCORD}}]})
  wizard()
  await chooseFile({installed: INSTALLED})
  fireEvent.click(await screen.findByRole('button', {name: 'Continue to Google'}))
  expect(await screen.findByRole('link', {name: /Discord/})).toHaveAttribute('href', DISCORD)
})

it('reconnects with the choices already made, without the first-time steps open', () => {
  api({})
  wizard({initial: {capabilities: {...READ_BOTH, 'gmail.read': false}, cloudModels: false}})
  expect(screen.getByRole('heading', {name: 'Reconnect Google'})).toBeVisible()
  expect(screen.getByRole('checkbox', {name: 'Read your calendar'})).toBeChecked()
  expect(screen.getByRole('checkbox', {name: 'Read email summaries'})).not.toBeChecked()
  expect(screen.getByRole('checkbox', {name: 'Also while Portal runs on a cloud model'})).not.toBeChecked()
  expect(screen.getByText('Create your Google sign-in app (first time only)').closest('details')).not.toHaveAttribute('open')
})
