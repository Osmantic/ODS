import { useState, useEffect, useRef } from 'react'
import { CheckCircle, XCircle, AlertCircle, Loader2, Wifi, Cpu, HardDrive, Layers } from 'lucide-react'

export function PreFlightChecks({ onComplete, onIssuesFound }) {
  const [checks, setChecks] = useState([])
  const [running, setRunning] = useState(true)

  const active = useRef(null)

  const cancelChecks = () => {
    const previous = active.current
    active.current = null
    if (previous) {
      clearTimeout(previous.timer)
      previous.controller.abort()
    }
  }

  useEffect(() => {
    void runChecks()
    return cancelChecks
  }, [])

  const runChecks = async () => {
    cancelChecks()
    const flight = { controller: new AbortController() }
    const signal = flight.controller.signal
    active.current = flight
    const current = () => active.current === flight && !signal.aborted
    setRunning(true)
    setChecks([])
    // Bound the entire readiness pass, including the initial port inventory
    // and response bodies. An abandoned pass must never complete a later one.
    flight.timer = setTimeout(() => {
      if (!current()) return
      cancelChecks()
      const issue = {
        name: 'System Readiness', icon: AlertCircle, status: 'error',
        message: 'System checks timed out',
        fix: 'Check the dashboard connection, then retry the checks.',
      }
      setChecks(previous => [...previous.filter(check => check.status !== 'checking'), issue])
      setRunning(false)
      onIssuesFound?.([issue])
    }, 30000)

    try {
      let ports = []
      try {
        const response = await fetch('/api/preflight/required-ports', { signal })
        const data = response.ok ? await response.json() : {}
        ports = data?.ports || []
      } catch { /* Individual checks retain their existing warning behavior. */ }
      if (!current()) return

      const results = []
      const steps = [
        ['Docker Available', Layers, 500, () => checkDocker(signal)],
        ['GPU Detected', Cpu, 500, () => checkGPU(signal)],
        ['Port Availability', Wifi, 800, () => checkPorts(ports, signal)],
        ['Disk Space', HardDrive, 500, () => checkDiskSpace(signal)],
      ]
      for (const [name, icon, delay, check] of steps) {
        results.push({ name, icon, status: 'checking' })
        setChecks([...results])
        await new Promise(resolve => setTimeout(resolve, delay))
        if (!current()) return
        const result = await check()
        if (!current()) return
        results[results.length - 1] = { name, icon, ...result }
        setChecks([...results])
      }
      setRunning(false)
      const errors = results.filter(result => result.status === 'error')
      if (errors.length > 0) onIssuesFound?.(errors)
      else onComplete?.()
    } finally {
      clearTimeout(flight.timer)
      if (active.current === flight) active.current = null
    }
  }

  const checkDocker = async (signal) => {
    try {
      const response = await fetch('/api/preflight/docker', { signal })
      if (!response.ok) {
        return { status: 'warning', message: `API error (${response.status})`, fix: 'Check dashboard-api logs' }
      }
      const data = await response.json()
      if (data.available) {
        return { status: 'success', message: `Docker ${data.version}` }
      }
      return { status: 'error', message: 'Docker not available', fix: 'Install Docker or ensure service is running' }
    } catch (e) {
      return { status: 'warning', message: 'Check skipped', details: e.message }
    }
  }

  const checkGPU = async (signal) => {
    try {
      const response = await fetch('/api/preflight/gpu', { signal })
      if (!response.ok) {
        return { status: 'warning', message: `API error (${response.status})`, fix: 'Check dashboard-api logs' }
      }
      const data = await response.json()
      if (data.available) {
        const vramLabel = data.memory_type === 'unified' ? data.memory_label : `${data.vram}GB VRAM`
        return { status: 'success', message: `${data.name} (${vramLabel})` }
      }
      // Use backend-specific error from API if available
      if (data.error) {
        return { status: 'warning', message: 'No GPU detected', fix: data.error }
      }
      return { status: 'warning', message: 'No GPU detected', fix: 'Check GPU drivers are installed' }
    } catch (e) {
      return { status: 'warning', message: 'Check skipped', details: e.message }
    }
  }

  const checkPorts = async (ports, signal) => {
    try {
      const response = await fetch('/api/preflight/ports', {
        signal,
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ports: ports.map(p => p.port) })
      })
      if (!response.ok) {
        return { status: 'warning', message: `API error (${response.status})`, fix: 'Check dashboard-api logs' }
      }
      const data = await response.json()
      const conflicts = data.conflicts || []

      if (conflicts.length === 0) {
        return { status: 'success', message: `${ports.length} ports available` }
      }

      // Ports in use by ODS services are expected, not conflicts
      // If all "conflicts" are our own services, treat as success
      const odsPorts = new Set(ports.map(p => p.port))
      const allOurs = conflicts.every(c => odsPorts.has(c.port))

      if (allOurs) {
        return { status: 'success', message: `${conflicts.length} services already running` }
      }

      const conflictList = conflicts.map(c => `Port ${c.port} (${c.service})`).join(', ')
      return {
        status: 'warning',
        message: `${conflicts.length} port(s) in use`,
        details: conflictList,
        fix: 'Some ports are in use. Edit .env to change port assignments if needed'
      }
    } catch (e) {
      return { status: 'warning', message: 'Check skipped', details: e.message }
    }
  }

  const checkDiskSpace = async (signal) => {
    try {
      const response = await fetch('/api/preflight/disk', { signal })
      if (!response.ok) {
        return { status: 'warning', message: `API error (${response.status})`, fix: 'Check dashboard-api logs' }
      }
      const data = await response.json()
      const gb = Math.round(data.free / 1e9)
      
      if (gb < 20) {
        return { status: 'error', message: `${gb}GB free`, fix: 'Need at least 20GB for models' }
      }
      if (gb < 50) {
        return { status: 'warning', message: `${gb}GB free`, details: 'OK for minimal install' }
      }
      return { status: 'success', message: `${gb}GB free` }
    } catch (e) {
      return { status: 'warning', message: 'Check skipped', details: e.message }
    }
  }

  const getStatusIcon = (check) => {
    if (check.status === 'checking') {
      return <Loader2 className="w-5 h-5 text-theme-accent animate-spin" />
    }
    if (check.status === 'success') {
      return <CheckCircle className="w-5 h-5 text-emerald-400" />
    }
    if (check.status === 'error') {
      return <XCircle className="w-5 h-5 text-red-400" />
    }
    return <AlertCircle className="w-5 h-5 text-amber-400" />
  }

  const getStatusClass = (status) => {
    if (status === 'success') return 'border-emerald-500/30 bg-emerald-500/5'
    if (status === 'error') return 'border-red-500/30 bg-red-500/5'
    if (status === 'warning') return 'border-amber-500/30 bg-amber-500/5'
    return 'border-theme-border bg-theme-card/50'
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium text-theme-text mb-3">
        {running ? 'Checking system readiness...' : 'System checks complete'}
      </h3>
      
      {checks.map((check, i) => {
        const Icon = check.icon || CheckCircle
        return (
          <div 
            key={i}
            className={`flex items-start gap-3 p-3 rounded-lg border ${getStatusClass(check.status)} transition-all duration-300`}
          >
            <div className="mt-0.5">
              {getStatusIcon(check)}
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <Icon className="w-4 h-4 text-theme-text-muted" />
                <span className="text-sm font-medium text-theme-text">{check.name}</span>
              </div>
              <p className={`text-sm mt-1 ${
                check.status === 'error' ? 'text-red-300' :
                check.status === 'warning' ? 'text-amber-300' :
                check.status === 'success' ? 'text-emerald-300' :
                'text-theme-text-secondary'
              }`}>
                {check.message}
              </p>
              {check.details && (
                <p className="text-xs text-theme-text-muted mt-1">{check.details}</p>
              )}
              {check.fix && (
                <div className="mt-2 p-2 bg-theme-card rounded text-xs text-theme-text-muted">
                  <span className="text-theme-accent font-medium">Fix:</span> {check.fix}
                </div>
              )}
            </div>
          </div>
        )
      })}

      {!running && checks.some(c => c.status === 'error') && (
        <div className="mt-4 p-4 bg-red-500/10 border border-red-500/30 rounded-lg">
          <p className="text-sm text-red-300 font-medium">Issues found that may prevent installation</p>
          <p className="text-xs text-red-200/70 mt-1">
            Fix the issues above, then click Retry to run checks again.
          </p>
          <button
            onClick={() => runChecks()}
            className="mt-3 px-4 py-2 bg-red-500/20 hover:bg-red-500/30 text-red-200 text-sm rounded-lg transition-colors"
          >
            Retry Checks
          </button>
        </div>
      )}
    </div>
  )
}
