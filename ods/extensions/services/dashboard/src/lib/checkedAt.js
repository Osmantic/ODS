// "When did ODS last look?" as a short phrase. Times come from the server, so a
// browser clock that runs slightly behind must never produce "in the future".
export function formatCheckedAt(iso, now = Date.now()) {
  const time = Date.parse(iso || '')
  if (!Number.isFinite(time)) return 'Not checked yet'
  const seconds = Math.max(0, Math.round((now - time) / 1000))
  if (seconds < 10) return 'Checked just now'
  if (seconds < 60) return `Checked ${seconds} s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `Checked ${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `Checked ${hours} h ago`
  return `Checked ${new Date(time).toLocaleDateString()}`
}
