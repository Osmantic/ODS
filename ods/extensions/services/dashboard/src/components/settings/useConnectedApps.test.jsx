import { act, cleanup, fireEvent, renderHook } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { connectGoogle, useConnectedAppsStatus } from './useConnectedApps'
import { statusFor } from './connectedAppsFixtures'

afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })
const response = (status, value) => ({ok: status === 200, status, json: async () => value})
const busy = () => response(409, {detail: {code: 'transition-busy', message: 'Busy', help: false}})
const fakeClock = () => vi.useFakeTimers({toFake: ['setTimeout', 'clearTimeout', 'Date', 'performance']})

it('shares an overlapping focus and manual read, then reads fresh after completion', async () => {
  let finish
  const value = statusFor()
  const fetch = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue(response(200, value))
  vi.stubGlobal('fetch', fetch)
  const {result} = renderHook(useConnectedAppsStatus)
  let first, second
  act(() => { first = result.current.refresh(); fireEvent.focus(window); second = result.current.refresh() })
  expect(first).toBe(second)
  expect(fetch).toHaveBeenCalledTimes(1)
  await act(async () => { finish(response(200, value)); await first })
  expect(result.current.status).toEqual(value)
  expect(result.current.unavailable).toBe(false)
  await act(async () => { await result.current.refresh() })
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('waits through a typed read refusal without duplicating focus requests', async () => {
  fakeClock()
  const fetch = vi.fn().mockResolvedValueOnce(busy()).mockResolvedValue(response(200, statusFor()))
  vi.stubGlobal('fetch', fetch)
  const {result} = renderHook(useConnectedAppsStatus)
  await act(async () => {})
  act(() => { fireEvent.focus(window); void result.current.refresh() })
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(result.current.unavailable).toBe(false)
  await act(async () => { await vi.advanceTimersByTimeAsync(500) })
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(result.current.status).toEqual(statusFor())
  expect(result.current.unavailable).toBe(false)
})

it('bounds lock retries and never turns a refusal into a successful empty status', async () => {
  fakeClock()
  const fetch = vi.fn().mockImplementation(async () => busy())
  vi.stubGlobal('fetch', fetch)
  const {result} = renderHook(useConnectedAppsStatus)
  await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
  expect(result.current.status).toBeNull()
  expect(result.current.unavailable).toBe(true)
  const calls = fetch.mock.calls.length
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  expect(fetch).toHaveBeenCalledTimes(calls)
})

it('stops retrying after unmount', async () => {
  fakeClock()
  const fetch = vi.fn().mockImplementation(async () => busy())
  vi.stubGlobal('fetch', fetch)
  const {unmount} = renderHook(useConnectedAppsStatus)
  await act(async () => {})
  unmount()
  await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('does not retry an untyped refusal or a sign-in mutation', async () => {
  const fetch = vi.fn().mockResolvedValue(response(409, {detail: 'Status unavailable'}))
  vi.stubGlobal('fetch', fetch)
  const {result} = renderHook(useConnectedAppsStatus)
  await act(async () => {})
  expect(result.current.unavailable).toBe(true)
  expect(fetch).toHaveBeenCalledTimes(1)
  fetch.mockResolvedValue(busy())
  await expect(connectGoogle({})).rejects.toMatchObject({code: 'transition-busy'})
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls[1][1].method).toBe('POST')
})

it('reads again after a change instead of reusing an earlier in-flight observation', async () => {
  let finish
  const updated = statusFor({state: 'connected'})
  const fetch = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue(response(200, updated))
  vi.stubGlobal('fetch', fetch)
  const {result} = renderHook(useConnectedAppsStatus)
  let changed
  act(() => { changed = result.current.refreshAfterChange() })
  expect(fetch).toHaveBeenCalledTimes(1)
  await act(async () => { finish(response(200, statusFor())); await changed })
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(result.current.status).toEqual(updated)
})

it('does not start a queued post-change read after unmount', async () => {
  let finish
  const fetch = vi.fn(() => new Promise(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetch)
  const {result, unmount} = renderHook(useConnectedAppsStatus)
  const changed = result.current.refreshAfterChange()
  unmount()
  finish(response(200, statusFor()))
  await expect(changed).resolves.toBeNull()
  expect(fetch).toHaveBeenCalledTimes(1)
})
