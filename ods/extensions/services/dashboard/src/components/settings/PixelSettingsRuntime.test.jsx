import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '../../test/test-utils'
import PixelSettingsRuntime from './PixelSettingsRuntime'
import PixelRuntimeSettings from './PixelRuntimeSettings'
import { runtimeDoc } from './pixelRuntimeStatusFixtures'

const response = data => ({ ok: true, status: 200, json: async () => data })
const doc = (revision = 3, preferences = {}) => ({ configuration: { schemaVersion: 1, revision, preferences },
  runtime: { status: 'not-inspected', reason: 'runtime-status-separate' } })
const button = name => screen.getByRole('button', { name })
const apply = () => button('Apply saved Pixel preferences')
const recover = () => button('Recover interrupted settings change')
const refresh = () => button('Refresh runtime status')
const confirmApply = () => { fireEvent.click(apply()); fireEvent.click(button('Confirm and apply')) }
const confirmRecover = () => { fireEvent.click(recover()); fireEvent.click(button('Confirm and recover')) }
const posts = mock => mock.mock.calls.filter(([, options]) => options.method === 'POST')
const gets = mock => mock.mock.calls.filter(([, options]) => options.method === 'GET')
const props = () => ({ savedRevision: 3, saving: false, blocked: false, onBusyChange: vi.fn() })
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })

it('inspects on mount, labels declared limits honestly, and requires restart confirmation', async () => {
  const mock = vi.fn(async () => response(runtimeDoc()))
  vi.stubGlobal('fetch', mock)
  render(<PixelSettingsRuntime {...props()} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  expect(screen.getByText('Not verified')).toBeVisible()
  expect(screen.getByText('Not qualified')).toBeVisible()
  fireEvent.click(apply())
  expect(screen.getByRole('dialog')).toHaveAccessibleDescription(/Apply saved revision 3.*Existing access mode will be preserved/)
  expect(button('Cancel')).toHaveFocus()
  expect(posts(mock)).toHaveLength(0)
  fireEvent.click(button('Cancel'))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(apply()).toHaveFocus()
  expect(posts(mock)).toHaveLength(0)
})

it('explains that a fresh owner store needs Save without advertising runtime activation', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => response({ ...runtimeDoc('unavailable'), reason: 'settings-store-not-initialized' })))
  render(<PixelSettingsRuntime {...props()} />)
  expect(await screen.findByText('Save Pixel preferences once to initialize runtime controls. Saving does not apply them.')).toBeVisible()
  expect(apply()).toBeDisabled()
  expect(button('Recover interrupted settings change')).toBeDisabled()
  expect(screen.queryByText('Runtime control is unavailable on this installation. Saving preferences is still available.')).not.toBeInTheDocument()
})

it('locks parent preferences during one Apply and requires matching fresh GET before success', async () => {
  let state = runtimeDoc()
  const pending = deferred()
  const mock = vi.fn(async (url, options) => {
    if (url === '/api/pixel/settings') return response(doc())
    if (options.method === 'POST') return pending.promise
    return response(state)
  })
  vi.stubGlobal('fetch', mock)
  render(<PixelRuntimeSettings />)
  await waitFor(() => expect(apply()).toBeEnabled())
  fireEvent.click(apply())
  const confirm = button('Confirm and apply')
  fireEvent.click(confirm); fireEvent.click(confirm); fireEvent.click(apply())
  expect(posts(mock)).toHaveLength(1)
  expect(JSON.parse(posts(mock)[0][1].body)).toEqual({ operation: 'apply', revision: 'a'.repeat(64), settingsRevision: 3 })
  expect(screen.getByLabelText('Context Window Size')).toBeDisabled()
  expect(button('Save Pixel preferences')).toBeDisabled()
  expect(button('Reload Pixel preferences')).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent(/Waiting for the settings controller/)
  state = { ...runtimeDoc('applied'), revision: 'b'.repeat(64) }
  await act(async () => pending.resolve(response({ outcome: 'applied', appliedRevision: 3 })))
  expect(await screen.findByRole('status')).toHaveTextContent(/revision 3.*applied and verified/)
  expect(apply()).toBeDisabled()
  expect(screen.getByLabelText('Context Window Size')).toBeEnabled()
})

