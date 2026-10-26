import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import CalendarActivity from './CalendarActivity'
import { activityLine, undoLine, undoNotice } from './calendarChangeView'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

const ADDED = {proposalId: 'calendar-1761500000000-0badc0de', action: 'create', title: 'Focus time',
  start: '2026-10-27T15:00:00-05:00', end: '2026-10-27T16:00:00-05:00', allDay: false, previous: null,
  appliedAt: '2026-10-26T15:00:01Z', undo: 'available'}
const MOVED = {...ADDED, proposalId: 'calendar-1761500000001-0badc0de', action: 'update', title: 'Gym',
  start: '2026-10-28', end: '2026-10-29', allDay: true, previous: {start: '2026-10-27', end: '2026-10-28', allDay: true},
  undo: 'changed'}

function api(routes) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const key = `${options.method ?? 'GET'} ${url}`
    calls.push([key, options.body === undefined ? undefined : JSON.parse(options.body)])
    const route = routes[key]
    if (!route) throw new Error(`unexpected request ${key}`)
    const [status, body] = typeof route === 'function' ? route() : route
    return {ok: status >= 200 && status < 300, status, json: async () => body}
  }))
  return calls
}

const LIST = 'GET /api/pixel/connections/google/activity'

it('says what Portal changed by itself and undoes one with a click', async () => {
  let listed = {changes: [ADDED, MOVED], more: 0}
  const calls = api({[LIST]: () => [200, listed],
    'POST /api/pixel/connections/google/activity/undo': () => {
      listed = {changes: [{...ADDED, undo: 'done'}, MOVED], more: 0}
      return [200, {proposalId: ADDED.proposalId, outcome: 'applied'}]
    }})
  const changed = vi.fn()
  render(<CalendarActivity onChanged={changed} />)
  const added = (await screen.findByText(/^Added “Focus time”/)).closest('li')
  const moved = screen.getByText(/^Moved “Gym” to All day/).closest('li')
  expect(within(moved).getByText(/\(it was All day/)).toBeVisible()
  expect(within(moved).queryByRole('button', {name: 'Undo'})).toBeNull()
  expect(within(moved).getByText("The event changed since, so ODS won't undo this.")).toBeVisible()
  fireEvent.click(within(added).getByRole('button', {name: 'Undo'}))
  expect(await screen.findByText('Removed “Focus time” from your calendar.')).toBeVisible()
  expect(calls).toContainEqual(['POST /api/pixel/connections/google/activity/undo', {proposalId: ADDED.proposalId}])
  await waitFor(() => expect(within(screen.getByText(/^Added “Focus time”/).closest('li')).getByText('Undone.')).toBeVisible())
  expect(changed).toHaveBeenCalled()
})

it('shows a refusal with its fix, and Discord only for real failures', async () => {
  api({[LIST]: [200, {changes: [ADDED], more: 0}],
    'POST /api/pixel/connections/google/activity/undo': [409, {detail: {code: 'calendar-undo-unavailable',
      message: "This change can't be undone any more: the event changed or is gone. Check your calendar.", help: null}}]})
  render(<CalendarActivity onChanged={() => {}} />)
  fireEvent.click(await screen.findByRole('button', {name: 'Undo'}))
  expect(await screen.findByRole('alert')).toHaveTextContent("This change can't be undone any more")
  expect(screen.queryByRole('link', {name: /Discord/})).toBeNull()
})

it('asks again shortly while a change waits for the next calendar sync', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true})
  let listed = {changes: [{...ADDED, undo: 'syncing'}], more: 0}
  const calls = api({[LIST]: () => [200, listed]})
  render(<CalendarActivity onChanged={() => {}} />)
  expect(await screen.findByText(/Undo appears once ODS has synced your calendar/)).toBeVisible()
  listed = {changes: [ADDED], more: 0}
  await vi.advanceTimersByTimeAsync(30000)
  expect(await screen.findByRole('button', {name: 'Undo'})).toBeVisible()
  expect(calls.filter(([key]) => key === LIST)).toHaveLength(2)
})

it('says plainly when there is nothing yet, and how many older changes are not shown', async () => {
  api({[LIST]: [200, {changes: [], more: 0}]})
  const {unmount} = render(<CalendarActivity onChanged={() => {}} />)
  expect(await screen.findByText(/Nothing this week. Private events Portal adds or moves appear here, with Undo./)).toBeVisible()
  unmount()
  api({[LIST]: [200, {changes: [ADDED], more: 3}]})
  render(<CalendarActivity onChanged={() => {}} />)
  expect(await screen.findByText('3 older changes this week aren’t shown.')).toBeVisible()
})

it('words every undo state, and never claims an undo Google did not confirm', () => {
  expect(activityLine(ADDED)).toMatch(/^Added “Focus time”, /)
  expect(activityLine({...MOVED, title: null, previous: null})).toMatch(/^Moved an event to /)
  expect(undoLine(ADDED)).toBeNull()
  for (const state of ['syncing', 'done', 'refused', 'unconfirmed', 'changed', 'gone']) {
    expect(undoLine({...ADDED, undo: state})).toMatch(/\.$/)
  }
  expect(undoNotice('applied', MOVED)).toBe('Moved “Gym” back.')
  expect(undoNotice('unconfirmed', ADDED)).toMatch(/hasn't confirmed the undo yet/)
  expect(undoNotice('rejected', ADDED)).toMatch(/refused the undo/)
})
