import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import CustomIntegrations from './CustomIntegrations'
import { formatCheckedAt } from '../lib/checkedAt'

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks() })

const policy = { maximum: 25, staleAfterSeconds: 60, timeoutSeconds: 5, method: 'GET', followsRedirects: false, sendsCredentials: false, readsResponseBody: false }
const now = () => new Date().toISOString()
const jev = (check = { status: 'healthy', detail: 'Answered HTTP 200', httpStatus: 200, latencyMs: 84, checkedAt: now() }) => ({
  id: 'jev', name: 'Jev', url: 'https://jev.example.com/health', notes: 'System 1 decision API', createdAt: now(), check,
})

function api(routes) {
  return vi.fn(async (url, init = {}) => {
    const key = `${init.method || 'GET'} ${url}`
    const handler = routes[key]
    if (!handler) throw new Error(`unexpected ${key}`)
    const { status = 200, body } = await handler(init)
    return { ok: status >= 200 && status < 300, status, text: async () => body === undefined ? '' : JSON.stringify(body), json: async () => body }
  })
}

it('explains the empty state and adds an integration that is checked right away', async () => {
  const fetch = api({
    'GET /api/integrations/custom': () => ({ body: { schemaVersion: 1, integrations: [], policy } }),
    'POST /api/integrations/custom': () => ({ status: 201, body: { integration: jev() } }),
  })
  vi.stubGlobal('fetch', fetch)
  render(<CustomIntegrations />)
  expect(await screen.findByText('No integrations yet.')).toBeVisible()

  fireEvent.click(screen.getByRole('button', { name: 'Add integration' }))
  const form = screen.getByRole('form', { name: 'Add an integration' })
  fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Jev' } })
  fireEvent.change(within(form).getByLabelText('Health URL'), { target: { value: 'https://jev.example.com/health' } })
  fireEvent.change(within(form).getByLabelText(/Notes/), { target: { value: 'System 1 decision API' } })
  fireEvent.submit(form)

  const row = await screen.findByRole('listitem', { name: 'Jev: Healthy' })
  expect(row).toHaveTextContent('jev.example.com/health')
  expect(row).toHaveTextContent('System 1 decision API')
  expect(row).toHaveTextContent(/Checked just now · HTTP 200 · 84 ms/)
  expect(screen.getByRole('status')).toHaveTextContent('Added Jev. It answered the first check.')
  expect(screen.queryByRole('form', { name: 'Add an integration' })).toBeNull()
  const post = fetch.mock.calls.find(([, init]) => init?.method === 'POST')
  expect(JSON.parse(post[1].body)).toEqual({ name: 'Jev', url: 'https://jev.example.com/health', notes: 'System 1 decision API' })
  expect(post[1].headers).toEqual({ 'Content-Type': 'application/json' })
})

it('keeps the form and shows why the server refused an integration', async () => {
  vi.stubGlobal('fetch', api({
    'GET /api/integrations/custom': () => ({ body: { integrations: [], policy } }),
    'POST /api/integrations/custom': () => ({ status: 400, body: { detail: 'Remove the query string (?…) or fragment (#…).' } }),
  }))
  render(<CustomIntegrations />)
  fireEvent.click(await screen.findByRole('button', { name: 'Add integration' }))
  const form = screen.getByRole('form', { name: 'Add an integration' })
  fireEvent.change(within(form).getByLabelText('Name'), { target: { value: 'Jev' } })
  fireEvent.change(within(form).getByLabelText('Health URL'), { target: { value: 'https://jev.example.com/health?key=x' } })
  fireEvent.submit(form)
  expect(await within(form).findByRole('alert')).toHaveTextContent('Remove the query string')
  expect(within(form).getByLabelText('Health URL')).toHaveValue('https://jev.example.com/health?key=x')
})

