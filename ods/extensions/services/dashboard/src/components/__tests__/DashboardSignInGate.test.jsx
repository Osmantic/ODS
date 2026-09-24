import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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

  it('returns to sign-in when a later request is turned away, and after signing out', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ signedIn: true, session: true }))
      .mockResolvedValueOnce(signInRequired())
    renderGate()
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()

    await window.fetch('/api/status')
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
})


describe('password setup and recovery', () => {
  it.each([false, true])('accepts six characters during setup or recovery (%s)', async recovery => {
    window.history.replaceState(null, '', recovery ? '/#ods-login=abcdefghijklmnopqrstuvwx' : '/')
    const fetchMock = vi.fn().mockResolvedValueOnce(json(recovery
      ? { signedIn: true, passwordSetup: true }
      : { signedIn: true, session: false, passwordConfigured: false }))
      .mockResolvedValueOnce(json({ signedIn: true, passwordConfigured: true }))
    window.fetch = fetchMock
    renderGate()
    const password = await screen.findByLabelText('New password')
    fireEvent.change(password, { target: { value: 'abcde' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'abcde' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Use 6 to 128 characters.')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    fireEvent.change(password, { target: { value: 'abcdef' } })
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'abcdef' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save password' }))
    expect(await screen.findByText('Dashboard content')).toBeInTheDocument()
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ password: 'abcdef' })
  })
  afterEach(() => { vi.restoreAllMocks(); window.history.replaceState(null, '', '/') })

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
