import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import CalendarChanges from './CalendarChanges'
import { CHANGE, MOVE, REMOVE, UNREADABLE } from './connectedAppsFixtures'

const DISCORD = 'https://discord.gg/4ntNp9MAwC'
afterEach(() => vi.unstubAllGlobals())

// The dashboard API as the list reaches it: GET the changes, POST an action on one.
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

const LIST = 'GET /api/pixel/connections/google/changes'

it('shows each waiting change with everything it would write, and approves exactly the one shown', async () => {
  let listed = {proposals: [CHANGE, MOVE], more: 0}
  const calls = api({[LIST]: () => [200, listed],
    'POST /api/pixel/connections/google/changes/approve': () => { listed = {proposals: [MOVE], more: 0}; return [200, {proposalId: CHANGE.proposalId, outcome: 'applied'}] }})
  const changed = vi.fn()
  render(<CalendarChanges waiting={2} writable onChanged={changed} />)
  const dentist = await screen.findByRole('article', {name: 'Add “Dentist”'})
  expect(within(dentist).getByText('12 Main St')).toBeVisible()
  expect(within(dentist).getByText('pat@example.com')).toBeVisible()
  expect(within(dentist).getByText("Google emails the event's guests when you approve.")).toBeVisible()
  expect(screen.getByRole('article', {name: 'Change “Team sync”'})).toBeVisible()
  expect(screen.getByText(/Nothing changes on your calendar until you approve it/)).toBeVisible()
  expect(screen.queryByText(CHANGE.sha256)).toBeNull()

  fireEvent.click(within(dentist).getByRole('button', {name: 'Approve'}))
  expect(await screen.findByText('Done. Your calendar has the change.')).toBeVisible()
  expect(calls).toContainEqual(['POST /api/pixel/connections/google/changes/approve',
    {proposalId: CHANGE.proposalId, sha256: CHANGE.sha256}])
  await waitFor(() => expect(screen.queryByRole('article', {name: 'Add “Dentist”'})).toBeNull())
  expect(changed).toHaveBeenCalled()
})

it('declines a change, and only dismisses one Google may already have', async () => {
  let listed = {proposals: [REMOVE, UNREADABLE], more: 0}
  const calls = api({[LIST]: () => [200, listed],
    'POST /api/pixel/connections/google/changes/decline': () => { listed = {proposals: [UNREADABLE], more: 0}; return [200, {proposalId: REMOVE.proposalId, outcome: 'declined'}] }})
  render(<CalendarChanges waiting={2} writable onChanged={() => {}} />)
  const lunch = await screen.findByRole('article', {name: 'Delete “Lunch”'})
  expect(within(lunch).queryByRole('button', {name: 'Approve'})).toBeNull()
  expect(within(lunch).queryByRole('button', {name: 'Check again'})).toBeNull()
  expect(within(lunch).getByText(/Check your calendar to see whether it happened/)).toBeVisible()
  const unreadable = screen.getByRole('article', {name: 'A calendar change'})
  expect(within(unreadable).getByRole('button', {name: 'Decline'})).toBeVisible()
  fireEvent.click(within(lunch).getByRole('button', {name: 'Dismiss'}))
  expect(await screen.findByText('Dismissed.')).toBeVisible()
  expect(calls).toContainEqual(['POST /api/pixel/connections/google/changes/decline', {proposalId: REMOVE.proposalId}])
})

it('checks an unconfirmed new event again without sending it', async () => {
  const unconfirmed = {...CHANGE, state: 'unconfirmed'}
  const calls = api({[LIST]: [200, {proposals: [unconfirmed], more: 0}],
    'POST /api/pixel/connections/google/changes/check': [200, {proposalId: CHANGE.proposalId, outcome: 'unconfirmed'}]})
  render(<CalendarChanges waiting={1} writable onChanged={() => {}} />)
  fireEvent.click(await screen.findByRole('button', {name: 'Check again'}))
  expect(await screen.findByText(/hasn't confirmed the change yet. ODS won't send it again/)).toBeVisible()
  expect(calls.map(([key]) => key)).not.toContain('POST /api/pixel/connections/google/changes/approve')
})

it('shows a refusal with its fix, and the Discord link only for real failures', async () => {
  api({[LIST]: [200, {proposals: [CHANGE], more: 0}],
    'POST /api/pixel/connections/google/changes/approve': [409, {detail: {code: 'calendar-change-not-made',
      message: "The change wasn't made. Try again, or decline it and ask Portal to suggest it again.", help: DISCORD}}]})
  render(<CalendarChanges waiting={1} writable onChanged={() => {}} />)
  fireEvent.click(await screen.findByRole('button', {name: 'Approve'}))
  expect(await screen.findByRole('alert')).toHaveTextContent("The change wasn't made. Try again")
  expect(screen.getByRole('link', {name: /Discord/})).toHaveAttribute('href', DISCORD)
  expect(screen.getByRole('button', {name: 'Approve'})).toBeEnabled()
})

it('says how many more changes wait beyond those shown', async () => {
  api({[LIST]: [200, {proposals: [CHANGE], more: 3}]})
  render(<CalendarChanges waiting={4} writable onChanged={() => {}} />)
  expect(await screen.findByText(/3 more changes wait. They appear here once you've handled these./)).toBeVisible()
})

it('lists nothing and asks nothing while no change waits', () => {
  const calls = api({})
  const {container, rerender} = render(<CalendarChanges waiting={0} writable onChanged={() => {}} />)
  expect(screen.getByText(/Calendar changes Portal suggests wait here until you approve them/)).toBeVisible()
  rerender(<CalendarChanges waiting={0} writable={false} onChanged={() => {}} />)
  expect(container).toBeEmptyDOMElement()
  expect(calls).toEqual([])
})
