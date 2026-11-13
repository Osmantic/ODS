import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import EmailDrafts from './EmailDrafts'
import { DRAFT, REPLY, UNREADABLE_DRAFT } from './connectedAppsFixtures'

const DISCORD = 'https://discord.gg/4ntNp9MAwC'
afterEach(() => vi.unstubAllGlobals())

// The dashboard API as the list reaches it: GET the drafts, POST an action on one.
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

const LIST = 'GET /api/pixel/connections/google/drafts'

it('shows each waiting draft whole, and creates exactly the one shown', async () => {
  let listed = {drafts: [DRAFT], more: 0}
  const calls = api({[LIST]: () => [200, listed],
    'POST /api/pixel/connections/google/drafts/approve': () => { listed = {drafts: [], more: 0}; return [200, {proposalId: DRAFT.proposalId, outcome: 'applied'}] }})
  const changed = vi.fn()
  render(<EmailDrafts waiting={1} writable onChanged={changed} />)
  const draft = await screen.findByRole('article', {name: 'Draft “Thursday plan”'})
  expect(within(draft).getByText('pat@example.com')).toBeVisible()
  expect(within(draft).getByText('sam@example.com')).toBeVisible()
  expect(within(draft).getByLabelText('Message')).toHaveTextContent('Thursday at 3 works for me.')
  expect(screen.getByText(/Nothing reaches Gmail until you approve it/)).toBeVisible()
  expect(screen.queryByText(DRAFT.sha256)).toBeNull()
  expect(screen.queryByRole('button', {name: /Send/})).toBeNull()

  fireEvent.click(within(draft).getByRole('button', {name: 'Create draft'}))
  expect(await screen.findByText(/The draft is in your Gmail Drafts. Open Gmail to read it over and send it./)).toBeVisible()
  expect(calls).toContainEqual(['POST /api/pixel/connections/google/drafts/approve',
    {proposalId: DRAFT.proposalId, sha256: DRAFT.sha256}])
  await waitFor(() => expect(screen.queryByRole('article', {name: 'Draft “Thursday plan”'})).toBeNull())
  expect(changed).toHaveBeenCalled()
})

it('names the message a reply answers, and checks an unconfirmed draft again without creating it twice', async () => {
  const calls = api({[LIST]: [200, {drafts: [REPLY], more: 0}],
    'POST /api/pixel/connections/google/drafts/check': [200, {proposalId: REPLY.proposalId, outcome: 'unconfirmed'}]})
  render(<EmailDrafts waiting={1} writable onChanged={() => {}} />)
  const reply = await screen.findByRole('article', {name: 'Draft “Re: Lunch”'})
  expect(within(reply).getByText('“Lunch” from Pat <pat@example.com>')).toBeVisible()
  expect(within(reply).getByText(/Gmail hasn't confirmed this draft, so ODS won't create it again/)).toBeVisible()
  expect(within(reply).queryByRole('button', {name: 'Create draft'})).toBeNull()
  fireEvent.click(within(reply).getByRole('button', {name: 'Check again'}))
  expect(await screen.findByText(/check your Drafts folder in a minute/)).toBeVisible()
  expect(calls.map(([key]) => key)).not.toContain('POST /api/pixel/connections/google/drafts/approve')
})

it('declines a draft, and an unreadable one can only be declined', async () => {
  let listed = {drafts: [DRAFT, UNREADABLE_DRAFT], more: 0}
  const calls = api({[LIST]: () => [200, listed],
    'POST /api/pixel/connections/google/drafts/decline': () => { listed = {drafts: [UNREADABLE_DRAFT], more: 0}; return [200, {proposalId: DRAFT.proposalId, outcome: 'declined'}] }})
  render(<EmailDrafts waiting={2} writable onChanged={() => {}} />)
  const unreadable = await screen.findByRole('article', {name: 'A Gmail draft'})
  expect(within(unreadable).queryByRole('button', {name: 'Create draft'})).toBeNull()
  expect(within(unreadable).getByText(/ODS can't read this draft, so it can't be created/)).toBeVisible()
  const draft = screen.getByRole('article', {name: 'Draft “Thursday plan”'})
  fireEvent.click(within(draft).getByRole('button', {name: 'Decline'}))
  expect(await screen.findByText('Declined. Nothing was written to Gmail.')).toBeVisible()
  expect(calls).toContainEqual(['POST /api/pixel/connections/google/drafts/decline', {proposalId: DRAFT.proposalId}])
})

it('shows a refusal with its fix, and the Discord link only for real failures', async () => {
  api({[LIST]: [200, {drafts: [DRAFT], more: 0}],
    'POST /api/pixel/connections/google/drafts/approve': [409, {detail: {code: 'gmail-draft-not-made',
      message: "The draft wasn't created. Try again, or decline it and ask Portal to write it again.", help: DISCORD}}]})
  render(<EmailDrafts waiting={1} writable onChanged={() => {}} />)
  fireEvent.click(await screen.findByRole('button', {name: 'Create draft'}))
  expect(await screen.findByRole('alert')).toHaveTextContent("The draft wasn't created. Try again")
  expect(screen.getByRole('link', {name: /Discord/})).toHaveAttribute('href', DISCORD)
})

it('says where drafts wait while there are none, and asks for nothing while drafts are off', () => {
  const calls = api({})
  const {rerender} = render(<EmailDrafts waiting={0} writable onChanged={() => {}} />)
  expect(screen.getByText(/Gmail drafts Portal writes wait here until you approve them. Portal never sends email./)).toBeVisible()
  rerender(<EmailDrafts waiting={0} writable={false} onChanged={() => {}} />)
  expect(screen.queryByText(/Gmail drafts Portal writes/)).toBeNull()
  expect(calls).toEqual([])
})
