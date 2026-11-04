import { StrictMode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import DashboardSignInGate, { useDashboardSession } from '../DashboardSignInGate'
import { ThemeProvider } from '../../contexts/ThemeContext'

const response = (body, status = 200, headers = {}) => ({
  status,
  ok: status >= 200 && status < 300,
  headers: { get: name => headers[name.toLowerCase()] ?? null },
  json: async () => body,
})
const signInRequired = () => response(
  { detail: 'Sign in to the ODS dashboard to continue.' }, 401, { 'x-ods-sign-in': 'required' },
)
const json = (body, status = 200) => response(body, status)
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
const navigateLink = token => {
  window.history.pushState(null, '', `/models?tab=local#ods-login=${token}`)
  fireEvent(window, new Event('hashchange'))
}

function Dashboard() {
  const { session, signOut } = useDashboardSession()
  return (
    <div>
      <p>Dashboard content</p>
      {session && <button type="button" onClick={signOut}>Sign out</button>}
    </div>
  )
}

const renderGate = () => render(
  <ThemeProvider><DashboardSignInGate><Dashboard /></DashboardSignInGate></ThemeProvider>,
)

describe('DashboardSignInGate', () => {
  let fetchMock

  beforeEach(() => {
    fetchMock = vi.fn()
    window.fetch = fetchMock
    globalThis.fetch = fetchMock
    window.history.replaceState(null, '', '/')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    window.history.replaceState(null, '', '/')
  })

  it('shows the dashboard straight away for a browser on the ODS machine', async () => {
    fetchMock.mockResolvedValueOnce(json({ signedIn: true, session: false }))
    renderGate()
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/dashboard-session', { credentials: 'same-origin' })
  })

  it('asks for the password only when nginx requires sign-in', async () => {
    fetchMock
      .mockResolvedValueOnce(signInRequired())
      .mockResolvedValueOnce(json({ detail: 'That password is not correct.' }, 401))
      .mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()

    const input = await screen.findByLabelText('Password')
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
    fireEvent.change(input, { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('That password is not correct.')

    fireEvent.change(input, { target: { value: 'my chosen passphrase' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    const [url, options] = fetchMock.mock.calls[2]
    expect(url).toBe('/api/auth/dashboard-session/login')
    expect(JSON.parse(options.body)).toEqual({ password: 'my chosen passphrase' })
  })

  it('signs in with a one-time link and removes the token from the address bar', async () => {
    const token = 'a'.repeat(43)
    window.history.replaceState(null, '', `/models#ods-login=${token}`)
    fetchMock.mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()

    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    expect(window.location.hash).toBe('')
    expect(window.location.pathname).toBe('/models')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ token })
  })

  it('explains an expired or reused link', async () => {
    window.history.replaceState(null, '', `/#ods-login=${'b'.repeat(43)}`)
    fetchMock.mockResolvedValueOnce(json({ detail: 'That sign-in link has expired or was already used.' }, 401))
    renderGate()
    expect(await screen.findByRole('alert')).toHaveTextContent('expired or was already used')
  })

  it('consumes a new fragment on the already-loaded sign-in screen and scrubs it before validation', async () => {
    const login = deferred()
    fetchMock.mockResolvedValueOnce(signInRequired()).mockReturnValueOnce(login.promise)
    renderGate()
    await screen.findByLabelText('Password')
    const token = 'n'.repeat(43)
    navigateLink(token)
    expect(window.location.hash).toBe('')
    expect(window.location.pathname + window.location.search).toBe('/models?tab=local')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ token })
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
    await act(async () => login.resolve(json({ signedIn: true })))
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
  })

  it('accepts a fresh fragment after an expired link and does not resubmit either token', async () => {
    const old = 'e'.repeat(43)
    const fresh = 'f'.repeat(43)
    window.history.replaceState(null, '', `/#ods-login=${old}`)
    fetchMock.mockResolvedValueOnce(json({ detail: 'Expired link.' }, 401)).mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()
    expect(await screen.findByRole('alert')).toHaveTextContent('Expired link.')
    navigateLink(fresh)
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    navigateLink(old)
    navigateLink(fresh)
    expect(window.location.hash).toBe('')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
  })

  it('removes malformed login fragments and accepts a later valid link', async () => {
    fetchMock.mockResolvedValueOnce(signInRequired()).mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()
    await screen.findByLabelText('Password')
    navigateLink('short')
    expect(window.location.hash).toBe('')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
    navigateLink('v'.repeat(43))
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
  })

  it('preserves unrelated fragments, query parameters, and history state when scrubbing a token', async () => {
    const token = 'h'.repeat(43)
    window.history.replaceState({ keep: true }, '', `/models?tab=local#section&ods-login=${token}&filter=a%20b`)
    fetchMock.mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()
    expect(window.location.pathname + window.location.search + window.location.hash).toBe('/models?tab=local#section&filter=a%20b')
    expect(window.history.state).toEqual({ keep: true })
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
  })

  it('posts a one-time token only once under StrictMode and repeated hash events', async () => {
    const token = 's'.repeat(43)
    window.history.replaceState(null, '', `/#ods-login=${token}`)
    fetchMock.mockResolvedValue(json({ signedIn: true }))
    render(<StrictMode><ThemeProvider><DashboardSignInGate><Dashboard /></DashboardSignInGate></ThemeProvider></StrictMode>)
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    navigateLink(token)
    fireEvent(window, new Event('hashchange'))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(window.location.hash).toBe('')
  })

  it.each([true, false])('ignores a stale session check after a newer link result (accepted=%s)', async accepted => {
    const check = deferred()
    fetchMock.mockReturnValueOnce(check.promise).mockResolvedValueOnce(
      accepted ? json({ signedIn: true }) : json({ detail: 'New link rejected.' }, 401),
    )
    renderGate()
    navigateLink('c'.repeat(43))
    if (accepted) await screen.findByText('Dashboard content')
    else expect(await screen.findByRole('alert')).toHaveTextContent('New link rejected.')
    await act(async () => check.resolve(accepted ? signInRequired() : json({ signedIn: true, session: true })))
    expect(Boolean(screen.queryByText('Dashboard content'))).toBe(accepted)
    if (!accepted) expect(screen.getByRole('alert')).toHaveTextContent('New link rejected.')
  })

  it('ignores a stale session body that finishes after link sign-in', async () => {
    const body = deferred()
    fetchMock.mockResolvedValueOnce({ ...json({}), json: () => body.promise }).mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()
    await act(async () => {})
    navigateLink('j'.repeat(43))
    await screen.findByText('Dashboard content')
    await act(async () => body.resolve({ signedIn: true, session: false, passwordConfigured: false }))
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
  })

  it.each([true, false])('keeps the newest link result when an older response arrives late (accepted=%s)', async accepted => {
    const older = deferred()
    window.history.replaceState(null, '', `/#ods-login=${'o'.repeat(43)}`)
    fetchMock.mockReturnValueOnce(older.promise).mockResolvedValueOnce(
      accepted ? json({ signedIn: true }) : json({ detail: 'Latest link rejected.' }, 401),
    )
    renderGate()
    navigateLink('l'.repeat(43))
    if (accepted) await screen.findByText('Dashboard content')
    else await screen.findByRole('alert')
    await act(async () => older.resolve(accepted ? json({ detail: 'Older link rejected.' }, 401) : json({ signedIn: true })))
    expect(Boolean(screen.queryByText('Dashboard content'))).toBe(accepted)
    if (!accepted) expect(screen.getByRole('alert')).toHaveTextContent('Latest link rejected.')
  })

  it('ignores a stale protected request after a fresh link establishes a session', async () => {
    const stale = deferred()
    fetchMock.mockResolvedValueOnce(json({ signedIn: true, session: true })).mockReturnValueOnce(stale.promise)
      .mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()
    await screen.findByText('Dashboard content')
    const request = window.fetch('/api/status')
    navigateLink('g'.repeat(43))
    await screen.findByText('Dashboard content')
    await act(async () => { stale.resolve(signInRequired()); await request })
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
    fetchMock.mockResolvedValueOnce(signInRequired())
    await act(async () => { await window.fetch('/api/status') })
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
  })

  it('ignores an older login body after the latest link is rejected', async () => {
    const body = deferred()
    window.history.replaceState(null, '', `/#ods-login=${'b'.repeat(43)}`)
    fetchMock.mockResolvedValueOnce({ ...json({}), json: () => body.promise })
      .mockResolvedValueOnce(json({ detail: 'Latest link rejected.' }, 401))
    renderGate()
    await act(async () => {})
    navigateLink('d'.repeat(43))
    expect(await screen.findByRole('alert')).toHaveTextContent('Latest link rejected.')
    await act(async () => body.resolve({ signedIn: true, passwordSetup: true }))
    expect(screen.getByRole('alert')).toHaveTextContent('Latest link rejected.')
    expect(screen.queryByLabelText('New password')).not.toBeInTheDocument()
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
  })

  it('ignores a stale sign-out response after a newer link signs in', async () => {
    const logout = deferred()
    fetchMock.mockResolvedValueOnce(json({ signedIn: true, session: true })).mockReturnValueOnce(logout.promise)
      .mockResolvedValueOnce(json({ signedIn: true }))
    renderGate()
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    navigateLink('u'.repeat(43))
    await screen.findByText('Dashboard content')
    await act(async () => logout.resolve(json({ signedIn: false })))
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
  })

  it('does not restore the session when an older login finishes after sign-out', async () => {
    const older = deferred()
    window.history.replaceState(null, '', `/#ods-login=${'w'.repeat(43)}`)
    fetchMock.mockReturnValueOnce(older.promise).mockResolvedValueOnce(json({ signedIn: true }))
      .mockResolvedValueOnce(json({ signedIn: false }))
    renderGate()
    navigateLink('x'.repeat(43))
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    await screen.findByLabelText('Password')
    await act(async () => older.resolve(json({ signedIn: true })))
    expect(screen.getByRole('alert')).toHaveTextContent('You signed out')
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
  })

  it('removes the fragment listener on unmount and ignores a pending login result', async () => {
    const login = deferred()
    window.history.replaceState(null, '', `/#ods-login=${'z'.repeat(43)}`)
    fetchMock.mockReturnValueOnce(login.promise)
    const page = renderGate()
    page.unmount()
    navigateLink('y'.repeat(43))
    await act(async () => login.resolve(json({ signedIn: true })))
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
    expect(window.location.hash).toBe(`#ods-login=${'y'.repeat(43)}`)
  })

  it('ignores an earlier password-save rejection after a new recovery link', async () => {
    const save = deferred()
    window.history.replaceState(null, '', `/#ods-login=${'p'.repeat(43)}`)
    fetchMock.mockResolvedValueOnce(json({ signedIn: true, passwordSetup: true })).mockReturnValueOnce(save.promise)
      .mockResolvedValueOnce(json({ signedIn: true, passwordSetup: true }))
    renderGate()
    fireEvent.change(await screen.findByLabelText('New password'), { target: { value: 'chosen password' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'chosen password' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
    navigateLink('q'.repeat(43))
    await screen.findByLabelText('New password')
    await act(async () => save.resolve(signInRequired()))
    expect(screen.getByLabelText('New password')).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Not now' })).not.toBeInTheDocument()
  })

  it('returns to sign-in when a later request is turned away, and after signing out', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ signedIn: true, session: true }))
      .mockResolvedValueOnce(signInRequired())
    renderGate()
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()

    await act(async () => { await window.fetch('/api/status') })
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('session ended')
  })

  it('signs out of this browser', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ signedIn: true, session: true }))
      .mockResolvedValueOnce(json({ signedIn: false }))
    renderGate()
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/auth/dashboard-session/logout', { method: 'POST', credentials: 'same-origin' })
  })

  it.each(['network', 'server'])('does not claim sign-out succeeded after a %s failure', async failure => {
    fetchMock.mockResolvedValueOnce(json({ signedIn: true, session: true }))
    if (failure === 'network') fetchMock.mockRejectedValueOnce(new Error('offline'))
    else fetchMock.mockResolvedValueOnce(json({}, 503))
    renderGate()
    fireEvent.click(await screen.findByRole('button', { name: 'Sign out' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not sign out')
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument()
    expect(screen.queryByText('You signed out of this browser.')).not.toBeInTheDocument()
  })

  it('never gates ODS Talk, which has its own session', async () => {
    window.history.replaceState(null, '', '/talk')
    renderGate()
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).not.toHaveBeenCalled())
  })

  it('does not consume dashboard login fragments on a Talk page', async () => {
    window.history.replaceState(null, '', `/talk#ods-login=${'t'.repeat(43)}`)
    renderGate()
    fireEvent(window, new Event('hashchange'))
    expect(screen.getByText('Dashboard content')).toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
    expect(window.location.hash).not.toBe('')
    await act(async () => {})
  })
})


