import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import ConnectedAppsNotice, { connectedAppsAttention } from './ConnectedAppsNotice'

afterEach(() => vi.unstubAllGlobals())

it.each([
  [{state: 'needs-reconnect', reason: 'invalid-grant', step: null}, 'Google needs reconnecting.'],
  [{state: 'missing-permission', reason: 'missing-scope', step: null}, 'Google is missing a permission Portal needs.'],
  [{state: 'setup-broken', reason: 'api-disabled', step: null}, "Google's setup needs a fix."],
  [{state: 'blocked-by-admin', reason: 'blocked-by-admin', step: null}, 'Your administrator blocks Google for Portal.'],
  [{state: 'unknown-error', reason: 'unknown', step: null}, 'Google sync has a problem.'],
  [{state: 'behind', reason: 'sync-stopped', step: null}, 'Google sync stopped.'],
  [{state: 'connected', reason: null, step: 'update-stopped'}, "Portal hasn't picked up your Google change yet."],
  [{state: 'connected', reason: null, step: 'changes-waiting', waiting: 1}, 'A calendar change from Portal waits for your approval.'],
  [{state: 'connected', reason: null, step: 'changes-waiting', waiting: 3}, '3 calendar changes from Portal wait for your approval.'],
  [{state: 'connected', reason: null, step: 'changes-waiting', waiting: 0, drafts: 1}, 'A Gmail draft from Portal waits for your approval.'],
  [{state: 'connected', reason: null, step: 'changes-waiting', waiting: 0, drafts: 2}, '2 Gmail drafts from Portal wait for your approval.'],
  [{state: 'connected', reason: null, step: 'changes-waiting', waiting: 1, drafts: 1},
    'Calendar changes and Gmail drafts from Portal wait for your approval.'],
])('says in one line what needs the owner: %j', (value, line) => {
  expect(connectedAppsAttention(value)).toBe(line)
})

it.each([null, undefined, 'needs-reconnect', [], {}, {state: 'connected', reason: null, step: null}, {state: 'made-up'}])(
  'says nothing for %j', value => {
    expect(connectedAppsAttention(value)).toBeNull()
  })

it('links to Connected apps and asks nothing of its own', () => {
  const fetch = vi.fn()
  vi.stubGlobal('fetch', fetch)
  render(<MemoryRouter><ConnectedAppsNotice value={{state: 'needs-reconnect', reason: 'sign-in-missing', step: null}} /></MemoryRouter>)
  expect(screen.getByRole('status', {name: 'Connected apps'})).toHaveTextContent('Google needs reconnecting. Open Connected apps')
  expect(screen.getByRole('link', {name: 'Open Connected apps'})).toHaveAttribute('href', '/settings?section=apps')
  expect(fetch).not.toHaveBeenCalled()
})
