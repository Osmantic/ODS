import {act, fireEvent, screen, within} from '@testing-library/react'
import {render} from '../test/test-utils'
import PortalRecovery from './PortalRecovery'

const DISCORD = 'https://discord.gg/4ntNp9MAwC'
const sandbox = {available:true, surface:'wsl-systemd', configured_mode:'sandboxed', effective_mode:'sandboxed',
  runtime_verified:true, revision:'b'.repeat(64), busy:false, pending:false, reason:null, scope:'owner-host'}
const stale = {...sandbox, effective_mode:'unknown', runtime_verified:false, revision:'a'.repeat(64), reason:'runtime-proof-required'}
const reply = (body, status = 200) => ({ok:status < 400, status, json:async () => body})
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return {promise, resolve} }
const banner = () => within(screen.getByRole('region', {name:'Portal recovery'}))
const posts = url => fetch.mock.calls.filter(([path, options]) => path === url && options?.method === 'POST')

function host(routes) {
  vi.stubGlobal('fetch', vi.fn(async (url, options = {}) => {
    const route = routes[`${options.method || 'GET'} ${url}`]
    if (!route) throw new Error(`unexpected ${options.method || 'GET'} ${url}`)
    return typeof route === 'function' ? route(options) : route
  }))
}

afterEach(() => { vi.unstubAllGlobals() })

it('verifies an idle Sandbox with a fresh revision, exactly as Portal permissions does', async () => {
  const change = deferred()
  host({'GET /api/pixel/access-mode': reply(stale), 'POST /api/pixel/access-mode': () => change.promise})
  const settled = vi.fn()
  render(<PortalRecovery status="available" action="verify-sandbox" onSettled={settled}/>)
  expect(banner().getByRole('status')).toHaveTextContent('ODS re-checks it automatically')
  // An out-of-date check is not a restart (laptop, 2026-10-31: shown after hours of uptime).
  expect(banner().getByRole('status')).toHaveTextContent('sandbox check is out of date')
  expect(banner().getByRole('status')).not.toHaveTextContent('restarted')
  // Nothing is sent before the owner chooses it.
  expect(fetch).not.toHaveBeenCalled()
  fireEvent.click(banner().getByRole('button', {name:'Verify now'}))
  fireEvent.click(banner().getByRole('button', {name:'Verify now'}))
  await act(async () => {})
  expect(banner().getByRole('button', {name:'Verify now'})).toBeDisabled()
  expect(banner().getByText('Checking Portal’s sandbox…')).toBeVisible()
  expect(posts('/api/pixel/access-mode')).toHaveLength(1)
  expect(JSON.parse(posts('/api/pixel/access-mode')[0][1].body)).toEqual({mode:'sandboxed', revision:'a'.repeat(64), confirmed:false})
  await act(async () => change.resolve(reply(sandbox)))
  expect(settled).toHaveBeenCalledOnce()
  expect(banner().getByText('Done. Checking Portal again…')).toBeVisible()
})

it('restores Sandbox for a held chat when Full Access was configured, and says what that changes', async () => {
  const fullAccess = {...sandbox, configured_mode:'full-access', effective_mode:'full-access', reason:'chat-recovery-required'}
  host({'GET /api/pixel/access-mode': reply(fullAccess), 'POST /api/pixel/access-mode': reply(sandbox)})
  const settled = vi.fn()
  render(<PortalRecovery status="recovery" action="restore-sandbox" onSettled={settled}
    detail="Portal stopped during an earlier turn, so new messages are held until its permissions are checked again."/>)
  expect(banner().getByRole('alert')).toHaveTextContent('Portal stopped during an earlier turn')
  expect(banner().getByText(/If Full Access was on, this turns it off/)).toBeVisible()
  fireEvent.click(banner().getByRole('button', {name:'Restore Sandbox'}))
  await act(async () => {})
  // Full Access is never requested: only the Sandbox restore Portal permissions offers here.
  expect(posts('/api/pixel/access-mode').map(([, options]) => JSON.parse(options.body)))
    .toEqual([{mode:'sandboxed', revision:'b'.repeat(64), confirmed:false}])
  expect(settled).toHaveBeenCalledOnce()
})

it('does nothing when the host already re-proved the runtime before the click', async () => {
  host({'GET /api/pixel/access-mode': reply(sandbox)})
  const settled = vi.fn()
  render(<PortalRecovery status="available" action="verify-sandbox" onSettled={settled}/>)
  fireEvent.click(banner().getByRole('button', {name:'Verify now'}))
  await act(async () => {})
  expect(posts('/api/pixel/access-mode')).toHaveLength(0)
  expect(settled).toHaveBeenCalledOnce()
})

