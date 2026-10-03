import { StrictMode, useEffect } from 'react'
import { act, fireEvent, render, screen, waitFor } from '../../test/test-utils'
import PixelProviderRuntime from './PixelProviderRuntime'
import PixelProviderSettings from './PixelProviderSettings'
import { binding, outcome, runtimeDoc } from './pixelProviderRuntimeFixtures'

// These transaction tests assume a prepared worker; the real setup integration
// is covered separately, including missing/drift/unknown and repair behavior.
vi.mock('../PixelAdviceRuntime', () => ({ default: function ReadyWorker({ onReadyChange }) {
  useEffect(() => { onReadyChange(true) }, [onReadyChange])
  return <p>Prepared worker fixture</p>
} }))

const response = data => ({ ok: true, status: 200, json: async () => data })
const button = name => screen.getByRole('button', { name })
const apply = () => button('Apply saved providers')
const recover = () => button('Recover interrupted provider change')
const off = () => button('Deactivate managed providers')
const refresh = () => button('Refresh provider runtime')
const props = () => ({ savedRevision: 3, saving: false, blocked: false, routingEnabled: true, allowCloud: false, onBusyChange: vi.fn() })
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
const posts = mock => mock.mock.calls.filter(([, options]) => options.method === 'POST')
const gets = mock => mock.mock.calls.filter(([, options]) => options.method === 'GET')
const confirm = operation => { fireEvent.click(operation === 'apply' ? apply() : operation === 'recover' ? recover() : off()); fireEvent.click(button(`Confirm and ${operation}`)) }
const doc = () => ({ configuration: { schemaVersion: 1, revision: 3, enabled: true,
  providers: [{ id: 'tower', label: 'Tower', kind: 'local', baseUrl: 'http://127.0.0.1:8080/v1', model: 'glm',
    contextTokens: 32768, maxOutputTokens: 4096, supportsTools: true, supportsVision: false,
    reasoning: false, hasCredential: false, enabled: true }], roles: { leader: 'tower', backups: [], advisor: null, handoff: null },
  policy: { allowCloud: false, maxAttempts: 3, deadlineSeconds: 120 } } })
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('shows a bounded controller reason without retrying or declaring the runtime unchanged', async () => {
  const mock = vi.fn(async (_url, options) => options.method === 'POST'
    ? new globalThis.Response(JSON.stringify({ detail: { reason: 'provider-inspection-changed', message: 'private-sentinel' } }), { status: 409 })
    : response(runtimeDoc('applied')))
  vi.stubGlobal('fetch', mock)
  render(<PixelProviderRuntime {...props()} />)
  await waitFor(() => expect(off()).toBeEnabled())
  confirm('deactivate')
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('provider-inspection-changed'))
  expect(screen.getByRole('alert')).toHaveTextContent('Refresh runtime status before any further change')
  expect(screen.getByRole('alert')).not.toHaveTextContent('private-sentinel')
  expect(posts(mock)).toHaveLength(1)
  expect(off()).toBeDisabled()
  expect(gets(mock)).toHaveLength(1)
})

it.each([
  '{"detail":{"reason":"private-sentinel","message":"private-sentinel"}}',
  '{"detail":{"reason":"provider-inspection-changed","message":"' + 'x'.repeat(2048) + '"}}',
  '{',
])('keeps unknown, oversized or malformed controller failures uncertain: %s', async raw => {
  const mock = vi.fn(async (_url, options) => options.method === 'POST'
    ? new globalThis.Response(raw, { status: 409 }) : response(runtimeDoc('applied')))
  vi.stubGlobal('fetch', mock)
  render(<PixelProviderRuntime {...props()} />)
  await waitFor(() => expect(off()).toBeEnabled())
  confirm('deactivate')
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('could not be confirmed'))
  expect(screen.getByRole('alert')).not.toHaveTextContent('private-sentinel')
  expect(posts(mock)).toHaveLength(1)
  expect(off()).toBeDisabled()
})

