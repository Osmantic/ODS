import { fireEvent, render, screen, waitFor } from '../../test/test-utils'
import PixelProviderRuntime from './PixelProviderRuntime'
import { runtimeDoc } from './pixelProviderRuntimeFixtures'

const response = value => ({ ok: true, status: 200, json: async () => value })
const props = { savedRevision: 3, saving: false, blocked: false, routingEnabled: true,
  allowCloud: false, onBusyChange: () => true }
const readiness = status => ({ status, revision: 1, runtimeId: status === 'missing' ? null : 'runtime-' + 'd'.repeat(32),
  sourceSha256: 'a'.repeat(64), host: 'canary', job: null,
  candidates: [{ id: 'b'.repeat(64), path: '/usr/bin/python3.12', version: '3.12.3', canPrepare: true }] })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear() })

it.each(['missing', 'drift', 'unknown'])('blocks Apply on %s while keeping recovery available', async status => {
  vi.stubGlobal('fetch', vi.fn(async url => url.includes('advice-runtime')
    ? response(status === 'unknown' ? {} : readiness(status)) : response(runtimeDoc('pending'))))
  render(<PixelProviderRuntime {...props} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Recover interrupted provider change' })).toBeEnabled())
  expect(screen.getByRole('button', { name: 'Apply saved providers' })).toBeDisabled()
  expect(screen.getByRole('region', { name: 'Provider worker runtime setup' })).toBeVisible()
})

it('allows deactivation despite drift and enables Apply only after refreshed readiness', async () => {
  let worker = 'drift'
  let state = runtimeDoc('applied')
  vi.stubGlobal('fetch', vi.fn(async url => response(url.includes('advice-runtime') ? readiness(worker) : state)))
  render(<PixelProviderRuntime {...props} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Deactivate managed providers' })).toBeEnabled())
  expect(screen.getByRole('button', { name: 'Repair private runtime' })).toBeDisabled()
  state = runtimeDoc('inactive')
  fireEvent.click(screen.getByRole('button', { name: 'Refresh provider runtime' }))
  await screen.findByText(/Managed providers are inactive/)
  expect(screen.getByRole('button', { name: 'Apply saved providers' })).toBeDisabled()
  worker = 'ready'
  fireEvent.click(screen.getByRole('button', { name: 'Refresh runtime readiness' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Apply saved providers' })).toBeEnabled())
})

it('invalidates open Apply confirmation as soon as readiness is refreshed', async () => {
  let resolveReadiness
  let refreshing = false
  const fetchMock = vi.fn(async url => {
    if (url.includes('advice-runtime')) return refreshing
      ? new Promise(resolve => { resolveReadiness = resolve }) : response(readiness('ready'))
    return response(runtimeDoc('inactive'))
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<PixelProviderRuntime {...props} />)
  const apply = screen.getByRole('button', { name: 'Apply saved providers' })
  await waitFor(() => expect(apply).toBeEnabled())
  fireEvent.click(apply)
  expect(screen.getByRole('dialog')).toBeVisible()
  refreshing = true
  fireEvent.click(screen.getByRole('button', { name: 'Refresh runtime readiness' }))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(apply).toBeDisabled()
  resolveReadiness(response(readiness('drift')))
  await screen.findByRole('button', { name: 'Repair private runtime' })
  expect(apply).toBeDisabled()
  expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
})
