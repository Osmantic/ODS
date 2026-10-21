import {StrictMode} from 'react'
import {act, cleanup, renderHook, waitFor} from '@testing-library/react'
import {useSessionBootstrap} from '../useSessionBootstrap'

const deferred = () => {let resolve; return {promise:new Promise(done => {resolve=done}), resolve:value=>resolve(value)}}
const session = secondsLeft => ({ok:true, json:async () => ({valid:true, expires_at:Math.floor(Date.now() / 1000) + secondsLeft})})
const verifications = () => fetch.mock.calls.filter(([url]) => url === '/api/auth/verify-session').length
const mints = () => fetch.mock.calls.filter(([url]) => url === '/api/auth/admin-session').length
const setVisibility = state => Object.defineProperty(document, 'visibilityState', {configurable:true, get:() => state})
beforeEach(() => {vi.stubGlobal('fetch', vi.fn()); vi.spyOn(console, 'warn').mockImplementation(() => {})})
afterEach(() => {cleanup(); vi.useRealTimers(); delete document.visibilityState; vi.restoreAllMocks(); vi.unstubAllGlobals()})

it.each(['disable', 'unmount'])('does not mint from a late verification after %s', async change => {
  const pending = deferred()
  fetch.mockReturnValue(pending.promise)
  const hook = renderHook(({enabled}) => useSessionBootstrap(enabled), {initialProps:{enabled:true}})
  const signal = fetch.mock.calls[0][1].signal
  if (change === 'disable') hook.rerender({enabled:false})
  else hook.unmount()
  await act(async () => {pending.resolve({ok:false, status:401})})
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(signal?.aborted).toBe(true)
  expect(console.warn).not.toHaveBeenCalled()
})

it('keeps StrictMode setup replay usable without a stale verification minting', async () => {
  const pending = []
  fetch.mockImplementation(url => {
    if (url.includes('admin-session')) return Promise.resolve({ok:true})
    const request = deferred(); pending.push(request); return request.promise
  })
  renderHook(() => useSessionBootstrap(true), {wrapper:({children}) => <StrictMode>{children}</StrictMode>})
  await act(async () => {pending.forEach(request => request.resolve({ok:false, status:401}))})
  const calls = fetch.mock.calls
  expect(calls.filter(([url]) => url.includes('admin-session'))).toHaveLength(1)
  expect(calls.at(-1)[1].signal?.aborted).toBe(false)
})

it('starts a fresh verification after leaving and returning to an enabled route', async () => {
  fetch.mockResolvedValue(session(6 * 3600))
  const hook = renderHook(({enabled}) => useSessionBootstrap(enabled), {initialProps:{enabled:false}})
  expect(fetch).not.toHaveBeenCalled()
  await act(async () => {hook.rerender({enabled:true})})
  await act(async () => {hook.rerender({enabled:true})})
  expect(fetch).toHaveBeenCalledTimes(1)
  hook.rerender({enabled:false})
  await act(async () => {hook.rerender({enabled:true})})
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('checks again when a long-open tab regains focus or becomes visible', async () => {
  fetch.mockImplementation(async url => url === '/api/auth/verify-session' ? {ok:false, status:401} : {ok:true})
  renderHook(() => useSessionBootstrap(true))
  await waitFor(() => expect(mints()).toBe(1))

  act(() => {window.dispatchEvent(new Event('focus'))})
  await waitFor(() => expect(mints()).toBe(2))

  setVisibility('hidden')
  act(() => {document.dispatchEvent(new Event('visibilitychange'))})
  await act(async () => {})
  expect(verifications()).toBe(2)

  setVisibility('visible')
  act(() => {document.dispatchEvent(new Event('visibilitychange'))})
  await waitFor(() => expect(mints()).toBe(3))
  expect(verifications()).toBe(3)
})

it('runs one check at a time when focus and visibility fire together', async () => {
  const pending = deferred()
  fetch.mockReturnValue(pending.promise)
  renderHook(() => useSessionBootstrap(true))
  setVisibility('visible')
  act(() => {
    window.dispatchEvent(new Event('focus'))
    document.dispatchEvent(new Event('visibilitychange'))
  })
  expect(fetch).toHaveBeenCalledTimes(1)
  await act(async () => {pending.resolve(session(6 * 3600))})
  act(() => {window.dispatchEvent(new Event('focus'))})
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
})

it('renews the session on a timer well inside its 12-hour lifetime and stops when unmounted', async () => {
  vi.useFakeTimers()
  fetch.mockImplementation(async url => url === '/api/auth/verify-session' ? session(45 * 60) : {ok:true})
  const hook = renderHook(() => useSessionBootstrap(true))
  await act(async () => {await vi.advanceTimersByTimeAsync(0)})
  expect(verifications()).toBe(1)
  expect(mints()).toBe(1)

  await act(async () => {await vi.advanceTimersByTimeAsync(60 * 60 * 1000)})
  expect(verifications()).toBeGreaterThanOrEqual(3)
  expect(mints()).toBe(verifications())

  hook.unmount()
  const seen = fetch.mock.calls.length
  await act(async () => {await vi.advanceTimersByTimeAsync(12 * 60 * 60 * 1000)})
  window.dispatchEvent(new Event('focus'))
  document.dispatchEvent(new Event('visibilitychange'))
  expect(fetch).toHaveBeenCalledTimes(seen)
})