it('requires explicit confirmation, focuses cancel, and restores focus on Escape', async () => {
  const mock = vi.fn(async () => response(runtimeDoc()))
  vi.stubGlobal('fetch', mock)
  render(<PixelProviderRuntime {...props()} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  fireEvent.click(apply())
  expect(button('Cancel')).toHaveFocus()
  expect(screen.getByRole('dialog')).toHaveAccessibleDescription(/saved provider revision 3.*sandbox mode will not change/)
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(apply()).toHaveFocus()
  expect(posts(mock)).toHaveLength(0)
})
it('requires fresh cloud acknowledgement on each confirmation', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response(runtimeDoc())))
  render(<PixelProviderRuntime {...props()} allowCloud />)
  await waitFor(() => expect(apply()).toBeEnabled())
  fireEvent.click(apply())
  expect(button('Confirm and apply')).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox'))
  expect(button('Confirm and apply')).toBeEnabled()
  fireEvent.click(button('Cancel')); fireEvent.click(apply())
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  expect(button('Confirm and apply')).toBeDisabled()
})
it('locks parent edits through exactly one POST and corroborating GET', async () => {
  let state = runtimeDoc()
  const pending = deferred()
  const mock = vi.fn(async (url, options) => url === '/api/pixel/providers' ? response(doc())
    : options.method === 'POST' ? pending.promise : response(state))
  vi.stubGlobal('fetch', mock)
  render(<PixelProviderSettings />)
  await waitFor(() => expect(apply()).toBeEnabled())
  fireEvent.click(apply()); const submit = button('Confirm and apply')
  fireEvent.click(submit); fireEvent.click(submit)
  expect(posts(mock)).toHaveLength(1)
  expect(JSON.parse(posts(mock)[0][1].body)).toEqual({ operation: 'apply', revision: 'a'.repeat(64), providerRevision: 3 })
  expect(screen.getByLabelText('Model')).toBeDisabled()
  expect(button('Reload providers')).toBeDisabled()
  expect(button('Save providers')).toBeDisabled()
  state = runtimeDoc('applied')
  await act(async () => pending.resolve(response(outcome())))
  expect(await screen.findByText('Provider revision 3 is applied and registration is verified.')).toBeVisible()
  expect(screen.getByLabelText('Model')).toBeEnabled()
  expect(off()).toBeEnabled()
  expect(screen.getByText(/Model connectivity, answer quality, and fallback behavior are not verified/)).toBeVisible()
})
it.each(['lost', 'http', 'wrong-binding', 'private'])('does not retry %s or claim success before verification', async kind => {
  let state = runtimeDoc()
  const mock = vi.fn(async (_url, options) => {
    if (options.method === 'GET') return response(state)
    state = runtimeDoc('applied')
    if (kind === 'lost') throw new Error('private-sentinel')
    if (kind === 'http') return { ok: false, status: 503 }
    if (kind === 'wrong-binding') return response(outcome({ ...binding(), allowCloud: true }))
    return response({ ...outcome(), secret: 'private-sentinel' })
  })
  const values = props()
  vi.stubGlobal('fetch', mock); render(<PixelProviderRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  const before = gets(mock).length
  confirm('apply')
  expect(await screen.findByRole('alert')).toHaveTextContent(/could not be confirmed|verification failed/)
  expect(screen.queryByText(/private-sentinel/)).not.toBeInTheDocument()
  expect(apply()).toBeDisabled()
  expect(posts(mock)).toHaveLength(1)
  expect(gets(mock)).toHaveLength(before + (kind === 'wrong-binding' ? 1 : 0))
  expect(values.onBusyChange.mock.calls).toEqual([[true], [false]])
  fireEvent.click(refresh())
  await waitFor(() => expect(off()).toBeEnabled())
  expect(posts(mock)).toHaveLength(1)
})
it('deactivates from saved-changes without deleting saved preferences', async () => {
  let state = runtimeDoc('saved-changes')
  const mock = vi.fn(async (_url, options) => {
    if (options.method === 'POST') { state = runtimeDoc('inactive'); return response(outcome(null)) }
    return response(state)
  })
  vi.stubGlobal('fetch', mock); render(<PixelProviderRuntime {...props()} />)
  await waitFor(() => expect(off()).toBeEnabled()); confirm('deactivate')
  expect(await screen.findByText(/Managed providers are deactivated;/)).toBeVisible()
  expect(JSON.parse(posts(mock)[0][1].body).operation).toBe('deactivate')
})
it('recovers an older binding when saved configuration cannot load', async () => {
  let state = runtimeDoc('pending')
  const mock = vi.fn(async (url, options) => {
    if (url === '/api/pixel/providers') return { ok: false, status: 503 }
    if (options.method === 'POST') { state = runtimeDoc('saved-changes'); return response(outcome(binding(2), 'rolled-back')) }
    return response(state)
  })
  vi.stubGlobal('fetch', mock); render(<PixelProviderSettings />)
  await waitFor(() => expect(recover()).toBeEnabled()); confirm('recover')
  expect(await screen.findByText(/recorded prior provider configuration is restored/)).toBeVisible()
  expect(JSON.parse(posts(mock)[0][1].body)).toEqual({ operation: 'recover', revision: 'a'.repeat(64), providerRevision: 3 })
  expect(screen.getByText(/Provider settings are unavailable/)).toBeVisible()
})
it.each([{ blocked: true }, { saving: true }, { savedRevision: 4 }, { allowCloud: true }, { routingEnabled: false }])('invalidates consent when inputs change %j', async delta => {
  const mock = vi.fn(async () => response(runtimeDoc()))
  vi.stubGlobal('fetch', mock); const values = props()
  const view = render(<PixelProviderRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled()); fireEvent.click(apply())
  view.rerender(<PixelProviderRuntime {...values} {...delta} />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(mock)).toHaveLength(0)
})
it('lets the synchronous parent guard refuse a mutation', async () => {
  const mock = vi.fn(async () => response(runtimeDoc()))
  vi.stubGlobal('fetch', mock); render(<PixelProviderRuntime {...props()} onBusyChange={() => false} />)
  await waitFor(() => expect(apply()).toBeEnabled()); confirm('apply')
  expect(posts(mock)).toHaveLength(0)
})
it('ignores late completion after unmount and releases the captured parent callback once', async () => {
  const pending = deferred(); const values = props()
  const mock = vi.fn(async (_url, options) => options.method === 'POST' ? pending.promise : response(runtimeDoc()))
  vi.stubGlobal('fetch', mock); const view = render(<PixelProviderRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled()); confirm('apply')
  const reads = gets(mock).length
  view.unmount(); await act(async () => pending.resolve(response(outcome())))
  expect(gets(mock)).toHaveLength(reads)
  expect(values.onBusyChange.mock.calls).toEqual([[true], [false]])
})
it('does not auto-apply in StrictMode', async () => {
  const mock = vi.fn(async () => response(runtimeDoc()))
  vi.stubGlobal('fetch', mock); render(<StrictMode><PixelProviderSettings /></StrictMode>)
  await waitFor(() => expect(screen.getAllByRole('alert').length).toBeGreaterThan(0))
  expect(posts(mock)).toHaveLength(0)
})

it('times out without assuming server cancellation, automatic GET, or a retry', async () => {
  const mock = vi.fn((_url, options) => options.method === 'GET' ? Promise.resolve(response(runtimeDoc()))
    : new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new window.DOMException('Aborted', 'AbortError')))))
  const values = props()
  vi.stubGlobal('fetch', mock); render(<PixelProviderRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  vi.useFakeTimers(); confirm('apply')
  await act(async () => vi.advanceTimersByTimeAsync(350000))
  expect(screen.getByRole('alert')).toHaveTextContent(/timed out.*may still be running/)
  expect(posts(mock)).toHaveLength(1)
  expect(gets(mock)).toHaveLength(1)
  expect(values.onBusyChange.mock.calls).toEqual([[true], [false]])
})

