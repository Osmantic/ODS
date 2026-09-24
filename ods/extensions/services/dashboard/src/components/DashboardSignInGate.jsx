import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import ODSLogo from './ODSLogo'
import WallpaperVideo from './WallpaperVideo'
import './dashboard-sign-in.css'

/**
 * Dashboard sign-in for access from another device.
 *
 * nginx lets a browser on the ODS machine itself (http://localhost) use the
 * dashboard without signing in. Any other route - LAN mode, ODS proxy, a
 * reverse proxy or Tailscale Serve - is answered with
 * `401` + `X-ODS-Sign-In: required` until this browser holds a dashboard
 * session. This gate shows the sign-in screen only in that case, so local
 * use is unchanged. ODS Talk has its own session and is never gated here.
 */

const DashboardSessionContext = createContext({ session: false, signOut: async () => {}, changePassword: () => {} })

export const useDashboardSession = () => useContext(DashboardSessionContext)

const LOGIN_FRAGMENT = /(?:^#|&)ods-login=([A-Za-z0-9_-]{16,128})(?:&|$)/

export function signInRequired(response) {
  return response?.status === 401 && response.headers?.get?.('x-ods-sign-in') === 'required'
}

function isTalkLocation() {
  return window.location.hostname.startsWith('talk.') || window.location.pathname.startsWith('/talk')
}

async function detailOf(response, fallback) {
  const body = await response.json().catch(() => ({}))
  return typeof body.detail === 'string' ? body.detail : fallback
}

export default function DashboardSignInGate({ children }) {
  const [bypass] = useState(isTalkLocation)
  const [state, setState] = useState(bypass ? 'ready' : 'checking')
  const [session, setSession] = useState(false)
  const [message, setMessage] = useState('')
  const [setupOptional, setSetupOptional] = useState(false)
  // One link sign-in per page load, shared across StrictMode's re-run.
  const linkAttempt = useRef(null)

  const signIn = useCallback(async (credential) => {
    let response
    try {
      response = await fetch('/api/auth/dashboard-session/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credential),
      })
    } catch {
      setMessage('Could not reach ODS. Check the connection and try again.')
      setState('sign-in')
      return
    }
    if (response.ok) {
      setSession(true)
      setMessage('')
      const body = await response.json().catch(() => ({}))
      setState(body.passwordSetup ? 'password' : 'ready')
      return
    }
    setMessage(await detailOf(response, 'Sign-in failed. Try again.'))
    setState('sign-in')
  }, [])

  useEffect(() => {
    if (bypass) return undefined
    let cancelled = false
    const check = async () => {
      const link = window.location.hash.match(LOGIN_FRAGMENT)
      if (link && !linkAttempt.current) {
        // Drop the one-time token from the address bar and history first.
        window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search)
        linkAttempt.current = signIn({ token: link[1] })
      }
      if (linkAttempt.current) {
        await linkAttempt.current
        return
      }
      try {
        const response = await fetch('/api/auth/dashboard-session', { credentials: 'same-origin' })
        if (cancelled) return
        if (signInRequired(response)) {
          setState('sign-in')
          return
        }
        if (response.ok) {
          const body = await response.json().catch(() => ({}))
          if (!cancelled) {
            setSession(body.session === true)
            if (body.passwordConfigured === false) {
              let dismissed = false
              try { dismissed = localStorage.getItem('ods-password-setup-dismissed') === 'true' } catch { /* Optional preference. */ }
              if (!dismissed) { setSetupOptional(body.session !== true); setState('password'); return }
            }
          }
        }
      } catch {
        // Unreachable API: let the dashboard show its usual service status.
      }
      if (!cancelled) setState('ready')
    }
    check()
    return () => { cancelled = true }
  }, [bypass, signIn])

  // An expired or cleared session returns the user to sign-in instead of
  // leaving every panel in an error state.
  useEffect(() => {
    if (bypass || state !== 'ready') return undefined
    const original = window.fetch
    const guarded = async (...args) => {
      const response = await original(...args)
      if (signInRequired(response)) {
        setSession(false)
        setMessage('Your dashboard session ended. Sign in again to continue.')
        setState('sign-in')
      }
      return response
    }
    window.fetch = guarded
    return () => {
      if (window.fetch === guarded) window.fetch = original
    }
  }, [bypass, state])

  const signOut = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/dashboard-session/logout', { method: 'POST', credentials: 'same-origin' })
      if (!response.ok) throw new Error('Sign-out rejected')
    } catch {
      setMessage('Could not sign out. Check the connection and try again.')
      return
    }
    setSession(false)
    setMessage('You signed out of this browser.')
    setState('sign-in')
  }, [])

  const savePassword = async password => {
    try {
      const response = await fetch('/api/auth/dashboard-session/password', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }),
      })
      if (signInRequired(response)) { setSession(false); setMessage('Sign in again to save your password.'); setState('sign-in'); return }
      if (!response.ok) { setMessage(await detailOf(response, 'Could not save the password. Try again.')); return }
      setSession(true)
      setMessage('')
      setState('ready')
    } catch { setMessage('Could not reach ODS. Check the connection and try again.') }
  }
  const changePassword = () => { setMessage(''); setSetupOptional(true); setState('password') }
  const skipSetup = () => {
    try { localStorage.setItem('ods-password-setup-dismissed', 'true') } catch { /* Optional preference. */ }
    setMessage(''); setState('ready')
  }

  if (state === 'checking') return <div className="min-h-screen bg-theme-bg" aria-busy="true" />
  if (state === 'sign-in') return <SignInScreen message={message} onSubmit={password => signIn({ password })} />
  if (state === 'password') return <SignInScreen key="password-setup" setup message={message} onSubmit={savePassword} onCancel={setupOptional ? skipSetup : null} />
  return (
    <DashboardSessionContext.Provider value={{ session, signOut, changePassword }}>
      {message && <p role="alert" className="ods-signout-error">{message}</p>}
      {children}
    </DashboardSessionContext.Provider>
  )
}

