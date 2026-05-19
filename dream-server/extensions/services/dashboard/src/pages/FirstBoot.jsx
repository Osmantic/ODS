// Phone-first first-boot wizard.
//
// Lives at /setup. App.jsx routes here when useFirstRun() says
// firstRun=true and locks all other routes out. Single-column,
// large tap targets, big text - the user is most likely on a
// phone scanning the device's setup link.
//
// 4 screens:
//   1. Welcome - label this setup
//   2. First user - username for the initial magic-link invite
//   3. Pick your stack - chat-only / chat+agents / everything
//   4. Done - generate magic-link, show QR
//
// Progress is mirrored to localStorage so a phone refresh doesn't
// lose state mid-wizard. The server-side flip happens only on the
// final "Finish" tap, via /api/setup/complete.

import { useEffect, useMemo, useState } from 'react'
import {
  Sparkles, User, Layers, Check, ChevronRight, ChevronLeft,
  MessageSquare, Workflow, Boxes, Loader2, AlertCircle, Copy,
  QrCode, Share2,
} from 'lucide-react'

const PROGRESS_KEY = 'dream-firstboot-progress'

// NOTE: this picker records a preference today; it does NOT enable
// extensions or start services. The matching backend wiring lives in
// the Extensions panel (and a follow-up PR will let Finish apply the
// chosen preset there). Until then the blurbs describe the *intent*
// of each stack, with the copy honest that nothing changes yet.
const STACK_OPTIONS = [
  {
    id: 'chat',
    title: 'Chat only',
    blurb: 'Just the chat surface. This is what runs out of the box.',
    Icon: MessageSquare,
  },
  {
    id: 'chat-agents',
    title: 'Chat + Agents',
    blurb: 'Adds n8n workflows and the agent runtime; enable from Extensions after setup.',
    Icon: Workflow,
  },
  {
    id: 'everything',
    title: 'Everything',
    blurb: 'Voice, image generation, search, the whole catalog; enable from Extensions after setup.',
    Icon: Boxes,
  },
]

const TOTAL_STEPS = 4

