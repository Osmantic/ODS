const byId = id => document.getElementById(id)
const number = value => typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0
const time = value => {
  if (typeof value !== 'string' || !value) return 'N/A'
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : 'N/A'
}

function renderMetrics(metrics) {
  const cluster = metrics.cluster || {}
  const agent = metrics.agent || {}
  const throughput = metrics.throughput || {}
  byId('cluster').textContent = `${number(cluster.active_gpus)}/${number(cluster.total_gpus)} GPUs`
  byId('failover').textContent = cluster.failover_ready === true ? 'Failover ready' : 'Failover unavailable'
  byId('sessions').textContent = String(number(agent.session_count))
  byId('agent-update').textContent = `Updated: ${time(agent.last_update)}`
  byId('throughput-current').textContent = number(throughput.current).toFixed(1)
  byId('throughput-average').textContent = number(throughput.average).toFixed(1)

  const history = Array.isArray(throughput.history) ? throughput.history.slice(-30) : []
  const samples = history.filter(sample => sample && Number.isFinite(sample.tokens_per_sec))
  const maximum = Math.max(1, ...samples.map(sample => number(sample.tokens_per_sec)))
  const points = samples.map((sample, index) => {
    const x = samples.length > 1 ? index * 600 / (samples.length - 1) : 300
    return `${x},${160 - number(sample.tokens_per_sec) * 160 / maximum}`
  })
  byId('throughput-line').setAttribute('points', points.join(' '))
  byId('history-summary').textContent = samples.length
    ? `${samples.length} samples, ${time(samples[0].timestamp)} – ${time(samples[samples.length - 1].timestamp)}`
    : 'No samples yet'
}

let active = true
let pending = false
let timer
let controller
const refresh = byId('refresh')

async function loadMetrics() {
  if (pending || !active) return
  pending = true
  clearTimeout(timer)
  refresh.disabled = true
  controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 8000)
  try {
    const response = await fetch('/api/agents/metrics', { signal: controller.signal })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const metrics = await response.json()
    if (!metrics || typeof metrics !== 'object' || Array.isArray(metrics)) throw new Error('Invalid metrics response')
    if (!active) return
    renderMetrics(metrics)
    byId('error').hidden = true
    byId('refresh-status').textContent = `Last refreshed: ${new Date().toLocaleTimeString()}`
  } catch (error) {
    if (!active) return
    byId('error').textContent = `Agent metrics unavailable: ${error.message}. Use Refresh metrics to try again.`
    byId('error').hidden = false
    byId('refresh-status').textContent = 'Metrics could not be refreshed'
  } finally {
    clearTimeout(timeout)
    pending = false
    if (active) {
      refresh.disabled = false
      timer = setTimeout(loadMetrics, 5000)
    }
  }
}

refresh.addEventListener('click', loadMetrics)
window.addEventListener('pagehide', () => {
  active = false
  clearTimeout(timer)
  controller?.abort()
})
loadMetrics()
window.addEventListener('pageshow', event => {
  if (event.persisted) {
    active = true
    loadMetrics()
  }
})