it('shows why an integration is not answering and re-checks on demand', async () => {
  const down = { status: 'down', detail: 'Connection refused', httpStatus: null, latencyMs: 3, checkedAt: new Date(Date.now() - 125000).toISOString() }
  const fetch = api({
    'GET /api/integrations/custom': () => ({ body: { integrations: [jev(down)], policy } }),
    'POST /api/integrations/custom/jev/check': () => ({ body: { integration: jev() } }),
  })
  vi.stubGlobal('fetch', fetch)
  render(<CustomIntegrations />)
  const row = await screen.findByRole('listitem', { name: 'Jev: Not reachable' })
  expect(row).toHaveTextContent('Connection refused')
  expect(row).toHaveTextContent('Checked 2 min ago · 3 ms')
  fireEvent.click(within(row).getByRole('button', { name: 'Check Jev now' }))
  expect(await screen.findByRole('listitem', { name: 'Jev: Healthy' })).not.toHaveTextContent('Connection refused')
})

it('asks before removing and only stops the checks', async () => {
  const fetch = api({
    'GET /api/integrations/custom': () => ({ body: { integrations: [jev()], policy } }),
    'DELETE /api/integrations/custom/jev': () => ({ status: 204 }),
  })
  vi.stubGlobal('fetch', fetch)
  render(<CustomIntegrations />)
  const row = await screen.findByRole('listitem', { name: 'Jev: Healthy' })
  fireEvent.click(within(row).getByRole('button', { name: 'Remove Jev' }))
  expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false)
  fireEvent.click(within(row).getByRole('button', { name: 'Keep' }))
  fireEvent.click(within(row).getByRole('button', { name: 'Remove Jev' }))
  fireEvent.click(within(row).getByRole('button', { name: 'Remove Jev', exact: true }))
  expect(await screen.findByText('Removed Jev. ODS no longer checks it.')).toBeVisible()
  expect(screen.queryByRole('listitem', { name: /^Jev:/ })).toBeNull()
  expect(fetch.mock.calls.filter(([, init]) => init?.method === 'DELETE')).toHaveLength(1)
})

it('reports an unreadable list with a retry', async () => {
  let fail = true
  vi.stubGlobal('fetch', api({
    'GET /api/integrations/custom': () => fail
      ? { status: 503, body: { detail: 'The saved integrations list could not be read; it was left untouched.' } }
      : { body: { integrations: [jev()], policy } },
  }))
  render(<CustomIntegrations />)
  expect(await screen.findByRole('alert')).toHaveTextContent('could not be read; it was left untouched')
  fail = false
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('listitem', { name: 'Jev: Healthy' })).toBeVisible()
})

it('explains exactly what a check sends and stores', async () => {
  vi.stubGlobal('fetch', api({ 'GET /api/integrations/custom': () => ({ body: { integrations: [], policy } }) }))
  render(<CustomIntegrations />)
  await screen.findByText('No integrations yet.')
  const help = screen.getByText('How checks work').closest('details')
  expect(help).toHaveTextContent('waits up to 5 seconds and does not follow redirects')
  expect(help).toHaveTextContent('no keys, cookies or passwords')
  expect(help).toHaveTextContent('integrations/custom.json')
})

it('polls while visible, pauses while hidden, and cleans up on unmount', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  let hidden = false
  vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden)
  const fetch = api({ 'GET /api/integrations/custom': () => ({ body: { integrations: [jev()], policy } }) })
  vi.stubGlobal('fetch', fetch)
  const view = render(<CustomIntegrations />)
  await act(async () => {})
  expect(fetch).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  expect(fetch).toHaveBeenCalledTimes(2)
  hidden = true
  await act(async () => { await vi.advanceTimersByTimeAsync(60000) })
  expect(fetch).toHaveBeenCalledTimes(2)
  hidden = false
  fireEvent(document, new Event('visibilitychange'))
  await act(async () => {})
  expect(fetch).toHaveBeenCalledTimes(3)
  view.unmount()
  expect(vi.getTimerCount()).toBe(0)
})

it('describes check times without ever claiming the future', () => {
  const base = Date.parse('2026-09-25T12:00:00Z')
  expect(formatCheckedAt(null, base)).toBe('Not checked yet')
  expect(formatCheckedAt('2026-09-25T12:00:05Z', base)).toBe('Checked just now')
  expect(formatCheckedAt('2026-09-25T11:59:30Z', base)).toBe('Checked 30 s ago')
  expect(formatCheckedAt('2026-09-25T11:55:00Z', base)).toBe('Checked 5 min ago')
  expect(formatCheckedAt('2026-09-25T09:00:00Z', base)).toBe('Checked 3 h ago')
})
