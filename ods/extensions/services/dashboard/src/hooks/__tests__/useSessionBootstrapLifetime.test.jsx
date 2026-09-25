import {StrictMode} from 'react'
import {act, cleanup, renderHook} from '@testing-library/react'
import {useSessionBootstrap} from '../useSessionBootstrap'

const deferred = () => {let resolve; return {promise:new Promise(done => {resolve=done}), resolve:value=>resolve(value)}}
beforeEach(() => {vi.stubGlobal('fetch', vi.fn()); vi.spyOn(console, 'warn').mockImplementation(() => {})})
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals()})

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
  fetch.mockResolvedValue({ok:true})
  const hook = renderHook(({enabled}) => useSessionBootstrap(enabled), {initialProps:{enabled:false}})
  expect(fetch).not.toHaveBeenCalled()
  await act(async () => {hook.rerender({enabled:true})})
  await act(async () => {hook.rerender({enabled:true})})
  expect(fetch).toHaveBeenCalledTimes(1)
  hook.rerender({enabled:false})
  await act(async () => {hook.rerender({enabled:true})})
  expect(fetch).toHaveBeenCalledTimes(2)
})
