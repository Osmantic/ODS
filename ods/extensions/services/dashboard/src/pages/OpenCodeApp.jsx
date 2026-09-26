import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Code, Copy, ExternalLink, Loader2, Play, Download, RefreshCw } from 'lucide-react'
import { dashboardHost, isLoopbackBrowser } from '../lib/serviceUrls'
import './opencode-app.css'

// OpenCode runs on the ODS host (systemd user unit, LaunchAgent, or scheduled
// task) and listens only on 127.0.0.1. This page reports its real lifecycle
// and offers the one action that applies: open, start, or set up.

const STATE_COPY = {
  running: { label: 'Running', tone: 'ok' },
  starting: { label: 'Starting', tone: 'busy' },
  installing: { label: 'Setting up', tone: 'busy' },
  stopped: { label: 'Stopped', tone: 'warn' },
  not_installed: { label: 'Not set up', tone: 'muted' },
}

const TROUBLESHOOTING = {
  linux: [
    'systemctl --user status opencode-web.service',
    'journalctl --user -u opencode-web.service --follow',
  ],
  darwin: [
    'launchctl print "gui/$(id -u)/com.ods.opencode-web"',
    'tail -f "$HOME/Library/Logs/ODS/opencode-web.log"',
  ],
  windows: [
    'Get-ScheduledTask -TaskName ODSOpenCodeWeb',
    '.\\ods.ps1 restart opencode',
  ],
}

function attachCommand(platform, port) {
  const target = `http://localhost:${port}`
  if (platform === 'windows') return `& "$env:USERPROFILE\\.opencode\\bin\\opencode.exe" attach ${target}`
  return `~/.opencode/bin/opencode attach ${target}`
}

function tunnelCommand(port) {
  // On the ODS machine itself the dashboard host is a loopback name, which is
  // not how another device reaches it.
  const host = isLoopbackBrowser() ? '<ods-host>' : (dashboardHost() || '<ods-host>')
  return `ssh -N -L ${port}:127.0.0.1:${port} <user>@${host}`
}

async function readJson(response) {
  try {
    return await response.json()
  } catch {
    return {}
  }
}

function CommandLine({ command, label }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }
  return (
    <div className="opencode-command">
      <code aria-label={label}>{command}</code>
      <button type="button" onClick={copy} aria-label={`Copy ${label}`} title="Copy">
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
    </div>
  )
}