it.each(['lost', 'http', 'wrong-revision', 'private'])('requires fresh inspection after %s POST, without auto-retry or secret echo', async kind => {
  let state = runtimeDoc()
  const mock = vi.fn(async (_url, options) => {
    if (options.method !== 'POST') return response(state)
    state = runtimeDoc('applied')
    if (kind === 'lost') throw new Error('private-sentinel')
    if (kind === 'http') return { ok: false, status: 409 }
    if (kind === 'wrong-revision') return response({ outcome: 'applied', appliedRevision: 4 })
    return response({ outcome: 'applied', appliedRevision: 3, token: 'private-sentinel' })
  })
  const values = props()
  vi.stubGlobal('fetch', mock)
  render(<PixelSettingsRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  const before = gets(mock).length
  confirmApply()
  expect(await screen.findByRole('alert')).toHaveTextContent(/could not be confirmed/)
  expect(screen.queryByText(/private-sentinel/)).not.toBeInTheDocument()
  expect(apply()).toBeDisabled()
  expect(posts(mock)).toHaveLength(1)
  expect(gets(mock)).toHaveLength(before)
  fireEvent.click(refresh())
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  expect(screen.getByText('Saved revision 3 is applied and verified.')).toBeVisible()
  expect(posts(mock)).toHaveLength(1)
  expect(values.onBusyChange.mock.calls).toEqual([[true], [false]])
})

it('refuses success when a newer saved revision appears during post-Apply readback', async () => {
  let state = runtimeDoc()
  const mock = vi.fn(async (_url, options) => {
    if (options.method === 'POST') {
      state = runtimeDoc('saved-changes', 4)
      return response({ outcome: 'applied', appliedRevision: 3 })
    }
    return response(state)
  })
  vi.stubGlobal('fetch', mock)
  render(<PixelSettingsRuntime {...props()} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  confirmApply()
  expect(await screen.findByRole('alert')).toHaveTextContent(/verification failed/)
  expect(apply()).toBeDisabled()
  expect(screen.queryByText(/is applied and verified/)).not.toBeInTheDocument()
})

it.each(['applied', 'rolled-back'])('recovers %s outcome even when saved preferences are unreadable', async outcome => {
  let state = runtimeDoc('pending')
  const mock = vi.fn(async (url, options) => {
    if (url === '/api/pixel/settings') return { ok: false, status: 503 }
    if (options.method === 'POST') {
      state = runtimeDoc(outcome === 'applied' ? 'applied' : 'restored')
      return response({ outcome, appliedRevision: outcome === 'applied' ? 3 : null })
    }
    return response(state)
  })
  vi.stubGlobal('fetch', mock)
  render(<PixelRuntimeSettings />)
  await waitFor(() => expect(recover()).toBeEnabled())
  expect(apply()).toBeDisabled()
  confirmRecover()
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(outcome === 'applied' ? /applied and verified/ : /restored and verified/))
  expect(JSON.parse(posts(mock)[0][1].body)).toEqual({ operation: 'recover', revision: 'a'.repeat(64), settingsRevision: 3 })
  expect(posts(mock)).toHaveLength(1)
})

it('continues to save sparse preferences when runtime control is unavailable', async () => {
  let saved = doc()
  const mock = vi.fn(async (url, options) => {
    if (url.endsWith('/runtime')) return response(runtimeDoc('unavailable'))
    if (options.method === 'POST') saved = doc(4, JSON.parse(options.body).changes)
    return response(saved)
  })
  vi.stubGlobal('fetch', mock)
  render(<PixelRuntimeSettings />)
  await waitFor(() => expect(screen.getByLabelText('Context Window Size')).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Context Window Size'), { target: { value: '65536' } })
  fireEvent.click(button('Save Pixel preferences'))
  await waitFor(() => expect(screen.getByText('Preferences saved. Pixel runtime is unchanged.')).toBeVisible())
  expect(apply()).toBeDisabled()
  expect(posts(mock)).toHaveLength(1)
  expect(posts(mock)[0][0]).toBe('/api/pixel/settings/save')
})

it('aborts a timed-out request without assuming server cancellation or retrying', async () => {
  const mock = vi.fn((_url, options) => options.method === 'GET' ? Promise.resolve(response(runtimeDoc()))
    : new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(new window.DOMException('Aborted', 'AbortError')))))
  vi.stubGlobal('fetch', mock)
  const values = props()
  render(<PixelSettingsRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  vi.useFakeTimers()
  confirmApply()
  await act(async () => vi.advanceTimersByTimeAsync(350000))
  expect(screen.getByRole('alert')).toHaveTextContent(/timed out.*may still be running/)
  expect(apply()).toBeDisabled()
  expect(posts(mock)).toHaveLength(1)
  expect(values.onBusyChange).toHaveBeenLastCalledWith(false)
})

it('ignores a late POST after unmount and does not issue verification GET', async () => {
  const pending = deferred()
  const mock = vi.fn(async (_url, options) => options.method === 'POST' ? pending.promise : response(runtimeDoc()))
  vi.stubGlobal('fetch', mock)
  const values = props()
  const view = render(<PixelSettingsRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  confirmApply()
  view.unmount()
  await act(async () => pending.resolve(response({ outcome: 'applied', appliedRevision: 3 })))
  expect(gets(mock)).toHaveLength(1)
  expect(values.onBusyChange).toHaveBeenLastCalledWith(false)
})

it('ignores late inspection from the discarded StrictMode mount', async () => {
  const old = deferred()
  const mock = vi.fn().mockImplementationOnce(() => old.promise).mockResolvedValue(response(runtimeDoc()))
  vi.stubGlobal('fetch', mock)
  render(<StrictMode><PixelSettingsRuntime {...props()} /></StrictMode>)
  await waitFor(() => expect(apply()).toBeEnabled())
  await act(async () => old.resolve(response(runtimeDoc('applied'))))
  expect(apply()).toBeEnabled()
  expect(posts(mock)).toHaveLength(0)
})

it('refreshes after saving a new revision, ignoring an older in-flight inspection', async () => {
  const old = deferred()
  const mock = vi.fn().mockImplementationOnce(() => old.promise).mockResolvedValue(response(runtimeDoc('not-applied', 4)))
  vi.stubGlobal('fetch', mock)
  const values = props()
  const view = render(<PixelSettingsRuntime {...values} />)
  view.rerender(<PixelSettingsRuntime {...values} savedRevision={4} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  await act(async () => old.resolve(response(runtimeDoc())))
  confirmApply()
  await waitFor(() => expect(posts(mock)).toHaveLength(1))
  expect(JSON.parse(posts(mock)[0][1].body).settingsRevision).toBe(4)
})

it('does not start a POST when the parent synchronous save guard rejects acquisition', async () => {
  const mock = vi.fn(async () => response(runtimeDoc()))
  vi.stubGlobal('fetch', mock)
  const values = { ...props(), onBusyChange: vi.fn(() => false) }
  render(<PixelSettingsRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  confirmApply()
  expect(values.onBusyChange).toHaveBeenCalledWith(true)
  expect(posts(mock)).toHaveLength(0)
})

it('releases the same parent callback that acquired the operation', async () => {
  let state = runtimeDoc()
  const pending = deferred()
  const mock = vi.fn(async (_url, options) => options.method === 'POST' ? pending.promise : response(state))
  vi.stubGlobal('fetch', mock)
  const values = props()
  const changed = vi.fn()
  const view = render(<PixelSettingsRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  confirmApply()
  view.rerender(<PixelSettingsRuntime {...values} onBusyChange={changed} />)
  state = runtimeDoc('applied')
  await act(async () => pending.resolve(response({ outcome: 'applied', appliedRevision: 3 })))
  expect(values.onBusyChange.mock.calls).toEqual([[true], [false]])
  expect(changed).not.toHaveBeenCalled()
})

it.each(['apply', 'recover'])('dismisses %s with Escape and restores trigger focus without a POST', async operation => {
  const mock = vi.fn(async () => response(runtimeDoc(operation === 'apply' ? 'not-applied' : 'pending')))
  vi.stubGlobal('fetch', mock)
  render(<PixelSettingsRuntime {...props()} />)
  const trigger = operation === 'apply' ? apply : recover
  await waitFor(() => expect(trigger()).toBeEnabled())
  fireEvent.click(trigger())
  fireEvent.keyDown(button('Cancel'), { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(trigger()).toHaveFocus()
  expect(posts(mock)).toHaveLength(0)
})

it.each(['saving', 'blocked', 'savedRevision'])('invalidates Apply consent when %s changes', async field => {
  const mock = vi.fn(async () => response(runtimeDoc()))
  vi.stubGlobal('fetch', mock)
  const values = props()
  const view = render(<PixelSettingsRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  fireEvent.click(apply())
  const oldConfirm = button('Confirm and apply')
  view.rerender(<PixelSettingsRuntime {...values} {...{ [field]: field === 'savedRevision' ? 4 : true }} />)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  fireEvent.click(oldConfirm)
  view.rerender(<PixelSettingsRuntime {...values} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(mock)).toHaveLength(0)
})

it('requires new consent after inspection replaces the runtime revision', async () => {
  let state = runtimeDoc()
  const mock = vi.fn(async () => response(state))
  vi.stubGlobal('fetch', mock)
  render(<PixelSettingsRuntime {...props()} />)
  await waitFor(() => expect(apply()).toBeEnabled())
  fireEvent.click(apply())
  state = { ...runtimeDoc(), revision: 'b'.repeat(64) }
  fireEvent.click(refresh())
  await waitFor(() => expect(apply()).toBeEnabled())
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(posts(mock)).toHaveLength(0)
  confirmApply()
  expect(JSON.parse(posts(mock)[0][1].body).revision).toBe('b'.repeat(64))
})