describe('password setup and recovery', () => {
  afterEach(() => { vi.restoreAllMocks(); window.history.replaceState(null, '', '/') })

  it('lets the owner save a one-character password but requires a nonempty value', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ signedIn: true, session: false, passwordConfigured: false }))
      .mockResolvedValueOnce(json({ signedIn: true, passwordConfigured: true }))
    window.fetch = fetchMock
    renderGate()
    const password = await screen.findByLabelText('New password')
    const save = screen.getByRole('button', { name: 'Save password' })
    expect(save).toBeDisabled()
    fireEvent.change(password, { target: { value: 'a' } })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'a' } })
    fireEvent.click(save)
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ password: 'a' })
  })

  it('lets the local owner choose and confirm a password before continuing', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json({ signedIn: true, session: false, passwordConfigured: false }))
      .mockResolvedValueOnce(json({ signedIn: true, passwordConfigured: true }))
    window.fetch = fetchMock
    renderGate()
    expect(await screen.findByRole('heading', { name: 'Choose your password' })).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: '  my chosen passphrase  ' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'different passphrase' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('do not match')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: '  my chosen passphrase  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ password: '  my chosen passphrase  ' })
    expect(fetchMock.mock.calls[1][0]).toBe('/api/auth/dashboard-session/password')
  })

  it('opens password recovery from a one-time link without requesting the old password', async () => {
    window.history.replaceState(null, '', `/#ods-login=${'r'.repeat(43)}`)
    window.fetch = vi.fn().mockResolvedValue(json({ signedIn: true, passwordSetup: true }))
    renderGate()
    expect(await screen.findByLabelText('New password')).toBeInTheDocument()
    expect(screen.getByLabelText('Confirm password')).toBeInTheDocument()
    expect(screen.queryByLabelText('Password')).not.toBeInTheDocument()
    expect(window.location.hash).toBe('')
  })
})


describe('local access without lockouts', () => {
  afterEach(() => { vi.restoreAllMocks(); localStorage.removeItem('ods-password-setup-dismissed') })

  it('can defer local password setup without opening remote access', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({signedIn:true, session:false, passwordConfigured:false}))
    window.fetch = fetchMock
    const page = renderGate()
    fireEvent.click(await screen.findByRole('button', {name:'Not now'}))
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    page.unmount()
    window.fetch = vi.fn().mockResolvedValue(signInRequired())
    renderGate()
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(screen.queryByText('Dashboard content')).not.toBeInTheDocument()
  })

  it('returns to sign-in if authorization expires while saving a password', async () => {
    window.fetch = vi.fn().mockResolvedValueOnce(json({signedIn:true, session:false, passwordConfigured:false}))
      .mockResolvedValueOnce(signInRequired())
    renderGate()
    fireEvent.change(await screen.findByLabelText('New password'), {target:{value:'my chosen passphrase'}})
    fireEvent.change(screen.getByLabelText('Confirm password'), {target:{value:'my chosen passphrase'}})
    fireEvent.click(screen.getByRole('button', {name:'Save password'}))
    expect(await screen.findByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Sign in again')
  })
})
