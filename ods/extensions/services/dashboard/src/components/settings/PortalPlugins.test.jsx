import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import PortalPlugins from './PortalPlugins'
import { statusFor } from './connectedAppsFixtures'

const DISCORD = 'https://discord.gg/4ntNp9MAwC'
afterEach(() => vi.unstubAllGlobals())

function api(reply) {
  const calls = []
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    calls.push([`${options.method ?? 'GET'} ${url}`, options.body === undefined ? undefined : JSON.parse(options.body)])
    const [status, body] = reply
    return {ok: status >= 200 && status < 300, status, json: async () => body}
  }))
  return calls
}

it('describes each reviewed plugin and turns it on exactly', async () => {
  const calls = api([200, {outcome: 'applied'}])
  const changed = vi.fn()
  render(<PortalPlugins status={statusFor()} onChanged={changed} />)
  const wiki = screen.getByRole('article', {name: 'Knowledge wiki'})
  expect(within(wiki).getByText('Off')).toBeVisible()
  expect(within(wiki).getByText(/reach the internet or run programs/)).toBeVisible()
  expect(screen.getByText(/Turning one on or off restarts Portal/)).toBeVisible()
  fireEvent.click(within(wiki).getByRole('button', {name: 'Turn on'}))
  expect(await screen.findByText('Knowledge wiki is on. Portal can use it now.')).toBeVisible()
  expect(calls).toEqual([['POST /api/pixel/connections/plugin', {plugin: 'memory-wiki', enabled: true}]])
  expect(changed).toHaveBeenCalled()
})

it('turns a plugin that is on off', async () => {
  const calls = api([200, {outcome: 'applied'}])
  render(<PortalPlugins status={statusFor({}, {plugins: {'memory-wiki': true}})} onChanged={() => {}} />)
  const wiki = screen.getByRole('article', {name: 'Knowledge wiki'})
  expect(within(wiki).getByText('On')).toBeVisible()
  fireEvent.click(within(wiki).getByRole('button', {name: 'Turn off'}))
  expect(await screen.findByText('Knowledge wiki is off. Portal no longer uses it.')).toBeVisible()
  expect(calls[0][1]).toEqual({plugin: 'memory-wiki', enabled: false})
})

