/**
 * formatUptime — render a duration in seconds as "1d 1h 1m".
 *
 * Shared by the Dashboard and Settings pages, which both display the same
 * `uptime` value from /api/settings/summary. Keeping one implementation stops
 * the two from disagreeing about long uptimes, where dropping the day unit
 * turns three days into "83h 20m".
 *
 * Falsy input (0, null, undefined — a service that is not running, or a field
 * the API did not return) renders as an em dash rather than "0m", because the
 * value is absent rather than zero.
 *
 * @param {number} seconds
 * @returns {string}
 */
export function formatUptime(seconds) {
  if (!seconds) return '—'
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h ${mins}m`
  if (hours > 0) return `${hours}h ${mins}m`
  return `${mins}m`
}
