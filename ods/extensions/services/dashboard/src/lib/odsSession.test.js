import {fireEvent, waitFor} from '@testing-library/react'
import {openWithFreshSession, opensWithOdsSession, refreshOdsSession} from './odsSession'

const HERMES = 'http://localhost:9120/auth/ods'
const deferred = () => {let resolve; return {promise:new Promise(done => {resolve=done}), resolve:value=>resolve(value)}}
const session = secondsLeft => ({ok:true, json:async () => ({valid:true, expires_at:Math.floor(Date.now() / 1000) + secondsLeft})})
const calls = () => fetch.mock.calls.map(([url]) => url)

function launchLink(href = HERMES) {
  const anchor = document.createElement('a')
  anchor.href = href
  anchor.target = '_blank'
  anchor.addEventListener('click', openWithFreshSession)
  // jsdom cannot navigate; record what the browser would do, then stop it.
  const seen = []
  anchor.addEventListener('click', event => {seen.push(event.defaultPrevented); event.preventDefault()})
  document.body.append(anchor)
  return {anchor, seen}
}

beforeEach(() => {vi.stubGlobal('fetch', vi.fn()); vi.spyOn(console, 'warn').mockImplementation(() => {})})
afterEach(() => {document.body.replaceChildren(); vi.restoreAllMocks(); vi.unstubAllGlobals()})

it('marks only the cookie-gated Hermes entry as needing the ODS session', () => {
  expect(opensWithOdsSession('hermes-proxy')).toBe(true)
  for (const id of ['hermes', 'open-webui', 'n8n', 'opencode', undefined]) expect(opensWithOdsSession(id)).toBe(false)
})

it.each([
  ['no session', {ok:false, status:401}, true],
  ['a session in its last hour', session(30 * 60), true],
  ['a session with hours left', session(6 * 3600), false],
])('mints a new session for %s', async (_label, verify, mints) => {
  fetch.mockImplementation(async url => url === '/api/auth/verify-session' ? verify : {ok:true})
  await refreshOdsSession()
  expect(calls()).toEqual(mints ? ['/api/auth/verify-session', '/api/auth/admin-session'] : ['/api/auth/verify-session'])
  if (mints) expect(fetch).toHaveBeenLastCalledWith('/api/auth/admin-session', expect.objectContaining({method:'POST', credentials:'same-origin'}))
  expect(console.warn).not.toHaveBeenCalled()
})

it('opens the tab inside the click and sends it to Hermes only after the session is fresh', async () => {
  const minted = deferred()
  fetch.mockImplementation(url => url === '/api/auth/verify-session' ? Promise.resolve({ok:false, status:401}) : minted.promise)
  const tab = {opener:window, closed:false, location:{replace:vi.fn()}}
  vi.spyOn(window, 'open').mockReturnValue(tab)
  const {anchor, seen} = launchLink()

  fireEvent.click(anchor)
  expect(window.open).toHaveBeenCalledWith('', '_blank')
  expect(seen).toEqual([true])
  expect(tab.opener).toBeNull()
  await waitFor(() => expect(calls()).toEqual(['/api/auth/verify-session', '/api/auth/admin-session']))
  expect(tab.location.replace).not.toHaveBeenCalled()

  minted.resolve({ok:true})
  await waitFor(() => expect(tab.location.replace).toHaveBeenCalledWith(HERMES))
})

it('still sends the tab to Hermes when the session cannot be refreshed', async () => {
  fetch.mockRejectedValue(new TypeError('Failed to fetch'))
  const tab = {opener:window, closed:false, location:{replace:vi.fn()}}
  vi.spyOn(window, 'open').mockReturnValue(tab)
  fireEvent.click(launchLink().anchor)
  await waitFor(() => expect(tab.location.replace).toHaveBeenCalledWith(HERMES))
  expect(console.warn).toHaveBeenCalledWith('[ods-session] bootstrap failed:', expect.any(TypeError))
})

it.each([
  ['a modified click', {ctrlKey:true}, {}],
  ['a blocked popup', {}, null],
])('leaves %s to the browser', (_label, init, tab) => {
  vi.spyOn(window, 'open').mockReturnValue(tab)
  const {anchor, seen} = launchLink()
  fireEvent.click(anchor, init)
  expect(seen).toEqual([false])
  if (init.ctrlKey) expect(window.open).not.toHaveBeenCalled()
  expect(fetch).not.toHaveBeenCalled()
})