it('shows why a change did not happen, with the Discord link only for real failures', async () => {
  api([409, {detail: {code: 'runtime-busy', message: 'Portal is busy right now. Try again when it has finished.', help: null}}])
  const {unmount} = render(<PortalPlugins status={statusFor()} onChanged={() => {}} />)
  fireEvent.click(screen.getByRole('button', {name: 'Turn on'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Portal is busy right now.')
  expect(screen.queryByRole('link', {name: /Discord/})).toBeNull()
  unmount()
  api([409, {detail: {code: 'plugin-change-rolled-back', message: "Portal couldn't start with that plugin change, so it's back as it was. Try again.", help: DISCORD}}])
  render(<PortalPlugins status={statusFor()} onChanged={() => {}} />)
  fireEvent.click(screen.getByRole('button', {name: 'Turn on'}))
  expect(await screen.findByRole('alert')).toHaveTextContent("so it's back as it was")
  expect(screen.getByRole('link', {name: /Discord/})).toHaveAttribute('href', DISCORD)
})

it('shows nothing where connected apps are not available', () => {
  render(<PortalPlugins status={statusFor({}, {available: false})} onChanged={() => {}} />)
  expect(screen.queryByText('Portal plugins')).toBeNull()
})


it('shows a known pending change and finishes it with the intended flag', async () => {
  const calls = api([200, {outcome: 'applied'}])
  const changed = vi.fn()
  render(<PortalPlugins status={statusFor({}, {pluginChange: {plugin: 'memory-wiki', enabled: true, error: null}})} onChanged={changed} />)
  const wiki = screen.getByRole('article', {name: 'Knowledge wiki'})
  expect(within(wiki).getByText('Change pending')).toBeVisible()
  expect(within(wiki).queryByText('On')).toBeNull()
  expect(within(wiki).queryByText('Off')).toBeNull()
  expect(screen.queryByRole('button', {name: 'Turn on'})).toBeNull()
  expect(screen.queryByRole('button', {name: 'Turn off'})).toBeNull()
  fireEvent.click(within(wiki).getByRole('button', {name: 'Finish change'}))
  expect(await screen.findByText('Knowledge wiki is on. Portal can use it now.')).toBeVisible()
  expect(calls).toEqual([['POST /api/pixel/connections/plugin', {plugin: 'memory-wiki', enabled: true}]])
  expect(changed).toHaveBeenCalled()
})

it('finishes a pending disable with enabled false even when the old flag was already false', async () => {
  const calls = api([200, {outcome: 'applied'}])
  render(<PortalPlugins status={statusFor({}, {plugins: {'memory-wiki': false}, pluginChange: {plugin: 'memory-wiki', enabled: false, error: null}})} onChanged={() => {}} />)
  const wiki = screen.getByRole('article', {name: 'Knowledge wiki'})
  fireEvent.click(within(wiki).getByRole('button', {name: 'Finish change'}))
  expect(await screen.findByText('Knowledge wiki is off. Portal no longer uses it.')).toBeVisible()
  expect(calls[0][1]).toEqual({plugin: 'memory-wiki', enabled: false})
})

it('keeps a known pending change retryable after an error', async () => {
  api([409, {detail: {code: 'runtime-busy', message: 'Portal is busy right now. Try again when it has finished.', help: null}}])
  render(<PortalPlugins status={statusFor({}, {pluginChange: {plugin: 'memory-wiki', enabled: true, error: 'connection-runtime-proof-failed'}})} onChanged={() => {}} />)
  const wiki = screen.getByRole('article', {name: 'Knowledge wiki'})
  expect(screen.getByText(/last attempt.*turn this plugin on.*did not finish/)).toBeVisible()
  expect(screen.queryByText('connection-runtime-proof-failed')).toBeNull()
  fireEvent.click(within(wiki).getByRole('button', {name: 'Finish change'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('Portal is busy right now.')
  expect(within(wiki).getByRole('button', {name: 'Finish change'})).toBeEnabled()
})

it('never claims On or Off for an unknown pending change and disables normal buttons', async () => {
  const calls = api([200, {outcome: 'applied'}])
  render(<PortalPlugins status={statusFor({}, {pluginChange: {plugin: null, enabled: null, error: 'journal-unreadable'}})} onChanged={() => {}} />)
  const wiki = screen.getByRole('article', {name: 'Knowledge wiki'})
  expect(within(wiki).getByText('Change pending')).toBeVisible()
  expect(within(wiki).queryByText('On')).toBeNull()
  expect(within(wiki).queryByText('Off')).toBeNull()
  expect(screen.getByRole('button', {name: 'Turn on'})).toBeDisabled()
  expect(screen.queryByRole('button', {name: 'Finish change'})).toBeNull()
  expect(screen.getByText(/details are unavailable/)).toBeVisible()
  expect(calls).toEqual([])
})

it('keeps the pending change and shows the error when finishing fails, then refreshes', async () => {
  api([502, {detail: {code: 'unknown', message: 'ODS could not finish this step.', help: true}}])
  const changed = vi.fn()
  render(<PortalPlugins status={statusFor({}, {pluginChange: {plugin: 'memory-wiki', enabled: true, error: null}})} onChanged={changed} />)
  const wiki = screen.getByRole('article', {name: 'Knowledge wiki'})
  fireEvent.click(within(wiki).getByRole('button', {name: 'Finish change'}))
  expect(await screen.findByRole('alert')).toHaveTextContent('ODS could not finish this step.')
  expect(within(wiki).getByText('Change pending')).toBeVisible()
  expect(within(wiki).getByRole('button', {name: 'Finish change'})).toBeEnabled()
  expect(changed).toHaveBeenCalled()
})
