const COLUMNS = [
  'timestamp',
  'gpu_index',
  'utilization_percent',
  'memory_percent',
  'temperature_c',
  'power_w',
]

function csvCell(value) {
  if (value == null) return ''
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : ''
  let text = String(value)
  if (/^[=+\-@]/.test(text)) text = `'${text}`
  if (/[",\r\n]/.test(text)) return `"${text.replaceAll('"', '""')}"`
  return text
}

function compareGpuKeys(left, right) {
  const leftNumber = Number(left)
  const rightNumber = Number(right)
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    return leftNumber - rightNumber
  }
  return left.localeCompare(right)
}

export function gpuHistoryToCsv(history) {
  const timestamps = Array.isArray(history?.timestamps) ? history.timestamps : []
  const gpus = history?.gpus && typeof history.gpus === 'object' ? history.gpus : {}
  const gpuKeys = Object.keys(gpus).sort(compareGpuKeys)
  const rows = [COLUMNS]

  timestamps.forEach((timestamp, sampleIndex) => {
    gpuKeys.forEach(gpuKey => {
      const metrics = gpus[gpuKey] || {}
      rows.push([
        timestamp,
        gpuKey,
        metrics.utilization?.[sampleIndex],
        metrics.memory_percent?.[sampleIndex],
        metrics.temperature?.[sampleIndex],
        metrics.power_w?.[sampleIndex],
      ])
    })
  })

  return `${rows.map(row => row.map(csvCell).join(',')).join('\r\n')}\r\n`
}

export function downloadGpuHistoryCsv(history, now = new Date()) {
  if (!history?.timestamps?.length) return null

  const csv = gpuHistoryToCsv(history)
  const blob = new window.Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = window.URL.createObjectURL(blob)
  const timestamp = now.toISOString().replace(/\.\d{3}Z$/, 'Z').replaceAll(':', '-')
  const filename = `ods-gpu-history-${timestamp}.csv`
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  try {
    link.click()
  } finally {
    link.remove()
    setTimeout(() => window.URL.revokeObjectURL(url), 1000)
  }
  return filename
}
