import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import ReviewedApps from './ReviewedApps'
import { authorizationLink, reviewedAppView } from './nativeAppsStatus'

const BASE = '/api/pixel/connections/apps'
const FLOW = 'a'.repeat(32)
const CATALOG = [{id: 'notion', label: 'Notion', summary: 'Find and read information available through your Notion connection.',
  tools: ['notion-search', 'notion-fetch'], details: [
    'Portal can search and read; this connection does not offer tools that change content.',
    'Notion may return content from other sources connected to your account. Review the access shown by Notion.',
    'Read content enters your conversation and is shared with its selected model provider.',
    'Disconnecting removes Portal access here. Provider-side authorization is managed separately in Notion.']}]
const AUTH_URL = 'https://provider.example/authorize?state=private-state'
let tab

beforeEach(() => {
  tab = {opener: {}, location: {replace: vi.fn()}, close: vi.fn()}
  vi.spyOn(window, 'open').mockReturnValue(tab)
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })

function appState(phase = 'absent', extra = {}) {
  return {app: 'notion', phase, flowId: phase === 'absent' ? null : FLOW, error: null,
    portalAccessVerified: phase === 'ready', scope: 'pixel-owner-default',
    ...(phase === 'ready' ? {checkedAt: '2026-10-28T12:00:00Z'} : {}), ...extra}
}
function status(phase = 'absent', extra = {}) {
  return {schemaVersion: 1, available: true, surface: 'wsl-systemd', reason: null,
    apps: {notion: appState(phase, extra)}, catalog: CATALOG}
}
function api(routes) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${url}`
    calls.push([key, options.body === undefined ? undefined : JSON.parse(options.body)])
    const route = routes[key]
    if (!route) throw new Error(`unexpected request ${key}`)
    const [code, body] = typeof route === 'function' ? route() : route
    return {ok: code >= 200 && code < 300, status: code, json: async () => body}
  }))
  return calls
}

it('shows the reviewed read tools and broader provider/model sharing before starting consent', async () => {
  const calls = api({[`GET ${BASE}`]: [200, status()]})
  render(<ReviewedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Connect Notion'}))
  expect(screen.getByText(/Notion may return content from other sources/)).toBeVisible()
  expect(screen.getByText(/shared with its selected model provider/)).toBeVisible()
  expect(screen.getByText(/authorization can be broader than the search and read tools/)).toBeVisible()
  expect(screen.getByRole('button', {name: 'Continue to Notion'})).toBeEnabled()
  expect(screen.queryByLabelText(/client file|server URL|command|MCP/i)).toBeNull()
  expect(calls.some(([key]) => key.startsWith('POST'))).toBe(false)
})

it('waits for verified Portal access after consent, and never stores the callback or replays it on Finish', async () => {
  let current = status()
  const calls = api({[`GET ${BASE}`]: () => [200, current],
    [`POST ${BASE}/notion/connect`]: () => {
      current = status('consent')
      return [200, {...appState('consent'), authUrl: AUTH_URL, expiresAt: '2026-10-28T12:15:00Z'}]
    }, [`POST ${BASE}/notion/complete`]: () => {
      current = status('authorized')
      return [200, appState('authorized')]
    }, [`POST ${BASE}/notion/finish`]: () => {
      current = status('ready')
      return [200, appState('ready')]
    }})
  render(<ReviewedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Connect Notion'}))
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Notion'}))
  expect(await screen.findByRole('link', {name: 'Open the sign-in tab'})).toHaveAttribute('href', AUTH_URL)
  expect(tab.opener).toBeNull()
  expect(tab.location.replace).toHaveBeenCalledWith(AUTH_URL)
  const address = 'http://127.0.0.1:43123/?state=private-state&code=PRIVATE-code'
  fireEvent.change(screen.getByLabelText(/Paste its whole address here/), {target: {value: address}})
  fireEvent.click(screen.getByRole('button', {name: 'Finish sign-in'}))
  expect(await screen.findByRole('button', {name: 'Finish'})).toBeEnabled()
  expect(screen.queryByText('Connected')).toBeNull()
  expect(localStorage.getItem('PRIVATE-code')).toBeNull()
  expect(document.body.innerHTML).not.toContain('PRIVATE-code')
  fireEvent.click(screen.getByRole('button', {name: 'Finish'}))
  expect(await screen.findByText('Connected')).toBeVisible()
  expect(screen.getByText('Portal can use this app with its current default model.')).toBeVisible()
  expect(calls.filter(([key]) => key.startsWith('POST'))).toEqual([
    [`POST ${BASE}/notion/connect`, {}], [`POST ${BASE}/notion/complete`, {flowId: FLOW, address}],
    [`POST ${BASE}/notion/finish`, {flowId: FLOW}]])
})

it('reuses a native sign-in without pretending a new account was selected or access is ready', async () => {
  let current = status()
  api({[`GET ${BASE}`]: () => [200, current], [`POST ${BASE}/notion/connect`]: () => {
    current = status('authorized')
    return [200, {...appState('authorized'), authUrl: null, expiresAt: '2026-10-28T12:15:00Z'}]
  }})
  render(<ReviewedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Connect Notion'}))
  fireEvent.click(screen.getByRole('button', {name: 'Continue to Notion'}))
  expect(await screen.findByRole('button', {name: 'Finish'})).toBeEnabled()
  expect(tab.close).toHaveBeenCalledOnce()
  expect(tab.location.replace).not.toHaveBeenCalled()
  expect(screen.queryByText('Connected')).toBeNull()
  expect(screen.queryByText(/selected.*account/i)).toBeNull()
})

it('recovers a consent view after reload without reconstructing or restarting its authorization URL', async () => {
  const calls = api({[`GET ${BASE}`]: [200, status('consent')]})
  render(<ReviewedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Finish sign-in'}))
  expect(screen.getByLabelText(/Paste its whole address here/)).toBeVisible()
  expect(screen.queryByRole('link', {name: 'Open the sign-in tab'})).toBeNull()
  expect(screen.queryByRole('button', {name: 'Continue to Notion'})).toBeNull()
  expect(calls.some(([key]) => key.startsWith('POST'))).toBe(false)
})

it('preserves a lost callback outcome for Finish instead of starting another exchange', async () => {
  let current = status('consent')
  const calls = api({[`GET ${BASE}`]: () => [200, current], [`POST ${BASE}/notion/complete`]: () => {
    current = status('verifying', {error: 'native-oauth-exchange-unconfirmed'})
    return [503, {detail: {code: 'native-app-unavailable', message: 'Check the saved connection.', help: null}}]
  }})
  render(<ReviewedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Finish sign-in'}))
  fireEvent.change(screen.getByLabelText(/Paste its whole address here/),
    {target: {value: 'http://127.0.0.1:43123/?state=x&code=PRIVATE-code'}})
  fireEvent.click(screen.getByRole('button', {name: 'Finish sign-in'}))
  expect(await screen.findByText('Update needs checking')).toBeVisible()
  expect(screen.getByRole('button', {name: 'Finish'})).toBeEnabled()
  expect(screen.queryByRole('button', {name: 'Connect Notion'})).toBeNull()
  expect(calls.filter(([key]) => key.startsWith('POST'))).toHaveLength(1)
})

it('confirms local withdrawal and never describes it as revocation at Notion', async () => {
  let current = status('ready')
  const calls = api({[`GET ${BASE}`]: () => [200, current], [`POST ${BASE}/notion/disconnect`]: () => {
    current = status('disconnected', {providerRevoked: false})
    return [200, appState('disconnected', {providerRevoked: false})]
  }})
  render(<ReviewedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Disconnect'}))
  const dialog = screen.getByRole('dialog', {name: 'Disconnect Notion?'})
  expect(dialog).toHaveTextContent('Authorization at Notion is managed separately.')
  expect(calls.some(([key]) => key.startsWith('POST'))).toBe(false)
  fireEvent.click(within(dialog).getByRole('button', {name: 'Disconnect'}))
  expect(await screen.findByText('Disconnected')).toBeVisible()
  expect(screen.getByText(/Manage authorization separately in Notion/)).toBeVisible()
  expect(calls.filter(([key]) => key.startsWith('POST'))).toEqual([[`POST ${BASE}/notion/disconnect`, {}]])
})

it('does not report disconnected when withdrawal is still held or its reply was lost', async () => {
  let current = status('ready')
  api({[`GET ${BASE}`]: () => [200, current], [`POST ${BASE}/notion/disconnect`]: () => {
    current = status('withdrawing', {error: 'native-app-withdrawal-rolled-back'})
    return [503, {detail: {code: 'native-app-unavailable', message: 'Check the saved connection.', help: null}}]
  }})
  render(<ReviewedApps />)
  fireEvent.click(await screen.findByRole('button', {name: 'Disconnect'}))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', {name: 'Disconnect'}))
  expect(await screen.findByText('Removing Portal access')).toBeVisible()
  expect(screen.getByRole('button', {name: 'Finish'})).toBeEnabled()
  expect(screen.queryByText('Disconnected')).toBeNull()
  expect(screen.queryByRole('button', {name: 'Connect Notion'})).toBeNull()
})

it('polls cached readiness but never starts a provider check from a status timer', async () => {
  let current = status('ready')
  const calls = api({[`GET ${BASE}`]: () => [200, current]})
  render(<ReviewedApps />)
  await screen.findByText('Connected')
  vi.useFakeTimers()
  current = status('configured', {error: 'native-app-verification-required'})
  await act(async () => { window.dispatchEvent(new Event('focus')) })
  expect(screen.getByText('Access needs checking')).toBeVisible()
  expect(screen.getByRole('button', {name: 'Check access'})).toBeEnabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(60001) })
  expect(calls.some(([key]) => key.startsWith('POST'))).toBe(false)
})

it('checks an existing configured grant and disconnects without inventing a sign-in flow', async () => {
  let current = status('configured', {flowId: null, error: 'native-app-verification-required'})
  const calls = api({[`GET ${BASE}`]: () => [200, current],
    [`POST ${BASE}/notion/check`]: () => {
      current = status('ready', {flowId: null})
      return [200, appState('ready', {flowId: null})]
    }, [`POST ${BASE}/notion/disconnect`]: () => {
      current = status('disconnected', {providerRevoked: false})
      return [200, appState('disconnected', {providerRevoked: false})]
    }})
  render(<ReviewedApps />)
  expect(await screen.findByText('Access needs checking')).toBeVisible()
  expect(screen.queryByText('Connected')).toBeNull()
  expect(screen.queryByRole('button', {name: 'Connect Notion'})).toBeNull()
  expect(screen.queryByRole('button', {name: 'Finish'})).toBeNull()
  fireEvent.click(screen.getByRole('button', {name: 'Check access'}))
  expect(await screen.findByText('Connected')).toBeVisible()
  fireEvent.click(screen.getByRole('button', {name: 'Disconnect'}))
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', {name: 'Disconnect'}))
  expect(await screen.findByText('Disconnected')).toBeVisible()
  expect(calls.filter(([key]) => key.startsWith('POST'))).toEqual([
    [`POST ${BASE}/notion/check`, {}], [`POST ${BASE}/notion/disconnect`, {}]])
  expect(tab.location.replace).not.toHaveBeenCalled()
})

it('supports an older host without exposing a partial Connect flow', async () => {
  api({[`GET ${BASE}`]: [404, {}]})
  const {container} = render(<ReviewedApps />)
  await waitFor(() => expect(container).toBeEmptyDOMElement())
})

it('offers no app actions on an unqualified system', async () => {
  api({[`GET ${BASE}`]: [200, {...status(), available: false, reason: 'not-available-on-this-system', apps: {}}]})
  render(<ReviewedApps />)
  expect(await screen.findByText('Not available')).toBeVisible()
  expect(screen.queryByRole('button', {name: 'Connect Notion'})).toBeNull()
})

it('does not turn a false ready bit or another scope into a Connected label', () => {
  expect(reviewedAppView(appState('verifying', {portalAccessVerified: true})).label).not.toBe('Connected')
  expect(reviewedAppView(appState('ready', {scope: 'all-conversations'})).label).not.toBe('Connected')
})

it.each(['javascript:alert(1)', 'http://provider.example/auth?state=x',
  'https://owner:secret@provider.example/auth?state=x', 'https://provider.example/auth?state=x#private'])(
  'does not navigate to an unconfirmed sign-in address: %s', value => {
  expect(authorizationLink(value)).toBeNull()
})
