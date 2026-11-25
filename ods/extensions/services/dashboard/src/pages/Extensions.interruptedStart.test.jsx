import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

/**
 * The first Hermes enable recreates dashboard-api while the enable request is
 * still open (Windows/WSL fleet test, 2026-10-18). Hermes, its proxy and
 * SearXNG came up, but the request ended in nginx's 502 or a dropped
 * connection, and the card kept "disabled" with "Add Hermes with web access"
 * until a full reload. A start whose answer was lost is followed like any
 * start; refusals from dashboard-api keep their error.
 */

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })
// nginx's own error page is HTML, so reading it as JSON fails.
const gatewayPage = status => ({
  ok: false, status, json: async () => { throw new SyntaxError('Unexpected token <') },
})
const ENABLE = '/api/extensions/hermes-proxy/enable?auto_enable_deps=true'
const PROGRESS = '/api/extensions/hermes-proxy/progress'
const INTERRUPTED = 'Connection to ODS was interrupted while starting Hermes Agent. Checking whether it started…'
const STARTED = 'Extension installed and started.'
const NAMES = { hermes: 'Hermes Agent', 'hermes-proxy': 'Hermes Auth Proxy', searxng: 'SearXNG' }

const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(3000) })
const requests = (fetchMock, path, method = 'GET') => fetchMock.mock.calls
  .filter(([url, options = {}]) => url === path && (options.method || 'GET') === method).length
const hermesCard = () => within(screen.getByRole('heading', { name: 'Hermes Agent' }).closest('article'))

