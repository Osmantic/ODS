import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import PixelDiagnostics, { summarizeCheck } from './PixelDiagnostics'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
const ok = data => ({ ok:true, json:async () => data })
const show = () => render(<MemoryRouter><PixelDiagnostics/></MemoryRouter>)
it('reports each real check independently and only performs reads', async () => {
  const fetch = vi.fn(async path => path.endsWith('access-mode') ? {ok:false}
    : path === '/api/status' ? ok({inference:{loadedModel:'local-Qwen.gguf',contextSize:65536}})
      : ok({available:true,detail:'Owner agent ready'}))
  vi.stubGlobal('fetch', fetch)
  show()
  expect(await screen.findByRole('button', {name:'Refresh checks'})).toBeEnabled()
  expect(screen.getByText('Ready')).toBeVisible()
  expect(screen.getByText(`${(65536).toLocaleString()} tokens`)).toBeVisible()
  expect(within(screen.getByRole('region', {name:'Access verification'})).getByText('Unavailable')).toBeVisible()
  expect(fetch).toHaveBeenCalledTimes(3)
  for (const [,options] of fetch.mock.calls) expect(options.method).toBeUndefined()
  expect(screen.getByRole('link', {name:'Access settings'})).toHaveAttribute('href','/settings?section=access')
})
it.each([null, ''])('does not substitute configured metadata for an unloaded runtime (%s)', async loadedModel => {
  vi.stubGlobal('fetch', vi.fn(async path => path === '/api/status'
    ? ok({ inference: { loadedModel, contextSize: null }, model: { name: 'Configured-only.gguf', contextLength: 65536 } })
    : ok({ available: true })))
  show()
  const region = screen.getByRole('region', { name: 'ODS model' })
  expect(await within(region).findByText('Not loaded')).toBeVisible()
  expect(within(region).queryByText('Configured-only.gguf')).toBeNull()
  expect(within(region).queryByText(`${(65536).toLocaleString()} tokens`)).toBeNull()
  expect(within(region).getAllByText('Not reported')).toHaveLength(2)
})

it('retains legacy model telemetry when the response has no inference section', async () => {
  vi.stubGlobal('fetch', vi.fn(async path => path === '/api/status'
    ? ok({ model: { name: 'Legacy.gguf', contextLength: 32768 } }) : ok({ available: true })))
  show()
  const region = screen.getByRole('region', { name: 'ODS model' })
  expect(await within(region).findByText('Legacy.gguf')).toBeVisible()
  expect(within(region).getByText(`${(32768).toLocaleString()} tokens`)).toBeVisible()
})

it('clears stale success on refresh and allows retry after errors', async () => {
  const fetch = vi.fn(async path => path === '/api/status' ? ok({model:null}) : ok({available:true}))
  vi.stubGlobal('fetch', fetch)
  show()
  await screen.findByRole('button', {name:'Refresh checks'})
  expect(screen.getByText('Ready')).toBeVisible()
  fetch.mockRejectedValue(new Error('private upstream details must not be shown'))
  fireEvent.click(screen.getByRole('button', {name:'Refresh checks'}))
  await waitFor(() => expect(screen.getAllByText('Unavailable')).toHaveLength(3))
  expect(screen.queryByText('Ready')).toBeNull()
  expect(screen.queryByText(/private upstream details/)).toBeNull()
})
it('aborts pending reads when the section is closed', () => {
  const signals = []
  vi.stubGlobal('fetch', vi.fn((path, options) => {
    signals.push(options.signal)
    return new Promise((resolve,reject) => options.signal.addEventListener('abort', () => reject(new Error('aborted'))))
  }))
  const view = show()
  view.unmount()
  expect(signals).toHaveLength(3)
  expect(signals.every(signal => signal.aborted)).toBe(true)
})
it('does not mistake configured access for verified access', () => {
  expect(summarizeCheck('access',{available:true,configured_mode:'full-access',effective_mode:'full-access',runtime_verified:false}).ok).toBe(false)
  expect(summarizeCheck('access',{available:true,effective_mode:'sandboxed',runtime_verified:true}).ok).toBe(true)
  expect(() => summarizeCheck('agent',{})).toThrow()
  expect(() => summarizeCheck('model',{})).toThrow()
})