export default function OpenCodeApp() {
  const [app, setApp] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [busy, setBusy] = useState('')
  const mounted = useRef(true)

  const load = useCallback(async () => {
    try {
      const response = await fetch('/api/apps/opencode', { cache: 'no-store', signal: AbortSignal.timeout(15000) })
      const body = await readJson(response)
      if (!mounted.current) return
      if (!response.ok) {
        setLoadError(typeof body.detail === 'string' ? body.detail : 'OpenCode status is unavailable right now.')
        return
      }
      setLoadError('')
      setApp(body)
    } catch {
      if (mounted.current) setLoadError('OpenCode status is unavailable right now.')
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    load()
    return () => { mounted.current = false }
  }, [load])

  const state = app?.state
  useEffect(() => {
    if (!state) return undefined
    const pending = state === 'installing' || state === 'starting'
    const timer = setInterval(load, pending ? 2000 : 15000)
    return () => clearInterval(timer)
  }, [state, load])

  const act = async (action) => {
    setBusy(action)
    setActionError('')
    try {
      const response = await fetch(`/api/apps/opencode/${action}`, {
        method: 'POST',
        signal: AbortSignal.timeout(action === 'start' ? 170000 : 45000),
      })
      const body = await readJson(response)
      if (!mounted.current) return
      if (body.opencode) setApp(body.opencode)
      if (!response.ok) {
        setActionError(typeof body.detail === 'string' ? body.detail : `Could not ${action === 'setup' ? 'set up' : 'start'} OpenCode.`)
      }
    } catch {
      if (mounted.current) setActionError(`Could not ${action === 'setup' ? 'set up' : 'start'} OpenCode. Check that the ODS host agent is running.`)
    } finally {
      if (mounted.current) setBusy('')
      load()
    }
  }

  const copy = STATE_COPY[state] || { label: 'Checking', tone: 'muted' }
  const port = app?.port || 3003
  const onThisMachine = isLoopbackBrowser()
  const openUrl = app?.publicUrl || (onThisMachine ? app?.localUrl : null)
  const setupError = app?.progress?.status === 'error' ? app.progress.error : ''

  return (
    <div className="opencode-app p-8">
      <header className="opencode-header">
        <div className="opencode-title">
          <span className="opencode-symbol"><Code size={20} /></span>
          <div>
            <h1>OpenCode</h1>
            <p>AI coding assistant that runs on this ODS machine and uses your active ODS model.</p>
          </div>
        </div>
        <div className={`opencode-state is-${copy.tone}`} role="status" aria-label={`OpenCode is ${copy.label.toLowerCase()}`}>
          <span />
          {copy.label}{state === 'running' && app?.version ? ` · v${app.version}` : ''}
        </div>
      </header>

      {loadError && (
        <div className="opencode-alert" role="alert">
          <AlertTriangle size={15} />
          <span>{loadError}</span>
          <button type="button" onClick={load}><RefreshCw size={12} /> Retry</button>
        </div>
      )}

      {!app && !loadError && (
        <section className="opencode-panel" aria-busy="true"><Loader2 size={15} className="animate-spin" /> Checking OpenCode…</section>
      )}

      {app && (
        <section className="opencode-panel opencode-primary" aria-label="OpenCode actions">
          {state === 'running' && openUrl && (
            <>
              <p>OpenCode is ready. It opens in a new tab.</p>
              <a className="opencode-button is-primary" href={openUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={14} /> Open OpenCode
              </a>
            </>
          )}

          {state === 'running' && !openUrl && (
            <>
              <p>
                OpenCode is running on <strong>{dashboardHost()}</strong>. For safety it only accepts connections from
                that machine and has no login of its own, so it cannot open directly on this device.
              </p>
              <p>To use it from here, forward its port over SSH, then open <strong>http://localhost:{port}</strong>:</p>
              <CommandLine command={tunnelCommand(port)} label="SSH port-forward command" />
              <a className="opencode-button" href={app.localUrl} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={14} /> Open http://localhost:{port}
              </a>
            </>
          )}

          {(state === 'starting' || state === 'installing') && (
            <p className="opencode-progress" aria-live="polite">
              <Loader2 size={14} className="animate-spin" />
              {state === 'installing' ? (app.progress?.phaseLabel || 'Setting up OpenCode…') : 'OpenCode is starting…'}
            </p>
          )}

          {state === 'stopped' && (
            <>
              <p>
                {app.portInUse
                  ? `OpenCode is set up, but another program is using port ${port}. Stop that program, then start OpenCode.`
                  : 'OpenCode is set up but not running.'}
              </p>
              <button type="button" className="opencode-button is-primary" disabled={!!busy || !app.startSupported} onClick={() => act('start')}>
                {busy === 'start' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                {busy === 'start' ? 'Starting…' : 'Start OpenCode'}
              </button>
            </>
          )}

          {state === 'not_installed' && (
            <>
              <p>
                OpenCode isn’t set up on this ODS machine. It’s an optional extension, so it is only installed when you choose it.
              </p>
              {setupError && <p className="opencode-error" role="alert">Last setup failed: {setupError}</p>}
              {app.setupSupported ? (
                <>
                  <p className="opencode-note">
                    Setup downloads the ODS-reviewed OpenCode release, checks its SHA-256 checksum, points it at your
                    active ODS model, and runs it as a user service that listens only on 127.0.0.1:{port}.
                  </p>
                  <button type="button" className="opencode-button is-primary" disabled={!!busy} onClick={() => act('setup')}>
                    {busy === 'setup' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                    {setupError ? 'Try setup again' : 'Set up OpenCode'}
                  </button>
                </>
              ) : (
                app.setupIssue && <p className="opencode-note">{app.setupIssue}</p>
              )}
            </>
          )}

          {actionError && <p className="opencode-error" role="alert">{actionError}</p>}
        </section>
      )}

      {app && (
        <div className="opencode-guides">
          <section className="opencode-panel" aria-labelledby="opencode-how">
            <h2 id="opencode-how">How to use it</h2>
            <ol>
              <li>Open OpenCode from here or from <strong>Applications</strong> in the sidebar.</li>
              <li>Choose the project folder to work in. OpenCode runs as your user on this machine, so it can read and change any file your account can.</li>
              <li>Describe what you want. OpenCode edits files and can run commands; review its changes before you rely on them.</li>
              <li>It uses your active ODS model. When you switch models in <strong>Models</strong>, ODS updates OpenCode too.</li>
            </ol>
            <p className="opencode-note">Prefer a terminal? On this machine, attach to the same sessions with:</p>
            <CommandLine command={attachCommand(app.platform, port)} label="terminal attach command" />
          </section>

          {!(state === 'running' && !openUrl) && (
            <section className="opencode-panel" aria-labelledby="opencode-remote">
              <h2 id="opencode-remote">From another device</h2>
              <p>
                OpenCode listens only on 127.0.0.1:{port} and has no login of its own, so ODS never exposes it to the network.
                Forward the port over SSH and open <strong>http://localhost:{port}</strong> on your device:
              </p>
              <CommandLine command={tunnelCommand(port)} label="SSH tunnel command" />
            </section>
          )}

          {TROUBLESHOOTING[app.platform] && (
            <section className="opencode-panel" aria-labelledby="opencode-troubleshoot">
              <h2 id="opencode-troubleshoot">Troubleshooting on the ODS machine</h2>
              {TROUBLESHOOTING[app.platform].map(command => (
                <CommandLine key={command} command={command} label="troubleshooting command" />
              ))}
            </section>
          )}
        </div>
      )}
    </div>
  )
}