// state.apiUp = false: dashboard-api is being recreated and the dashboard's
// nginx answers every API request with its own 502 page.
async function addHermes(answerEnable, { progress = { service_id: 'hermes-proxy', status: 'idle' } } = {}) {
  vi.useFakeTimers()
  const state = { apiUp: true, selected: false, progress }
  const extensions = () => Object.entries(NAMES).map(([id, name]) => ({
    id, name, source: 'core', library_manageable: true, library_selected: state.selected,
    status: state.selected ? 'enabled' : 'disabled', features: [],
  }))
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    if (target === ENABLE && options.method === 'POST') return answerEnable(state)
    if (!state.apiUp) return gatewayPage(502)
    if (target === '/api/extensions/catalog') {
      return response({ agent_available: true, extensions: extensions(), summary: { total: 3 } })
    }
    if (target === '/api/templates') return response({ templates: [] })
    if (target === '/api/webui/selection') return response({ supported: false, enabled: true })
    if (target === '/api/extensions/hermes-proxy/prepare?auto_enable_deps=true' && options.method === 'POST') {
      return response({ status: 'ready', service_ids: Object.keys(NAMES) })
    }
    if (target === PROGRESS) return response(state.progress)
    throw new Error(`Unexpected request: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  await act(async () => { render(<Extensions compact />) })
  fireEvent.click(screen.getByRole('button', { name: 'Add Hermes with web access' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Enable' })) })
  return { state, fetchMock }
}

// The host goes on starting Hermes while dashboard-api is recreated.
const restartDuringEnable = lostAnswer => state => {
  state.selected = true
  state.apiUp = false
  return lostAnswer()
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it.each([
  ['a dropped connection', () => { throw new TypeError('Failed to fetch') }],
  ['a 502 page from nginx', () => gatewayPage(502)],
])('follows a Hermes add whose answer was lost to %s until the card shows it running', async (_, lostAnswer) => {
  const { state, fetchMock } = await addHermes(restartDuringEnable(lostAnswer))
  expect(screen.getByText(INTERRUPTED)).toBeVisible()
  expect(screen.queryByText(/Failed to/)).toBeNull()

  // dashboard-api is still starting: polling continues without an error.
  await tick()
  expect(requests(fetchMock, PROGRESS)).toBe(1)
  expect(screen.getByText(INTERRUPTED)).toBeVisible()
  expect(screen.queryByText(/Failed to|Connection to dashboard lost/)).toBeNull()

  state.apiUp = true
  await tick()
  expect(screen.getByText(STARTED)).toBeVisible()
  expect(screen.queryByText(INTERRUPTED)).toBeNull()
  expect(hermesCard().getByText('enabled')).toBeVisible()
  expect(hermesCard().queryByRole('button', { name: 'Add Hermes with web access' })).toBeNull()

  // Finished: no more polling, and Enable was never sent again.
  const polls = requests(fetchMock, PROGRESS)
  await tick()
  expect(requests(fetchMock, PROGRESS)).toBe(polls)
  expect(requests(fetchMock, ENABLE, 'POST')).toBe(1)
})

it('reads the catalog once the API is back even when the progress record is stuck mid-start', async () => {
  // Left behind when the restart cut off the request that would finish it.
  const orphan = {
    service_id: 'hermes-proxy', status: 'pulling', phase_label: 'Starting installation...',
    started_at: '2026-10-18T10:00:00+00:00', updated_at: '2026-10-18T10:00:00+00:00',
  }
  const { state } = await addHermes(restartDuringEnable(() => gatewayPage(502)), { progress: orphan })
  state.apiUp = true
  await tick()
  expect(screen.getByText(STARTED)).toBeVisible()
  expect(hermesCard().getByText('enabled')).toBeVisible()
  expect(screen.queryByText('Starting installation...')).toBeNull()
})

it.each([
  [400, 'Compose file is a symlink'],
  [503, 'Host agent unavailable'],
])('keeps the error for a %i refusal from dashboard-api and does not poll', async (status, detail) => {
  const { fetchMock } = await addHermes(() => response({ detail }, status))
  expect(screen.getByText(detail)).toBeVisible()
  expect(screen.queryByText(INTERRUPTED)).toBeNull()
  await tick()
  expect(requests(fetchMock, PROGRESS)).toBe(0)
  expect(hermesCard().getByRole('button', { name: 'Add Hermes with web access' })).toBeEnabled()
})

it('follows a Hermes add that was answered without the interruption notice', async () => {
  const { fetchMock } = await addHermes(state => {
    state.selected = true
    return response({ enabled_services: Object.keys(NAMES), failed_services: [] })
  })
  expect(screen.queryByText(INTERRUPTED)).toBeNull()
  await tick()
  expect(screen.getByText(STARTED)).toBeVisible()
  expect(hermesCard().getByText('enabled')).toBeVisible()
  expect(requests(fetchMock, ENABLE, 'POST')).toBe(1)
})

it('follows a library install whose answer was lost to a gateway timeout', async () => {
  vi.useFakeTimers()
  const state = { apiUp: true, installed: false }
  const fetchMock = vi.fn(async (url, options = {}) => {
    const target = String(url)
    if (target === '/api/extensions/gotify/install' && options.method === 'POST') {
      state.installed = true
      state.apiUp = false
      return gatewayPage(504)
    }
    if (!state.apiUp) return gatewayPage(502)
    if (target === '/api/extensions/catalog') {
      return response({ agent_available: true, summary: { total: 1 }, extensions: [{
        id: 'gotify', name: 'Gotify', installable: true, features: [],
        ...(state.installed ? { source: 'user', status: 'enabled' } : { source: 'library', status: 'not_installed' }),
      }] })
    }
    if (target === '/api/templates') return response({ templates: [] })
    if (target === '/api/webui/selection') return response({ supported: false, enabled: true })
    if (target === '/api/extensions/gotify/install-plan') return response({ detail: 'Not Found' }, 404)
    if (target === '/api/extensions/gotify/progress') {
      return response({ service_id: 'gotify', status: 'started', phase_label: 'Service started' })
    }
    throw new Error(`Unexpected request: ${target}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  await act(async () => { render(<Extensions compact />) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Install' })) })
  const dialog = within(screen.getByRole('dialog', { name: 'Confirm action' }))
  await act(async () => { fireEvent.click(dialog.getByRole('button', { name: 'Install' })) })
  expect(screen.getByText('Connection to ODS was interrupted while installing Gotify. Checking whether it finished…')).toBeVisible()

  await tick()
  state.apiUp = true
  await tick()
  expect(screen.getByText(STARTED)).toBeVisible()
  expect(screen.getByText('enabled')).toBeVisible()
  expect(requests(fetchMock, '/api/extensions/gotify/install', 'POST')).toBe(1)
})