it.each([
  ['busy runtime', {'GET /api/pixel/access-mode': reply({...stale, busy:true})},
    'Portal is still finishing earlier work, so nothing was changed. Try again when that work stops.', false],
  ['busy model lifecycle', {'GET /api/pixel/access-mode': reply(stale), 'POST /api/pixel/access-mode':
    reply({detail:{code:'model_lifecycle_busy', message:'ODS is busy'}}, 409)},
    'ODS is busy with a model or Portal change, so nothing was changed. Try again in a moment.', false],
  ['unconfirmed change', {'GET /api/pixel/access-mode': reply(stale), 'POST /api/pixel/access-mode':
    reply({detail:'The access change was not verified. Refresh status before retrying or restoring safer mode.'}, 409)},
    'Portal could not confirm its sandbox, so messages stay held. Try again. If it fails again, open Portal permissions in Settings to see what is unfinished.', true],
  ['unreadable permissions', {'GET /api/pixel/access-mode': reply({detail:'Portal access service is unavailable'}, 503)},
    'ODS could not read Portal’s permissions, so nothing was changed. Wait a moment and try again.', true],
  ['no answer', {'GET /api/pixel/access-mode': () => { throw new TypeError('Failed to fetch') }},
    'ODS did not answer, so the result is not confirmed. Portal keeps checking. If this notice stays, choose the button again.', true],
])('explains a %s with its cause and next step', async (_name, routes, text, help) => {
  host(routes)
  const settled = vi.fn()
  render(<PortalRecovery status="available" action="verify-sandbox" onSettled={settled}/>)
  fireEvent.click(banner().getByRole('button', {name:'Verify now'}))
  await act(async () => {})
  const failure = banner().getByRole('alert')
  expect(failure).toHaveTextContent(text)
  if (help) expect(within(failure).getByRole('link', {name:'Get help on Discord'})).toHaveAttribute('href', DISCORD)
  else expect(within(failure).queryByRole('link')).toBeNull()
  expect(settled).not.toHaveBeenCalled()
  // The button stays for another attempt.
  expect(banner().getByRole('button', {name:'Verify now'})).toBeEnabled()
})

it('resumes an unloaded model and keeps showing a failure while Portal reports the attempt as a switch', async () => {
  const start = deferred()
  host({'POST /api/models/runtime/start': () => start.promise})
  const view = render(<PortalRecovery status="unloaded" detail="The model is unloaded, so Portal is paused. Choose Resume model to load it again."/>)
  fireEvent.click(banner().getByRole('button', {name:'Resume model'}))
  await act(async () => {})
  expect(posts('/api/models/runtime/start').map(([, options]) => options.body)).toEqual(['{}'])
  // The host owns a model operation while it resumes, so status reads "switching".
  view.rerender(<PortalRecovery status="switching"/>)
  expect(banner().getByText('Loading the model. This can take a few minutes…')).toBeVisible()
  await act(async () => start.resolve(reply({detail:{error:'Windows runtime start is unconfirmed', code:'runtime_control_unconfirmed'}}, 409)))
  const failure = banner().getByText(/The model did not start/)
  expect(failure).toHaveAttribute('role', 'alert')
  expect(failure).toHaveTextContent('The model did not start: Windows runtime start is unconfirmed. Wait a moment and choose Resume model again.')
  expect(within(failure).getByRole('link', {name:'Get help on Discord'})).toHaveAttribute('href', DISCORD)
  view.rerender(<PortalRecovery status="unloaded" detail="The model is unloaded, so Portal is paused. Choose Resume model to load it again."/>)
  expect(banner().getByText(/The model did not start/)).toBeVisible()
  view.rerender(<PortalRecovery status="available"/>)
  expect(screen.queryByRole('region', {name:'Portal recovery'})).toBeNull()
})

it('recovers a stopped switch, and offers the release Models offers when nothing changed', async () => {
  const held = {pending:true, phase:'held', transactionId:'c'.repeat(64)}
  const answers = [reply({...held, reason:'model-recovery-proof-required', releasable:true}, 409),
    reply({pending:false, phase:'completed', transactionId:'c'.repeat(64), outcome:'rollback'})]
  host({'POST /api/models/recovery': () => answers.shift()})
  const settled = vi.fn()
  render(<PortalRecovery status="model-recovery" onSettled={settled}/>)
  fireEvent.click(banner().getByRole('button', {name:'Recover model switch'}))
  await act(async () => {})
  expect(banner().getByText(/cannot finish the switch the usual way/)).toBeVisible()
  expect(settled).not.toHaveBeenCalled()
  fireEvent.click(banner().getByRole('button', {name:'Release without checking'}))
  await act(async () => {})
  expect(posts('/api/models/recovery').map(([, options]) => options.body)).toEqual(['{}', '{"releaseUnverified":true}'])
  expect(settled).toHaveBeenCalledOnce()
})

it('offers Resume model for a stopped runtime only when the API names it', async () => {
  host({'POST /api/models/runtime/start': reply({status:'started'})})
  const settled = vi.fn()
  const view = render(<PortalRecovery status="unavailable" detail="The local model runtime is unavailable."/>)
  // Unreachable is not stopped: without the host's proof there is no button.
  expect(screen.queryByRole('region', {name:'Portal recovery'})).toBeNull()
  view.rerender(<PortalRecovery status="unavailable" action="resume-model" onSettled={settled}
    detail="The local model runtime is not running, so Portal is paused. Choose Resume model to start it."/>)
  expect(banner().getByRole('alert')).toHaveTextContent('The model runtime is not running, so Portal is paused.')
  fireEvent.click(banner().getByRole('button', {name:'Resume model'}))
  await act(async () => {})
  expect(posts('/api/models/runtime/start')).toHaveLength(1)
  expect(settled).toHaveBeenCalledOnce()
  view.rerender(<PortalRecovery status="unavailable" action="verify-sandbox"/>)
  expect(screen.queryByRole('region', {name:'Portal recovery'})).toBeNull()
})

it('shows nothing for a healthy Portal, and keeps the settings link for an older API', () => {
  host({})
  const view = render(<PortalRecovery status="available"/>)
  expect(screen.queryByRole('region', {name:'Portal recovery'})).toBeNull()
  view.rerender(<PortalRecovery status="available" action="enable-full-access"/>)
  expect(screen.queryByRole('region', {name:'Portal recovery'})).toBeNull()
  view.rerender(<PortalRecovery status="recovery" detail="Portal stopped during an earlier turn."/>)
  expect(screen.getByRole('link', {name:'Portal permissions'})).toHaveAttribute('href', '/settings?section=access')
  expect(fetch).not.toHaveBeenCalled()
})
