import { Link } from 'react-router-dom'

// One plain line per state the owner must act on, as Portal's status reports it.
const LINES = {
  'needs-reconnect': 'Google needs reconnecting.',
  'missing-permission': 'Google is missing a permission Portal needs.',
  'setup-broken': "Google's setup needs a fix.",
  'blocked-by-admin': 'Your administrator blocks Google for Portal.',
  'unknown-error': 'Google sync has a problem.',
  behind: 'Google sync stopped.',
}

export function connectedAppsAttention(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  if (value.step === 'update-stopped') return "Portal hasn't picked up your Google change yet."
  if (value.step === 'changes-waiting') {
    return Number.isInteger(value.waiting) && value.waiting > 1
      ? `${value.waiting} calendar changes from Portal wait for your approval.`
      : 'A calendar change from Portal waits for your approval.'
  }
  return Object.hasOwn(LINES, value.state) ? LINES[value.state] : null
}

// Portal's chat points the owner to Connected apps when their Google connection
// needs them, or a calendar change Portal suggested waits for their approval.
// Drawn from Portal's status only, never from Portal's words; the status carries
// nothing while connected apps are off.
export default function ConnectedAppsNotice({ value }) {
  const text = connectedAppsAttention(value)
  if (!text) return null
  return <p role="status" aria-label="Connected apps" className="shrink-0 border-b border-theme-border px-4 py-2 text-xs text-theme-text-secondary sm:px-6">
    {text}{' '}<Link className="text-theme-accent-light underline" to="/settings?section=apps">Open Connected apps</Link>
  </p>
}