function readProgress() {
  try {
    const raw = globalThis.localStorage?.getItem(PROGRESS_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

function writeProgress(progress) {
  try {
    globalThis.localStorage?.setItem(PROGRESS_KEY, JSON.stringify(progress))
  } catch {
    // localStorage may be blocked in private windows; wizard still works.
  }
}

function clearProgress() {
  try {
    globalThis.localStorage?.removeItem(PROGRESS_KEY)
  } catch {
    // Ignore.
  }
}

export default function FirstBoot({ onComplete }) {
  const initial = useMemo(() => readProgress() || {}, [])
  const [step, setStep] = useState(initial.step || 1)
  const [deviceName, setDeviceName] = useState(initial.deviceName || 'dream')
  const [username, setUsername] = useState(initial.username || '')
  const [stack, setStack] = useState(initial.stack || 'chat')
  const [finishing, setFinishing] = useState(false)
  const [finishError, setFinishError] = useState(null)
  const [invite, setInvite] = useState(null)

  // Persist progress whenever the user moves forward.
  useEffect(() => {
    writeProgress({ step, deviceName, username, stack })
  }, [step, deviceName, username, stack])

  const next = () => setStep(s => Math.min(s + 1, TOTAL_STEPS))
  const prev = () => setStep(s => Math.max(s - 1, 1))

  const finish = async () => {
    setFinishing(true)
    setFinishError(null)
    try {
      // Generate the first magic-link for the named user. Reuses PR-4's
      // backend; this is the same endpoint /Invites consumes.
      const genResp = await fetch('/api/auth/magic-link/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          target_username: username,
          scope: 'chat',
          expires_in: 86400, // 24h; the wizard target may not redeem immediately
          reusable: false,
          note: `First-boot invite (${deviceName.trim() || 'dream'})`,
        }),
      })
      if (!genResp.ok) {
        const body = await genResp.json().catch(() => ({}))
        throw new Error(body.detail || `generate failed: ${genResp.status}`)
      }
      const inviteData = await genResp.json()

      // Flip the server-side sentinel so this device is "configured".
      // Check the response; an earlier draft fired the request and
      // moved on regardless of status, which left the server in
      // first-run mode while the UI said "You're set." If complete
      // fails, throw and let the catch surface the error to the user
      // (with the invite still safely visible on the previous screen).
      const completeResp = await fetch('/api/setup/complete', { method: 'POST' })
      if (!completeResp.ok) {
        const body = await completeResp.json().catch(() => ({}))
        throw new Error(
          body.detail || `Failed to mark setup complete (${completeResp.status}). Your invite was generated; ask the admin to re-run setup.`,
        )
      }

      // Mint an admin dream-session cookie for the operator who just
      // completed the wizard. Without this, after onboarding they'd
      // be blocked at the Hermes / chat tiles by the same forward_auth
      // gate that requires magic-link redemption — having to mint and
      // redeem an invite to themselves is absurd UX. The endpoint is
      // gated by DASHBOARD_API_KEY (nginx injects the header) so only
      // someone with admin trust can mint. Non-fatal: if it fails the
      // wizard still finishes; the user can re-mint by reloading the
      // dashboard (App.jsx's useSessionBootstrap retries on every load).
      try {
        const adminResp = await fetch('/api/auth/admin-session', { method: 'POST' })
        if (!adminResp.ok && adminResp.status !== 503) {
          // 503 = signing not configured server-side; surfaced elsewhere.
          // Other errors are operationally interesting but non-fatal here.
          // eslint-disable-next-line no-console
          console.warn('[dream-session] admin-session returned', adminResp.status)
        }
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('[dream-session] admin-session network failure:', err)
      }

      setInvite(inviteData)
      clearProgress()
      // Stay on the success screen until the user taps "Open dashboard".
      // Calling onComplete() immediately would route them away before they
      // can copy the QR. onComplete fires on the final tap.
    } catch (err) {
      setFinishError(err.message)
    } finally {
      setFinishing(false)
    }
  }

  const handleDone = () => {
    onComplete?.()
  }

  return (
    <div className="min-h-screen bg-theme-bg flex flex-col">
      <header className="px-6 pt-8 pb-4 flex items-center justify-between">
        <div className="font-mono text-sm font-bold text-theme-accent tracking-widest">DREAM SERVER</div>
        {!invite && <StepDots step={step} total={TOTAL_STEPS} />}
      </header>

      <main className="flex-1 flex items-stretch px-6 pb-8">
        <div className="w-full max-w-md mx-auto flex flex-col justify-center">
          {invite ? (
            <DoneScreen invite={invite} onDone={handleDone} />
          ) : (
            <>
              {step === 1 && (
                <WelcomeStep
                  deviceName={deviceName}
                  setDeviceName={setDeviceName}
                  onNext={next}
                />
              )}
              {step === 2 && (
                <UserStep
                  username={username}
                  setUsername={setUsername}
                  onNext={next}
                  onBack={prev}
                />
              )}
              {step === 3 && (
                <StackStep
                  stack={stack}
                  setStack={setStack}
                  onNext={next}
                  onBack={prev}
                />
              )}
              {step === 4 && (
                <ConfirmStep
                  deviceName={deviceName}
                  username={username}
                  stack={stack}
                  onBack={prev}
                  onFinish={finish}
                  finishing={finishing}
                  error={finishError}
                />
              )}
            </>
          )}
        </div>
      </main>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step dots
// ---------------------------------------------------------------------------

function StepDots({ step, total }) {
  return (
    <div className="flex items-center gap-2">
      {Array.from({ length: total }).map((_, i) => {
        const n = i + 1
        const active = n === step
        const done = n < step
        return (
          <div
            key={n}
            className={`w-2 h-2 rounded-full transition-colors ${
              done ? 'bg-theme-accent' : active ? 'bg-theme-accent ring-2 ring-theme-accent/40' : 'bg-theme-border'
            }`}
          />
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 1 - Welcome / setup label
// ---------------------------------------------------------------------------

function WelcomeStep({ deviceName, setDeviceName, onNext }) {
  const valid = /^[a-z0-9-]{1,32}$/i.test(deviceName.trim())
  return (
    <div>
      <div className="w-16 h-16 rounded-2xl bg-theme-accent/15 text-theme-accent flex items-center justify-center mb-6">
        <Sparkles size={32} />
      </div>
      <h1 className="text-3xl font-bold text-theme-text mb-3">Welcome to Dream.</h1>
      <p className="text-theme-text-muted mb-8 leading-relaxed">
        Let&apos;s get you set up in about a minute. First, give this setup a friendly label for the invite audit trail.
      </p>

      <label className="block mb-6">
        <span className="text-sm text-theme-text-muted">Setup label</span>
        <input
          type="text"
          value={deviceName}
          onChange={e => setDeviceName(e.target.value)}
          autoFocus
          maxLength={32}
          className="mt-2 w-full bg-theme-card border border-theme-border rounded-xl px-4 py-3 text-lg text-theme-text focus:outline-none focus:border-theme-accent"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
        />
        <span className="text-xs text-theme-text-muted mt-2 block">
          This label is recorded on the first invite only. It does not rename the host yet;
          change <code className="text-theme-accent">DREAM_DEVICE_NAME</code> in Settings before expecting
          <code className="text-theme-accent"> {deviceName.trim() || 'dream'}.local</code> to resolve.
          Letters, numbers, and dashes only.
        </span>
      </label>

      <button
        onClick={onNext}
        disabled={!valid}
        className="w-full flex items-center justify-center gap-2 bg-theme-accent text-white py-4 rounded-xl text-base font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
      >
        Continue
        <ChevronRight size={18} />
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 2 - First user
// ---------------------------------------------------------------------------

function UserStep({ username, setUsername, onNext, onBack }) {
  const trimmed = username.trim()
  const valid = /^[A-Za-z0-9._-]{1,64}$/.test(trimmed)
  return (
    <div>
      <div className="w-16 h-16 rounded-2xl bg-theme-accent/15 text-theme-accent flex items-center justify-center mb-6">
        <User size={32} />
      </div>
      <h1 className="text-3xl font-bold text-theme-text mb-3">Who&apos;s the first user?</h1>
      <p className="text-theme-text-muted mb-8 leading-relaxed">
        We&apos;ll generate a magic link for them at the end. They scan it once to reach the chat surface.
      </p>

      <label className="block mb-6">
        <span className="text-sm text-theme-text-muted">Username</span>
        <input
          type="text"
          value={username}
          onChange={e => setUsername(e.target.value)}
          autoFocus
          maxLength={64}
          placeholder="alice"
          className="mt-2 w-full bg-theme-card border border-theme-border rounded-xl px-4 py-3 text-lg text-theme-text focus:outline-none focus:border-theme-accent"
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
        />
        <span className="text-xs text-theme-text-muted mt-2 block">
          Recorded with the invite for the audit trail. Open WebUI may still ask the
          recipient to sign in or pick a profile name on first arrival.
        </span>
      </label>

      <div className="flex gap-3">
        <button
          onClick={onBack}
          className="flex items-center justify-center gap-2 bg-theme-card border border-theme-border text-theme-text py-4 px-5 rounded-xl"
        >
          <ChevronLeft size={18} />
        </button>
        <button
          onClick={onNext}
          disabled={!valid}
          className="flex-1 flex items-center justify-center gap-2 bg-theme-accent text-white py-4 rounded-xl text-base font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          Continue
          <ChevronRight size={18} />
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 3 - Stack picker
// ---------------------------------------------------------------------------

function StackStep({ stack, setStack, onNext, onBack }) {
  return (
    <div>
      <div className="w-16 h-16 rounded-2xl bg-theme-accent/15 text-theme-accent flex items-center justify-center mb-6">
        <Layers size={32} />
      </div>
      <h1 className="text-3xl font-bold text-theme-text mb-3">Pick your stack.</h1>
      <p className="text-theme-text-muted mb-6 leading-relaxed">
        You can change this later. Start small if you want and add things as you go.
      </p>

      <div className="space-y-3 mb-8">
        {STACK_OPTIONS.map(opt => {
          const Icon = opt.Icon
          const selected = stack === opt.id
          return (
            <button
              key={opt.id}
              onClick={() => setStack(opt.id)}
              className={`w-full text-left p-4 rounded-xl border-2 transition-colors flex gap-4 ${
                selected
                  ? 'border-theme-accent bg-theme-accent/10'
                  : 'border-theme-border bg-theme-card hover:border-theme-text-muted'
              }`}
            >
              <div className={`w-12 h-12 rounded-xl flex items-center justify-center flex-shrink-0 ${
                selected ? 'bg-theme-accent text-white' : 'bg-theme-border text-theme-text-muted'
              }`}>
                <Icon size={24} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-theme-text">{opt.title}</span>
                  {selected && <Check size={18} className="text-theme-accent flex-shrink-0" />}
                </div>
                <p className="text-sm text-theme-text-muted mt-1">{opt.blurb}</p>
              </div>
            </button>
          )
        })}
      </div>

      <div className="flex gap-3">
        <button
          onClick={onBack}
          className="flex items-center justify-center gap-2 bg-theme-card border border-theme-border text-theme-text py-4 px-5 rounded-xl"
        >
          <ChevronLeft size={18} />
        </button>
        <button
          onClick={onNext}
          className="flex-1 flex items-center justify-center gap-2 bg-theme-accent text-white py-4 rounded-xl text-base font-medium hover:opacity-90 transition-opacity"
        >
          Continue
          <ChevronRight size={18} />
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 4 - Confirm & finish
// ---------------------------------------------------------------------------

function ConfirmStep({ deviceName, username, stack, onBack, onFinish, finishing, error }) {
  const stackTitle = STACK_OPTIONS.find(s => s.id === stack)?.title || stack
  return (
    <div>
      <h1 className="text-3xl font-bold text-theme-text mb-6">Ready?</h1>
      <p className="text-theme-text-muted mb-6 leading-relaxed">
        Tap Finish and we&apos;ll generate the first invite. Bring it up on a phone or share the QR.
      </p>

      <dl className="bg-theme-card border border-theme-border rounded-xl divide-y divide-theme-border mb-8">
        <Row label="Setup label" value={deviceName.trim() || 'dream'} hint="invite audit note" />
        <Row label="First user" value={username.trim()} />
        <Row label="Stack" value={stackTitle} hint="enable extras from Extensions" />
      </dl>

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-red-400 text-sm flex items-start gap-2">
          <AlertCircle size={18} className="flex-shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex gap-3">
        <button
          onClick={onBack}
          disabled={finishing}
          className="flex items-center justify-center gap-2 bg-theme-card border border-theme-border text-theme-text py-4 px-5 rounded-xl disabled:opacity-50"
        >
          <ChevronLeft size={18} />
        </button>
        <button
          onClick={onFinish}
          disabled={finishing}
          className="flex-1 flex items-center justify-center gap-2 bg-theme-accent text-white py-4 rounded-xl text-base font-medium hover:opacity-90 disabled:opacity-50 transition-opacity"
        >
          {finishing && <Loader2 size={18} className="animate-spin" />}
          {finishing ? 'Finishing...' : 'Finish'}
        </button>
      </div>
    </div>
  )
}

function Row({ label, value, hint }) {
  return (
    <div className="px-4 py-3 flex items-center justify-between gap-4">
      <span className="text-sm text-theme-text-muted">{label}</span>
      <span className="text-theme-text font-medium text-right">
        {value}
        {hint && <span className="text-xs text-theme-text-muted block font-normal">{hint}</span>}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Done - show generated invite
// ---------------------------------------------------------------------------

function DoneScreen({ invite, onDone }) {
  const [copied, setCopied] = useState(false)
  const [qrDataUrl, setQrDataUrl] = useState(null)
  const [qrError, setQrError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const loadQr = async () => {
      try {
        const resp = await fetch(
          `/api/auth/magic-link/qr?url=${encodeURIComponent(invite.url)}`,
        )
        if (!resp.ok) {
          if (!cancelled) setQrError('QR generation unavailable on the server.')
          return
        }
        const data = await resp.json()
        if (!cancelled) setQrDataUrl(data.data_url)
      } catch (err) {
        if (!cancelled) setQrError(err.message)
      }
    }
    loadQr()
    return () => { cancelled = true }
  }, [invite.url])

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(invite.url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback: a visible input would let the user select manually.
    }
  }

  const share = async () => {
    if (!navigator.share) {
      copy()
      return
    }
    try {
      await navigator.share({
        title: `Dream Server invite for ${invite.target_username}`,
        text: 'Tap to open Dream Server',
        url: invite.url,
      })
    } catch {
      // User cancelled.
    }
  }

  return (
    <div>
      <div className="w-16 h-16 rounded-2xl bg-green-500/15 text-green-400 flex items-center justify-center mb-6">
        <Check size={32} />
      </div>
      <h1 className="text-3xl font-bold text-theme-text mb-3">You&apos;re set.</h1>
      <p className="text-theme-text-muted mb-6 leading-relaxed">
        Here&apos;s the magic link for <strong className="text-theme-text">{invite.target_username}</strong>.
        They scan or tap it to reach the chat surface. (Open WebUI may still prompt for a
        sign-in until SSO is wired up.)
      </p>

      {qrDataUrl ? (
        <div className="bg-white p-4 rounded-xl flex justify-center mb-6">
          <img src={qrDataUrl} alt="QR code for invite link" className="w-56 h-56" />
        </div>
      ) : (
        <div className="bg-theme-card border border-theme-border rounded-xl p-8 flex flex-col items-center justify-center mb-6 min-h-56">
          <QrCode size={48} className="text-theme-text-muted mb-2" />
          <p className="text-xs text-theme-text-muted text-center">
            {qrError || 'Generating QR...'}
          </p>
        </div>
      )}

      <div className="flex gap-2 mb-6">
        <input
          readOnly
          value={invite.url}
          onFocus={e => e.target.select()}
          className="flex-1 bg-theme-card border border-theme-border rounded-lg px-3 py-2 text-xs font-mono text-theme-text"
        />
        <button
          onClick={copy}
          title="Copy link"
          aria-label="Copy invite link"
          className="flex items-center gap-1 px-3 py-2 bg-theme-card border border-theme-border rounded-lg text-theme-text hover:bg-theme-surface-hover text-sm"
        >
          {copied ? <Check size={16} className="text-green-400" /> : <Copy size={16} />}
        </button>
      </div>

      <div className="flex gap-3">
        {typeof navigator !== 'undefined' && navigator.share && (
          <button
            onClick={share}
            className="flex-1 flex items-center justify-center gap-2 bg-theme-card border border-theme-border text-theme-text py-4 rounded-xl"
          >
            <Share2 size={18} />
            Share
          </button>
        )}
        <button
          onClick={onDone}
          className="flex-1 bg-theme-accent text-white py-4 rounded-xl font-medium hover:opacity-90 transition-opacity"
        >
          Open dashboard
        </button>
      </div>

      <p className="text-xs text-theme-text-muted mt-6 text-center">
        Need more invites later? They live under <strong>Invites</strong> in the sidebar.
      </p>
    </div>
  )
}
