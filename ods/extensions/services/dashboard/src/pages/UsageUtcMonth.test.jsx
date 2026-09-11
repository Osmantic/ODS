import {cleanup, render, screen, fireEvent, act} from '@testing-library/react'
import Usage from './Usage'

beforeEach(() => {
  vi.useFakeTimers({toFake:['Date']})
  vi.stubGlobal('fetch', vi.fn(async url => ({
    ok:true, json:async () => String(url).includes('/readiness')
      ? {actions:{}}
      : {source:{status:'ok'}, summary:{}, daily:[], models:[], services:[]},
  })))
})
afterEach(() => {cleanup(); vi.useRealTimers(); vi.unstubAllGlobals()})

it.each([
  ['2026-08-31T23:30:00Z', '2026-08-01', '2026-08-31', 'August 2026'],
  ['2026-09-01T00:30:00Z', '2026-09-01', '2026-09-30', 'September 2026'],
  ['2026-12-31T23:30:00Z', '2026-12-01', '2026-12-31', 'December 2026'],
  ['2027-01-01T00:30:00Z', '2027-01-01', '2027-01-31', 'January 2027'],
])('requests and labels the current UTC month at %s', async (instant, start, end, label) => {
  vi.setSystemTime(new Date(instant))
  await act(async () => {render(<Usage/>)})
  expect(fetch).toHaveBeenCalledWith(`/api/usage/report?start=${start}&end=${end}`, expect.any(Object))
  expect(screen.getByText(label)).toBeVisible()
})

it('navigates the leap-year boundary using UTC months', async () => {
  vi.setSystemTime(new Date('2024-03-01T00:30:00Z'))
  await act(async () => {render(<Usage/>)})
  await act(async () => {fireEvent.click(screen.getByRole('button', {name:'Previous month'}))})
  expect(fetch).toHaveBeenLastCalledWith('/api/usage/readiness', expect.any(Object))
  expect(fetch).toHaveBeenCalledWith('/api/usage/report?start=2024-02-01&end=2024-02-29', expect.any(Object))
  expect(screen.getByText('February 2024')).toBeVisible()
})