it('discards old StrictMode inspection and confirms against the replacement only', async () => {
  const old = deferred()
  const mock = vi.fn().mockImplementationOnce(() => old.promise).mockResolvedValue(response(runtimeDoc()))
  vi.stubGlobal('fetch', mock); render(<StrictMode><PixelProviderRuntime {...props()} /></StrictMode>)
  await waitFor(() => expect(apply()).toBeEnabled())
  await act(async () => old.resolve(response(runtimeDoc('applied'))))
  expect(apply()).toBeEnabled()
  expect(posts(mock)).toHaveLength(0)
})

it('refuses obsolete success when saved revision changes during mutation', async () => {
  const pending = deferred(); const values = props()
  let state = runtimeDoc()
  const mock = vi.fn(async (_url, options) => options.method === 'POST' ? pending.promise : response(state))
  vi.stubGlobal('fetch', mock); const view = render(<PixelProviderRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled()); confirm('apply')
  view.rerender(<PixelProviderRuntime {...values} savedRevision={4} />)
  state = runtimeDoc('applied')
  await act(async () => pending.resolve(response(outcome())))
  expect(screen.getByRole('alert')).toHaveTextContent(/verification failed/)
  expect(screen.queryByText('Provider revision 3 is applied and registration is verified.')).not.toBeInTheDocument()
  expect(posts(mock)).toHaveLength(1)
})

it('releases the callback that acquired the operation even if props change', async () => {
  const pending = deferred(); const values = props(); const changed = vi.fn()
  let state = runtimeDoc()
  const mock = vi.fn(async (_url, options) => options.method === 'POST' ? pending.promise : response(state))
  vi.stubGlobal('fetch', mock); const view = render(<PixelProviderRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled()); confirm('apply')
  view.rerender(<PixelProviderRuntime {...values} onBusyChange={changed} />)
  state = runtimeDoc('applied')
  await act(async () => pending.resolve(response(outcome())))
  expect(values.onBusyChange.mock.calls).toEqual([[true], [false]])
  expect(changed).not.toHaveBeenCalled()
})

it('keeps unsaved edits and prevents Apply until they are saved or cancelled', async () => {
  vi.stubGlobal('fetch', vi.fn(async url => response(url === '/api/pixel/providers' ? doc() : runtimeDoc())))
  render(<PixelProviderSettings />)
  await waitFor(() => expect(apply()).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'unsaved-model' } })
  expect(apply()).toBeDisabled()
  fireEvent.click(refresh())
  await waitFor(() => expect(refresh()).toBeEnabled())
  expect(screen.getByLabelText('Model')).toHaveValue('unsaved-model')
  expect(apply()).toBeDisabled()
  fireEvent.click(button('Cancel provider edits'))
  expect(apply()).toBeEnabled()
})
