import {render,screen,fireEvent,cleanup,waitFor,act} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import DashboardTokens, {tokenLineSegments, visibleMinuteSamples} from './DashboardTokens'

const withTimeline = report => ({...report,source:{status:'ok'},points:report.daily})

afterEach(() => {cleanup();vi.useRealTimers();vi.unstubAllGlobals()})
it('plots real daily fields and changes the requested period without writes', async () => {
  const fetch = vi.fn(async url => ({ok:true,json:async () => String(url).includes('readiness') ? {available:true} : withTimeline({summary:{total_tokens:150,input_tokens:100,output_tokens:50},daily:[{date:'2026-09-01',input_tokens:100,output_tokens:50}]})}))
  vi.stubGlobal('fetch',fetch)
  render(<DashboardTokens/>)
  expect(await screen.findByRole('img',{name:'Recorded tokens today (UTC)'})).toBeVisible()
  expect(screen.getByRole('button',{name:'1 day'})).toHaveAttribute('aria-pressed','true')
  expect(screen.getByText('2026-09-01: 150 tokens')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button',{name:'7 days'}))
  expect(await screen.findByRole('img',{name:'Daily recorded tokens for the last 7 days'})).toBeVisible()
  await waitFor(() => expect(fetch.mock.calls.filter(([url]) => url.includes('/report?'))).toHaveLength(2))
  fireEvent.click(screen.getByRole('button',{name:'30 days'}))
  expect(await screen.findByRole('img',{name:'Daily recorded tokens for the last 30 days'})).toBeVisible()
  const urls = fetch.mock.calls.filter(([url]) => url.includes('/report?')).map(([url]) => new URL(url,'http://localhost'))
  expect(urls[0].searchParams.get('start')).toBe(urls[0].searchParams.get('end'))
  expect(urls.map(url => (Date.parse(url.searchParams.get('end')) - Date.parse(url.searchParams.get('start'))) / 86400000 + 1)).toEqual([1,7,30])
  expect(fetch.mock.calls.every(([,options]) => !options.method)).toBe(true)
})
it('shows unavailable telemetry rather than a fabricated chart or zero total', async () => {
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:false})))
  render(<DashboardTokens/>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Token telemetry unavailable')
  expect(screen.queryByRole('img')).toBeNull()
})
it('does not turn absent daily fields into zero samples', async () => {
  vi.stubGlobal('fetch',vi.fn(async () => ({ok:true,json:async () => ({summary:{},daily:[{date:'2026-09-01'}]})})))
  render(<DashboardTokens/>)
  await waitFor(() => expect(screen.getByText('No daily token samples for this period.')).toBeVisible())
  expect(screen.queryByRole('img')).toBeNull()
})
it('does not display backend fallback zeros as recorded usage when its source is unavailable', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok:true, json:async () => ({source:{status:'unavailable'},summary:{total_tokens:0,requests:0},daily:[{date:'2026-09-08',total_tokens:0}]})})))
  render(<DashboardTokens/>)
  expect(await screen.findByRole('alert')).toHaveTextContent('Token telemetry unavailable')
  expect(screen.queryByRole('img')).toBeNull()
  expect(screen.queryByText('Total tokens')).toBeNull()
})
it('refreshes recorded tokens automatically without clearing the chart while loading', async () => {
  vi.useFakeTimers()
  let requests = 0
  const fetch = vi.fn(async url => ({ok:true,json:async () => String(url).includes('readiness') ? {available:true} : withTimeline({summary:{total_tokens:url.includes('/report?') ? ++requests : requests},daily:[{date:'2026-09-08',total_tokens:requests}]})}))
  vi.stubGlobal('fetch', fetch)
  await act(async () => {render(<DashboardTokens/>)})
  expect(screen.getByRole('img')).toBeVisible()
  await act(async () => {await vi.advanceTimersByTimeAsync(5000)})
  expect(requests).toBe(2)
  expect(screen.getByRole('img')).toBeVisible()
  expect(screen.queryByText('Loading token usage…')).toBeNull()
})

it('draws separate neutral input/output and request lines from recorded values', async () => {
  vi.stubGlobal('fetch',vi.fn(async url => ({ok:true,json:async () => String(url).includes('readiness') ? {available:true} : withTimeline({summary:{total_tokens:150,input_tokens:100,output_tokens:50,requests:2},daily:[{date:'2026-09-08',input_tokens:100,output_tokens:50,requests:2}]})})))
  const {container} = render(<DashboardTokens/>)
  expect(await screen.findByRole('img',{name:'Recorded requests today (UTC)'})).toBeVisible()
  expect(container.querySelectorAll('polyline')).toHaveLength(3)
  expect(container.querySelector('.token-trend linearGradient')).toBeNull()
  const sample = screen.getByRole('button',{name:'2026-09-08: Input 100, Output 50'})
  fireEvent.focus(sample)
  expect(screen.getByText('2026-09-08 · Input 100 · Output 50')).toBeVisible()
})

