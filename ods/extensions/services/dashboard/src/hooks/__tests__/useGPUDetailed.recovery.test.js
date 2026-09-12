import {act, renderHook} from '@testing-library/react'
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
