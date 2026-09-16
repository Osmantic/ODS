import {act, renderHook} from '@testing-library/react'
import {StrictMode, createElement} from 'react'
import {useGPUDetailed} from '../useGPUDetailed'

beforeEach(() => {vi.useFakeTimers(); vi.stubGlobal('fetch', vi.fn())})
afterEach(() => {vi.useRealTimers(); vi.unstubAllGlobals()})
const ok = value => ({ok:true, json:async () => value})
it('releases a stalled body and polls again after the deadline', async () => {
  let signal
  fetch.mockImplementation((_url, options) => {
    signal ||= options?.signal
    return Promise.resolve({ok:true,json:() => new Promise(() => {})})
  })
  const {result,unmount} = renderHook(() => useGPUDetailed())
  await act(async () => {await vi.advanceTimersByTimeAsync(15000)})
  expect(result.current.loading).toBe(false)
  expect(result.current.error).toMatch(/timed out/i)
  expect(signal.aborted).toBe(true)
  fetch.mockResolvedValue(ok({fresh:true}))
  await act(async () => {await vi.advanceTimersByTimeAsync(20000)})
  expect(result.current.detailed).toEqual({fresh:true})
  expect(result.current.error).toBeNull()
  unmount()
})
it('aborts the active poll on unmount', async () => {
  let signal
  fetch.mockImplementation((_url,options) => {signal=options?.signal;return new Promise(() => {})})
  const {unmount}=renderHook(() => useGPUDetailed())
  unmount()
  expect(signal?.aborted).toBe(true)
})

it('aborts sibling requests when one endpoint rejects before the deadline', async () => {
  const signals = []
  fetch.mockImplementation((url, options) => {
    signals.push(options.signal)
    return url.endsWith('/history') ? Promise.reject(new Error('Network failed')) : new Promise(() => {})
  })
  const {result,unmount} = renderHook(() => useGPUDetailed())
  await act(async () => {})
  expect(result.current.error).toBe('Network failed')
  expect(signals).toHaveLength(3)
  expect(signals.every(signal => signal.aborted)).toBe(true)
  fetch.mockResolvedValue(ok({recovered:true}))
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(result.current.detailed).toEqual({recovered:true})
  unmount()
})

it('starts a fresh poll immediately during StrictMode effect replay', async () => {
  fetch.mockImplementation((_url, options) => options.signal.aborted
    ? Promise.reject(new Error('aborted')) : Promise.resolve(ok({fresh:true})))
  const {result,unmount} = renderHook(() => useGPUDetailed(), {
    wrapper:({children}) => createElement(StrictMode, null, children),
  })
  await act(async () => {})
  expect(result.current.detailed).toEqual({fresh:true})
  expect(fetch).toHaveBeenCalledTimes(6)
  expect(fetch.mock.calls.slice(0,3).every(([,options]) => options.signal.aborted)).toBe(true)
  unmount()
})
