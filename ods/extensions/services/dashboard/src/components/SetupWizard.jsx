import { useState, useEffect, useCallback, useRef } from 'react'
import { CheckCircle, Circle, ChevronRight, ChevronLeft, Mic, User, Settings, Play, Shield, Layers } from 'lucide-react'
import { PreFlightChecks } from './PreFlightChecks'
import { TemplatePicker } from './TemplatePicker'
import { getTemplateStatus } from '../lib/templates'

export default function SetupWizard({ onComplete }) {
  const [step, setStep] = useState(1)
  const [config, setConfig] = useState({
    userName: '',
    voice: 'af_heart',
    preflightPassed: false
  })
  const [testStatus, setTestStatus] = useState({ running: false, output: [], done: false, success: false })
  const [preflightIssues, setPreflightIssues] = useState([])
  const [templates, setTemplates] = useState([])
  const [extensions, setExtensions] = useState([])
  const totalSteps = 6

  // Holds the AbortController for the currently in-flight /api/setup/test
  // stream (if any). Aborting it tells the server to release the subprocess
  // and async generator so a user who abandons the wizard mid-diagnostic
  // doesn't leave the backend running curls for ~2 minutes.
  const diagControllerRef = useRef(null)

  useEffect(() => {
    return () => {
      if (diagControllerRef.current) {
        diagControllerRef.current.abort()
      }
    }
  }, [])

  // Fetches templates and extensions in parallel and applies their state
  // updates only after BOTH have settled. React 18 auto-batches the two
  // setState calls that land in the same async tick, so template cards
  // don't flash "available" for ~200ms while extensions are still in-flight.
  // Promise.allSettled lets one side fail without aborting the other.
  const refreshTemplateData = useCallback(async () => {
    const [tRes, eRes] = await Promise.allSettled([
      fetch('/api/templates').then(r => r.ok ? r.json() : { templates: [] }),
      fetch('/api/extensions/catalog').then(r => r.ok ? r.json() : { extensions: [] })
    ])
    if (tRes.status === 'fulfilled') {
      setTemplates(tRes.value.templates || [])
    } else {
      console.error('Failed to load templates:', tRes.reason)
    }
    if (eRes.status === 'fulfilled') {
      setExtensions(eRes.value.extensions || [])
    } else {
      console.error('Failed to load extensions:', eRes.reason)
    }
  }, [])

  // Re-fetch on every navigation to Step 2: the user may have just applied
  // a template on a previous visit, in which case extensions state is stale
  // and the "applied" indicator would lie.
  useEffect(() => {
    if (step !== 2) return
    refreshTemplateData()
  }, [step, refreshTemplateData])

  const voices = [
    { id: 'af_heart', name: 'Heart', desc: 'Warm, friendly female' },
    { id: 'af_bella', name: 'Bella', desc: 'Professional female' },
    { id: 'af_sky', name: 'Sky', desc: 'Casual female' },
    { id: 'am_adam', name: 'Adam', desc: 'Natural male' },
    { id: 'am_michael', name: 'Michael', desc: 'Deep male' }
  ]

  // Stable callbacks so PreFlightChecks doesn't re-run on parent re-render
  const handlePreflightComplete = useCallback(() => {
    setConfig(c => ({ ...c, preflightPassed: true }))
  }, [])

  const handlePreflightIssues = useCallback((issues) => {
    setPreflightIssues(issues)
  }, [])

  const runDiagnostics = async () => {
    // Cancel any in-flight diagnostic (re-running before previous completes).
    if (diagControllerRef.current) {
      diagControllerRef.current.abort()
    }
    const controller = new AbortController()
    diagControllerRef.current = controller

    setTestStatus({ running: true, output: ['Starting diagnostic tests...'], done: false, success: false })

    try {
      const res = await fetch('/api/setup/test', { method: 'POST', signal: controller.signal })
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      // The backend streams plain text. We split on newlines so each script
      // line becomes its own <div>, and scan for a machine-readable sentinel
      // (`__ODS_RESULT__:PASS|FAIL:<returncode>`) to determine success.
      let buffer = ''
      let resultStatus = null // 'PASS' | 'FAIL' | null
      const collected = [] // local mirror of displayed lines for fallback scan

      const pushLine = (line) => {
        const match = line.match(/^__ODS_RESULT__:(PASS|FAIL):(-?\d+)$/)
        if (match) {
          resultStatus = match[1]
          return // don't display the sentinel to the user
        }
        collected.push(line)
        setTestStatus(prev => ({ ...prev, output: [...prev.output, line] }))
      }

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() // keep the trailing partial line for the next chunk
        for (const line of lines) pushLine(line)
      }

      // Flush any decoder tail plus any remaining unterminated line.
      buffer += decoder.decode()
      if (buffer) pushLine(buffer)

      // Prefer the structured sentinel. Fall back to scanning accumulated
      // output for the human-readable trailer if the sentinel is absent
      // (older backends, truncated stream). Absence defaults to failure —
      // we refuse to greenlight a user through a stream of unknown outcome.
      const success = resultStatus !== null
        ? resultStatus === 'PASS'
        : collected.some(l => l.includes('All tests passed!'))

      setTestStatus(prev => ({ ...prev, running: false, done: true, success }))
    } catch (err) {
      // Aborted fetches throw AbortError. That's the user cancelling or the
      // component unmounting — don't surface it as a user-visible error.
      if (err.name === 'AbortError') {
        return
      }
      setTestStatus(prev => ({ ...prev, running: false, done: true, success: false, output: [...prev.output, `Error: ${err.message}`] }))
    } finally {
      if (diagControllerRef.current === controller) {
        diagControllerRef.current = null
      }
    }
  }

  const saveConfig = async () => {
    // localStorage retains the wizard's *answers* (voice / userName etc.)
    // for the dashboard to consult later, but is no longer the source of
    // truth for "have we onboarded?" — that lives on the server now.
    localStorage.setItem('ods-config', JSON.stringify(config))
    try {
      // The server endpoint writes setup-complete.json which the
      // useFirstRun hook reads. Best-effort: if it fails (e.g. dashboard-api
      // restarting mid-wizard) we still dismiss the wizard so the user
      // isn't trapped, and rely on the next refresh to converge.
      await fetch('/api/setup/complete', { method: 'POST' })
    } catch (err) {
      console.error('Failed to mark setup complete on server:', err)
    }
    onComplete()
  }

  return (
    <div className="fixed inset-0 bg-theme-bg z-50 overflow-y-auto">
      <div className="min-h-screen flex flex-col">
        <div className="flex-1 flex flex-col justify-center p-8">
          {/* Step Indicator */}
          <div className="flex items-center justify-center gap-2 mb-8">
            {[1, 2, 3, 4, 5, 6].map(i => (
              <div key={i} className="flex items-center">
                {i < step ? (
                  <CheckCircle className="w-6 h-6 text-green-500" />
                ) : i === step ? (
                  <Circle className="w-6 h-6 text-theme-accent fill-indigo-500/20" />
                ) : (
                  <Circle className="w-6 h-6 text-theme-text-muted" />
                )}
                {i < 6 && <div className={`w-8 h-0.5 mx-1 ${i < step ? 'bg-green-500' : 'bg-theme-border'}`} />}
              </div>
            ))}
          </div>

          {/* Step 1: Preflight */}
          {step === 1 && (
            <div className="text-center max-w-lg mx-auto">
              <div className="w-20 h-20 bg-amber-500/20 rounded-2xl flex items-center justify-center mx-auto mb-6">
                <Shield className="w-10 h-10 text-amber-400" />
              </div>
              <h2 className="text-3xl font-bold text-theme-text mb-4">System Check</h2>
              <p className="text-theme-text-secondary mb-8">
                Let's verify your system is ready for ODS. This checks Docker, GPU, ports, and disk space.
              </p>
              <PreFlightChecks
                onComplete={handlePreflightComplete}
                onIssuesFound={handlePreflightIssues}
              />
            </div>
          )}

          {/* Step 2: Templates (optional) */}
          {step === 2 && (
            <div className="text-center max-w-2xl mx-auto">
              <div className="w-20 h-20 bg-theme-accent/20 rounded-2xl flex items-center justify-center mx-auto mb-6">
                <Layers className="w-10 h-10 text-theme-accent" />
              </div>
              <h2 className="text-3xl font-bold text-theme-text mb-4">Choose a Template</h2>
              <p className="text-theme-text-secondary mb-8">
                Pick a pre-configured set of services to get started quickly, or skip to customize later.
              </p>
              {templates.length > 0 ? (
                <TemplatePicker
                  templates={templates.map(t => ({ ...t, _status: getTemplateStatus(t, extensions) }))}
                  compact
                  onApplied={refreshTemplateData}
                />
              ) : (
                <p className="text-sm text-theme-text-muted">No templates available.</p>
              )}
            </div>
          )}

          {/* Step 3: Welcome */}
          {step === 3 && (
            <div className="text-center max-w-lg mx-auto">
              <div className="w-20 h-20 bg-theme-accent/20 rounded-2xl flex items-center justify-center mx-auto mb-6">
                <Settings className="w-10 h-10 text-theme-accent" />
              </div>
              <h2 className="text-3xl font-bold text-theme-text mb-4">Welcome to ODS</h2>
              <p className="text-theme-text-secondary mb-8">
                Let's get your local AI set up in just a few steps.
                Everything runs on your hardware — no cloud, no subscriptions.
              </p>
              <div className="space-y-3 text-left bg-theme-card rounded-xl p-6 mb-8">
                <div className="flex items-center gap-3 text-theme-text">
                  <CheckCircle className="w-5 h-5 text-green-500" />
                  <span>Personalize your assistant</span>
                </div>
                <div className="flex items-center gap-3 text-theme-text">
                  <CheckCircle className="w-5 h-5 text-green-500" />
                  <span>Choose your voice</span>
                </div>
                <div className="flex items-center gap-3 text-theme-text">
                  <CheckCircle className="w-5 h-5 text-green-500" />
                  <span>Run diagnostics</span>
                </div>
              </div>
            </div>
          )}

          {/* Step 4: Name */}
          {step === 4 && (
            <div className="text-center max-w-md mx-auto">
              <div className="w-20 h-20 bg-purple-500/20 rounded-2xl flex items-center justify-center mx-auto mb-6">
                <User className="w-10 h-10 text-purple-400" />
              </div>
              <h2 className="text-3xl font-bold text-theme-text mb-4">What should we call you?</h2>
              <p className="text-theme-text-secondary mb-8">
                Your AI assistant will use this name when talking to you.
              </p>
              <input
                type="text"
                value={config.userName}
                onChange={(e) => setConfig(c => ({ ...c, userName: e.target.value }))}
                placeholder="Enter your name"
                className="w-full px-4 py-3 bg-theme-card border border-theme-border rounded-lg text-theme-text placeholder-theme-text-muted focus:outline-none focus:border-theme-accent"
                autoFocus
              />
            </div>
          )}

          {/* Step 5: Voice */}
          {step === 5 && (
            <div className="text-center max-w-lg mx-auto">
              <div className="w-20 h-20 bg-pink-500/20 rounded-2xl flex items-center justify-center mx-auto mb-6">
                <Mic className="w-10 h-10 text-pink-400" />
              </div>
              <h2 className="text-3xl font-bold text-theme-text mb-4">Choose a voice</h2>
              <p className="text-theme-text-secondary mb-8">
                Pick the voice your AI assistant will use when speaking to you.
              </p>
              <div className="grid gap-3">
                {voices.map(voice => (
                  <button
                    key={voice.id}
                    onClick={() => setConfig(c => ({ ...c, voice: voice.id }))}
                    className={`flex items-center gap-4 p-4 rounded-xl border transition-all text-left ${
                      config.voice === voice.id
                        ? 'border-theme-accent bg-theme-accent/10'
                        : 'border-theme-border bg-theme-card/50 hover:border-theme-border'
                    }`}
                  >
                    <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${
                      config.voice === voice.id ? 'border-theme-accent' : 'border-theme-border'
                    }`}>
                      {config.voice === voice.id && <div className="w-2.5 h-2.5 rounded-full bg-theme-accent" />}
                    </div>
                    <div className="flex-1">
                      <div className="font-medium text-theme-text">{voice.name}</div>
                      <div className="text-sm text-theme-text-muted">{voice.desc}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Step 6: Diagnostics */}
          {step === 6 && (
            <div className="text-center max-w-2xl mx-auto">
              <div className="w-20 h-20 bg-green-500/20 rounded-2xl flex items-center justify-center mx-auto mb-6">
                <Play className="w-10 h-10 text-green-400" />
              </div>
              <h2 className="text-3xl font-bold text-theme-text mb-4">Run diagnostics</h2>
              <p className="text-theme-text-secondary mb-8">
                Let's verify everything is working correctly. This will test LLM, STT, TTS, and voice pipeline.
              </p>

              {!testStatus.running && !testStatus.done && (
                <button
                  onClick={runDiagnostics}
                  className="px-6 py-3 bg-theme-accent hover:bg-theme-accent-hover text-white rounded-lg font-medium transition-colors"
                >
                  Start Diagnostics
                </button>
              )}

              {(testStatus.running || testStatus.done) && (
                <div className="bg-theme-card rounded-xl p-4 text-left font-mono text-sm max-h-64 overflow-y-auto">
                  {testStatus.output.map((line, i) => (
                    <div key={i} className="text-theme-text-secondary">{line}</div>
                  ))}
                  {testStatus.running && <div className="text-theme-accent animate-pulse">...</div>}
                </div>
              )}

              {testStatus.done && (
                <div className={`mt-4 p-4 rounded-lg ${testStatus.success ? 'bg-green-500/20 text-green-400' : 'bg-amber-500/20 text-amber-400'}`}>
                  {testStatus.success
                    ? '✓ All systems operational'
                    : '⚠ Some tests failed — review the log above. You can re-run, or continue anyway and revisit from the Diagnostics tab later.'}
                </div>
              )}

              {testStatus.done && !testStatus.success && (
                <button
                  onClick={runDiagnostics}
                  disabled={testStatus.running}
                  className="mt-4 px-4 py-2 text-sm bg-theme-card hover:bg-theme-surface-hover text-theme-text rounded-lg transition-colors disabled:opacity-50"
                >
                  Re-run Diagnostics
                </button>
              )}
            </div>
          )}
        </div>

        <div className="p-6 border-t border-theme-border">
          <div className="max-w-4xl mx-auto flex items-center justify-between">
            <button
              onClick={() => setStep(s => Math.max(1, s - 1))}
              disabled={step === 1}
              className="flex items-center gap-2 px-4 py-2 text-theme-text-secondary hover:text-theme-text disabled:opacity-0 transition-colors"
            >
              <ChevronLeft className="w-5 h-5" />
              Back
            </button>

            <div className="text-theme-text-muted text-sm">
              Step {step} of {totalSteps}
            </div>

            {step < totalSteps ? (
              <button
                onClick={() => setStep(s => s + 1)}
                disabled={step === 4 && !config.userName.trim()}
                className="flex items-center gap-2 px-6 py-2 bg-theme-accent hover:bg-theme-accent-hover disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors"
              >
                Next
                <ChevronRight className="w-5 h-5" />
              </button>
            ) : (
              <button
                onClick={saveConfig}
                disabled={!testStatus.done}
                className={`flex items-center gap-2 px-6 py-2 ${
                  testStatus.success
                    ? 'bg-green-600 hover:bg-green-700'
                    : 'bg-amber-600 hover:bg-amber-700'
                } disabled:bg-zinc-700 disabled:cursor-not-allowed text-white rounded-lg transition-colors`}
              >
                <CheckCircle className="w-5 h-5" />
                {testStatus.success ? 'Complete Setup' : 'Continue Anyway'}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