// A quiet silhouette of the workspace under the frosted pane. Decorative only:
// nothing from the dashboard loads until sign-in succeeds.
function WorkspaceSilhouette() {
  return (
    <div className="ods-signin-ghost" aria-hidden="true">
      <div className="ods-signin-ghost-rail">
        <ODSLogo />
        {[0, 1, 2, 3, 4, 5, 6, 7].map(index => <b key={index}><i /></b>)}
      </div>
      <div className="ods-signin-ghost-shell"><i /><i /><i /><i /><i /></div>
    </div>
  )
}

function SignInScreen({ message, onSubmit, setup = false, onCancel = null }) {
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [validation, setValidation] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event) => {
    event.preventDefault()
    if (busy || !password) return
    if (setup && (password.length < 6 || password.length > 128)) { setValidation('Use 6 to 128 characters. A memorable passphrase works well.'); return }
    if (setup && password !== confirmation) { setValidation('The passwords do not match.'); return }
    setValidation('')
    setBusy(true)
    await onSubmit(password)
    setBusy(false)
  }
  return (
    <div className="ods-signin">
      <WallpaperVideo />
      <WorkspaceSilhouette />
      <div className="ods-signin-veil">
        <form className="ods-signin-card" onSubmit={submit} aria-labelledby="ods-signin-title">
          <ODSLogo />
          <h1 id="ods-signin-title">{setup ? 'Choose your password' : 'Sign in to ODS'}</h1>
          <p className="ods-signin-lede">{setup ? 'Use a memorable passphrase to sign in on your devices.' : 'Welcome back. Enter your password to continue.'}</p>
          <div className="ods-signin-field">
            <input
              type="password"
              aria-label={setup ? 'New password' : 'Password'}
              placeholder={setup ? 'New password' : 'Password'}
              autoComplete={setup ? 'new-password' : 'current-password'}
              spellCheck={false}
              maxLength={128}
              autoFocus
              value={password}
              onChange={event => setPassword(event.target.value)}
            />
            {!setup && (
            <button type="submit" className="ods-signin-submit" aria-label={setup ? 'Save password' : 'Sign in'} aria-busy={busy} disabled={busy || !password || (setup && !confirmation)}>
              <ArrowRight size={16} strokeWidth={2.2} aria-hidden="true" />
            </button>
            )}
          </div>
          {setup && <div className="ods-signin-field ods-signin-confirm">
            <input type="password" aria-label="Confirm password" placeholder="Confirm password"
              autoComplete="new-password" maxLength={128} value={confirmation}
              onChange={event => setConfirmation(event.target.value)} />
            <button type="submit" className="ods-signin-submit" aria-label={setup ? 'Save password' : 'Sign in'} aria-busy={busy} disabled={busy || !password || (setup && !confirmation)}>
              <ArrowRight size={16} strokeWidth={2.2} aria-hidden="true" />
            </button>
          </div>}
          {(validation || message) && <p role="alert" className="ods-signin-message">{validation || message}</p>}
          <details className="ods-signin-help">
            <summary>{setup ? 'About your password' : 'Forgot password?'}</summary>
            <p className="ods-signin-hint">
              {setup ? 'Your password stays on this ODS machine. Saving a new password signs out your other devices.' : <>
                On the ODS computer, open Your profile, then choose Change dashboard password.
                If you cannot sign in there, run <code>ods dashboard-login</code> on that computer
                and open the one-time link. No old password is needed.
              </>}
            </p>
          </details>
          {onCancel && <button type="button" className="ods-signin-later" onClick={onCancel}>Not now</button>}
        </form>
      </div>
    </div>
  )
}
