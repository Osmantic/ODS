import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
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

function wizard({status = statusFor(), catalog = GOOGLE_CATALOG, advanced = true, ...props} = {}) {
  const handlers = {refresh: vi.fn(async () => null), onClose: vi.fn(), onConnected: vi.fn()}
  const view = render(<GoogleConnectWizard catalog={catalog} {...handlers} {...props} status={status} />)
  // The existing owner-client cases explicitly choose Advanced; it is never
  // the initial ordinary journey, including against an older host catalog.
  if (advanced) fireEvent.click(screen.getByRole('button', {name: 'Advanced: use your own Google app'}))
  const update = next => view.rerender(<GoogleConnectWizard catalog={catalog} {...handlers} {...props} status={next} />)
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

it('keeps explicit Advanced setup with links to the exact console pages', () => {
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
  fireEvent.change(screen.getByLabelText('Google account to use (optional)'), {target: {value: 'owner@example.com'}})
  const sent = await begin(calls)
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
  expect(sent).toEqual({client: {installed: INSTALLED}, capabilities: READ_BOTH, cloudModels: true,
    ownerTimeZone: /^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$/.test(zone) ? zone : null, loginHint: 'owner@example.com'})
  expect(window.open).toHaveBeenCalledWith('', '_blank')
  expect(tab.opener).toBeNull()
  expect(tab.location.replace).toHaveBeenCalledWith(BEGUN.authUrl)
  expect(screen.getByRole('link', {name: 'Open the Google sign-in again'})).toHaveAttribute('href', BEGUN.authUrl)
  expect(container.innerHTML).not.toContain(INSTALLED.client_secret)
  expect(localStorage.getItem('ods.connectedApps.googleProject')).toBe('ods-owner-calendar')
  expect(refresh).toHaveBeenCalled()
})

it('defaults to the capabilities ODS can deliver, and says a cloud model sees what Portal reads', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard()
  expect(screen.getByRole('checkbox', {name: /your own private events/})).toBeDisabled()
  // Calendar changes and Gmail drafts are offered, off until the owner turns them on.
  for (const name of [/Suggest calendar changes/, /Write Gmail drafts/]) {
    expect(screen.getByRole('checkbox', {name})).toBeEnabled()
    expect(screen.getByRole('checkbox', {name})).not.toBeChecked()
  }
  expect(screen.getByText(/Portal never sends email: you send the draft from Gmail/)).toBeVisible()
  expect(screen.getByText(/When Portal runs on a cloud model, that model's provider sees what Portal reads/)).toBeVisible()
  // Cloud models may use connected data, as decided; no switch offers what nothing enforces yet.
  expect(screen.queryByRole('checkbox', {name: /cloud model/})).toBeNull()
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

it.each(['consent', 'activating', 'deactivating'])('leaves a retained %s callback recovery to Finish without reporting a grant', async phase => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const {update, onClose, onConnected} = wizard()
  await begin(calls)
  update(statusFor({account: null, pending: pendingStep(phase, 'invalid-grant')}, {reason: 'connection-admission-held'}))
  expect(onClose).toHaveBeenCalledOnce()
  expect(onConnected).not.toHaveBeenCalled()
  expect(calls.filter(([key]) => key.startsWith('POST')).map(([key]) => key))
    .toEqual(['POST /api/pixel/connections/google/connect'])
})

it('does not mistake an old account or a normal callback hold for a new grant', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const {update, onClose, onConnected} = wizard({status: statusFor({state: 'connected'})})
  await begin(calls)
  update(statusFor({state: 'connected', pending: pendingStep('consent')}, {reason: 'connection-admission-held'}))
  expect(onConnected).not.toHaveBeenCalled()
  expect(onClose).not.toHaveBeenCalled()
  expect(screen.getByRole('button', {name: 'View connection update'})).toBeEnabled()
  fireEvent.click(screen.getByRole('button', {name: 'View connection update'}))
  expect(onClose).toHaveBeenCalledOnce()
  expect(calls.some(([key]) => key.endsWith('/cancel'))).toBe(false)
})

it('does not report an older restored account as success when activation still needs recovery', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const {update, onClose, onConnected} = wizard({status: statusFor({state: 'connected'})})
  await begin(calls)
  update(statusFor({state: 'connected', pending: pendingStep('activating', 'runtime-busy')}))
  expect(onClose).toHaveBeenCalledOnce()
  expect(onConnected).not.toHaveBeenCalled()
})

it('waits for an account before reporting success during activation', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  const {update, onConnected, onClose} = wizard()
  await begin(calls)
  update(statusFor({pending: pendingStep('consent')}))
  update(statusFor({pending: pendingStep('activating')}))
  expect(onConnected).not.toHaveBeenCalled()
  expect(onClose).not.toHaveBeenCalled()
  expect(screen.getByText(/Sign in to Google in the tab that opened/)).toBeVisible()
  update(statusFor({state: 'connected', pending: pendingStep('deactivating')}))
  expect(onConnected).not.toHaveBeenCalled()
  update(statusFor({state: 'first-sync', pending: pendingStep('activating')}))
  expect(onConnected).toHaveBeenCalledWith([])
})

