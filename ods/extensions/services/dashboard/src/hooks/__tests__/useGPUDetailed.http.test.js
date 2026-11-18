import {act, renderHook} from '@testing-library/react'
import {useGPUDetailed} from '../useGPUDetailed'

beforeEach(() => {vi.useFakeTimers();vi.stubGlobal('fetch',vi.fn())})
afterEach(() => {vi.useRealTimers();vi.unstubAllGlobals()})
const ok = value => ({ok:true,json:async () => value})

it('reports a failed primary observation, keeps its last snapshot and recovers on a successful poll', async () => {
  fetch.mockResolvedValue(ok({snapshot:'first'}))
  const {result,unmount}=renderHook(() => useGPUDetailed())
  await act(async () => {})
  expect(result.current.detailed).toEqual({snapshot:'first'})
  const failedBody=vi.fn()
  fetch.mockImplementation(url => Promise.resolve(url.endsWith('/detailed')
    ? {ok:false,status:503,json:failedBody} : ok({snapshot:'outage'})))
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(result.current.error).toBe('GPU detail request failed (HTTP 503)')
  expect(result.current.detailed).toEqual({snapshot:'first'})
  expect(failedBody).not.toHaveBeenCalled()
  fetch.mockResolvedValue(ok({snapshot:'recovered'}))
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(result.current.error).toBeNull()
  expect(result.current.detailed).toEqual({snapshot:'recovered'})
  unmount()
})

it('reports initial HTTP failure instead of completing an error-free empty observation', async () => {
  fetch.mockResolvedValue({ok:false,status:401})
  const {result,unmount}=renderHook(() => useGPUDetailed())
  await act(async () => {})
  expect(result.current.loading).toBe(false)
  expect(result.current.error).toBe('GPU detail request failed (HTTP 401)')
  expect(result.current.detailed).toBeNull()
  unmount()
})

it('preserves the optional history and topology degradation contract', async () => {
  fetch.mockImplementation(url => Promise.resolve(url.endsWith('/detailed')
    ? ok({snapshot:'fresh'}) : {ok:false,status:503}))
  const {result,unmount}=renderHook(() => useGPUDetailed())
  await act(async () => {})
  expect(result.current.detailed).toEqual({snapshot:'fresh'})
  expect(result.current.error).toBeNull()
  expect(result.current.history).toBeNull()
  expect(result.current.topology).toBeNull()
  unmount()
})
