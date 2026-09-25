import {act, cleanup, fireEvent, screen} from '@testing-library/react'
import {render} from '../test/test-utils'
import Usage from './Usage'

const reports = () => fetch.mock.calls.map(([url]) => url).filter(url => url.startsWith('/api/usage/report?'))
const ranges = () => reports().map(url => new URLSearchParams(url.split('?')[1]).get('start'))
const ready = () => expect(screen.getByText('Recorded activity · refreshes every 10s')).toBeVisible()
const click = async name => {await act(async () => fireEvent.click(screen.getByRole('button',{name})))}

beforeEach(() => {
  vi.useFakeTimers({toFake:['Date','setTimeout','clearTimeout','setInterval','clearInterval']})
  vi.setSystemTime(new Date('2026-09-14T12:00:00Z'))
  localStorage.clear()
  vi.spyOn(document,'hidden','get').mockReturnValue(false)
  vi.stubGlobal('fetch',vi.fn(async url => {
    if(url === '/api/usage/readiness')return {ok:true,json:async () => ({status:'ready',actions:{}})}
    const params = new URLSearchParams(url.split('?')[1])
    return {ok:true,json:async () => ({period:{start:params.get('start'),end:params.get('end')},
      source:{status:'ok'},summary:{total_tokens:params.get('start') === '2026-09-01' ? 123 : 456},
      daily:[],models:[],services:[],
    })}
  }))
})
afterEach(() => {cleanup();vi.restoreAllMocks();vi.unstubAllGlobals();vi.useRealTimers()})

test('initial load, timed poll and explicit refresh query only the displayed month', async () => {
  await act(async () => render(<Usage/>))
  ready();expect(screen.getByText('123')).toBeVisible()
  expect(ranges()).toEqual(['2026-09-01'])
  await act(async () => {await vi.advanceTimersByTimeAsync(10000)})
  ready()
  await click('Refresh usage')
  ready()
  expect(ranges()).toEqual(['2026-09-01','2026-09-01','2026-09-01'])
})

test('month navigation fetches each selected period and retains its recorded history', async () => {
  await act(async () => render(<Usage/>))
  await click('Previous month')
  ready();expect(screen.getByText('August 2026')).toBeVisible();expect(screen.getByText('456')).toBeVisible()
  await click('Next month')
  ready();expect(screen.getByText('September 2026')).toBeVisible();expect(screen.getByText('123')).toBeVisible()
  expect(ranges()).toEqual(['2026-09-01','2026-08-01','2026-09-01'])
  const periods = JSON.parse(localStorage.getItem('ods-usage-summary-history-v1')).map(item => item.period)
  expect(new Set(periods)).toEqual(new Set(['2026-09-01:2026-09-30','2026-08-01:2026-08-31']))
})

test('one report per visible poll, one on return, and none after unmount', async () => {
  let mounted
  await act(async () => {mounted=render(<Usage/>)})
  await act(async () => {await vi.advanceTimersByTimeAsync(60000)})
  ready()
  expect(reports()).toHaveLength(7)
  expect(fetch.mock.calls.filter(([url]) => url === '/api/usage/readiness')).toHaveLength(7)
  vi.spyOn(document,'hidden','get').mockReturnValue(true)
  await act(async () => {await vi.advanceTimersByTimeAsync(20000)})
  expect(reports()).toHaveLength(7)
  vi.spyOn(document,'hidden','get').mockReturnValue(false)
  await act(async () => document.dispatchEvent(new Event('visibilitychange')))
  ready();expect(reports()).toHaveLength(8)
  mounted.unmount()
  await act(async () => {await vi.advanceTimersByTimeAsync(30000)})
  expect(reports()).toHaveLength(8)
  expect(vi.getTimerCount()).toBe(0)
})