it.each([200, 409, 503])('refreshes after a pasted callback returns %s and preserves the retained recovery', async code => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN],
    'POST /api/pixel/connections/google/complete': [code, code === 200
      ? {outcome: 'failed', errorClass: 'invalid-grant', account: null, capabilities: null, missing: []}
      : {detail: {code: 'connections-unavailable', message: 'The connection update still needs checking.', help: null}}]})
  const {update, refresh, onClose, onConnected} = wizard()
  await begin(calls)
  refresh.mockClear()
  fireEvent.change(screen.getByLabelText(/Copy its whole address and paste it here/),
    {target: {value: `http://127.0.0.1:53682/?state=${'s'.repeat(43)}&code=4/pasted`}})
  fireEvent.click(screen.getByRole('button', {name: 'Finish connecting'}))
  await waitFor(() => expect(refresh).toHaveBeenCalled())
  update(statusFor({pending: pendingStep('activating', 'invalid-grant')}, {reason: 'connection-admission-held'}))
  expect(onClose).toHaveBeenCalledOnce()
  expect(onConnected).not.toHaveBeenCalled()
  expect(calls.filter(([key]) => key.endsWith('/complete'))).toHaveLength(1)
  expect(calls.some(([key]) => key.endsWith('/cancel'))).toBe(false)
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
  wizard({initial: {capabilities: {...READ_BOTH, 'gmail.read': false}, cloudModels: true}})
  expect(screen.getByRole('heading', {name: 'Reconnect Google'})).toBeVisible()
  expect(screen.getByRole('checkbox', {name: 'Read your calendar'})).toBeChecked()
  expect(screen.getByRole('checkbox', {name: 'Read email summaries'})).not.toBeChecked()
  expect(screen.getByText('Create your Google sign-in app (first time only)').closest('details')).not.toHaveAttribute('open')
})

it('offers private changes only on top of calendar changes, and explains both', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard()
  const changes = screen.getByRole('checkbox', {name: /Suggest calendar changes/})
  const own = screen.getByRole('checkbox', {name: /your own private events/})
  expect(own).toBeDisabled()
  expect(screen.getByText(/nothing happens until you approve it in Settings/)).toBeVisible()
  expect(screen.getByText(/At most 20 an hour, and paused while Portal has Full Access/)).toBeVisible()
  fireEvent.click(changes)
  expect(own).toBeEnabled()
  fireEvent.click(own)
  expect(own).toBeChecked()
  // Turning calendar changes off takes private changes with them.
  fireEvent.click(changes)
  expect(own).not.toBeChecked()
  expect(own).toBeDisabled()
  fireEvent.click(changes)
  expect(own).toBeChecked()
  const sent = await begin(calls)
  expect(sent.capabilities).toEqual({...READ_BOTH, 'calendar.write': true, 'calendar.direct': true})
})

it('offers Gmail drafts only on top of reading email', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard()
  const email = screen.getByRole('checkbox', {name: 'Read email summaries'})
  const drafts = screen.getByRole('checkbox', {name: /Write Gmail drafts/})
  fireEvent.click(drafts)
  expect(drafts).toBeChecked()
  // Without email summaries Portal has nothing to answer, so drafts go with them.
  fireEvent.click(email)
  expect(drafts).not.toBeChecked()
  expect(drafts).toBeDisabled()
  fireEvent.click(email)
  expect(drafts).toBeChecked()
  const sent = await begin(calls)
  expect(sent.capabilities).toEqual({...READ_BOTH, 'gmail.drafts': true})
})

function managedCatalog(state = 'configured', advancedAvailable = true) {
  return {...GOOGLE_CATALOG, auth: {...GOOGLE_CATALOG.auth, client: 'ods-managed-desktop-app',
    mode: 'managed', state, advancedAvailable}}
}

