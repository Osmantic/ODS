import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { sha256 } from '@noble/hashes/sha2.js'
import { bytesToHex } from '@noble/hashes/utils.js'
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

const LOGIN_TOKEN = /^[A-Za-z0-9_-]{16,128}$/

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
  // Keep fingerprints, not credentials, to deduplicate tokens across hash events
  // and StrictMode's re-run without retaining owner links for the page lifetime.
  const linkAttempts = useRef(new Set())
  const auth = useRef({ version: 0, pending: false, mounted: false })
  const beginAuth = useCallback(() => {
    auth.current.pending = true
    return ++auth.current.version
  }, [])
  const finishAuth = useCallback(version => {
    if (!auth.current.mounted || auth.current.version !== version) return false
    auth.current.pending = false
    return true
  }, [])

  const signIn = useCallback(async (credential) => {
    const version = beginAuth()
    let response
    try {
      response = await fetch('/api/auth/dashboard-session/login', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(credential),
      })
    } catch {
      if (!finishAuth(version)) return
      setSession(false)
      setMessage('Could not reach ODS. Check the connection and try again.')
      setState('sign-in')
      return
    }
    if (response.ok) {
      const body = await response.json().catch(() => ({}))
      if (!finishAuth(version)) return
      setSession(true)
      setMessage('')
      setSetupOptional(false)
      setState(body.passwordSetup ? 'password' : 'ready')
      return
    }
    const detail = await detailOf(response, 'Sign-in failed. Try again.')
    if (!finishAuth(version)) return
    setSession(false)
    setMessage(detail)
    setState('sign-in')
  }, [beginAuth, finishAuth])

  useEffect(() => {
    auth.current.mounted = true
    if (bypass) return () => { auth.current.mounted = false }
    let cancelled = false
    const consumeLink = () => {
      const fragments = window.location.hash.slice(1).split('&')
      const links = fragments.filter(part => part.startsWith('ods-login='))
      if (!links.length) return false
      // Scrub even malformed credentials before any asynchronous work.
      const remaining = fragments.filter(part => !part.startsWith('ods-login=')).join('&')
      window.history.replaceState(window.history.state, '', window.location.pathname + window.location.search + (remaining ? `#${remaining}` : ''))
      const token = links[0].slice('ods-login='.length)
      if (links.length !== 1 || !LOGIN_TOKEN.test(token)) {
        ++auth.current.version
        auth.current.pending = false
        setSession(false)
        setMessage('That sign-in link is invalid. Open a new one-time link.')
        setState('sign-in')
        return true
      }
      const fingerprint = bytesToHex(sha256(new TextEncoder().encode(token)))
      if (!linkAttempts.current.has(fingerprint)) {
        linkAttempts.current.add(fingerprint)
        setMessage('')
        setState('checking')
        void signIn({ token })
      }
      return true
    }
    const check = async () => {
      const version = auth.current.version
      const current = () => !cancelled && auth.current.version === version
      try {
        const response = await fetch('/api/auth/dashboard-session', { credentials: 'same-origin' })
        if (!current()) return
        if (signInRequired(response)) {
          setState('sign-in')
          return
        }
        if (response.ok) {
          const body = await response.json().catch(() => ({}))
          if (current()) {
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
      if (current()) setState('ready')
    }
    window.addEventListener('hashchange', consumeLink)
    // StrictMode may re-run after the fragment was removed while login is still
    // pending. The original attempt owns that result; do not start a check.
    if (!consumeLink() && auth.current.version === 0) void check()
    return () => {
      cancelled = true
      auth.current.mounted = false
      window.removeEventListener('hashchange', consumeLink)
    }
  }, [bypass, signIn])

  // An expired or cleared session returns the user to sign-in instead of
  // leaving every panel in an error state.
  useEffect(() => {
    if (bypass || state !== 'ready') return undefined
    const original = window.fetch
    const guarded = async (...args) => {
      const version = auth.current.version
      const response = await original(...args)
      if (auth.current.mounted && auth.current.version === version && !auth.current.pending && signInRequired(response)) {
        ++auth.current.version
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
    const version = beginAuth()
    try {
      const response = await fetch('/api/auth/dashboard-session/logout', { method: 'POST', credentials: 'same-origin' })
      if (!response.ok) throw new Error('Sign-out rejected')
    } catch {
      if (!finishAuth(version)) return
      setMessage('Could not sign out. Check the connection and try again.')
      return
    }
    if (!finishAuth(version)) return
    setSession(false)
    setMessage('You signed out of this browser.')
    setState('sign-in')
  }, [beginAuth, finishAuth])

  const savePassword = async password => {
    const version = beginAuth()
    try {
      const response = await fetch('/api/auth/dashboard-session/password', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password }),
      })
      if (signInRequired(response)) {
        if (!finishAuth(version)) return
        setSession(false); setMessage('Sign in again to save your password.'); setState('sign-in'); return
      }
      if (!response.ok) {
        const detail = await detailOf(response, 'Could not save the password. Try again.')
        if (finishAuth(version)) setMessage(detail)
        return
      }
      if (!finishAuth(version)) return
      setSession(true)
      setMessage('')
      setState('ready')
    } catch {
      if (finishAuth(version)) setMessage('Could not reach ODS. Check the connection and try again.')
    }
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
    if (setup && password.length > 128) { setValidation('Use up to 128 characters.'); return }
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
          <p className="ods-signin-lede">{setup ? 'Choose a password to sign in on your devices.' : 'Welcome back. Enter your password to continue.'}</p>
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
