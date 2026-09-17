import {act, cleanup, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, beforeEach, expect, it, vi} from 'vitest'
import PixelAccessCard from './PixelAccessCard'

const verified = {available: true, surface: 'linux-systemd', configured_mode: 'sandboxed',
  effective_mode: 'sandboxed', runtime_verified: true, revision: 'a'.repeat(64), busy: false, pending: false}
const response = value => ({ok: true, json: async () => value})
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return {promise, resolve}
}
const tick = ms => act(async () => { await vi.advanceTimersByTimeAsync(ms) })
beforeEach(() => vi.useFakeTimers())
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

it.each(['pending', 'busy'].flatMap(state => ['headers', 'body'].map(stage => [state, stage])))(
  'accepts a slow %s inspection through delayed %s without superseding it every five seconds', async (state, stage) => {
  const slow = deferred()
  const fetch = vi.fn().mockResolvedValueOnce(response({...verified, [state]: true}))
    .mockImplementation(() => stage === 'headers' ? slow.promise : {ok: true, json: () => slow.promise})
  vi.stubGlobal('fetch', fetch)
  render(<PixelAccessCard />)
  await tick(0)
  await tick(5000)
  expect(fetch).toHaveBeenCalledTimes(2)
  await tick(10000)
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(screen.getByText('Effective').nextElementSibling).toHaveTextContent('Not verified')
  expect(screen.getByRole('button', {name: 'Enable Full Access'})).toBeDisabled()
  await act(async () => slow.resolve(stage === 'headers' ? response(verified) : verified))
  expect(screen.getByText('Effective').nextElementSibling).toHaveTextContent('Safer mode')
  expect(screen.getByRole('button', {name: 'Enable Full Access'})).toBeEnabled()
  await tick(15000)
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(fetch.mock.calls.every(call => !call[1])).toBe(true)
})

it('does not let an older manual inspection release the newer request to background polling', async () => {
  const older = deferred(), latest = deferred()
  const fetch = vi.fn().mockResolvedValueOnce(response({...verified, pending: true}))
    .mockReturnValueOnce(older.promise).mockReturnValueOnce(latest.promise)
    .mockResolvedValue(response(verified))
  vi.stubGlobal('fetch', fetch)
  render(<PixelAccessCard />)
  await tick(0)
  await tick(5000)
  fireEvent.click(screen.getByRole('button', {name: 'Refresh status'}))
  expect(fetch).toHaveBeenCalledTimes(3)
  await act(async () => older.resolve({ok: false}))
  await tick(10000)
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(screen.queryByText(/status is unavailable/)).toBeNull()
  await act(async () => latest.resolve(response(verified)))
  expect(screen.getByRole('button', {name: 'Enable Full Access'})).toBeEnabled()
})

it('releases a failed read for the next pending poll and stops polling on unmount', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(response({...verified, pending: true}))
    .mockResolvedValueOnce({ok: false}).mockResolvedValue(response(verified))
  vi.stubGlobal('fetch', fetch)
  const view = render(<PixelAccessCard />)
  await tick(0)
  await tick(5000)
  expect(screen.getByText(/status is unavailable/)).toBeInTheDocument()
  await tick(5000)
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(screen.getByRole('button', {name: 'Enable Full Access'})).toBeEnabled()
  expect(screen.queryByText(/status is unavailable/)).toBeNull()
  view.unmount()
  await tick(30000)
  expect(fetch).toHaveBeenCalledTimes(3)
})