it('breaks lines across missing days and absent readings, while retaining real zeros', () => {
  const rows = [{date:'2026-09-01',input:0},{date:'2026-09-02',input:10},{date:'2026-09-04',input:20},{date:'2026-09-05'},{date:'2026-09-06',input:0}]
  const segments = tokenLineSegments(rows,'input',20)
  expect(segments.map(segment => segment.map(point => point.index))).toEqual([[0,1],[2],[4]])
  expect(segments[0][0].y).toBe(128)
  expect(segments[1][0].y).toBe(28)
  expect(tokenLineSegments([{date:'2026-09-01',input:20}], 'input',20)[0][0].x).toBe(180)
})

it('uses minute samples for 1d instead of plotting its single daily total', async () => {
  const points = [0,1,2,3].map((minute) => ({date:`2026-09-08T10:0${minute}:00Z`,input_tokens:minute === 1 ? 100 : 0,output_tokens:minute === 2 ? 20 : 0,requests:minute === 1 || minute === 2 ? 1 : 0}))
  vi.stubGlobal('fetch',vi.fn(async url => ({ok:true,json:async () => url.includes('/timeline') ? {source:{status:'ok'},points} : url.includes('readiness') ? {available:true} : {summary:{total_tokens:120},daily:[{date:'2026-09-08',total_tokens:120}]}})))
  const {container} = render(<DashboardTokens/>)
  await screen.findByRole('img',{name:'Recorded tokens today (UTC)'})
  expect(container.querySelector('[data-token-series=input_tokens] polyline').getAttribute('points').split(' ')).toHaveLength(4)
  expect(container.querySelectorAll('.token-chart-dates span')).toHaveLength(4)
  expect(container.querySelector('.token-chart-dates')).toHaveTextContent('10:00 UTC10:03 UTC')
  expect(screen.getByText('Updates every 5s · tokens and requests per minute')).toBeVisible()
})

it('keeps an honest empty/error state when minute telemetry is unavailable', async () => {
  vi.stubGlobal('fetch',vi.fn(async url => ({ok:!url.includes('timeline'),json:async () => ({source:{status:'ok'},summary:{total_tokens:120},daily:[{date:'2026-09-08',total_tokens:120}]})})))
  render(<DashboardTokens/>)
  expect(await screen.findByText('Minute-level history unavailable. Daily totals remain above.')).toBeVisible()
  expect(screen.queryByRole('img')).toBeNull()
})

it('frames the latest 15 minute buckets and preserves the full day on request', () => {
  const points = Array.from({length:60},(_,index)=>({date:`2026-09-08T10:${String(index).padStart(2,'0')}:00Z`,requests:index===10?100:1}))
  expect(visibleMinuteSamples(points)).toEqual(points.slice(45))
  expect(visibleMinuteSamples(points,'day')).toEqual(points)
  expect(visibleMinuteSamples(points,15,'2026-09-08T12:00:00Z')).toEqual([])
})

it('rescales only the visible interval and selected series without changing daily totals',async()=>{
  const points = Array.from({length:30},(_,index)=>({date:`2026-09-08T10:${String(index).padStart(2,'0')}:00Z`,input_tokens:index===0?1000000:100,output_tokens:10,requests:index===0?68:2}))
  vi.stubGlobal('fetch',vi.fn(async url=>({ok:true,json:async()=>url.includes('timeline')?{source:{status:'ok'},points}:url.includes('readiness')?{available:true}:{summary:{total_tokens:1003200},daily:[]}})))
  const {container}=render(<DashboardTokens/>)
  await screen.findByRole('img',{name:'Recorded tokens today (UTC)'})
  const ceiling=()=>container.querySelector('.token-trend .token-chart-scale').textContent
  expect(screen.getByRole('button',{name:'Last 15 minutes'})).toHaveAttribute('aria-pressed','true')
  expect(ceiling()).toBe('100 auto')
  fireEvent.click(screen.getByRole('button',{name:'Full day',exact:true}))
  expect(ceiling()).toBe('1M auto')
  fireEvent.click(screen.getByRole('button',{name:'Last 15 minutes'}))
  fireEvent.click(screen.getByRole('button',{name:'Output tokens only'}))
  expect(ceiling()).toBe('10 auto')
  expect(container.querySelector('[data-token-series=input_tokens]')).toBeNull()
  expect(container.querySelector('[data-token-series=output_tokens]')).toBeInTheDocument()
  expect(container.querySelector('.dashboard-token-total strong')).toHaveTextContent((1003200).toLocaleString())
  fireEvent.click(screen.getByRole('button',{name:'All token series'}))
  expect(ceiling()).toBe('100 auto')
})