it('starts ordinary Google sign-in directly with the selected permissions and no owner client', async () => {
  localStorage.setItem('ods.connectedApps.googleProject', 'previous-owner-project')
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard({catalog: managedCatalog(), advanced: false})
  expect(screen.queryByLabelText('Google client file')).toBeNull()
  expect(screen.queryByRole('link', {name: 'Create a Google Cloud project'})).toBeNull()
  expect(screen.queryByText(/Google will say it hasn't verified/)).toBeNull()
  expect(screen.queryByLabelText('Google account to use (optional)')).toBeNull()
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeEnabled()
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Google'}))
  expect(await screen.findByText(/Sign in to Google in the tab that opened/)).toBeVisible()
  const sent = calls.find(([key]) => key === 'POST /api/pixel/connections/google/connect')[1]
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
  expect(sent).toEqual({authMode: 'managed', capabilities: READ_BOTH, cloudModels: true,
    ownerTimeZone: /^[A-Za-z][A-Za-z0-9_+\-/]{0,63}$/.test(zone) ? zone : null, loginHint: null})
  expect(Object.hasOwn(sent, 'client')).toBe(false)
  expect(tab.location.replace).toHaveBeenCalledWith(BEGUN.authUrl)
})

it.each(['not-configured', 'unavailable'])('does not silently fall back when managed sign-in is %s', state => {
  const calls = api({})
  wizard({catalog: managedCatalog(state), advanced: false})
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeDisabled()
  expect(screen.queryByLabelText('Google client file')).toBeNull()
  expect(screen.queryByRole('link', {name: 'Create a Google Cloud project'})).toBeNull()
  expect(screen.getByRole('status')).toHaveTextContent(state === 'not-configured'
    ? 'Standard Google sign-in is not available' : 'ODS could not load its Google sign-in configuration')
  expect(screen.getByRole('button', {name: 'Advanced: use your own Google app'})).toBeEnabled()
  expect(calls).toEqual([])
})

it('does not offer Advanced setup when the catalog does not allow it', () => {
  api({})
  wizard({catalog: managedCatalog('not-configured', false), advanced: false})
  expect(screen.queryByRole('button', {name: /Advanced/})).toBeNull()
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeDisabled()
})

it('requires explicit Advanced choice on a legacy host and keeps its exact old request contract', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard({advanced: false})
  expect(screen.getByText('This ODS version does not provide standard Google sign-in yet.')).toBeVisible()
  expect(screen.queryByLabelText('Google client file')).toBeNull()
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeDisabled()
  fireEvent.click(screen.getByRole('button', {name: 'Advanced: use your own Google app'}))
  const sent = await begin(calls)
  expect(sent.client).toEqual({installed: INSTALLED})
  expect(Object.hasOwn(sent, 'authMode')).toBe(false)
})

it.each(['configured', 'not-configured'])('marks the explicit own-app request as Advanced when managed sign-in is %s', async state => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard({catalog: managedCatalog(state)})
  const sent = await begin(calls)
  expect(sent.authMode).toBe('advanced')
  expect(sent.client).toEqual({installed: INSTALLED})
})

it('clears the uploaded client before switching back to ordinary Google sign-in', async () => {
  const calls = api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard({catalog: managedCatalog()})
  await chooseFile({installed: INSTALLED})
  expect(await screen.findByText('Desktop app client for the project ods-owner-calendar.')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name: 'Use standard Google sign-in'}))
  expect(screen.queryByLabelText('Google client file')).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Google'}))
  expect(await screen.findByText(/Sign in to Google in the tab that opened/)).toBeVisible()
  const sent = calls.find(([key]) => key === 'POST /api/pixel/connections/google/connect')[1]
  expect(sent.authMode).toBe('managed')
  expect(Object.hasOwn(sent, 'client')).toBe(false)
})

it('does not retain a slow client-file read after leaving Advanced setup', async () => {
  api({})
  wizard({catalog: managedCatalog()})
  let resolve
  const file = new File(['pending'], 'client.json', {type: 'application/json'})
  Object.defineProperty(file, 'text', {value: () => new Promise(done => { resolve = done })})
  fireEvent.change(screen.getByLabelText('Google client file'), {target: {files: [file]}})
  fireEvent.click(screen.getByRole('button', {name: 'Use standard Google sign-in'}))
  await act(async () => { resolve(JSON.stringify({installed: INSTALLED})) })
  fireEvent.click(screen.getByRole('button', {name: 'Advanced: use your own Google app'}))
  expect(screen.queryByText('Desktop app client for the project ods-owner-calendar.')).toBeNull()
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeDisabled()
})

it('offers the same sign-in link when the browser blocks a popup', async () => {
  vi.mocked(window.open).mockReturnValue(null)
  api({'POST /api/pixel/connections/google/connect': [200, BEGUN]})
  wizard({catalog: managedCatalog(), advanced: false})
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Google'}))
  expect(await screen.findByText(/Open the Google sign-in below/)).toBeVisible()
  expect(screen.getByRole('link', {name: 'Open the Google sign-in again'})).toHaveAttribute('href', BEGUN.authUrl)
})

it('keeps a managed configuration failure out of the owner Cloud-project setup path', async () => {
  api({'POST /api/pixel/connections/google/connect': [503, {detail: {code: 'client-invalid',
    message: 'Download your Desktop app client file again.', help: null}}]})
  wizard({catalog: managedCatalog(), advanced: false})
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Google'}))
  expect(await screen.findByText("Google isn't accepting ODS sign-in right now.")).toBeVisible()
  expect(screen.queryByText('Download your Desktop app client file again.')).toBeNull()
  expect(screen.queryByLabelText('Google client file')).toBeNull()
  expect(screen.getByRole('button', {name: 'Continue to Google'})).toBeEnabled()
})
