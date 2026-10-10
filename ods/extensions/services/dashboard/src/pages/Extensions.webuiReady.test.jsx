import { act, cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from '../test/test-utils'
import Extensions from './Extensions' // eslint-disable-line no-unused-vars

const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data })

// The host selection can finish before the dashboard's background health
// cache observes the new container. Image preparation is already terminal.
async function show({ progressStatus = 'prepared', addStatus = 200 } = {}) {
  vi.useFakeTimers()
  const state = { enabled: false, health: 'disabled', progress: { status: progressStatus } }
  const fetchMock = vi.fn(async (url, options = {}) => {
    if (url === '/api/extensions/catalog') return response({
      agent_available: true,
      extensions: [{ id: 'open-webui', name: 'Open WebUI', source: 'core',
        status: state.health, port: 3000, features: [] }],
      summary: { total: 1 },
    })
    if (url === '/api/templates') return response({ templates: [] })
    if (url === '/api/extensions/open-webui/prepare') return response({ status: 'ready' })
    if (url === '/api/extensions/open-webui/progress') return response(state.progress)
    if (url === '/api/webui/selection' && options.method === 'POST') {
      if (addStatus !== 200) return response({ detail: 'Open WebUI could not be added' }, addStatus)
      state.enabled = true
      return response({ enabled: true, action: 'enabled' })
    }
    if (url === '/api/webui/selection') return response({ supported: true, enabled: state.enabled })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  let view
  await act(async () => { view = render(<Extensions compact />) })
  fireEvent.click(screen.getByRole('button', { name: 'Add Open WebUI' }))
  await act(async () => {
    const add = screen.getByRole('button', { name: 'Add', exact: true })
    // Two clicks in the same turn must still dispatch only one selection.
    fireEvent.click(add)
    fireEvent.click(add)
  })
  return { state, fetchMock, view }
}

const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(3000) })
const requestCount = (fetchMock, path, method = 'GET') => fetchMock.mock.calls
  .filter(([url, options = {}]) => url === path && (options.method || 'GET') === method).length

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it.each(['prepared', 'idle'])('shows the launch link after cached health catches up with %s image progress', async progressStatus => {
  const { state, fetchMock } = await show({ progressStatus })
  expect(screen.queryByRole('button', { name: 'Add Open WebUI' })).toBeNull()
  expect(screen.queryByRole('link', { name: ':3000' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Details for Open WebUI' }))
  expect(within(screen.getByRole('dialog')).getByText('disabled')).toBeVisible()

  await tick()
  expect(screen.queryByRole('link', { name: ':3000' })).toBeNull()
  state.health = 'enabled'
  await tick()

  expect(screen.getByRole('link', { name: ':3000' })).toHaveAttribute('href', 'http://localhost:3000')
  expect(within(screen.getByRole('dialog')).getByText('enabled')).toBeVisible()
  expect(screen.getByText('Extension installed and started.')).toBeVisible()
  const completedCount = requestCount(fetchMock, '/api/extensions/catalog')
  await tick()
  expect(requestCount(fetchMock, '/api/extensions/catalog')).toBe(completedCount)
  expect(requestCount(fetchMock, '/api/webui/selection', 'POST')).toBe(1)
  expect(requestCount(fetchMock, '/api/extensions/open-webui/prepare', 'POST')).toBe(1)
})

it('does not report ready while the selected container is unhealthy', async () => {
  const { state, fetchMock } = await show()
  state.health = 'unhealthy'
  await tick()
  expect(screen.getByText('Open WebUI selected. Waiting for its service to become ready.')).toBeVisible()
  expect(screen.queryByText('Extension installed and started.')).toBeNull()
  expect(screen.queryByRole('link', { name: ':3000' })).toBeNull()
  expect(requestCount(fetchMock, '/api/webui/selection', 'POST')).toBe(1)
})

it('surfaces a terminal startup error and stops polling without another Add request', async () => {
  const { state, fetchMock } = await show()
  state.health = 'error'
  state.progress = { status: 'error', error: 'Open WebUI startup failed' }
  await tick()
  expect(screen.getAllByText(/Open WebUI startup failed/).length).toBeGreaterThan(0)
  expect(screen.queryByRole('link', { name: ':3000' })).toBeNull()
  const completedCount = requestCount(fetchMock, '/api/extensions/open-webui/progress')
  await tick()
  expect(requestCount(fetchMock, '/api/extensions/open-webui/progress')).toBe(completedCount)
  expect(requestCount(fetchMock, '/api/webui/selection', 'POST')).toBe(1)
})

it('does not start readiness polling after a refused Add', async () => {
  const { fetchMock } = await show({ addStatus: 503 })
  expect(screen.getByText('Open WebUI could not be added')).toBeVisible()
  await tick()
  expect(requestCount(fetchMock, '/api/extensions/open-webui/progress')).toBe(0)
  expect(requestCount(fetchMock, '/api/webui/selection', 'POST')).toBe(1)
})

it('cleans up readiness polling when leaving Extensions', async () => {
  const { fetchMock, view } = await show()
  await tick()
  const count = requestCount(fetchMock, '/api/extensions/catalog')
  view.unmount()
  await tick()
  expect(requestCount(fetchMock, '/api/extensions/catalog')).toBe(count)
})
