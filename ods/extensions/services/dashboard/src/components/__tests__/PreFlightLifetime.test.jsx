import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PreFlightChecks } from '../PreFlightChecks' // eslint-disable-line no-unused-vars

const ports = '/api/preflight/required-ports'
const docker = '/api/preflight/docker'
const payload = url => ({
  [ports]: { ports: [{ port: 3001, service: 'dashboard' }] },
  [docker]: { available: true, version: 'fixture' },
  '/api/preflight/gpu': { available: true, name: 'fixture', vram: 8 },
  '/api/preflight/ports': { conflicts: [] },
  '/api/preflight/disk': { free: 100e9 },
}[url])
const response = url => ({ ok: true, json: async () => payload(url) })
const advance = ms => act(async () => { await vi.advanceTimersByTimeAsync(ms) })

beforeEach(() => vi.useFakeTimers())
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals() })

test.each([[ports, 'headers'], [docker, 'headers'], [docker, 'body']])(
  'a stalled %s %s releases Retry without accepting late results',
  async (target, phase) => {
    let finish
    const blocked = new Promise(resolve => { finish = resolve })
    let waiting = true
    let stalledSignal
    vi.stubGlobal('fetch', vi.fn((url, options) => {
      if (waiting && url === target) {
        waiting = false
        stalledSignal = options?.signal
        return phase === 'headers' ? blocked : Promise.resolve({ ok: true, json: () => blocked })
      }
      return Promise.resolve(response(url))
    }))
    const onComplete = vi.fn(), onIssuesFound = vi.fn()
    render(<PreFlightChecks onComplete={onComplete} onIssuesFound={onIssuesFound}/>)
    await advance(30000)
    expect(screen.getByText('System checks timed out')).toBeInTheDocument()
    expect(stalledSignal?.aborted).toBe(true)
    expect(onComplete).not.toHaveBeenCalled()
    expect(onIssuesFound).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', {name:'Retry Checks'}))
    await advance(3000)
    expect(screen.getByText('System checks complete')).toBeInTheDocument()
    expect(screen.getByText('1 ports available')).toBeInTheDocument()
    expect(onComplete).toHaveBeenCalledTimes(1)
    const calls = fetch.mock.calls.length
    await act(async () => { finish(phase === 'headers' ? response(target) : payload(target)) })
    await advance(31000)
    expect(fetch).toHaveBeenCalledTimes(calls)
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('System checks timed out')).not.toBeInTheDocument()
    expect(fetch.mock.calls.filter(([url]) => url === ports)).toHaveLength(2)
  },
)

test('leaving setup aborts the active check and suppresses later work', async () => {
  let finish, activeSignal
  vi.stubGlobal('fetch', vi.fn((url, options) => {
    activeSignal = options?.signal
    return new Promise(resolve => { finish = resolve })
  }))
  const onComplete = vi.fn(), onIssuesFound = vi.fn()
  const { unmount } = render(<PreFlightChecks onComplete={onComplete} onIssuesFound={onIssuesFound}/>)
  await advance(0)
  unmount()
  expect(activeSignal?.aborted).toBe(true)
  await act(async () => finish(response(ports)))
  await advance(35000)
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(onComplete).not.toHaveBeenCalled()
  expect(onIssuesFound).not.toHaveBeenCalled()
})

test('normal readiness completes once and clears its deadline', async () => {
  vi.stubGlobal('fetch', vi.fn(url => Promise.resolve(response(url))))
  const onComplete = vi.fn(), onIssuesFound = vi.fn()
  render(<PreFlightChecks onComplete={onComplete} onIssuesFound={onIssuesFound}/>)
  await advance(3000)
  expect(onComplete).toHaveBeenCalledTimes(1)
  expect(onIssuesFound).not.toHaveBeenCalled()
  await advance(30000)
  expect(onComplete).toHaveBeenCalledTimes(1)
  expect(screen.queryByText('System checks timed out')).not.toBeInTheDocument()
})
