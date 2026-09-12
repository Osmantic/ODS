import {act, cleanup, screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import Usage from './Usage'

const report = count => ({
  period:{start:'2026-09-01',end:'2026-09-30'},
  source:{status:'ok'},summary:{total_tokens:count},daily:[],models:[],services:[],
})
const response = data => ({ok:true,json:async () => data})
const settle = async () => {await act(async () => {})}

beforeEach(() => {
  vi.useFakeTimers({toFake:['Date','setTimeout','clearTimeout','setInterval','clearInterval']})
  vi.setSystemTime(new Date('2026-09-12T12:00:00Z'))
  localStorage.clear()
})
afterEach(() => {cleanup();vi.useRealTimers();vi.unstubAllGlobals()})

it.each(['report headers','report body','readiness body'])(
  'recovers after stalled %s without accepting its late result',
  async phase => {
    let resolveOld
    const pending = new Promise(resolve => {resolveOld = resolve})
    const signals = []
    let calls = 0
    vi.stubGlobal('fetch', vi.fn((url, options) => {
      const call = calls++
      signals.push(options.signal)
      const readiness = String(url).includes('/readiness')
      if (call < 3) {
        if (phase === 'report headers' && call === 0) return pending
        if ((phase === 'report body' && call === 0) || (phase === 'readiness body' && readiness)) {
          return Promise.resolve({ok:true,json:() => pending})
        }
      }
      return Promise.resolve(response(readiness ? {status:'ready'} : report(call < 3 ? 900 : 123)))
    }))
    const mounted = render(<Usage/>)
    await settle()
    expect(screen.getByRole('button',{name:'Refresh usage'})).toBeDisabled()
    await act(async () => {await vi.advanceTimersByTimeAsync(15000)})
    expect(screen.getByRole('alert')).toHaveTextContent('Could not load usage')
    expect(screen.getByRole('button',{name:'Refresh usage'})).toBeEnabled()
    expect(signals.slice(0,3).every(signal => signal.aborted)).toBe(true)
    await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
    expect(screen.getByText('Recorded activity · refreshes every 10s')).toBeVisible()
    expect(screen.getByText('123')).toBeVisible()
    expect(calls).toBe(6)
    await act(async () => {
      resolveOld(phase === 'report headers' ? response(report(900)) : phase === 'readiness body' ? {status:'ready'} : report(900))
    })
    expect(screen.getByText('123')).toBeVisible()
    expect(screen.queryByText('900')).not.toBeInTheDocument()
    mounted.unmount()
    await act(async () => {await vi.advanceTimersByTimeAsync(60000)})
    expect(calls).toBe(6)
    expect(vi.getTimerCount()).toBe(0)
  },
)

it('aborts an unfinished poll and removes its deadline when unmounted', async () => {
  const signals = []
  vi.stubGlobal('fetch', vi.fn((_url, options) => {
    signals.push(options.signal)
    return new Promise(() => {})
  }))
  const mounted = render(<Usage/>)
  await settle()
  mounted.unmount()
  await act(async () => {await vi.advanceTimersByTimeAsync(0)})
  expect(signals).toHaveLength(3)
  expect(signals.every(signal => signal.aborted)).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})
